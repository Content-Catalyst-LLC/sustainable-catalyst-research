"""SC-FIG-02B — map an sc.figure-bundle.v1 into the existing Research object registry.

A figure bundle (from Workbench, Workspace, Lab, Site Intelligence or Library) becomes one private,
unreviewed ``figure`` object, registered through ``object_registry.register_metadata_object``. There
is no separate figure table or registry. The object's identity is the producer, the figure id and
version, plus the SHA-256 of the exact bundle bytes, which bind every artifact, source and
calculation hash inside the bundle.

Datasets and sources already in the same workspace whose SHA-256 matches a bundle source or
calculation output get a proposed, private ``derived-from`` relation. No records are invented for
anything else, and nothing becomes public: visibility, review and rights stay with the existing
approval flow. Possession of a bundle grants no publication right.
"""
from __future__ import annotations

import hashlib
import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import object_registry as md
from . import workspaces as ws

BUNDLE_SCHEMA = "sc.figure-bundle.v1"
FIGURE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,95}$")
MAX_BUNDLE_BYTES = 1024 * 1024
EVIDENCE = {"observed", "literature-derived", "modelled", "hypothetical", "conceptual", "status-only"}
# Which measurement status each evidence class may carry: no promotion to observed.
MEASUREMENT = {
    "observed": {"observed"},
    "literature-derived": {"published-literature"},
    "modelled": {"computed-from-specified-inputs"},
    "hypothetical": {"hypothetical-only"},
    "conceptual": {"conceptual-only", "not-applicable"},
    "status-only": {"not-applicable"},
}
CLASSIFICATION = {
    "observed": "observed", "modelled": "modelled", "hypothetical": "illustrative",
    "conceptual": "illustrative", "literature-derived": "unknown", "status-only": "unknown",
}
# Producers that are also Research source authorities. Core and local review are not.
PRODUCERS = {"workbench", "workspace", "lab", "site-intelligence", "library"}


def _hashes(bundle: dict) -> list[tuple[str, str]]:
    out = []
    for i, s in enumerate(bundle.get("sourceRefs") or []):
        if isinstance(s, dict) and md.SHA256.fullmatch(str(s.get("sha256") or "")):
            out.append((s["sha256"], f"sourceRefs[{i}] {s.get('sourceId', '')}"[:300]))
    for i, c in enumerate(bundle.get("calculationRefs") or []):
        if isinstance(c, dict) and md.SHA256.fullmatch(str(c.get("outputSha256") or "")):
            out.append((c["outputSha256"], f"calculationRefs[{i}] {c.get('runRef', '')}"[:300]))
    return out


def parse_bundle(raw: bytes) -> tuple[dict, str]:
    if not isinstance(raw, (bytes, bytearray)) or not raw or len(raw) > MAX_BUNDLE_BYTES:
        raise ValueError("Figure bundle must be 1 byte to 1 MiB")
    try:
        bundle = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Figure bundle is not UTF-8 JSON") from exc
    if not isinstance(bundle, dict) or bundle.get("schema") != BUNDLE_SCHEMA:
        raise ValueError("Not an sc.figure-bundle.v1 document")
    if not FIGURE_ID.fullmatch(str(bundle.get("figureId") or "")):
        raise ValueError("Invalid figureId")
    version = bundle.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError("Invalid figure version")
    evidence = bundle.get("evidenceClass")
    status = (bundle.get("scientificContext") or {}).get("measurementStatus")
    if evidence not in EVIDENCE or status not in MEASUREMENT[evidence]:
        raise ValueError("Evidence class and measurement status disagree (no promotion to observed)")
    if (bundle.get("provenance") or {}).get("authorityOwner") not in PRODUCERS:
        raise ValueError("Figure producer is not a Research source authority")
    if not isinstance(bundle.get("title"), str) or not bundle["title"].strip():
        raise ValueError("Figure title required")
    if not bundle.get("artifacts") or not bundle.get("sourceRefs"):
        raise ValueError("A figure bundle needs artifacts and source references")
    return bundle, hashlib.sha256(bytes(raw)).hexdigest()


def register_figure_bundle(session: Session, *, workspace: ws.EntryWorkspace, owner_scope: str, raw: bytes) -> dict:
    """Register (idempotently) one figure bundle revision as a private Research figure object.

    The caller controls commit/rollback, as for every registry write.
    """
    bundle, sha = parse_bundle(raw)
    authority, version = bundle["provenance"]["authorityOwner"], bundle.get("version", 1)
    source_id = f"figure:{bundle['figureId']}"
    prefix = f"v{version}+"
    clash = session.scalar(select(md.ResearchObject).where(
        md.ResearchObject.workspace_id == workspace.id, md.ResearchObject.authority == authority,
        md.ResearchObject.source_id == source_id, md.ResearchObject.source_version.startswith(prefix),
        md.ResearchObject.source_sha256 != sha))
    if clash is not None:
        raise ValueError("This figure version is already registered with different bytes; issue a new version")
    synthetic = (bundle.get("project") or {}).get("ownerScope") == "isolated-synthetic" or \
        (bundle.get("provenance") or {}).get("reproducibility") == "synthetic-fixture-tested"
    figure = md.register_metadata_object(
        session, workspace=workspace, owner_scope=owner_scope, authority=authority,
        source_id=source_id, source_version=f"{prefix}{sha[:16]}", kind="figure",
        title=bundle["title"][:350],
        # Public text is written at review, never copied from the producer's caption.
        public_summary="", source_sha256=sha,
        data_classification="synthetic" if synthetic else CLASSIFICATION[bundle["evidenceClass"]],
    )
    relations, unmatched = [], []
    for digest, basis in _hashes(bundle):
        targets = session.scalars(select(md.ResearchObject).where(
            md.ResearchObject.workspace_id == workspace.id, md.ResearchObject.source_sha256 == digest,
            md.ResearchObject.id != figure.id, md.ResearchObject.kind.in_(("dataset", "source", "statistical-result", "model", "notebook", "analysis")),
        ).order_by(md.ResearchObject.created_at, md.ResearchObject.id)).all()
        if not targets:
            unmatched.append(basis)
        for target in targets:
            relations.append(md.propose_relation(
                session, workspace=workspace, owner_scope=owner_scope, subject=figure, target=target,
                predicate="derived-from", private_basis=f"{BUNDLE_SCHEMA} {sha} {basis}"))
    return {
        "object": figure, "bundleSha256": sha, "relations": relations, "unmatched": unmatched,
        "visibility": figure.visibility, "reviewState": figure.review_state, "rightsCleared": figure.rights_cleared,
    }
