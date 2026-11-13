"""RH-08D-04: storage for governed discovery intake (tables only).

Kept apart from ``discovery_intake`` (the gated logic and routes) so the schema, its migration and the PostgreSQL
acceptance need no discovery adapter code. Provenance rows, Library proposals and audit events are append-only in
PostgreSQL (migration ``0013_discovery_intake``).
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from . import workspaces as ws


def _id() -> str:
    return str(uuid4())


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
    # The candidate metadata exactly as it arrived with this record, so every metadata_sha256 can be checked and
    # later disagreements (a retraction, a corrected title) reach the Library reviewer.
    candidate_metadata: Mapped[dict] = mapped_column("metadata", JSON, nullable=False)
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


