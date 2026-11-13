"""RH-08D-04: governed discovery intake. Disabled by default.

A reviewed path from a discovery result to the owner's saved candidates, their Research article associations and a
recorded Library review proposal:

- **Provenance is server-held.** A candidate can be saved only from a search this process ran: with intake enabled the
  search response carries a short-lived ``run_handle``, and saving names the handle and a candidate id. Clients never
  supply the metadata or the provenance they want stored.
- **Deduplication** is per owner: by normalised DOI, otherwise by a hash of normalised title, year and first author.
  The key is a hash, so long DOIs cannot collide by truncation. A repeat save adds only the new provider records to the existing candidate; a same-title candidate with another
  identity is flagged as a possible duplicate, never merged.
- **Authorisation** reuses the Research write identity: the staging service credential names the owner, and anyone
  else needs a Workspace-signed editor attestation (``editorial.resolve_actor``). Owners never see each other's
  candidates. Editors may save and associate only within the Workspace project their grant covers; only the owner
  can dismiss a candidate or propose it to the Library.
- **Separation is unchanged.** A saved candidate is still discovered, unreviewed metadata: not Library-reviewed, not
  Research evidence, not publishable. A Library proposal is recorded here with ``sent = False``; nothing is
  transmitted, because Library submission is not authorised (``LIBRARY_SUBMISSION_AUTHORIZED``) and
  ``library_intake.submit`` still refuses.
- Every change appends to a hash-chained audit log that holds ids and hashes only, never search text.
- **One damaged record never fails the owner's list.** A saved candidate whose stored metadata cannot be read is shown
  with the ``metadata-invalid`` blocking issue and cannot be proposed; the others are unaffected (RH-08D-05 F1).

The gate: ``RESEARCH_DISCOVERY_INTAKE=staging`` with ``RESEARCH_ENV`` staging or development, and Research write
identity enabled. Production is refused by ``PRODUCTION_INTAKE_AUTHORIZED``.
"""
from __future__ import annotations

import os
import re
import secrets
import threading
import time
import unicodedata
from dataclasses import dataclass

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
import json
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import convergence as cv
from . import editorial as ed
from . import workspaces as ws
from .discovery_intake_store import (  # noqa: F401 (re-exported)
    DiscoveryAssociation, DiscoveryCandidateSource, DiscoveryIntakeEvent, DiscoveryLibraryProposal,
    DiscoverySavedCandidate,
)
from .discovery_adapters.library_intake import blocking_issues, draft_proposal
from .discovery_adapters.models import Candidate, CandidateSource, FieldConflict

STAGE = "RH-08D-04"
GATE_ENV = "RESEARCH_DISCOVERY_INTAKE"
PRODUCTION_INTAKE_AUTHORIZED = False
LIBRARY_SUBMISSION_AUTHORIZED = False
EVENT_SCHEMA = "sc-research-discovery-intake-event/1.0"
GENESIS = "0" * 64
SAVED, DISMISSED = "saved-unreviewed", "dismissed"
PROPOSAL_STATUS = "awaiting-library-endpoint"
METADATA_INVALID = "metadata-invalid"
log = logging.getLogger("discovery-intake")
RELATION = "related-candidate"
# Fields whose disagreement between provider records a Library reviewer must see.
REVIEW_FIELDS = ("doi", "title", "publication_year", "venue", "work_type", "retracted")
HANDLE_TTL_SECONDS = 900
MAX_HANDLES = 32
SEPARATION = {"classification": "discovered-unreviewed", "review_status": "unreviewed", "library_reviewed": False,
              "research_evidence": False, "publishable": False}


# ---------------------------------------------------------------------------
# Gate


@dataclass(frozen=True)
class IntakeState:
    enabled: bool
    reason: str


def intake_state(env=None) -> IntakeState:
    env = os.environ if env is None else env
    mode = (env.get(GATE_ENV) or "disabled").strip().lower()
    environment = (env.get("RESEARCH_ENV") or "").strip().lower()
    if mode == "disabled":
        return IntakeState(False, "mode-disabled")
    if mode != "staging":
        return IntakeState(False, "unknown-mode")
    if environment == "production" and not PRODUCTION_INTAKE_AUTHORIZED:
        return IntakeState(False, "production-not-authorized")
    if environment not in {"staging", "development"}:
        return IntakeState(False, "environment-not-staging")
    if not cv.writes_enabled():
        return IntakeState(False, "identity-gate-closed")
    return IntakeState(True, "active")


# ---------------------------------------------------------------------------
# Run handles: the server's own copy of recent search results


class RunHandles:
    """Recent discovery results, in memory only, so saves take metadata and provenance from the server."""

    def __init__(self, ttl=HANDLE_TTL_SECONDS, capacity=MAX_HANDLES, clock=time.monotonic):
        self._ttl, self._capacity, self._clock = ttl, capacity, clock
        self._runs: dict[str, tuple[float, dict[str, Candidate]]] = {}
        self._lock = threading.Lock()

    def put(self, candidates) -> str:
        handle = "run-" + secrets.token_urlsafe(18)
        with self._lock:
            self._expire()
            while len(self._runs) >= self._capacity:
                self._runs.pop(next(iter(self._runs)))
            self._runs[handle] = (self._clock() + self._ttl, {c.candidate_id: c for c in candidates})
        return handle

    def candidate(self, handle: str, candidate_id: str) -> Candidate:
        with self._lock:
            self._expire()
            found = self._runs.get(handle, (0, {}))[1].get(candidate_id)
        if found is None:
            raise LookupError("This search result is no longer available; search again before saving")
        return found

    def _expire(self):
        now = self._clock()
        for handle in [h for h, (expires, _) in self._runs.items() if expires <= now]:
            del self._runs[handle]


HANDLES = RunHandles()


# ---------------------------------------------------------------------------
# Identity keys


def _norm(text: str | None) -> str:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    return re.sub(r"[\W_]+", " ", text).strip()


def _first_author(candidate: Candidate) -> str:
    return _norm(candidate.authors[0].name).split(" ")[-1] if candidate.authors else ""


def title_key(candidate: Candidate) -> str:
    return cv.canonical_sha256([_norm(candidate.title), candidate.publication_year])


def dedup_key(candidate: Candidate) -> str:
    if candidate.doi:
        return "doi:" + cv.canonical_sha256(candidate.doi.lower())
    return "meta:" + cv.canonical_sha256([_norm(candidate.title), candidate.publication_year,
                                          _first_author(candidate)])


def candidate_metadata(candidate: Candidate) -> dict:
    return candidate.model_dump(mode="json", exclude={"sources"})


# ---------------------------------------------------------------------------
# Audit chain


def _event(session: Session, actor: ed.Actor, action: str, saved: DiscoverySavedCandidate | None = None,
           detail: dict | None = None) -> DiscoveryIntakeEvent:
    last = session.scalar(select(DiscoveryIntakeEvent).order_by(DiscoveryIntakeEvent.seq.desc()).limit(1)
                          .with_for_update())
    seq, prev = (last.seq + 1, last.event_sha256) if last else (1, GENESIS)
    body = {"schema": EVENT_SCHEMA, "seq": seq, "prev": prev, "action": action, "actor": actor.scope,
            "actorRole": actor.role, "owner": actor.owner, "saved": saved.id if saved else None,
            "detail": detail or {}, "at": ws.now_utc().isoformat()}
    row = DiscoveryIntakeEvent(seq=seq, action=action, owner_scope=actor.owner, saved_id=saved.id if saved else None,
                               body=body, prev_sha256=prev, event_sha256=cv.canonical_sha256(body))
    session.add(row)
    session.flush()
    return row


def verify_chain(session: Session) -> dict:
    prev, n = GENESIS, 0
    for row in session.scalars(select(DiscoveryIntakeEvent).order_by(DiscoveryIntakeEvent.seq)):
        n += 1
        if (row.seq != n or row.prev_sha256 != prev or row.body.get("prev") != prev or row.body.get("seq") != row.seq
                or cv.canonical_sha256(row.body) != row.event_sha256):
            return {"valid": False, "events": n, "brokenAt": row.seq}
        prev = row.event_sha256
    return {"valid": True, "events": n, "head": prev}


# ---------------------------------------------------------------------------
# Operations


def _saved(session: Session, owner: str, saved_id: str) -> DiscoverySavedCandidate:
    row = session.get(DiscoverySavedCandidate, saved_id)
    if row is None or row.owner_scope != owner:
        raise LookupError("Saved candidate not found")  # same answer for absent and foreign
    return row


def _sources(session: Session, row: DiscoverySavedCandidate) -> list[DiscoveryCandidateSource]:
    return list(session.scalars(select(DiscoveryCandidateSource).where(DiscoveryCandidateSource.saved_id == row.id)
                                .order_by(DiscoveryCandidateSource.created_at, DiscoveryCandidateSource.id)))


def stored_candidate(session: Session, row: DiscoverySavedCandidate) -> Candidate:
    """The first saved metadata with every provider record now behind it."""
    sources = [CandidateSource(provider=s.provider, provider_record_id=s.provider_record_id,
                               retrieved_at=s.retrieved_at, query_sha256=s.query_sha256,
                               request_sha256=s.request_sha256, response_sha256=s.response_sha256,
                               record_sha256=s.record_sha256, adapter_version=s.adapter_version)
               for s in _sources(session, row)]
    return Candidate(**row.candidate_metadata, sources=sources)


def _twins(session: Session, row: DiscoverySavedCandidate) -> list[DiscoverySavedCandidate]:
    """The owner's other saved candidates with the same normalised title and year: possible duplicates, both ways."""
    return list(session.scalars(select(DiscoverySavedCandidate).where(
        DiscoverySavedCandidate.owner_scope == row.owner_scope, DiscoverySavedCandidate.title_key == row.title_key,
        DiscoverySavedCandidate.id != row.id).order_by(DiscoverySavedCandidate.created_at, DiscoverySavedCandidate.id)))


def review_candidate(session: Session, row: DiscoverySavedCandidate) -> Candidate:
    """What a Library reviewer must see: the first saved metadata plus everything later provider records disagree on.

    A retraction reported by any record counts. Fields that differ between records become conflicts keyed by record,
    and the owner's same-title candidates become possible duplicates, so none of it is hidden by deduplication."""
    base = stored_candidate(session, row)
    snapshots = [(f"{s.provider_record_id}@{s.retrieved_at}", s.candidate_metadata) for s in _sources(session, row)]
    conflicts = list(base.conflicts)
    for field in REVIEW_FIELDS:
        values = {key: json.dumps(meta.get(field), ensure_ascii=False) for key, meta in snapshots}
        if len(set(values.values())) > 1 and all(c.field != field for c in conflicts):
            conflicts.append(FieldConflict(field=field, values=values))
    retracted = True if any(meta.get("retracted") is True for _, meta in snapshots) else base.retracted
    duplicates = tuple(dict.fromkeys(base.possible_duplicate_of + tuple(t.candidate_id for t in _twins(session, row))))
    return base.model_copy(update={"conflicts": tuple(conflicts), "retracted": retracted,
                                   "possible_duplicate_of": duplicates})


def readable_review(session: Session, row: DiscoverySavedCandidate) -> Candidate | None:
    """``review_candidate``, or None when the row's stored metadata cannot be read back as a candidate.

    Only this record is affected: the caller shows it with the ``metadata-invalid`` blocking issue. The log names the
    record id only, never its metadata."""
    try:
        return review_candidate(session, row)
    except (TypeError, ValueError):  # pydantic's ValidationError is a ValueError
        log.warning("saved candidate %s has unreadable stored metadata", row.id)
        return None


def save_candidate(session: Session, actor: ed.Actor, candidate: Candidate) -> dict:
    """Save a server-held discovery candidate for the actor's owner, deduplicating against what they already hold."""
    metadata = candidate_metadata(candidate)
    digest = cv.canonical_sha256(metadata)
    key = dedup_key(candidate)
    row = session.scalar(select(DiscoverySavedCandidate).where(DiscoverySavedCandidate.owner_scope == actor.owner,
                                                                DiscoverySavedCandidate.dedup_key == key))
    created, restored = row is None, False
    if created:
        row = DiscoverySavedCandidate(owner_scope=actor.owner, dedup_key=key, title_key=title_key(candidate),
                                      candidate_id=candidate.candidate_id, doi=candidate.doi,
                                      title=candidate.title, candidate_metadata=metadata, metadata_sha256=digest,
                                      status=SAVED, saved_by=actor.scope)
        session.add(row)
        session.flush()
    elif row.status == DISMISSED:
        row.status, restored = SAVED, True
    known = {s.record_sha256 for s in _sources(session, row)}
    added = 0
    for source in candidate.sources:
        if source.record_sha256 in known:
            continue
        known.add(source.record_sha256)
        session.add(DiscoveryCandidateSource(saved_id=row.id, metadata_sha256=digest, candidate_metadata=metadata,
                                             saved_by=actor.scope, **source.model_dump(mode="json")))
        added += 1
    session.flush()
    _event(session, actor, "candidate-saved" if created else "candidate-deduplicated", row,
           {"candidateId": candidate.candidate_id, "dedup": key.split(":")[0], "sourcesAdded": added,
            "metadataSha256": digest, "metadataDiffers": digest != row.metadata_sha256, "restored": restored})
    return {**saved_view(session, row), "created": created, "deduplicated": not created, "sourcesAdded": added,
            "metadataDiffers": digest != row.metadata_sha256}


def dismiss(session: Session, actor: ed.Actor, saved_id: str) -> dict:
    ed._require_owner(actor, "dismiss saved candidates")
    row = _saved(session, actor.owner, saved_id)
    if row.status != DISMISSED:
        row.status = DISMISSED
        _event(session, actor, "candidate-dismissed", row)
    return saved_view(session, row)


def _entry_for(session: Session, actor: ed.Actor, entry_slug: str) -> ws.ResearchEntry:
    entry, project, *_ = ed._entry(session, actor.owner, entry_slug)
    ed._require_project(actor, project)
    return entry


def associate(session: Session, actor: ed.Actor, saved_id: str, entry_slug: str) -> dict:
    """Link a saved candidate to one of the owner's Research articles, as a related candidate (never as evidence)."""
    row = _saved(session, actor.owner, saved_id)
    if row.status != SAVED:
        raise ValueError("A dismissed candidate cannot be associated; save it again first")
    entry = _entry_for(session, actor, entry_slug)
    link = session.scalar(select(DiscoveryAssociation).where(DiscoveryAssociation.saved_id == row.id,
                                                             DiscoveryAssociation.entry_id == entry.id))
    if link is None:
        session.add(DiscoveryAssociation(saved_id=row.id, entry_id=entry.id, owner_scope=actor.owner,
                                         relation=RELATION, active=True, changed_by=actor.scope))
    elif link.active:
        return saved_view(session, row)
    else:
        link.active, link.changed_by = True, actor.scope
    session.flush()
    _event(session, actor, "candidate-associated", row, {"entry": entry.slug, "relation": RELATION})
    return saved_view(session, row)


def dissociate(session: Session, actor: ed.Actor, saved_id: str, entry_slug: str) -> dict:
    row = _saved(session, actor.owner, saved_id)
    entry = _entry_for(session, actor, entry_slug)
    link = session.scalar(select(DiscoveryAssociation).where(DiscoveryAssociation.saved_id == row.id,
                                                             DiscoveryAssociation.entry_id == entry.id))
    if link is not None and link.active:
        link.active, link.changed_by = False, actor.scope
        session.flush()
        _event(session, actor, "candidate-dissociated", row, {"entry": entry.slug})
    return saved_view(session, row)


def propose_to_library(session: Session, actor: ed.Actor, saved_id: str, acknowledged_issues) -> dict:
    """Record the owner's Library review proposal. Nothing is sent: Library submission is not authorised."""
    ed._require_owner(actor, "propose sources for Library review")
    row = _saved(session, actor.owner, saved_id)
    if row.status != SAVED:
        raise ValueError("A dismissed candidate cannot be proposed")
    if session.scalar(select(DiscoveryLibraryProposal.id).where(DiscoveryLibraryProposal.saved_id == row.id)):
        raise ValueError("This candidate has already been proposed for Library review")
    review = readable_review(session, row)
    if review is None:
        raise ValueError("This saved source's stored metadata cannot be read, so it cannot be proposed")
    proposal = draft_proposal(review)
    issues = sorted(proposal.blocking_issues)
    if not isinstance(acknowledged_issues, (list, tuple)) or sorted(set(map(str, acknowledged_issues))) != issues:
        raise ValueError(f"Acknowledge exactly the blocking issues a Library reviewer must resolve: {issues}")
    body = proposal.model_dump(mode="json", by_alias=True)
    digest = cv.canonical_sha256(body)
    session.add(DiscoveryLibraryProposal(saved_id=row.id, owner_scope=actor.owner, proposal=body,
                                         proposal_sha256=digest, acknowledged_issues=issues, status=PROPOSAL_STATUS,
                                         sent=False, proposed_by=actor.scope))
    session.flush()
    _event(session, actor, "library-proposal-recorded", row, {"proposalSha256": digest, "blockingIssues": issues,
                                                              "sent": False})
    return saved_view(session, row)


def saved_view(session: Session, row: DiscoverySavedCandidate) -> dict:
    entries = session.scalars(select(ws.ResearchEntry.slug).join(
        DiscoveryAssociation, DiscoveryAssociation.entry_id == ws.ResearchEntry.id).where(
        DiscoveryAssociation.saved_id == row.id, DiscoveryAssociation.active.is_(True)).order_by(ws.ResearchEntry.slug))
    proposal = session.scalar(select(DiscoveryLibraryProposal).where(DiscoveryLibraryProposal.saved_id == row.id))
    review = readable_review(session, row)
    if review is None:
        retracted = any(isinstance(s.candidate_metadata, dict) and s.candidate_metadata.get("retracted") is True
                        for s in _sources(session, row))
        summary = {"blockingIssues": [METADATA_INVALID], "retractionReported": retracted, "conflictingFields": []}
    else:
        summary = {"blockingIssues": list(blocking_issues(review)), "retractionReported": review.retracted is True,
                   "conflictingFields": sorted({c.field for c in review.conflicts})}
    return {
        "id": row.id, "candidateId": row.candidate_id, "status": row.status, **SEPARATION,
        "doi": row.doi, "title": row.title, "metadata": row.candidate_metadata, "metadataSha256": row.metadata_sha256,
        "possibleDuplicateOf": [t.id for t in _twins(session, row)], "savedBy": row.saved_by,
        "review": summary,
        "sources": [{"provider": s.provider, "providerRecordId": s.provider_record_id, "retrievedAt": s.retrieved_at,
                     "querySha256": s.query_sha256, "requestSha256": s.request_sha256,
                     "responseSha256": s.response_sha256, "recordSha256": s.record_sha256,
                     "adapterVersion": s.adapter_version, "metadataSha256": s.metadata_sha256}
                    for s in _sources(session, row)],
        "associations": [{"entry": slug, "relation": RELATION} for slug in entries],
        "libraryProposal": None if proposal is None else {
            "status": proposal.status, "sent": proposal.sent, "proposalSha256": proposal.proposal_sha256,
            "blockingIssues": proposal.acknowledged_issues, "proposedBy": proposal.proposed_by},
    }


def list_saved(session: Session, actor: ed.Actor) -> list[dict]:
    rows = session.scalars(select(DiscoverySavedCandidate).where(DiscoverySavedCandidate.owner_scope == actor.owner)
                           .order_by(DiscoverySavedCandidate.created_at, DiscoverySavedCandidate.id))
    return [saved_view(session, row) for row in rows]


# ---------------------------------------------------------------------------
# HTTP

router = APIRouter(prefix="/intake", tags=["Discovery intake"])


def _closed(state: IntakeState):
    raise HTTPException(status_code=503, detail={
        "code": "DISCOVERY_INTAKE_DISABLED", "reason": state.reason, "stage": STAGE, "saved": False,
        "message": "Discovery intake has not been enabled here."})


def intake_actor(request: Request, authorization: str | None = Header(default=None),
                 x_sc_owner_scope: str | None = Header(default=None),
                 x_sc_actor_scope: str | None = Header(default=None),
                 x_sc_editor_attestation: str | None = Header(default=None)) -> ed.Actor:
    state = intake_state()
    if not state.enabled:
        _closed(state)
    owner = cv.internal_principal(authorization, x_sc_owner_scope)
    return ed.editorial_actor(request, owner, x_sc_actor_scope, x_sc_editor_attestation)


def _do(session: Session, fn, *args):
    try:
        return ed._do(session, fn, *args)
    except IntegrityError as exc:  # a concurrent save of the same candidate or audit sequence; ed._do rolled back
        raise HTTPException(status_code=409, detail="A concurrent change was made; retry") from exc


def _reply(data) -> JSONResponse:
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.get("/status")
def intake_status():
    state = intake_state()
    return {"schema": "sc-research-discovery-intake-status/1.0", "stage": STAGE, "enabled": state.enabled,
            "reason": state.reason, "production_intake_authorized": PRODUCTION_INTAKE_AUTHORIZED,
            "library_submission_authorized": LIBRARY_SUBMISSION_AUTHORIZED, "separation": SEPARATION,
            "publication_enabled": False}


@router.get("/candidates")
def list_route(actor: ed.Actor = Depends(intake_actor), session: Session = Depends(ws.get_session)):
    return _reply({"items": list_saved(session, actor)})


@router.post("/candidates")
def save_route(body: dict = Body(...), actor: ed.Actor = Depends(intake_actor),
               session: Session = Depends(ws.get_session)):
    candidate = cv._call(HANDLES.candidate, str(body.get("run_handle") or ""), str(body.get("candidate_id") or ""))
    return _reply(_do(session, save_candidate, actor, candidate))


@router.get("/candidates/{saved_id}")
def view_route(saved_id: str, actor: ed.Actor = Depends(intake_actor), session: Session = Depends(ws.get_session)):
    return _reply(saved_view(session, cv._call(_saved, session, actor.owner, saved_id)))


@router.post("/candidates/{saved_id}/dismiss")
def dismiss_route(saved_id: str, actor: ed.Actor = Depends(intake_actor), session: Session = Depends(ws.get_session)):
    return _reply(_do(session, dismiss, actor, saved_id))


@router.post("/candidates/{saved_id}/associations/{entry_slug}")
def associate_route(saved_id: str, entry_slug: str, actor: ed.Actor = Depends(intake_actor),
                    session: Session = Depends(ws.get_session)):
    return _reply(_do(session, associate, actor, saved_id, entry_slug))


@router.post("/candidates/{saved_id}/associations/{entry_slug}/remove")
def dissociate_route(saved_id: str, entry_slug: str, actor: ed.Actor = Depends(intake_actor),
                     session: Session = Depends(ws.get_session)):
    return _reply(_do(session, dissociate, actor, saved_id, entry_slug))


@router.post("/candidates/{saved_id}/library-proposal")
def propose_route(saved_id: str, body: dict = Body(...), actor: ed.Actor = Depends(intake_actor),
                  session: Session = Depends(ws.get_session)):
    return _reply(_do(session, propose_to_library, actor, saved_id, body.get("acknowledged_blocking_issues")))
