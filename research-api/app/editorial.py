"""SC-ED-01 — Research editorial publishing.

Authorized owners and editors draft, revise, submit, approve and publish Research articles
that cite notebook-derived outputs, figures, model results, study packages and citations.

* Ownership stays with Workspace. The acting person is the owner (the owner scope the caller
  authenticated) or an editor holding a Workspace-signed ``editor`` role attestation for that
  owner's project. Research keeps no separate membership list.
* Every article revision is immutable, hash-identified, and registered as a ``research-article``
  object in Research's existing object registry with its own provenance, so the existing
  convergence approval contract (four dimensions bound to ``subject_ref``) applies unchanged.
* References pin ``objectId`` + the object's immutable provenance hash. They are verified when a
  revision is written, again at submission (reproduction) and again at publication (signed
  approvals and public projection). Withdrawn, unverifiable, stale, superseded and insufficiently
  reproduced outputs are refused.
* Only Workspace-signed approval records count. Areas publish through a separate signed
  ``area-publication`` approval held by the area steward.
* Every editorial action appends to a hash-chained audit log; publication state can be checked
  against it (``editorial_integrity``) so direct SQL changes are detectable.
* The public Research Hub sees only released revisions of published articles in published areas;
  private drafts, owner scopes, pins and approval records never reach the public projection.
* SC-PUB-M1 release lifecycle: a published article can be *unlisted* (still served by link, never
  discoverable in the catalog or object listings) and relisted, or *withdrawn* (final: content is no
  longer served; the public URL answers 410 with a tombstone notice). Every release-changing action
  yields a receipt bound into the audit chain.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import convergence as cv
from . import figure_releases as fr
from . import object_registry as md
from . import workspaces as ws

PREFIX = "/api/research/v1"
router = APIRouter(prefix=PREFIX, tags=["SC-ED-01 Research editorial"])
REVISION_SCHEMA = "sc-research-editorial-revision/1.0"
EDITORIAL_PROVENANCE_SCHEMA = "sc-research-editorial-provenance/1.0"
EVENT_SCHEMA = "sc-research-editorial-event/1.0"
ATTESTATION_SCHEMA = "sc-workspace-research-role-attestation/1.0"
AREA_DIMENSION = "area-publication"
AREA_ACTION = "research_area_publication"
REFERENCE_ROLES = {
    "figure": frozenset({"figure"}),
    "result": frozenset({"result", "statistical-result", "computation", "simulation", "table", "scenario",
                         "scenario-comparison", "decision-analysis"}),
    "notebook": frozenset({"notebook"}),
    "study-package": frozenset({"computation"}),
    "dataset": frozenset({"dataset"}),
    "model": frozenset({"model"}),
    "citation": frozenset({"source", "publication"}),
}
CITATION_KINDS = REFERENCE_ROLES["citation"]
MAX_SECTIONS, MAX_REFERENCES, MAX_BODY = 40, 200, 200_000
SHA = re.compile(r"^[0-9a-f]{64}$")
GENESIS = "0" * 64


class Conflict(ValueError):
    """Optimistic-concurrency or state conflict (HTTP 409)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Tables (alembic 0008_editorial_publishing)


class EditorialArticle(ws.Base):
    """Mutable workflow pointers for one Research entry; every change is also an audit event."""
    __tablename__ = "research_editorial_articles"
    entry_id: Mapped[str] = mapped_column(ForeignKey("research_entries.id"), primary_key=True)
    head_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    submitted_revision: Mapped[int | None] = mapped_column(Integer)
    published_revision: Mapped[int | None] = mapped_column(Integer)
    review_status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    publication_status: Mapped[str] = mapped_column(String(24), default="unpublished", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc, nullable=False)


class EditorialRevision(ws.Base):
    """One immutable article revision; ``object_id`` is the research-article object approvals bind to."""
    __tablename__ = "research_editorial_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ws.new_id)
    entry_id: Mapped[str] = mapped_column(ForeignKey("research_entries.id"), nullable=False, index=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_revision: Mapped[int | None] = mapped_column(Integer)
    author_scope: Mapped[str] = mapped_column(String(160), nullable=False)
    author_role: Mapped[str] = mapped_column(String(16), nullable=False)
    change_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(ForeignKey("research_metadata_objects.id"), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc, nullable=False)
    __table_args__ = (UniqueConstraint("entry_id", "revision_number", name="uq_research_editorial_revision"),)


class AreaSteward(ws.Base):
    """The owner scope that controls an area's public presentation (assigned once)."""
    __tablename__ = "research_area_stewards"
    area_id: Mapped[str] = mapped_column(ForeignKey("research_areas.id"), primary_key=True)
    steward_scope: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc, nullable=False)


class AreaPublication(ws.Base):
    """Append-only area publish/retract record; a publish carries its signed Workspace approval."""
    __tablename__ = "research_area_publications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ws.new_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("research_areas.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    steward_scope: Mapped[str] = mapped_column(String(160), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    workspace_approval_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    record: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc, nullable=False)


class EditorialEvent(ws.Base):
    """Append-only, hash-chained editorial audit log."""
    __tablename__ = "research_editorial_events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    owner_scope: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    entry_id: Mapped[str | None] = mapped_column(String(36), index=True)
    area_id: Mapped[str | None] = mapped_column(String(36))
    body: Mapped[dict] = mapped_column(JSON, nullable=False)
    prev_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    event_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc, nullable=False)


# ---------------------------------------------------------------------------
# Actors


@dataclass(frozen=True)
class Actor:
    scope: str
    role: str  # owner | editor
    owner: str
    project_ref: str  # Workspace project the editor grant covers ("*" = all of the owner's projects)


def resolve_actor(owner: str, actor_scope: str = "", attestation: Any = None, key=None) -> Actor:
    """The owner acts as themself; anyone else needs a Workspace-signed editor attestation."""
    actor_scope = (actor_scope or owner).strip()
    if actor_scope == owner:
        return Actor(owner, "owner", owner, "*")
    if not attestation:
        raise PermissionError("Editors need a Workspace editor attestation for this owner")
    from .workspace_ingest import load_public_key, verify_document
    body = verify_document(key or load_public_key(), ATTESTATION_SCHEMA, attestation)
    if (body.get("ownerScope"), body.get("grantee"), body.get("role")) != (owner, actor_scope, "editor"):
        raise PermissionError("The editor attestation is for another owner, person or role")
    try:
        expires = datetime.fromisoformat(str(body.get("expiresAt")))
    except ValueError as exc:
        raise PermissionError("The editor attestation has no valid expiry") from exc
    if expires <= _now():
        raise PermissionError("The editor attestation has expired; request a new one from Workspace")
    return Actor(actor_scope, "editor", owner, str(body.get("projectId") or ""))


def workspace_ref(project_id: str) -> str:
    """Research records the Workspace-owned project the same way convergence ingest does."""
    return f"workspace-project:{project_id}"


def _require_project(actor: Actor, project: ws.ResearchProject) -> None:
    if actor.role == "editor" and actor.project_ref != "*" and project.external_workspace_ref != workspace_ref(actor.project_ref):
        raise PermissionError("The editor grant does not cover this article's Workspace project")


def _require_owner(actor: Actor, what: str) -> None:
    if actor.role != "owner":
        raise PermissionError(f"Only the Workspace project owner can {what}")


# ---------------------------------------------------------------------------
# Audit chain


def _event(session: Session, actor: Actor, action: str, *, entry: ws.ResearchEntry | None = None,
           area: ws.ResearchArea | None = None, detail: dict | None = None) -> EditorialEvent:
    last = session.scalar(select(EditorialEvent).order_by(EditorialEvent.seq.desc()).limit(1).with_for_update())
    seq, prev = (last.seq + 1, last.event_sha256) if last else (1, GENESIS)
    body = {"schema": EVENT_SCHEMA, "seq": seq, "prev": prev, "action": action, "actor": actor.scope,
            "actorRole": actor.role, "owner": actor.owner, "entry": entry.slug if entry else None,
            "area": area.slug if area else None, "detail": detail or {}, "at": _now().isoformat()}
    row = EditorialEvent(seq=seq, action=action, owner_scope=actor.owner, entry_id=entry.id if entry else None,
                         area_id=area.id if area else None, body=body, prev_sha256=prev,
                         event_sha256=cv.canonical_sha256(body))
    session.add(row)
    session.flush()
    return row


def verify_chain(session: Session) -> dict:
    prev, n = GENESIS, 0
    for row in session.scalars(select(EditorialEvent).order_by(EditorialEvent.seq)):
        n += 1
        if (row.seq != n or row.prev_sha256 != prev or row.body.get("prev") != prev or row.body.get("seq") != row.seq
                or cv.canonical_sha256(row.body) != row.event_sha256):
            return {"valid": False, "events": n, "brokenAt": row.seq}
        prev = row.event_sha256
    return {"valid": True, "events": n, "head": prev}


# ---------------------------------------------------------------------------
# Lookups


def _entry(session: Session, owner: str, slug: str):
    row = session.execute(
        select(ws.ResearchEntry, ws.ResearchProject, ws.ResearchArea, ws.EntryWorkspace)
        .join(ws.ResearchProject, ws.ResearchProject.id == ws.ResearchEntry.project_id)
        .join(ws.ResearchArea, ws.ResearchArea.id == ws.ResearchProject.area_id)
        .join(ws.EntryWorkspace, ws.EntryWorkspace.entry_id == ws.ResearchEntry.id)
        .where(ws.ResearchEntry.slug == slug)).first()
    article = session.get(EditorialArticle, row[0].id) if row else None
    if row is None or row[1].owner_scope != owner or article is None:
        raise LookupError("Research article not found")  # same answer for absent and foreign
    return (*row, article)


def _revision(session: Session, entry: ws.ResearchEntry, number: int) -> EditorialRevision:
    rev = session.scalar(select(EditorialRevision).where(
        EditorialRevision.entry_id == entry.id, EditorialRevision.revision_number == number))
    if rev is None:
        raise LookupError("Article revision not found")
    return rev


def _area(session: Session, slug: str) -> ws.ResearchArea:
    area = session.scalar(select(ws.ResearchArea).where(ws.ResearchArea.slug == slug))
    if area is None:
        raise LookupError("Research area not found")
    return area


def area_document(area: ws.ResearchArea) -> dict:
    return {"slug": area.slug, "title": area.title, "summary": area.summary}


def area_subject_ref(area: ws.ResearchArea) -> str:
    return f"research-area:{area.id}#{cv.canonical_sha256(area_document(area))}"


def _steward(session: Session, area: ws.ResearchArea) -> str | None:
    row = session.get(AreaSteward, area.id)
    return row.steward_scope if row else None


# ---------------------------------------------------------------------------
# Reference verification


def identity_pin(obj: md.ResearchObject) -> str:
    """Immutable identity of a citation without computed provenance (frozen columns only)."""
    return cv.canonical_sha256({"authority": obj.authority, "sourceId": obj.source_id, "sourceVersion": obj.source_version,
                                "sourceSha256": obj.source_sha256, "doi": obj.doi, "kind": obj.kind})


def current_pin(session: Session, obj: md.ResearchObject) -> str | None:
    prov = cv._provenance(session, obj)
    if prov is not None:
        return prov.record_sha256
    return identity_pin(obj) if obj.kind in CITATION_KINDS else None


def _superseded_by(session: Session, obj: md.ResearchObject) -> list[str]:
    later = session.scalars(select(md.ResearchObject).where(
        md.ResearchObject.workspace_id == obj.workspace_id, md.ResearchObject.authority == obj.authority,
        md.ResearchObject.source_id == obj.source_id, md.ResearchObject.id != obj.id,
        md.ResearchObject.review_state != "revoked", md.ResearchObject.created_at > obj.created_at)).all()
    return [o.source_version for o in later]


def verify_reference(session: Session, owner: str, ref: dict, *, require_reproduction: bool = False,
                     require_public: bool = False, home_workspace: str | None = None) -> dict:
    """Return the reference with ``status`` 'verified' or the first failure, plus all problems."""
    out = {"objectId": ref["objectId"], "role": ref["role"], "pin": ref["pin"], "problems": []}
    obj = session.get(md.ResearchObject, ref["objectId"])
    if obj is None or md._workspace_owner(session, obj.workspace_id) != owner:
        out.update(status="unverifiable", problems=["not-found-or-foreign"])
        return out
    problems = out["problems"]
    prov = cv._provenance(session, obj)
    out.update(kind=obj.kind, title=obj.title, sourceVersion=obj.source_version,
               classification=(prov.record.get("scientificClassification") if prov else None) or obj.data_classification)
    if obj.kind not in REFERENCE_ROLES[ref["role"]]:
        problems.append("unverifiable:kind-does-not-match-role")
    if obj.review_state == "revoked":
        problems.append("withdrawn")
    pin = current_pin(session, obj)
    if pin is None:
        problems.append("unverifiable:no-immutable-provenance")
    elif pin != ref["pin"]:
        problems.append("unverifiable:pin-does-not-match-provenance")
    if prov is not None:
        status = (prov.record.get("verification") or {}).get("status")
        if status in cv.UNVERIFIED_STATUSES:
            problems.append(f"unverifiable:{status}")
        claim = cv.notebook_claim_status(session, obj, prov.record)
        if claim["status"] in ("stale", "superseded"):
            problems.append(claim["status"])
        if ref["role"] == "study-package":
            packages = {p.get("packageId"): p for p in prov.record.get("studyPackages") or []}
            if not (packages.get(ref.get("packageId")) or {}).get("verified"):
                problems.append("unverifiable:study-package-not-verified")
    later = _superseded_by(session, obj)
    if later:
        problems.append("superseded")
        out["supersededBy"] = later
    if require_reproduction and obj.kind in cv.REPRODUCTION_REQUIRED_KINDS:
        if prov is None or prov.record.get("inputLineage") != cv.VERIFIED_INPUT_LINEAGE:
            problems.append("insufficiently-reproduced:input-lineage-unverified")
        elif cv.reproduction_status(session, obj) != "reproduced":
            problems.append("insufficiently-reproduced")
    if require_public:
        state = cv.approval_state(session, obj, signed_only=True) if prov is not None else {}
        if obj.visibility != md.APPROVED or obj.review_state != md.APPROVED or not obj.rights_cleared:
            problems.append("not-publicly-approved")
        elif prov is not None and not all(state.values()):
            problems.append("missing-signed-approvals")
        if obj.workspace_id != home_workspace:
            # Outputs registered under another entry are public only while that entry is published.
            other = session.get(ws.ResearchEntry, session.get(ws.EntryWorkspace, obj.workspace_id).entry_id)
            if ws.approved_workspace_view(session, other.slug) is None:
                problems.append("not-publicly-approved:entry-not-published")
    out["status"] = "verified" if not problems else problems[0].split(":")[0]
    return out


def _require_verified(results: list[dict], phase: str) -> None:
    bad = [r for r in results if r["status"] != "verified"]
    if bad:
        detail = "; ".join(f"{r['role']} {r['objectId']}: {', '.join(r['problems'])}" for r in bad)
        raise PermissionError(f"References cannot be {phase}: {detail}")


# ---------------------------------------------------------------------------
# Document validation


def _text(value: Any, name: str, limit: int, required: bool = True) -> str:
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise ValueError(f"{name} must be text of at most {limit} characters")
    return value


def normalize_sections(sections: Any) -> list[dict]:
    if not isinstance(sections, list) or not 1 <= len(sections) <= MAX_SECTIONS:
        raise ValueError(f"sections must list 1..{MAX_SECTIONS} sections")
    out = [{"heading": _text(s.get("heading") if isinstance(s, dict) else None, f"sections[{i}].heading", 300),
            "body": _text(s.get("body") if isinstance(s, dict) else None, f"sections[{i}].body", MAX_BODY, False)}
           for i, s in enumerate(sections)]
    if sum(len(s["body"]) for s in out) > MAX_BODY:
        raise ValueError("article body is too long")
    return out


def normalize_references(refs: Any) -> list[dict]:
    if not isinstance(refs, list) or len(refs) > MAX_REFERENCES:
        raise ValueError(f"references must list at most {MAX_REFERENCES} pinned objects")
    out, seen = [], set()
    for i, r in enumerate(refs):
        if not isinstance(r, dict) or not md.UUID.fullmatch(str(r.get("objectId") or "")):
            raise ValueError(f"references[{i}].objectId must be a Research object id")
        if r.get("role") not in REFERENCE_ROLES:
            raise ValueError(f"references[{i}].role must be one of {sorted(REFERENCE_ROLES)}")
        if not SHA.fullmatch(str(r.get("pin") or "")):
            raise ValueError(f"references[{i}].pin must be the object's provenance SHA-256")
        item = {"objectId": r["objectId"], "role": r["role"], "pin": r["pin"],
                "caption": _text(r.get("caption", ""), f"references[{i}].caption", 1000, False)}
        if r["role"] == "study-package":
            item["packageId"] = _text(r.get("packageId"), f"references[{i}].packageId", 160)
        key = (item["objectId"], item["role"], item.get("packageId"))
        if key in seen:
            raise ValueError(f"references[{i}] repeats an earlier reference")
        seen.add(key)
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Article operations


def create_article(session: Session, actor: Actor, *, area_slug: str, project_key: str, workspace_project_id: str,
                   slug: str, title: str, summary: str = "") -> dict:
    """Start editorial control of a new entry, or adopt an owner's existing entry (e.g. one created by
    Workspace ingest) so its registered outputs can be cited and published with it."""
    _require_owner(actor, "create Research articles")
    _text(workspace_project_id, "workspaceProjectId", 160)
    entry = session.scalar(select(ws.ResearchEntry).where(ws.ResearchEntry.slug == slug))
    if entry is None:
        workspace = ws.create_entry_workspace(session, area_slug=area_slug, project_key=project_key,
                                              owner_scope=actor.owner, entry_slug=slug, title=title,
                                              public_summary=summary)
        entry = session.get(ws.ResearchEntry, workspace.entry_id)
    project = session.get(ws.ResearchProject, entry.project_id)
    area = session.get(ws.ResearchArea, project.area_id)
    if project.owner_scope != actor.owner or area.slug != area_slug:
        raise Conflict("This article slug is not available in that Research area")
    if session.scalar(select(ws.EntryWorkspace).where(ws.EntryWorkspace.entry_id == entry.id)) is None:
        session.add(ws.EntryWorkspace(entry_id=entry.id))
    if project.external_workspace_ref not in (None, "", workspace_ref(workspace_project_id)):
        raise Conflict("This Research project is bound to a different Workspace project")
    project.external_workspace_ref = workspace_ref(workspace_project_id)
    if session.get(AreaSteward, area.id) is None:
        session.add(AreaSteward(area_id=area.id, steward_scope=actor.owner))
        _event(session, actor, "area-steward-assigned", area=area)
    created = session.get(EditorialArticle, entry.id) is None
    if created:
        session.add(EditorialArticle(entry_id=entry.id))
        session.flush()
        _event(session, actor, "article-created", entry=entry, area=area,
               detail={"workspaceProjectId": workspace_project_id})
    return {"slug": entry.slug, "area": area.slug, "created": created}


def add_revision(session: Session, actor: Actor, *, slug: str, base_revision: int, title: str, summary: str,
                 sections: Any, references: Any, change_summary: str = "") -> dict:
    entry, project, area, workspace, art = _entry(session, actor.owner, slug)
    _require_project(actor, project)
    if base_revision != art.head_revision:
        raise Conflict(f"Article head is revision {art.head_revision}, not {base_revision}; reload before revising")
    title, summary = _text(title, "title", 350), _text(summary, "summary", 10000, False)
    sections, refs = normalize_sections(sections), normalize_references(references)
    checks = [verify_reference(session, actor.owner, r) for r in refs]
    _require_verified(checks, "used in a revision")
    number = art.head_revision + 1
    document = {"schema": REVISION_SCHEMA, "entry": entry.slug, "revision": number,
                "parent": art.head_revision or None, "title": title, "summary": summary,
                "sections": sections, "references": refs}
    digest = cv.canonical_sha256(document)
    obj = md.register_metadata_object(
        session, workspace=workspace, owner_scope=actor.owner, authority="research",
        source_id=f"editorial:{entry.id}", source_version=f"r{number}", kind="research-article",
        title=title, public_summary=summary[:10000], source_sha256=digest)
    record = {"schema": EDITORIAL_PROVENANCE_SCHEMA, "documentSha256": digest, "entry": entry.slug,
              "revision": number, "author": actor.scope, "authorRole": actor.role,
              "references": [{k: r[k] for k in ("objectId", "role", "pin")} for r in refs],
              "verification": {"status": "editorial-references-verified"}}
    session.add(cv.OutputProvenance(object_id=obj.id, schema=EDITORIAL_PROVENANCE_SCHEMA, record=record,
                                    record_sha256=cv.canonical_sha256(record)))
    session.add(EditorialRevision(entry_id=entry.id, revision_number=number, parent_revision=art.head_revision or None,
                                  author_scope=actor.scope, author_role=actor.role,
                                  change_summary=_text(change_summary, "changeSummary", 2000, False),
                                  document=document, document_sha256=digest, object_id=obj.id))
    withdrawn = art.submitted_revision
    art.head_revision, art.review_status, art.submitted_revision, art.updated_at = number, "draft", None, _now()
    session.flush()
    _event(session, actor, "revision-created", entry=entry,
           detail={"revision": number, "documentSha256": digest, "references": len(refs),
                   **({"withdrewSubmission": withdrawn} if withdrawn else {})})
    return {"revision": number, "documentSha256": digest, "objectId": obj.id,
            "subjectRef": cv.subject_ref(obj, cv._provenance(session, obj)), "references": checks}


def submit_revision(session: Session, actor: Actor, *, slug: str, revision: int) -> dict:
    entry, project, area, workspace, art = _entry(session, actor.owner, slug)
    _require_project(actor, project)
    if revision != art.head_revision or revision == 0:
        raise Conflict("Only the current head revision can be submitted for review")
    rev = _revision(session, entry, revision)
    checks = [verify_reference(session, actor.owner, r, require_reproduction=True) for r in rev.document["references"]]
    _require_verified(checks, "submitted for review")
    art.submitted_revision, art.review_status, art.updated_at = revision, "in-review", _now()
    obj = session.get(md.ResearchObject, rev.object_id)
    subject = cv.subject_ref(obj, cv._provenance(session, obj))
    _event(session, actor, "revision-submitted", entry=entry, detail={"revision": revision, "subjectRef": subject})
    return {"revision": revision, "subjectRef": subject, "approvalActions": cv.APPROVAL_ACTIONS, "references": checks}


def record_article_approval(session: Session, actor: Actor, *, slug: str, revision: int, dimension: str,
                            record: Any, key=None) -> dict:
    entry, project, area, workspace, art = _entry(session, actor.owner, slug)
    _require_project(actor, project)
    if art.submitted_revision != revision:
        raise Conflict("Approvals are recorded only for the revision currently in review")
    rev = _revision(session, entry, revision)
    row = cv.record_signed_approval(session, owner_scope=actor.owner, object_id=rev.object_id,
                                    dimension=dimension, record=record, key=key)
    _event(session, actor, "approval-recorded", entry=entry,
           detail={"revision": revision, "dimension": dimension, "approver": row.actor_id,
                   "approverRole": row.actor_role, "recordSha256": row.record_sha256})
    return {"revision": revision, "dimension": dimension, "approver": row.actor_id, "recordSha256": row.record_sha256}


LIVE = ("published", "unlisted")
RELEASE_ACTIONS = ("article-published", "article-rolled-back", "article-unlisted", "article-relisted",
                   "article-withdrawn", "article-retracted")


def _release(session: Session, entry: ws.ResearchEntry, rev: EditorialRevision, art: EditorialArticle) -> None:
    entry.title, entry.public_summary = rev.document["title"], rev.document["summary"]
    entry.visibility, entry.rights_cleared = md.APPROVED, True
    entry.state = "updated" if art.published_revision else "published"
    # An update to an unlisted article stays unlisted; relisting is a separate, explicit act.
    status = "unlisted" if art.publication_status == "unlisted" else "published"
    art.published_revision, art.publication_status, art.updated_at = rev.revision_number, status, _now()


def _publication_checks(session: Session, owner: str, area: ws.ResearchArea, rev: EditorialRevision) -> list[dict]:
    if area.visibility != md.APPROVED:
        raise PermissionError(f"Research area {area.slug!r} is not published; its steward must publish it first")
    obj = session.get(md.ResearchObject, rev.object_id)
    state = cv.approval_state(session, obj, signed_only=True)
    missing = [d for d, ok in state.items() if not ok]
    if missing:
        raise PermissionError("Missing signed Workspace approvals for this revision: " + ", ".join(missing))
    home = session.scalar(select(ws.EntryWorkspace.id).where(ws.EntryWorkspace.entry_id == rev.entry_id))
    checks = [verify_reference(session, owner, r, require_reproduction=True, require_public=True, home_workspace=home)
              for r in rev.document["references"]]
    _require_verified(checks, "published")
    return checks


def publish_revision(session: Session, actor: Actor, *, slug: str, revision: int) -> dict:
    entry, project, area, workspace, art = _entry(session, actor.owner, slug)
    _require_project(actor, project)
    if art.publication_status == "withdrawn":
        raise Conflict("The article was withdrawn; withdrawal is final. Publish the work as a new article")
    if art.submitted_revision != revision:
        raise Conflict("Only the revision currently in review can be published")
    rev = _revision(session, entry, revision)
    checks = _publication_checks(session, actor.owner, area, rev)
    approvals = {r.dimension: r.record_sha256 for r in session.scalars(select(cv.PublicationApproval).where(
        cv.PublicationApproval.object_id == rev.object_id)) if "signedRecord" in (r.record or {})}
    cv.promote_public_projection(session, owner_scope=actor.owner, object_id=rev.object_id, signed_only=True)
    last = session.scalar(select(ws.WorkspaceRevision.revision_number).where(
        ws.WorkspaceRevision.workspace_id == workspace.id).order_by(ws.WorkspaceRevision.revision_number.desc()).limit(1))
    released = _now()
    session.add(ws.WorkspaceRevision(
        workspace_id=workspace.id, revision_number=(last or 0) + 1, change_summary=rev.change_summary,
        content_ref=f"research-editorial:{rev.id}", content_sha256=rev.document_sha256,
        review_state=md.APPROVED, rights_cleared=True, released_at=released))
    previous = art.published_revision
    _release(session, entry, rev, art)
    art.review_status, art.submitted_revision = "published", None
    session.flush()
    # SC-FIG-03A: referenced figures are re-verified and copied to the release store only now.
    figures = fr.release_figures(session, entry=entry, revision_number=revision, references=rev.document["references"])
    fr.purge_release_store(session)
    _event(session, actor, "article-published", entry=entry, area=area,
           detail={"revision": revision, "documentSha256": rev.document_sha256, "previous": previous,
                   "approvalRecords": approvals, "listed": art.publication_status == "published",
                   "references": [{k: c[k] for k in ("objectId", "role", "status")} for c in checks],
                   "figureReleases": figures})
    return {"slug": entry.slug, "publishedRevision": revision, "state": entry.state, "releasedAt": released.isoformat(),
            "publicationStatus": art.publication_status, "receipt": _receipt(session.scalar(
                select(EditorialEvent).order_by(EditorialEvent.seq.desc()).limit(1)))}


def retract_article(session: Session, actor: Actor, *, slug: str, reason: str) -> dict:
    _require_owner(actor, "retract a published article")
    entry, project, area, workspace, art = _entry(session, actor.owner, slug)
    if art.publication_status != "published":
        raise Conflict("The article is not published")
    entry.visibility, entry.state = "private", "retracted"
    art.publication_status, art.updated_at = "retracted", _now()
    session.flush()
    fr.purge_release_store(session)
    _event(session, actor, "article-retracted", entry=entry,
           detail={"revision": art.published_revision, "reason": _text(reason, "reason", 2000)})
    return {"slug": entry.slug, "publicationStatus": "retracted", "publicRevision": None}


def _receipt(event: EditorialEvent) -> dict:
    """A release receipt is the audit-chain event itself: verifiable against the chain head."""
    d = event.body["detail"]
    return {"schema": "sc-research-release-receipt/1.0", "action": event.action, "article": event.body["entry"],
            "revision": d.get("revision"), "documentSha256": d.get("documentSha256"),
            "approvalRecords": d.get("approvalRecords"), "listed": d.get("listed"), "actor": event.body["actor"],
            # SC-FIG-03A: the figure bytes this event released or removed, as recorded on the chain.
            "figureReleases": d.get("figureReleases"), "figureBytesRemoved": d.get("figureBytesRemoved"),
            "at": event.body["at"], "seq": event.seq, "prevSha256": event.prev_sha256, "eventSha256": event.event_sha256}


def _live(session: Session, actor: Actor, slug: str, what: str):
    _require_owner(actor, what)
    entry, project, area, workspace, art = _entry(session, actor.owner, slug)
    if art.publication_status not in LIVE:
        raise Conflict("The article is not published")
    return entry, area, art


def unlist_article(session: Session, actor: Actor, *, slug: str, reason: str) -> dict:
    entry, area, art = _live(session, actor, slug, "unlist a published article")
    if art.publication_status == "unlisted":
        raise Conflict("The article is already unlisted")
    art.publication_status, art.updated_at = "unlisted", _now()
    ev = _event(session, actor, "article-unlisted", entry=entry,
                detail={"revision": art.published_revision, "listed": False, "reason": _text(reason, "reason", 2000)})
    return {"slug": entry.slug, "publicationStatus": "unlisted", "receipt": _receipt(ev)}


def relist_article(session: Session, actor: Actor, *, slug: str, reason: str) -> dict:
    entry, area, art = _live(session, actor, slug, "relist an article")
    if art.publication_status != "unlisted":
        raise Conflict("The article is not unlisted")
    art.publication_status, art.updated_at = "published", _now()
    ev = _event(session, actor, "article-relisted", entry=entry,
                detail={"revision": art.published_revision, "listed": True, "reason": _text(reason, "reason", 2000)})
    return {"slug": entry.slug, "publicationStatus": "published", "receipt": _receipt(ev)}


def withdraw_article(session: Session, actor: Actor, *, slug: str, reason: str, public_notice: str) -> dict:
    """Final withdrawal: the content stops being served and the public URL answers with a tombstone.
    ``reason`` stays private in the audit chain; only ``public_notice`` is shown."""
    entry, area, art = _live(session, actor, slug, "withdraw a published article")
    revision = art.published_revision
    entry.visibility, entry.state = "private", "withdrawn"
    art.publication_status, art.updated_at = "withdrawn", _now()
    session.flush()
    purged = fr.purge_release_store(session)
    ev = _event(session, actor, "article-withdrawn", entry=entry, area=area, detail={
        "revision": revision, "title": entry.title, "reason": _text(reason, "reason", 2000),
        "publicNotice": _text(public_notice, "publicNotice", 1000), "figureBytesRemoved": purged})
    return {"slug": entry.slug, "publicationStatus": "withdrawn", "publicRevision": None, "receipt": _receipt(ev)}


def release_receipts(session: Session, owner: str, slug: str) -> list[dict]:
    entry, *_ = _entry(session, owner, slug)
    rows = session.scalars(select(EditorialEvent).where(EditorialEvent.entry_id == entry.id,
                                                        EditorialEvent.action.in_(RELEASE_ACTIONS))
                           .order_by(EditorialEvent.seq)).all()
    return [_receipt(r) for r in rows]


def rollback_article(session: Session, actor: Actor, *, slug: str, revision: int, reason: str) -> dict:
    """Serve an earlier released revision again; it must still pass every publication check."""
    _require_owner(actor, "roll back a published article")
    entry, project, area, workspace, art = _entry(session, actor.owner, slug)
    if art.publication_status not in LIVE:
        raise Conflict("Only a published article can be rolled back; publish a new revision after a retraction")
    rev = _revision(session, entry, revision)
    if revision == art.published_revision:
        raise Conflict("That revision is already the published one")
    released = session.scalar(select(ws.WorkspaceRevision).where(
        ws.WorkspaceRevision.workspace_id == workspace.id,
        ws.WorkspaceRevision.content_ref == f"research-editorial:{rev.id}",
        ws.WorkspaceRevision.released_at.is_not(None)))
    if released is None:
        raise Conflict("Only a previously released revision can be restored")
    _publication_checks(session, actor.owner, area, rev)
    previous = art.published_revision
    _release(session, entry, rev, art)
    session.flush()
    figures = fr.release_figures(session, entry=entry, revision_number=revision, references=rev.document["references"])
    fr.purge_release_store(session)
    _event(session, actor, "article-rolled-back", entry=entry,
           detail={"revision": revision, "previous": previous, "documentSha256": rev.document_sha256,
                   "listed": art.publication_status == "published", "reason": _text(reason, "reason", 2000),
                   "figureReleases": figures})
    return {"slug": entry.slug, "publishedRevision": revision, "previous": previous}


# ---------------------------------------------------------------------------
# Area operations


def _require_steward(session: Session, actor: Actor, area: ws.ResearchArea) -> None:
    _require_owner(actor, "manage a Research area")
    if _steward(session, area) != actor.owner:
        raise PermissionError("Only the area steward can change or publish this Research area")


def update_area(session: Session, actor: Actor, *, slug: str, title: str, summary: str) -> dict:
    area = _area(session, slug)
    _require_steward(session, actor, area)
    if area.visibility == md.APPROVED:
        raise Conflict("Retract the area before changing its public title or summary")
    area.title, area.summary = _text(title, "title", 240), _text(summary, "summary", 10000, False)
    session.flush()
    _event(session, actor, "area-updated", area=area, detail={"subjectRef": area_subject_ref(area)})
    return {"slug": area.slug, "subjectRef": area_subject_ref(area)}


def publish_area(session: Session, actor: Actor, *, slug: str, record: Any, key=None) -> dict:
    area = _area(session, slug)
    _require_steward(session, actor, area)
    body = cv.verify_signed_approval(record, key)
    cv.require_step_up_evidence(body)
    approval, decision = body["approval"], body["decision"]
    if body.get("ownerScope") != actor.owner:
        raise PermissionError("The approval was issued for another Workspace owner")
    if body.get("dimension") != AREA_DIMENSION or approval.get("action") != AREA_ACTION:
        raise ValueError("An area-publication approval is required")
    if approval.get("subjectRef") != area_subject_ref(area):
        raise ValueError("Approval subject does not match the area's current title and summary")
    if approval.get("status") != "approved" or decision.get("decision") != "approve":
        raise ValueError("Only an approve decision can publish an area")
    who = decision.get("actor") or {}
    if who.get("actorType") != "human" or who.get("actorId") == (approval.get("requester") or {}).get("actorId"):
        raise ValueError("A different human Workspace approver is required")
    if session.scalar(select(AreaPublication).where(AreaPublication.workspace_approval_id == approval["approvalId"])):
        raise Conflict("This approval was already used; a re-publication needs a new approval")
    session.add(AreaPublication(area_id=area.id, action="publish", steward_scope=actor.owner,
                                subject_ref=approval["subjectRef"], workspace_approval_id=approval["approvalId"],
                                record=record))
    area.visibility = md.APPROVED
    session.flush()
    _event(session, actor, "area-published", area=area,
           detail={"subjectRef": approval["subjectRef"], "approver": who.get("actorId"),
                   "recordSha256": cv.canonical_sha256(record)})
    return {"slug": area.slug, "visibility": area.visibility}


def retract_area(session: Session, actor: Actor, *, slug: str, reason: str) -> dict:
    area = _area(session, slug)
    _require_steward(session, actor, area)
    if area.visibility != md.APPROVED:
        raise Conflict("The area is not published")
    area.visibility = "private"
    session.add(AreaPublication(area_id=area.id, action="retract", steward_scope=actor.owner,
                                subject_ref=area_subject_ref(area)))
    session.flush()
    _event(session, actor, "area-retracted", area=area, detail={"reason": _text(reason, "reason", 2000)})
    return {"slug": area.slug, "visibility": area.visibility}


# ---------------------------------------------------------------------------
# Views


def article_view(session: Session, owner: str, slug: str) -> dict:
    entry, project, area, workspace, art = _entry(session, owner, slug)
    revisions = session.scalars(select(EditorialRevision).where(EditorialRevision.entry_id == entry.id)
                                .order_by(EditorialRevision.revision_number)).all()
    out = []
    for rev in revisions:
        obj = session.get(md.ResearchObject, rev.object_id)
        out.append({"revision": rev.revision_number, "documentSha256": rev.document_sha256, "author": rev.author_scope,
                    "authorRole": rev.author_role, "changeSummary": rev.change_summary,
                    "createdAt": rev.created_at.isoformat() if rev.created_at else None, "objectId": rev.object_id,
                    "subjectRef": cv.subject_ref(obj, cv._provenance(session, obj)),
                    "signedApprovals": cv.approval_state(session, obj, signed_only=True)})
    head = revisions[-1] if revisions else None
    return {"slug": entry.slug, "area": {"slug": area.slug, "visibility": area.visibility},
            "workspaceProjectId": project.external_workspace_ref, "headRevision": art.head_revision,
            "submittedRevision": art.submitted_revision, "publishedRevision": art.published_revision,
            "reviewStatus": art.review_status, "publicationStatus": art.publication_status,
            "entryState": entry.state, "revisions": out,
            "head": head.document if head else None,
            "headReferences": [verify_reference(session, owner, r, require_reproduction=True, require_public=True,
                                                home_workspace=workspace.id)
                               for r in (head.document["references"] if head else [])]}


def area_view(session: Session, slug: str) -> dict:
    area = _area(session, slug)
    return {**area_document(area), "visibility": area.visibility, "steward": _steward(session, area),
            "subjectRef": area_subject_ref(area), "approvalAction": AREA_ACTION}


def owner_articles(session: Session, owner: str) -> list[dict]:
    rows = session.execute(
        select(ws.ResearchEntry, ws.ResearchArea, EditorialArticle)
        .join(EditorialArticle, EditorialArticle.entry_id == ws.ResearchEntry.id)
        .join(ws.ResearchProject, ws.ResearchProject.id == ws.ResearchEntry.project_id)
        .join(ws.ResearchArea, ws.ResearchArea.id == ws.ResearchProject.area_id)
        .where(ws.ResearchProject.owner_scope == owner).order_by(ws.ResearchEntry.slug)).all()
    return [{"slug": e.slug, "title": e.title, "area": a.slug, "headRevision": art.head_revision,
             "reviewStatus": art.review_status, "publicationStatus": art.publication_status,
             "publishedRevision": art.published_revision} for e, a, art in rows]


def audit_log(session: Session, owner: str, slug: str = "") -> list[dict]:
    rows = session.scalars(select(EditorialEvent).where(EditorialEvent.owner_scope == owner)
                           .order_by(EditorialEvent.seq)).all()
    return [{**r.body, "eventSha256": r.event_sha256} for r in rows if not slug or r.body.get("entry") == slug]


def editorial_integrity(session: Session) -> dict:
    """Public state must be explained by the audit chain; anything else was changed outside the editor."""
    chain = verify_chain(session)
    findings = []
    events = session.scalars(select(EditorialEvent).order_by(EditorialEvent.seq)).all()
    last_article: dict[str, EditorialEvent] = {}
    last_area: dict[str, EditorialEvent] = {}
    for e in events:
        if e.entry_id and e.action in RELEASE_ACTIONS:
            last_article[e.entry_id] = e
        if e.area_id and e.action in ("area-published", "area-retracted"):
            last_area[e.area_id] = e
    for entry in session.scalars(select(ws.ResearchEntry).where(ws.ResearchEntry.visibility == md.APPROVED)):
        art = session.get(EditorialArticle, entry.id)
        ev = last_article.get(entry.id)
        if art is None:
            findings.append({"entry": entry.slug, "finding": "public-without-editorial-record"})
        elif ev is None or ev.action in ("article-retracted", "article-withdrawn") \
                or ev.body["detail"].get("revision") != art.published_revision \
                or (art.publication_status == "unlisted") != (ev.action == "article-unlisted" or ev.body["detail"].get("listed") is False):
            findings.append({"entry": entry.slug, "finding": "public-state-not-explained-by-audit-chain"})
    for area in session.scalars(select(ws.ResearchArea).where(ws.ResearchArea.visibility == md.APPROVED)):
        ev = last_area.get(area.id)
        if ev is None or ev.action != "area-published":
            findings.append({"area": area.slug, "finding": "public-area-without-signed-publication"})
    return {"schema": "sc-research-editorial-integrity/1.0", "chain": chain, "findings": findings,
            "consistent": chain["valid"] and not findings}


def public_article(session: Session, slug: str) -> dict | None:
    """Released content only. No owner scopes, pins, object ids, approval records or drafts."""
    if ws.approved_workspace_view(session, slug) is None:
        return None
    entry = session.scalar(select(ws.ResearchEntry).where(ws.ResearchEntry.slug == slug))
    art = session.get(EditorialArticle, entry.id)
    if art is None or art.publication_status not in LIVE or not art.published_revision:
        return None
    rev = session.scalar(select(EditorialRevision).where(
        EditorialRevision.entry_id == entry.id, EditorialRevision.revision_number == art.published_revision))
    released = session.scalar(select(ws.WorkspaceRevision).where(
        ws.WorkspaceRevision.content_ref == f"research-editorial:{rev.id}", ws.WorkspaceRevision.released_at.is_not(None)))
    if released is None:
        return None
    public = {o.id: (o, area_title) for o, _slug, area_title in session.execute(md.public_query(include_unlisted=True))}
    refs, notices = [], set()
    for r in rev.document["references"]:
        hit = public.get(r["objectId"])
        if hit is None:
            refs.append({"role": r["role"], "status": "unavailable"})
            continue
        obj = hit[0]
        prov = cv._provenance(session, obj)
        classification = (prov.record.get("scientificClassification") if prov else None) or obj.data_classification
        if classification in cv.NOT_OBSERVED:
            notices.add(classification)
        refs.append({"role": r["role"], "status": "available", "publicKey": obj.public_key, "kind": obj.kind,
                     "title": obj.title, "caption": r.get("caption", ""), "classification": classification,
                     "provenance": f"{PREFIX}/objects/{obj.public_key}/provenance"})
    area = ws.approved_workspace_view(session, slug)["area"]
    return {"schema": "sc-research-public-article/1.0", "slug": entry.slug, "title": rev.document["title"],
            "listed": art.publication_status == "published",
            "summary": rev.document["summary"], "area": area, "revision": released.revision_number,
            "releasedAt": released.released_at.isoformat(), "sections": rev.document["sections"], "references": refs,
            "classificationNotice": (
                "Cites " + ", ".join(sorted(notices)) + " outputs. These are not measurements." if notices else None)}


def public_tombstone(session: Session, slug: str) -> dict | None:
    """What the public URL of a withdrawn article says. Nothing else about the article is served."""
    entry = session.scalar(select(ws.ResearchEntry).where(ws.ResearchEntry.slug == slug))
    art = session.get(EditorialArticle, entry.id) if entry else None
    if art is None or art.publication_status != "withdrawn":
        return None
    ev = session.scalar(select(EditorialEvent).where(EditorialEvent.entry_id == entry.id,
                                                     EditorialEvent.action == "article-withdrawn")
                        .order_by(EditorialEvent.seq.desc()).limit(1))
    if ev is None:
        return None
    return {"schema": "sc-research-public-tombstone/1.0", "slug": entry.slug, "status": "withdrawn",
            "title": ev.body["detail"].get("title"), "withdrawnAt": ev.body["at"],
            "notice": ev.body["detail"].get("publicNotice")}


# ---------------------------------------------------------------------------
# HTTP


def editorial_actor(
    owner: str = Depends(cv.internal_principal),
    x_sc_actor_scope: str | None = Header(default=None),
    x_sc_editor_attestation: str | None = Header(default=None),
) -> Actor:
    attestation = None
    if x_sc_editor_attestation:
        try:
            attestation = json.loads(base64.urlsafe_b64decode(x_sc_editor_attestation + "=" * (-len(x_sc_editor_attestation) % 4)))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="X-SC-Editor-Attestation must be base64url JSON") from exc
    return cv._call(resolve_actor, owner, (x_sc_actor_scope or "").strip()[:160], attestation)


def _commit(session: Session, fn, *args, **kwargs):
    try:
        result = fn(session, *args, **kwargs)
        session.commit()
        return result
    except Conflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BaseException:
        session.rollback()
        raise


def _do(session: Session, fn, *args, **kwargs):
    return cv._call(_commit, session, fn, *args, **kwargs)


@router.get("/editorial/status")
def editorial_status():
    return {"schema": "sc-research-editorial-status/1.0", "build": "SC-ED-01", "privateWritesEnabled": cv.writes_enabled(),
            "approvalRecords": cv.WS_APPROVAL_RECORD_SCHEMA, "editorAttestation": ATTESTATION_SCHEMA,
            "referenceRoles": sorted(REFERENCE_ROLES), "approvalDimensions": sorted(cv.APPROVAL_ACTIONS),
            "areaApproval": AREA_ACTION, "automaticPublication": False}


@router.get("/editorial/articles")
def list_route(actor: Actor = Depends(editorial_actor), session: Session = Depends(ws.get_session)):
    return {"items": owner_articles(session, actor.owner)}


@router.post("/editorial/articles")
def create_route(body: dict = Body(...), actor: Actor = Depends(editorial_actor), session: Session = Depends(ws.get_session)):
    return _do(session, create_article, actor, area_slug=str(body.get("areaSlug") or ""),
               project_key=str(body.get("projectKey") or ""), workspace_project_id=str(body.get("workspaceProjectId") or ""),
               slug=str(body.get("slug") or ""), title=str(body.get("title") or ""), summary=str(body.get("summary") or ""))


@router.get("/editorial/articles/{slug}")
def article_route(slug: str, actor: Actor = Depends(editorial_actor), session: Session = Depends(ws.get_session)):
    cv._call(_require_project, actor, cv._call(_entry, session, actor.owner, slug)[1])
    return cv._call(article_view, session, actor.owner, slug)


@router.get("/editorial/articles/{slug}/revisions/{revision}")
def revision_route(slug: str, revision: int, actor: Actor = Depends(editorial_actor),
                   session: Session = Depends(ws.get_session)):
    entry, project, *_ = cv._call(_entry, session, actor.owner, slug)
    cv._call(_require_project, actor, project)
    rev = cv._call(_revision, session, entry, revision)
    home = session.scalar(select(ws.EntryWorkspace.id).where(ws.EntryWorkspace.entry_id == entry.id))
    return {"revision": revision, "document": rev.document, "documentSha256": rev.document_sha256,
            "references": [verify_reference(session, actor.owner, r, require_reproduction=True, require_public=True,
                                            home_workspace=home) for r in rev.document["references"]]}


@router.post("/editorial/articles/{slug}/revisions")
def revise_route(slug: str, body: dict = Body(...), actor: Actor = Depends(editorial_actor),
                 session: Session = Depends(ws.get_session)):
    return _do(session, add_revision, actor, slug=slug, base_revision=int(body.get("baseRevision", -1)),
               title=body.get("title"), summary=body.get("summary", ""), sections=body.get("sections"),
               references=body.get("references", []), change_summary=body.get("changeSummary", ""))


@router.post("/editorial/articles/{slug}/revisions/{revision}/submit")
def submit_route(slug: str, revision: int, actor: Actor = Depends(editorial_actor),
                 session: Session = Depends(ws.get_session)):
    return _do(session, submit_revision, actor, slug=slug, revision=revision)


@router.post("/editorial/articles/{slug}/revisions/{revision}/approvals/{dimension}")
def approval_route(slug: str, revision: int, dimension: str, body: dict = Body(...),
                   actor: Actor = Depends(editorial_actor), session: Session = Depends(ws.get_session)):
    if "record" not in body:
        raise HTTPException(status_code=422, detail="A Workspace-signed approval record is required")
    return _do(session, record_article_approval, actor, slug=slug, revision=revision, dimension=dimension,
               record=body["record"])


@router.post("/editorial/articles/{slug}/revisions/{revision}/publish")
def publish_route(slug: str, revision: int, actor: Actor = Depends(editorial_actor),
                  session: Session = Depends(ws.get_session)):
    return _do(session, publish_revision, actor, slug=slug, revision=revision)


@router.post("/editorial/articles/{slug}/retract")
def retract_route(slug: str, body: dict = Body(...), actor: Actor = Depends(editorial_actor),
                  session: Session = Depends(ws.get_session)):
    return _do(session, retract_article, actor, slug=slug, reason=body.get("reason"))


@router.post("/editorial/articles/{slug}/rollback")
def rollback_route(slug: str, body: dict = Body(...), actor: Actor = Depends(editorial_actor),
                   session: Session = Depends(ws.get_session)):
    return _do(session, rollback_article, actor, slug=slug, revision=int(body.get("revision", 0)), reason=body.get("reason"))


@router.post("/editorial/articles/{slug}/unlist")
def unlist_route(slug: str, body: dict = Body(...), actor: Actor = Depends(editorial_actor),
                 session: Session = Depends(ws.get_session)):
    return _do(session, unlist_article, actor, slug=slug, reason=body.get("reason"))


@router.post("/editorial/articles/{slug}/relist")
def relist_route(slug: str, body: dict = Body(...), actor: Actor = Depends(editorial_actor),
                 session: Session = Depends(ws.get_session)):
    return _do(session, relist_article, actor, slug=slug, reason=body.get("reason"))


@router.post("/editorial/articles/{slug}/withdraw")
def withdraw_route(slug: str, body: dict = Body(...), actor: Actor = Depends(editorial_actor),
                   session: Session = Depends(ws.get_session)):
    return _do(session, withdraw_article, actor, slug=slug, reason=body.get("reason"),
               public_notice=body.get("publicNotice"))


@router.get("/editorial/articles/{slug}/receipts")
def receipts_route(slug: str, actor: Actor = Depends(editorial_actor), session: Session = Depends(ws.get_session)):
    cv._call(_require_owner, actor, "read release receipts")
    return {"items": cv._call(release_receipts, session, actor.owner, slug)}


@router.get("/editorial/areas/{slug}")
def area_route(slug: str, actor: Actor = Depends(editorial_actor), session: Session = Depends(ws.get_session)):
    return cv._call(area_view, session, slug)


@router.post("/editorial/areas/{slug}")
def area_update_route(slug: str, body: dict = Body(...), actor: Actor = Depends(editorial_actor),
                      session: Session = Depends(ws.get_session)):
    return _do(session, update_area, actor, slug=slug, title=body.get("title"), summary=body.get("summary", ""))


@router.post("/editorial/areas/{slug}/publish")
def area_publish_route(slug: str, body: dict = Body(...), actor: Actor = Depends(editorial_actor),
                       session: Session = Depends(ws.get_session)):
    if "record" not in body:
        raise HTTPException(status_code=422, detail="A Workspace-signed area-publication record is required")
    return _do(session, publish_area, actor, slug=slug, record=body["record"])


@router.post("/editorial/areas/{slug}/retract")
def area_retract_route(slug: str, body: dict = Body(...), actor: Actor = Depends(editorial_actor),
                       session: Session = Depends(ws.get_session)):
    return _do(session, retract_area, actor, slug=slug, reason=body.get("reason"))


@router.get("/editorial/audit")
def audit_route(article: str = Query(default="", max_length=150), actor: Actor = Depends(editorial_actor),
                session: Session = Depends(ws.get_session)):
    return {"events": audit_log(session, actor.owner, article), "chain": verify_chain(session)}


@router.get("/editorial/integrity")
def integrity_route(actor: Actor = Depends(editorial_actor), session: Session = Depends(ws.get_session)):
    return editorial_integrity(session)


UI_DIR = Path(__file__).with_name("editorial_ui")
UI_CSP = ("default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; "
          "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


@router.get("/editorial/ui/{name}", include_in_schema=False)
def ui_route(name: str):
    """The private console exists only where the private write gate is open (never on the public reader)."""
    files = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8"}
    if not cv.writes_enabled() or name not in files:
        raise HTTPException(status_code=404, detail="Not found")
    return Response((UI_DIR / name).read_bytes(), media_type=files[name],
                    headers={"Content-Security-Policy": UI_CSP, "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})


@router.get("/articles/{slug}/content")
def public_article_route(slug: str, session: Session = Depends(ws.get_session)):
    if not ws.SLUG.fullmatch(slug):
        raise HTTPException(status_code=404, detail="Research article not found")
    record = public_article(session, slug)
    if record is None:
        tombstone = public_tombstone(session, slug)
        if tombstone is not None:
            return JSONResponse(status_code=410, content=tombstone)
        raise HTTPException(status_code=404, detail="Research article not found")
    if not record["listed"]:
        return JSONResponse(content=record, headers={"X-Robots-Tag": "noindex"})
    return record
