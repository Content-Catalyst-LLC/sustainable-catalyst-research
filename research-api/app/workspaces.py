"""RH-02 — Persistent, article-scoped Research workspaces.

The public API exposes approved metadata only. Editors use internal services until
the existing Workspace identity/authorization bridge has been accepted. This
module MUST NOT offer unauthenticated write routes.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
    create_engine, select,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker,
)

PREFIX = "/api/research/v1"
router = APIRouter(prefix=PREFIX, tags=["Public research workspaces"])
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SOURCES = frozenset({
    "library", "research-librarian", "workspace", "reporting-studio",
    "workbench", "lab", "site-intelligence", "publications", "research",
})
KINDS = frozenset({"figure", "dataset", "source", "analysis", "notebook", "publication", "method"})
PUBLIC_STATES = frozenset({"active-research", "published", "updated", "superseded"})
APPROVED = "public-approved"


def new_id() -> str:
    return str(uuid4())


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ResearchArea(Base):
    __tablename__ = "research_areas"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), default="private", nullable=False)


class ResearchProject(Base):
    __tablename__ = "research_projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    area_id: Mapped[str] = mapped_column(ForeignKey("research_areas.id"), nullable=False)
    project_key: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_scope: Mapped[str] = mapped_column(String(160), nullable=False)
    external_workspace_ref: Mapped[str | None] = mapped_column(String(255))
    __table_args__ = (UniqueConstraint("area_id", "project_key", name="uq_research_project_key"),)


class ResearchEntry(Base):
    __tablename__ = "research_entries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("research_projects.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(150), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(350), nullable=False)
    public_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), default="private", nullable=False)
    rights_cleared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="active-research", nullable=False)


class EntryWorkspace(Base):
    __tablename__ = "research_entry_workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entry_id: Mapped[str] = mapped_column(
        ForeignKey("research_entries.id"), unique=True, nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False,
    )


class WorkspaceRevision(Base):
    __tablename__ = "research_workspace_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("research_entry_workspaces.id"), nullable=False, index=True,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    change_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    review_state: Mapped[str] = mapped_column(String(32), default="private", nullable=False)
    rights_cleared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False,
    )
    __table_args__ = (
        UniqueConstraint("workspace_id", "revision_number", name="uq_workspace_revision"),
    )


class WorkspaceObjectRef(Base):
    __tablename__ = "research_workspace_object_refs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("research_entry_workspaces.id"), nullable=False, index=True,
    )
    source_authority: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    public_title: Mapped[str] = mapped_column(String(350), default="", nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), default="private", nullable=False)
    rights_cleared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    __table_args__ = (
        UniqueConstraint("workspace_id", "source_authority", "source_ref", name="uq_workspace_source"),
    )


# A database connection is configured ONLY when explicitly requested.
_engine = None
_session_factory = None


def configure_engine(db_url: str) -> None:
    """Set up Research's separate database; never connect to upstream product DBs."""
    global _engine, _session_factory
    _engine = create_engine(db_url, pool_pre_ping=True, connect_args={"connect_timeout": 5, "options": "-c statement_timeout=5000"} if db_url.startswith("postgresql") else {})
    _session_factory = sessionmaker(_engine, expire_on_commit=False)


def bootstrap_dev_schema() -> None:
    """Local/isolated development only; production requires reviewed migrations."""
    if os.getenv("RESEARCH_ENV") != "development" or os.getenv("RESEARCH_DEV_AUTO_CREATE_SCHEMA") != "1":
        raise RuntimeError("Auto-creating a schema is disabled outside explicit development")
    if _engine is None:
        raise RuntimeError("Research DB must be configured")
    Base.metadata.create_all(_engine)


def get_session():
    if _session_factory is None:
        raise HTTPException(status_code=503, detail="Research workspace storage not configured")
    with _session_factory() as session:
        yield session


def _valid_slug(slug: str) -> str:
    if not SLUG.fullmatch(slug):
        raise ValueError("Invalid research slug")
    return slug


def create_entry_workspace(
    session: Session, *,
    area_slug: str,
    project_key: str,
    owner_scope: str,
    entry_slug: str,
    title: str,
    public_summary: str = "",
) -> EntryWorkspace:
    """Internal project registrar: one stable logical workspace per entry.

    Idempotence prevents duplicate workspaces on import replays. Existing records
    cannot be silently reassigned to a different owner or project.
    Caller must authenticate and authorize owner scope before invoking this.
    """
    _valid_slug(area_slug)
    _valid_slug(project_key)
    _valid_slug(entry_slug)
    if not owner_scope.strip() or not title.strip():
        raise ValueError("Owner scope and title are required")
    area = session.scalar(select(ResearchArea).where(ResearchArea.slug == area_slug))
    if area is None:
        area = ResearchArea(slug=area_slug, title=area_slug.replace("-", " ").title())
        session.add(area)
        session.flush()
    project = session.scalar(select(ResearchProject).where(
        ResearchProject.area_id == area.id, ResearchProject.project_key == project_key,
    ))
    if project is None:
        project = ResearchProject(area_id=area.id, project_key=project_key, owner_scope=owner_scope)
        session.add(project)
        session.flush()
    if project.owner_scope != owner_scope:
        raise PermissionError("A project belongs to another owner scope")
    entry = session.scalar(select(ResearchEntry).where(ResearchEntry.slug == entry_slug))
    if entry is None:
        entry = ResearchEntry(
            project_id=project.id, slug=entry_slug, title=title, public_summary=public_summary,
        )
        session.add(entry)
        session.flush()
    if entry.project_id != project.id:
        raise ValueError("An existing research entry cannot be moved by import replay")
    workspace = session.scalar(select(EntryWorkspace).where(EntryWorkspace.entry_id == entry.id))
    if workspace is None:
        workspace = EntryWorkspace(entry_id=entry.id)
        session.add(workspace)
        session.flush()
    return workspace


def add_private_revision(
    session: Session, *, workspace: EntryWorkspace, change_summary: str,
    content_ref: str, content_sha256: str,
) -> WorkspaceRevision:
    """Create a new immutable private revision; no public promotion here."""
    if not re.fullmatch(r"[0-9a-f]{64}", content_sha256):
        raise ValueError("A verified SHA-256 is required")
    if not content_ref:
        raise ValueError("Revision content reference required")
    last = session.scalar(select(WorkspaceRevision.revision_number)
        .where(WorkspaceRevision.workspace_id == workspace.id)
        .order_by(WorkspaceRevision.revision_number.desc()).limit(1))
    revision = WorkspaceRevision(
        workspace_id=workspace.id, revision_number=(last or 0) + 1,
        change_summary=change_summary, content_ref=content_ref,
        content_sha256=content_sha256, review_state="private",
    )
    session.add(revision)
    session.flush()
    return revision


def attach_private_object(
    session: Session, *, workspace: EntryWorkspace,
    source_authority: str, source_ref: str, kind: str,
) -> WorkspaceObjectRef:
    """Only references upstream objects; does not download or copy data."""
    if source_authority not in SOURCES or kind not in KINDS or not source_ref:
        raise ValueError("Unsupported source authority, object type, or reference")
    obj = session.scalar(select(WorkspaceObjectRef).where(
        WorkspaceObjectRef.workspace_id == workspace.id,
        WorkspaceObjectRef.source_authority == source_authority,
        WorkspaceObjectRef.source_ref == source_ref,
    ))
    if obj is None:
        obj = WorkspaceObjectRef(
            workspace_id=workspace.id, source_authority=source_authority,
            source_ref=source_ref, kind=kind,
        )
        session.add(obj)
        session.flush()
    return obj


def approved_workspace_view(session: Session, slug: str) -> dict | None:
    if not SLUG.fullmatch(slug):
        return None
    row = session.execute(
        select(ResearchEntry, EntryWorkspace, ResearchArea)
        .join(EntryWorkspace, EntryWorkspace.entry_id == ResearchEntry.id)
        .join(ResearchProject, ResearchProject.id == ResearchEntry.project_id)
        .join(ResearchArea, ResearchArea.id == ResearchProject.area_id)
        .where(ResearchEntry.slug == slug)
    ).first()
    if row is None:
        return None
    entry, workspace, area = row
    if entry.visibility != APPROVED or area.visibility != APPROVED or not entry.rights_cleared:
        return None
    if entry.state not in PUBLIC_STATES:
        return None
    revisions = session.scalars(
        select(WorkspaceRevision)
        .where(
            WorkspaceRevision.workspace_id == workspace.id,
            WorkspaceRevision.review_state == APPROVED,
            WorkspaceRevision.rights_cleared.is_(True),
            WorkspaceRevision.released_at.is_not(None),
        )
        .order_by(WorkspaceRevision.revision_number.desc())
    ).all()
    objects = session.scalars(
        select(WorkspaceObjectRef).where(
            WorkspaceObjectRef.workspace_id == workspace.id,
            WorkspaceObjectRef.visibility == APPROVED,
            WorkspaceObjectRef.rights_cleared.is_(True),
        )
    ).all()
    # The public projection deliberately excludes DB IDs, owner scopes, source_refs,
    # content_refs, hashes, original/private objects, and external workspace IDs.
    return {
        "slug": entry.slug,
        "title": entry.title,
        "summary": entry.public_summary,
        "state": entry.state,
        "area": {"slug": area.slug, "title": area.title},
        "sections": ["overview", "figures-and-data", "evidence", "methods", "updates", "related-work"],
        "revisions": [
            {"version": rev.revision_number, "summary": rev.change_summary,
             "released_at": rev.released_at.isoformat()}
            for rev in revisions
        ],
        "objects": [
            {"kind": obj.kind, "title": obj.public_title,
             "source_authority": obj.source_authority}
            for obj in objects
        ],
    }


@router.get("/workspaces/{slug}")
def public_workspace(slug: str, session: Session = Depends(get_session)):
    record = approved_workspace_view(session, slug)
    if record is None:
        raise HTTPException(status_code=404, detail="Research workspace not found")
    return record


def approved_catalog_from_database(session: Session) -> tuple[list[dict], list[dict]]:
    """One Research-owned source of truth for public areas and article discovery.

    Only entries with their own workspace are discoverable. No private project
    identifiers or backend references appear in the anonymous catalog.
    """
    rows = session.execute(
        select(ResearchArea, ResearchEntry)
        .join(ResearchProject, ResearchProject.area_id == ResearchArea.id)
        .join(ResearchEntry, ResearchEntry.project_id == ResearchProject.id)
        .join(EntryWorkspace, EntryWorkspace.entry_id == ResearchEntry.id)
        .where(
            ResearchArea.visibility == APPROVED,
            ResearchEntry.visibility == APPROVED,
            ResearchEntry.rights_cleared.is_(True),
            ResearchEntry.state.in_(PUBLIC_STATES),
        )
    ).all()
    areas_by_slug = {}
    articles = []
    for area, entry in rows:
        areas_by_slug[area.slug] = {
            "slug": area.slug, "title": area.title, "summary": area.summary,
        }
        articles.append({
            "slug": entry.slug, "area_slug": area.slug,
            "title": entry.title, "summary": entry.public_summary,
            "version": "research-record", "state": entry.state,
        })
    return (
        sorted(areas_by_slug.values(), key=lambda item: item["title"].casefold()),
        sorted(articles, key=lambda item: item["title"].casefold()),
    )


def persisted_public_catalog() -> tuple[list[dict], list[dict]] | None:
    """None means the persistent Research store has not been configured."""
    if _session_factory is None:
        return None
    with _session_factory() as session:
        return approved_catalog_from_database(session)
