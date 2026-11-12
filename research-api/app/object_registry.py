"""RH-05.2 — Research-owned, persistent object registry and public graph API.

Every metadata record belongs to an existing EntryWorkspace, references an
original source authority and immutable version, and starts private.
No anonymous mutation, external URL fetching, or source database access.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint,
    and_, func, or_, select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import workspaces as ws

PREFIX = "/api/research/v1"
router = APIRouter(prefix=PREFIX, tags=["Public Research object catalog"])
APPROVED = "public-approved"
KINDS = frozenset({
    "research-article", "publication", "source", "claim", "evidence",
    "dataset", "statistical-result", "figure", "model", "notebook",
    "place", "scene", "method", "policy", "research-question", "analysis",
    # SC-MODEL-01 convergence outputs (see convergence.py).
    "computation", "simulation", "result", "table", "scenario", "scenario-comparison",
    "decision-analysis", "assumption", "conclusion", "reproduction",
})
SOURCE_AUTHORITIES = ws.SOURCES | frozenset({"openalex", "crossref", "europe-pmc", "arxiv"})
PUBLIC_PREDICATES = frozenset({
    "contains", "cites", "derived-from", "references", "related-to",
    "same-doi-reviewed", "shared-topics-reviewed", "declared-dependency",
    "uses-dataset", "produced-by", "visualizes", "compares", "reproduces", "synthesizes",
})
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DATA_CLASSIFICATIONS = frozenset({
    "unknown", "observed", "modelled", "synthetic", "illustrative", "literature-derived",
})
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ResearchObject(ws.Base):
    __tablename__ = "research_metadata_objects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ws.new_id)
    public_key: Mapped[str] = mapped_column(String(36), default=ws.new_id, unique=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("research_entry_workspaces.id"), index=True, nullable=False,
    )
    authority: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(512), nullable=False)
    source_version: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(String(350), nullable=False)
    public_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    doi: Mapped[str | None] = mapped_column(String(240))
    concepts: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_license_ref: Mapped[str | None] = mapped_column(String(300))
    data_classification: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), default="private", nullable=False)
    rights_cleared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    review_state: Mapped[str] = mapped_column(String(32), default="unreviewed", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc)
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "authority", "source_id", "source_version",
            name="uq_research_object_source_version",
        ),
        Index("ix_research_objects_status_kind", "visibility", "kind"),
    )


class ResearchObjectRelation(ws.Base):
    __tablename__ = "research_metadata_relations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ws.new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("research_entry_workspaces.id"), nullable=False, index=True,
    )
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("research_metadata_objects.id"), nullable=False, index=True,
    )
    object_id: Mapped[str] = mapped_column(
        ForeignKey("research_metadata_objects.id"), nullable=False, index=True,
    )
    predicate: Mapped[str] = mapped_column(String(80), nullable=False)
    private_basis: Mapped[str] = mapped_column(Text, default="", nullable=False)
    public_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    review_state: Mapped[str] = mapped_column(String(32), default="proposed", nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), default="private", nullable=False)
    evidence_assertion: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc)
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "subject_id", "object_id", "predicate",
            name="uq_research_metadata_relation",
        ),
    )


def _workspace_owner(session: Session, workspace_id: str) -> str | None:
    return session.scalar(
        select(ws.ResearchProject.owner_scope)
        .join(ws.ResearchEntry, ws.ResearchEntry.project_id == ws.ResearchProject.id)
        .join(ws.EntryWorkspace, ws.EntryWorkspace.entry_id == ws.ResearchEntry.id)
        .where(ws.EntryWorkspace.id == workspace_id)
    )


def _require_workspace_owner(session: Session, workspace: ws.EntryWorkspace, owner_scope: str) -> None:
    if not owner_scope or _workspace_owner(session, workspace.id) != owner_scope:
        raise PermissionError("Research workspace ownership is not authorized")


def register_metadata_object(
    session: Session, *, workspace: ws.EntryWorkspace, owner_scope: str,
    authority: str, source_id: str, source_version: str,
    kind: str, title: str, public_summary: str = "", doi: str | None = None,
    concepts: list[str] | None = None, source_sha256: str | None = None,
    retrieved_at: datetime | None = None, source_license_ref: str | None = None,
    data_classification: str = "unknown",
) -> ResearchObject:
    """Internal-only registration by verified owner, idempotent on source/version.

    Source data must already have been obtained through authorized adapters.
    Repeated source identity must not silently overwrite its metadata/version.
    The caller controls commit/rollback. No editor route is exposed yet.
    """
    _require_workspace_owner(session, workspace, owner_scope)
    if authority not in SOURCE_AUTHORITIES or kind not in KINDS:
        raise ValueError("Unsupported Research authority or object kind")
    if not source_id or len(source_id) > 512 or not source_version or len(source_version) > 128:
        raise ValueError("Complete bounded source identity and version required")
    if not title.strip() or len(title) > 350 or len(public_summary) > 10000:
        raise ValueError("Invalid title or public summary length")
    if source_sha256 is not None and not SHA256.fullmatch(source_sha256):
        raise ValueError("Invalid source SHA-256")
    if data_classification not in DATA_CLASSIFICATIONS:
        raise ValueError("Invalid scientific data classification")
    if doi is not None and (len(doi) > 240 or not doi.startswith("10.")):
        raise ValueError("DOI must be canonical metadata or omitted")
    if concepts is not None and (
        not isinstance(concepts, list) or len(concepts) > 32
        or any(not isinstance(s, str) or len(s) > 80 for s in concepts)
    ):
        raise ValueError("Invalid concepts metadata")
    record = session.scalar(
        select(ResearchObject).where(
            ResearchObject.workspace_id == workspace.id,
            ResearchObject.authority == authority,
            ResearchObject.source_id == source_id,
            ResearchObject.source_version == source_version,
        )
    )
    if record is not None:
        return record
    record = ResearchObject(
        workspace_id=workspace.id, authority=authority, source_id=source_id,
        source_version=source_version, kind=kind, title=title.strip(),
        public_summary=public_summary, doi=doi, concepts=concepts or [],
        source_sha256=source_sha256, retrieved_at=retrieved_at,
        source_license_ref=source_license_ref, data_classification=data_classification,
        visibility="private", rights_cleared=False, review_state="unreviewed",
    )
    session.add(record)
    session.flush()
    return record


def propose_relation(
    session: Session, *, workspace: ws.EntryWorkspace, owner_scope: str,
    subject: ResearchObject, target: ResearchObject,
    predicate: str, private_basis: str = "",
) -> ResearchObjectRelation:
    """Internal-only proposed edge; both endpoints must belong to same owner."""
    _require_workspace_owner(session, workspace, owner_scope)
    for endpoint in (subject, target):
        if _workspace_owner(session, endpoint.workspace_id) != owner_scope:
            raise PermissionError("Cross-owner relationship forbidden")
    if subject.id == target.id or not predicate or len(predicate) > 80:
        raise ValueError("Invalid relation or self-link")
    if len(private_basis) > 4000:
        raise ValueError("Relation rationale too long")
    row = session.scalar(select(ResearchObjectRelation).where(
        ResearchObjectRelation.workspace_id == workspace.id,
        ResearchObjectRelation.subject_id == subject.id,
        ResearchObjectRelation.object_id == target.id,
        ResearchObjectRelation.predicate == predicate,
    ))
    if row is not None:
        return row
    row = ResearchObjectRelation(
        workspace_id=workspace.id, subject_id=subject.id, object_id=target.id,
        predicate=predicate, private_basis=private_basis,
        visibility="private", review_state="proposed",
    )
    session.add(row)
    session.flush()
    return row


def public_query(*, article: str = "", kind: str = "", q: str = "", include_unlisted: bool = False, apply_hold: bool = True):
    query = (
        select(ResearchObject, ws.ResearchEntry.slug, ws.ResearchArea.title)
        .join(ws.EntryWorkspace, ws.EntryWorkspace.id == ResearchObject.workspace_id)
        .join(ws.ResearchEntry, ws.ResearchEntry.id == ws.EntryWorkspace.entry_id)
        .join(ws.ResearchProject, ws.ResearchProject.id == ws.ResearchEntry.project_id)
        .join(ws.ResearchArea, ws.ResearchArea.id == ws.ResearchProject.area_id)
        .where(
            ResearchObject.visibility == APPROVED,
            ResearchObject.rights_cleared.is_(True),
            ResearchObject.review_state == APPROVED,
            ws.ResearchEntry.visibility == APPROVED,
            ws.ResearchEntry.rights_cleared.is_(True),
            ws.ResearchEntry.state.in_(ws.PUBLIC_STATES),
            ws.ResearchArea.visibility == APPROVED,
        )
    )
    # SC-FIG-03A: a figure is public only while a live article revision releases its exact bundle,
    # so a figure dropped by an update leaves listings, search and the graph with its bytes.
    from . import figure_releases as fr  # figure_releases imports this module
    query = query.where(or_(ResearchObject.kind != "figure", ResearchObject.id.in_(fr.live_release_object_ids(apply_hold=apply_hold))))
    if article:
        query = query.where(ws.ResearchEntry.slug == article)
    elif not include_unlisted:
        query = query.where(ws.ResearchEntry.id.not_in(ws.unlisted_entry_ids()))
    if kind:
        query = query.where(ResearchObject.kind == kind)
    if q:
        escaped = q.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        term = "%" + escaped + "%"
        query = query.where(or_(
            ResearchObject.title.ilike(term, escape="!"),
            ResearchObject.public_summary.ilike(term, escape="!"),
        ))
    return query.order_by(ResearchObject.title, ResearchObject.public_key)


def object_public_dict(obj: ResearchObject, article: str, area: str) -> dict:
    """Explicit field-level projection. Never emit original IDs or source paths."""
    return {
        "id": obj.public_key, "title": obj.title, "summary": obj.public_summary,
        "kind": obj.kind, "authority": obj.authority,
        "article": article, "area": area,
        "doi": obj.doi, "concepts": list(obj.concepts or []),
        "data_classification": obj.data_classification,
        "review_status": "approved-public",
        "rights": "cleared",
        "retrieved_at": obj.retrieved_at.isoformat() if obj.retrieved_at else None,
    }


@router.get("/objects")
def list_public_objects(
    article: str = Query(default="", max_length=150),
    kind: str = Query(default="", max_length=48),
    q: str = Query(default="", max_length=160),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=10000),
    session: Session = Depends(ws.get_session),
):
    rows = session.execute(public_query(article=article, kind=kind, q=q)
                           .offset(offset).limit(limit)).all()
    return {
        "schema": "sc-research-public-objects/1.0",
        "items": [object_public_dict(obj, article_slug, area)
                  for obj, article_slug, area in rows],
        "limit": limit, "offset": offset,
    }


@router.get("/objects/{public_key}")
def public_object(public_key: str, session: Session = Depends(ws.get_session)):
    if not UUID.fullmatch(public_key):
        raise HTTPException(status_code=404, detail="Research object not found")
    # A direct link (unguessable key) is not discovery: an unlisted article's objects stay reachable.
    row = session.execute(
        public_query(include_unlisted=True).where(ResearchObject.public_key == public_key)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Research object not found")
    return object_public_dict(*row)


def approved_metadata_graph(
    session: Session, *, article: str = "", limit: int = 80,
) -> dict:
    """Bounded graph snapshot; include only nodes approved at every level.

    A graph edge is only emitted when its own review is approved AND both
    endpoint objects occur in this fully approved projection.
    """
    rows = session.execute(public_query(article=article).limit(limit)).all()
    lookup = {obj.id: obj.public_key for obj, _, _ in rows}
    ids = list(lookup)
    nodes = [object_public_dict(*row) for row in rows]
    if not ids:
        return {
            "schema": "sc-research-public-object-graph/1.0",
            "nodes": [], "edges": [], "truncated_at": limit,
            "interpretation": "Explicit approved metadata relations only; not scientific proof.",
        }
    records = session.scalars(
        select(ResearchObjectRelation)
        .where(
            ResearchObjectRelation.workspace_id.in_(
                select(ws.EntryWorkspace.id)
                .join(ws.ResearchEntry, ws.ResearchEntry.id == ws.EntryWorkspace.entry_id)
                .join(ws.ResearchProject, ws.ResearchProject.id == ws.ResearchEntry.project_id)
                .join(ws.ResearchArea, ws.ResearchArea.id == ws.ResearchProject.area_id)
                .where(ws.ResearchEntry.visibility == APPROVED,
                       ws.ResearchEntry.rights_cleared.is_(True),
                       ws.ResearchEntry.state.in_(ws.PUBLIC_STATES),
                       ws.ResearchArea.visibility == APPROVED)
            ),
            ResearchObjectRelation.subject_id.in_(ids),
            ResearchObjectRelation.object_id.in_(ids),
            ResearchObjectRelation.visibility == APPROVED,
            ResearchObjectRelation.review_state == APPROVED,
            ResearchObjectRelation.evidence_assertion.is_(False),
            ResearchObjectRelation.predicate.in_(PUBLIC_PREDICATES),
        ).order_by(ResearchObjectRelation.predicate, ResearchObjectRelation.id)
        .limit(300)
    ).all()
    edges = [{
        "source": lookup[r.subject_id], "target": lookup[r.object_id],
        "relation": r.predicate, "note": r.public_note,
        "interpretation": "reviewed metadata/structural link",
    } for r in records]
    return {
        "schema": "sc-research-public-object-graph/1.0",
        "nodes": nodes, "edges": edges, "truncated_at": limit,
        "interpretation": "Explicit approved metadata relations only; not scientific proof.",
    }


@router.get("/graph/metadata")
def metadata_graph(
    article: str = Query(default="", max_length=150),
    limit: int = Query(default=80, ge=1, le=150),
    session: Session = Depends(ws.get_session),
):
    return approved_metadata_graph(session, article=article, limit=limit)


@router.get("/objects/stats/summary")
def metadata_stats(session: Session = Depends(ws.get_session)):
    """Public-only counts; never reveal how many private sources exist."""
    rows = session.execute(public_query().limit(500)).all()
    kinds: dict[str, int] = {}
    for obj, _, _ in rows:
        kinds[obj.kind] = kinds.get(obj.kind, 0) + 1
    return {
        "schema": "sc-research-public-object-stats/1.0",
        "approved_sample_count": len(rows),
        "by_kind": dict(sorted(kinds.items())),
        "sample_limit": 500,
        "is_complete": len(rows) < 500,
    }


# RH-05.2 graph analysis is performed strictly on the already-approved graph.
# It never inspects owner-private objects, unreviewed edges, or source bytes.
from . import graph_analysis


@router.get("/graph/metadata/analysis")
def public_graph_analysis(
    article: str = Query(default="", max_length=150),
    session: Session = Depends(ws.get_session),
):
    graph = approved_metadata_graph(session, article=article, limit=150)
    return graph_analysis.analyze_public_graph(graph)


@router.get("/graph/metadata/neighborhood/{public_key}")
def public_graph_neighborhood(
    public_key: str,
    article: str = Query(default="", max_length=150),
    depth: int = Query(default=1, ge=1, le=3),
    session: Session = Depends(ws.get_session),
):
    if not UUID.fullmatch(public_key):
        raise HTTPException(status_code=404, detail="Research object not found")
    graph = approved_metadata_graph(session, article=article, limit=150)
    result = graph_analysis.neighborhood_public_graph(graph, public_key, depth)
    if result is None:
        raise HTTPException(status_code=404, detail="Approved graph object not found")
    return result


@router.get("/graph/metadata/path")
def public_graph_path(
    source: str = Query(max_length=36),
    target: str = Query(max_length=36),
    article: str = Query(default="", max_length=150),
    max_depth: int = Query(default=5, ge=1, le=6),
    session: Session = Depends(ws.get_session),
):
    if not UUID.fullmatch(source) or not UUID.fullmatch(target):
        raise HTTPException(status_code=404, detail="Approved graph objects not found")
    graph = approved_metadata_graph(session, article=article, limit=150)
    result = graph_analysis.shortest_public_path(graph, source, target, max_depth)
    if result is None:
        raise HTTPException(status_code=404, detail="No approved relationship path found")
    return result
