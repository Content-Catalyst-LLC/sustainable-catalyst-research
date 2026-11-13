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
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import convergence as cv
from . import editorial as ed
from . import workspaces as ws
from .discovery_adapters.library_intake import draft_proposal
from .discovery_adapters.models import Candidate, CandidateSource

STAGE = "RH-08D-04"
GATE_ENV = "RESEARCH_DISCOVERY_INTAKE"
PRODUCTION_INTAKE_AUTHORIZED = False
LIBRARY_SUBMISSION_AUTHORIZED = False
EVENT_SCHEMA = "sc-research-discovery-intake-event/1.0"
GENESIS = "0" * 64
SAVED, DISMISSED = "saved-unreviewed", "dismissed"
PROPOSAL_STATUS = "awaiting-library-endpoint"
RELATION = "related-candidate"
HANDLE_TTL_SECONDS = 900
MAX_HANDLES = 32
SEPARATION = {"classification": "discovered-unreviewed", "review_status": "unreviewed", "library_reviewed": False,
              "research_evidence": False, "publishable": False}


def _id() -> str:
    return str(uuid4())


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
# Tables


class DiscoverySavedCandidate(ws.Base):
    __tablename__ = "research_discovery_saved_candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_scope: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    dedup_key: Mapped[str] = mapped_column(String(80), nullable=False)
    title_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(29), nullable=False)
    doi: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_metadata: Mapped[dict] = mapped_column("metadata", JSON, nullable=False)
    metadata_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    possible_duplicate_of: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    saved_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc, nullable=False)
    __table_args__ = (UniqueConstraint("owner_scope", "dedup_key", name="uq_research_discovery_saved_identity"),)


class DiscoveryCandidateSource(ws.Base):
    """One provider record behind a saved candidate, exactly as retrieved. Append-only."""
    __tablename__ = "research_discovery_candidate_sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    saved_id: Mapped[str] = mapped_column(ForeignKey("research_discovery_saved_candidates.id"), nullable=False,
                                          index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    retrieved_at: Mapped[str] = mapped_column(String(40), nullable=False)
    query_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    response_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    record_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    saved_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc, nullable=False)
    __table_args__ = (UniqueConstraint("saved_id", "record_sha256", name="uq_research_discovery_source_record"),)


class DiscoveryAssociation(ws.Base):
    __tablename__ = "research_discovery_associations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    saved_id: Mapped[str] = mapped_column(ForeignKey("research_discovery_saved_candidates.id"), nullable=False,
                                          index=True)
    entry_id: Mapped[str] = mapped_column(ForeignKey("research_entries.id"), nullable=False, index=True)
    owner_scope: Mapped[str] = mapped_column(String(160), nullable=False)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    changed_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc, nullable=False)
    __table_args__ = (UniqueConstraint("saved_id", "entry_id", name="uq_research_discovery_association"),)


class DiscoveryLibraryProposal(ws.Base):
    """A Library review proposal recorded in Research. ``sent`` stays False until Library submission is authorised."""
    __tablename__ = "research_discovery_library_proposals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    saved_id: Mapped[str] = mapped_column(ForeignKey("research_discovery_saved_candidates.id"), nullable=False,
                                          unique=True)
    owner_scope: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    proposal: Mapped[dict] = mapped_column(JSON, nullable=False)
    proposal_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    acknowledged_issues: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    sent: Mapped[bool] = mapped_column(Boolean, nullable=False)
    proposed_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc, nullable=False)


class DiscoveryIntakeEvent(ws.Base):
    """Append-only, hash-chained intake audit log: ids, hashes and counts only."""
    __tablename__ = "research_discovery_intake_events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    owner_scope: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    saved_id: Mapped[str | None] = mapped_column(String(36), index=True)
    body: Mapped[dict] = mapped_column(JSON, nullable=False)
    prev_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    event_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc, nullable=False)


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
    """The saved metadata with every provider record now behind it."""
    sources = [CandidateSource(provider=s.provider, provider_record_id=s.provider_record_id,
                               retrieved_at=s.retrieved_at, query_sha256=s.query_sha256,
                               request_sha256=s.request_sha256, response_sha256=s.response_sha256,
                               record_sha256=s.record_sha256, adapter_version=s.adapter_version)
               for s in _sources(session, row)]
    return Candidate(**row.candidate_metadata, sources=sources)


def save_candidate(session: Session, actor: ed.Actor, candidate: Candidate) -> dict:
    """Save a server-held discovery candidate for the actor's owner, deduplicating against what they already hold."""
    metadata = candidate_metadata(candidate)
    digest = cv.canonical_sha256(metadata)
    key = dedup_key(candidate)
    row = session.scalar(select(DiscoverySavedCandidate).where(DiscoverySavedCandidate.owner_scope == actor.owner,
                                                                DiscoverySavedCandidate.dedup_key == key))
    created, restored = row is None, False
    if created:
        tkey = title_key(candidate)
        twins = session.scalars(select(DiscoverySavedCandidate.id).where(
            DiscoverySavedCandidate.owner_scope == actor.owner, DiscoverySavedCandidate.title_key == tkey))
        row = DiscoverySavedCandidate(owner_scope=actor.owner, dedup_key=key, title_key=tkey,
                                      candidate_id=candidate.candidate_id, doi=candidate.doi,
                                      title=candidate.title, candidate_metadata=metadata, metadata_sha256=digest,
                                      possible_duplicate_of=sorted(twins), status=SAVED, saved_by=actor.scope)
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
        session.add(DiscoveryCandidateSource(saved_id=row.id, metadata_sha256=digest, saved_by=actor.scope,
                                             **source.model_dump(mode="json")))
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
    proposal = draft_proposal(stored_candidate(session, row))
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
    return {
        "id": row.id, "candidateId": row.candidate_id, "status": row.status, **SEPARATION,
        "doi": row.doi, "title": row.title, "metadata": row.candidate_metadata, "metadataSha256": row.metadata_sha256,
        "possibleDuplicateOf": row.possible_duplicate_of, "savedBy": row.saved_by,
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


def intake_actor(authorization: str | None = Header(default=None),
                 x_sc_owner_scope: str | None = Header(default=None),
                 x_sc_actor_scope: str | None = Header(default=None),
                 x_sc_editor_attestation: str | None = Header(default=None)) -> ed.Actor:
    state = intake_state()
    if not state.enabled:
        _closed(state)
    owner = cv.internal_principal(authorization, x_sc_owner_scope)
    return ed.editorial_actor(owner, x_sc_actor_scope, x_sc_editor_attestation)


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
    return _reply(ed._do(session, save_candidate, actor, candidate))


@router.get("/candidates/{saved_id}")
def view_route(saved_id: str, actor: ed.Actor = Depends(intake_actor), session: Session = Depends(ws.get_session)):
    return _reply(saved_view(session, cv._call(_saved, session, actor.owner, saved_id)))


@router.post("/candidates/{saved_id}/dismiss")
def dismiss_route(saved_id: str, actor: ed.Actor = Depends(intake_actor), session: Session = Depends(ws.get_session)):
    return _reply(ed._do(session, dismiss, actor, saved_id))


@router.post("/candidates/{saved_id}/associations/{entry_slug}")
def associate_route(saved_id: str, entry_slug: str, actor: ed.Actor = Depends(intake_actor),
                    session: Session = Depends(ws.get_session)):
    return _reply(ed._do(session, associate, actor, saved_id, entry_slug))


@router.post("/candidates/{saved_id}/associations/{entry_slug}/remove")
def dissociate_route(saved_id: str, entry_slug: str, actor: ed.Actor = Depends(intake_actor),
                     session: Session = Depends(ws.get_session)):
    return _reply(ed._do(session, dissociate, actor, saved_id, entry_slug))


@router.post("/candidates/{saved_id}/library-proposal")
def propose_route(saved_id: str, body: dict = Body(...), actor: ed.Actor = Depends(intake_actor),
                  session: Session = Depends(ws.get_session)):
    return _reply(ed._do(session, propose_to_library, actor, saved_id, body.get("acknowledged_blocking_issues")))
