"""SC-RIGHTS-02: reconciliation and freshness of Workspace source rights in Research.

Workspace is the rights authority. It signs notices (applied by ``workspace_ingest.apply_source_rights_notice``)
and, periodically, an attestation of the latest notice it has issued for each source of one owner. Research
keeps no second registry: it compares the attestation with the notices it applied and records, per owner and
source, whether the two agree (``in-sync``) and when Workspace last said so.

Rights-freshness policy. A released figure drawn from Workspace sources is served only while every one of its
sources was confirmed ``in-sync`` within the freshness window. If Workspace has issued a notice Research has
not applied, if the two disagree, if Workspace does not recognise the source, or if no confirmation is recent
enough, the figure is held: it leaves listings, search, the graph and the figure routes. A hold is not a
revocation. Approvals, releases and bytes are kept, nothing is written to the editorial chain, and the figure
is served again once a fresh attestation confirms the same rights. A restriction still revokes through its
notice. The window is ``RESEARCH_RIGHTS_FRESHNESS_SECONDS``; production and staging default to 24 hours; an
invalid value holds everything.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, and_, exists, or_, select
from sqlalchemy.orm import Mapped, Session, aliased, mapped_column

from . import convergence as cv
from . import object_registry as md
from . import workspaces as ws

ATTESTATION_SCHEMA = "sc-workspace-source-rights-attestation/1.0"
STATE_SCHEMA = "sc-research-source-rights-state/1.0"
DEFAULT_WINDOW = 86400
MIN_WINDOW, MAX_WINDOW = 60, 7 * 86400
MAX_FUTURE_SKEW = 300
MAX_SOURCES = 500
IN_SYNC = "in-sync"


class FigureRightsSource(ws.Base):
    """The Workspace sources of a released figure (its signed binding's sourceLineage), for the public query."""
    __tablename__ = "research_figure_rights_sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ws.new_id)
    object_id: Mapped[str] = mapped_column(ForeignKey("research_metadata_objects.id"), nullable=False, index=True)
    owner_scope: Mapped[str] = mapped_column(String(160), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    __table_args__ = (UniqueConstraint("object_id", "source_sha256", name="uq_research_figure_rights_source"),)


class SourceRightsConfirmation(ws.Base):
    """Latest comparison of Workspace's attested rights with the notices Research applied (one row per source)."""
    __tablename__ = "research_source_rights_confirmations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ws.new_id)
    owner_scope: Mapped[str] = mapped_column(String(160), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attested_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    research_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    attestation_id: Mapped[str] = mapped_column(String(96), nullable=False)
    attested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attested_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc, nullable=False)
    __table_args__ = (UniqueConstraint("owner_scope", "source_sha256", name="uq_research_source_rights_confirmation"),)


class SourceRightsAttestationLog(ws.Base):
    """Append-only: every attestation Research accepted and what it concluded."""
    __tablename__ = "research_source_rights_attestations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ws.new_id)
    attestation_id: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    attestation_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    owner_scope: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    issued_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    results: Mapped[list] = mapped_column(JSON, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc, nullable=False)


# ---------------------------------------------------------------------------
# Policy


def freshness_window() -> int | None:
    """Seconds a confirmation stays valid, or None when the hold is off (local development and tests only)."""
    value = os.getenv("RESEARCH_RIGHTS_FRESHNESS_SECONDS", "").strip()
    env = os.getenv("RESEARCH_ENV", "").strip().lower()
    if not value:
        return DEFAULT_WINDOW if env in {"production", "staging"} else None
    if value == "off" and env not in {"production", "staging"}:
        return None
    try:
        seconds = int(value)
    except ValueError:
        return 0  # unparseable: nothing can be confirmed, so every Workspace figure is held
    return min(max(seconds, MIN_WINDOW), MAX_WINDOW)


def _epoch(when: datetime) -> int:
    return int(when.timestamp())


def held_condition(release_object_id, release_object_authority):
    """SQL: true when a figure must be held (used inside ``figure_releases.live_release_object_ids``)."""
    window = freshness_window()
    if window is None:
        return None
    cutoff = _epoch(datetime.now(timezone.utc)) - window
    src, conf = aliased(FigureRightsSource), aliased(SourceRightsConfirmation)
    confirmed = exists().where(conf.owner_scope == src.owner_scope, conf.source_sha256 == src.source_sha256,
                               conf.status == IN_SYNC, conf.attested_epoch >= cutoff)
    unconfirmed_source = exists().where(src.object_id == release_object_id, ~confirmed)
    src2 = aliased(FigureRightsSource)
    # Fail closed: a Workspace figure whose sources were never recorded is held as well.
    unrecorded = and_(release_object_authority == "workspace", ~exists().where(src2.object_id == release_object_id))
    return or_(unconfirmed_source, unrecorded)


def binding_problems(session: Session, owner_scope: str | None, binding: dict) -> list[str]:
    """The reader's own check for one figure (the figure routes call it in addition to the public query)."""
    window = freshness_window()
    if window is None:
        return []
    cutoff = _epoch(datetime.now(timezone.utc)) - window
    shas = [s.get("sha256") for s in binding.get("sourceLineage") or [] if s.get("sha256")]
    if not owner_scope or not shas:
        return ["source rights cannot be confirmed for this figure"]
    problems = []
    for sha in shas:
        row = session.scalar(select(SourceRightsConfirmation).where(
            SourceRightsConfirmation.owner_scope == owner_scope, SourceRightsConfirmation.source_sha256 == sha))
        if row is None or row.status != IN_SYNC or row.attested_epoch < cutoff:
            problems.append(f"source {sha[:12]} rights are not confirmed as current "
                            f"({'never confirmed' if row is None else row.status if row.status != IN_SYNC else 'confirmation expired'})")
    return problems


def record_release_sources(session: Session, obj: md.ResearchObject, binding: dict) -> None:
    owner = md._workspace_owner(session, obj.workspace_id) or ""
    for s in binding.get("sourceLineage") or []:
        sha = s.get("sha256")
        if sha and session.scalar(select(FigureRightsSource).where(
                FigureRightsSource.object_id == obj.id, FigureRightsSource.source_sha256 == sha)) is None:
            session.add(FigureRightsSource(object_id=obj.id, owner_scope=owner, source_sha256=sha))
    session.flush()


# ---------------------------------------------------------------------------
# State and attestations


def _applied(session: Session, owner: str | None = None) -> dict[tuple[str, str], Any]:
    from .workspace_ingest import WorkspaceSourceRightsNotice as N  # workspace_ingest imports figure_releases
    q = select(N).order_by(N.owner_scope, N.source_sha256, N.sequence)
    if owner is not None:
        q = q.where(N.owner_scope == owner)
    latest, gaps = {}, {}
    for row in session.scalars(q):
        k = (row.owner_scope, row.source_sha256)
        if k in latest and row.sequence != latest[k].sequence + 1:
            gaps.setdefault(k, []).extend(range(latest[k].sequence + 1, row.sequence))
        elif k not in latest and row.sequence != 1:
            gaps.setdefault(k, []).extend(range(1, row.sequence))
        latest[k] = row
    return {k: (row, gaps.get(k, [])) for k, row in latest.items()}


def state(session: Session) -> dict:
    """What Research has applied and which sources its public figures need confirmed, per owner."""
    from .figure_releases import live_release_object_ids
    live = set(session.scalars(live_release_object_ids(apply_hold=False)))
    needed: dict[tuple[str, str], int] = {}
    for src in session.scalars(select(FigureRightsSource)):
        if src.object_id in live:
            needed[(src.owner_scope, src.source_sha256)] = needed.get((src.owner_scope, src.source_sha256), 0) + 1
    applied = _applied(session)
    confirmations = {(c.owner_scope, c.source_sha256): c for c in session.scalars(select(SourceRightsConfirmation))}
    owners: dict[str, list] = {}
    for k in sorted(set(applied) | set(needed)):
        row, gaps = applied.get(k, (None, []))
        c = confirmations.get(k)
        owners.setdefault(k[0], []).append({
            "sourceSha256": k[1], "appliedSequence": row.sequence if row else 0,
            "appliedNoticeSha256": row.notice_sha256 if row else None,
            "licenceStatus": row.licence_status if row else None, "chainGaps": gaps,
            "liveFigures": needed.get(k, 0),
            "confirmation": ({"status": c.status, "attestedAt": c.attested_at.isoformat() if c.attested_at else None,
                              "attestationId": c.attestation_id} if c else None)})
    return {"schema": STATE_SCHEMA, "generatedAt": datetime.now(timezone.utc).isoformat(),
            "freshnessWindowSeconds": freshness_window(),
            "owners": [{"ownerScope": o, "sources": srcs} for o, srcs in owners.items()]}


def apply_attestation(session: Session, *, key, attestation: dict) -> dict:
    """Verify a Workspace attestation, compare it with what Research applied, and record the confirmations."""
    from .workspace_ingest import Keyring, verify_document
    try:
        body = verify_document(key, ATTESTATION_SCHEMA, attestation)
        if isinstance(key, Keyring):
            key.require_current((attestation.get("signature") or {}).get("keyId"))
    except ValueError as exc:
        raise PermissionError(f"Source rights attestation refused: {exc}") from exc
    owner, att_id = body.get("ownerScope"), body.get("attestationId")
    issued = _issued(body.get("issuedAt"))
    sources = body.get("sources")
    if not (isinstance(owner, str) and owner.startswith("workspace-user:") and isinstance(att_id, str)
            and 0 < len(att_id) <= 96 and issued is not None and isinstance(sources, list) and len(sources) <= MAX_SOURCES):
        raise ValueError("Source rights attestation is malformed")
    digest = cv.canonical_sha256(attestation)
    seen = session.scalar(select(SourceRightsAttestationLog).where(SourceRightsAttestationLog.attestation_id == att_id))
    if seen is not None:
        if seen.attestation_sha256 != digest:
            raise PermissionError("A different attestation with this id was already recorded")
        return {"attestationId": att_id, "replayed": True, "results": seen.results, "missing": []}
    now = datetime.now(timezone.utc)
    if issued > now + timedelta(seconds=MAX_FUTURE_SKEW):
        raise ValueError("Source rights attestation is dated in the future")
    from .workspace_ingest import StaleNotice
    newest = session.scalar(select(SourceRightsAttestationLog.issued_epoch).where(
        SourceRightsAttestationLog.owner_scope == owner).order_by(SourceRightsAttestationLog.issued_epoch.desc()).limit(1))
    if newest is not None and _epoch(issued) < newest:
        raise StaleNotice("A newer source rights attestation was already recorded for this owner")
    applied = _applied(session, owner)
    results, listed = [], set()
    for s in sources:
        if not isinstance(s, dict):
            raise ValueError("Source rights attestation sources must be objects")
        sha, seq = s.get("sourceSha256"), s.get("sequence")
        if not (isinstance(sha, str) and len(sha) == 64 and isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0):
            raise ValueError("Source rights attestation names a malformed source")
        listed.add(sha)
        row, _gaps = applied.get((owner, sha), (None, []))
        have = row.sequence if row else 0
        if s.get("known") is False:
            status = "unknown-to-workspace"
        elif seq > have:
            status = "research-behind"
        elif seq < have:
            # Built before Research applied a newer notice: not a confirmation, and not a conflict either.
            status = "attestation-older"
        elif seq and s.get("noticeSha256") != row.notice_sha256:
            status = "divergent"
        else:
            # Same latest notice (sequence and digest): the source's current rights are identical. Earlier
            # gaps are notices a restriction overtook; they are stale and can never be applied, so they
            # neither make Research behind nor block confirmation. They stay visible in ``state``.
            status = IN_SYNC
        results.append({"sourceSha256": sha, "status": status, "researchSequence": have, "attestedSequence": seq})
        c = session.scalar(select(SourceRightsConfirmation).where(
            SourceRightsConfirmation.owner_scope == owner, SourceRightsConfirmation.source_sha256 == sha))
        if c is None:
            c = SourceRightsConfirmation(owner_scope=owner, source_sha256=sha)
            session.add(c)
        c.status, c.attested_sequence, c.research_sequence = status, seq, have
        c.attestation_id, c.attested_at, c.attested_epoch, c.received_at = att_id, issued, _epoch(issued), now
    needed = {k[1] for k in _needed_sources(session) if k[0] == owner}
    session.add(SourceRightsAttestationLog(attestation_id=att_id, attestation_sha256=digest, owner_scope=owner,
                                           key_id=str((attestation.get("signature") or {}).get("keyId") or ""),
                                           issued_epoch=_epoch(issued), results=results))
    session.flush()
    return {"attestationId": att_id, "replayed": False, "results": results, "missing": sorted(needed - listed)}


def _needed_sources(session: Session) -> set[tuple[str, str]]:
    from .figure_releases import live_release_object_ids
    live = set(session.scalars(live_release_object_ids(apply_hold=False)))
    return {(s.owner_scope, s.source_sha256) for s in session.scalars(select(FigureRightsSource)) if s.object_id in live}


def _issued(value: Any) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else None
