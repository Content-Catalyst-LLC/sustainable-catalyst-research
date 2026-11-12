"""SC-MODEL-01 — Research as the convergence environment for modelling outputs.

Research does not own projects, run computations, analyse decisions or render
figures. It records what the authoritative products produced and relates it:

* Workspace owns projects, permissions, collaboration and approvals. Research keys
  every record to an existing owner scope and only promotes a public projection
  after four separate Workspace human-approval decisions (scientific, rights,
  ownership, publication) bound to the exact provenance record hash.
* Workbench and the Workspace/Core language runtimes compute; Decision Studio
  analyses decisions; Lab renders and inspects figures. Their outputs arrive as
  hash-verified convergence bundles and are stored through the existing RH-05.2
  object registry and relation graph, never a second registry.
* Follow-up analyses, comparisons, reproductions and visualizations are emitted as
  Workspace handoff requests. Research never dispatches or executes them.

Every write starts private. Write routes are disabled unless an isolated staging
or development deployment explicitly enables the staging service credential; they
stay disabled in production until SC-UP-01 identity and ownership acceptance.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from sqlalchemy import DateTime, ForeignKey, JSON, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import object_registry as md
from . import workspaces as ws

PREFIX = "/api/research/v1"
router = APIRouter(prefix=PREFIX, tags=["SC-MODEL-01 Research convergence"])

BUNDLE_SCHEMA = "sc-research-convergence-bundle/1.0"
PROVENANCE_SCHEMA = "sc-research-output-provenance/1.0"
REPRODUCTION_SCHEMA = "sc-research-reproduction-verification/1.0"
VERIFIED_INPUT_LINEAGE = "artifact-bound-verified"
GRAPH_SCHEMA = "sc-research-private-knowledge-graph/1.0"
PUBLIC_PROVENANCE_SCHEMA = "sc-research-public-provenance/1.0"
# Workspace human-governance runtime v3.61.0 record schemas (reused, not redefined).
WS_APPROVAL_SCHEMA = "sc-workspace-human-approval-request/1.0"
WS_DECISION_SCHEMA = "sc-workspace-human-approval-decision/1.0"
# SC-ED-01: decisions made through Workspace's authenticated approval service, signed by Workspace.
WS_APPROVAL_RECORD_SCHEMA = "sc-workspace-research-approval-record/1.0"
# Workspace cross-product research handoff fabric (reused for follow-up requests).
WS_HANDOFF_PRODUCTS = frozenset({"workbench", "research-lab", "decision-studio", "platform-core"})
WS_HANDOFF_INTENTS = frozenset({"analyze", "simulate", "visualize", "validate", "compare", "decide"})

# Separate approvals; none implies another.
APPROVAL_ACTIONS = {
    "scientific": "research_scientific_review",
    "rights": "research_rights_clearance",
    "ownership": "research_ownership_confirmation",
    "publication": "research_publication",
}
COMPUTED_KINDS = frozenset({
    "computation", "simulation", "result", "table", "figure",
    "scenario-comparison", "decision-analysis", "reproduction", "statistical-result",
})
# Execution states that can never be presented as a verified output.
UNVERIFIED_STATUSES = frozenset({
    "failed", "blocked", "cancelled", "incomplete", "planned", "queued", "running",
    "succeeded-without-runtime-receipt", "not-reproduced", "not-reproducible",
    "reproduced-unverified-inputs", "inputs-differ", "replayed-deterministic", "replicated-same-implementation",
})
# Results must be independently reproduced before a public projection.
REPRODUCTION_REQUIRED_KINDS = frozenset({"computation", "simulation", "result", "statistical-result"})
CONVERGENCE_PREDICATES = frozenset({
    "derived-from", "uses-dataset", "executes-model", "produced-by", "visualizes",
    "compares", "reproduces", "informs", "answers", "assumes", "synthesizes",
    "contributes-to", "cites", "references",
})
# Upstream direction of these predicates: subject is computed from / depends on object.
DERIVATION_PREDICATES = frozenset({
    "derived-from", "uses-dataset", "executes-model", "produced-by", "visualizes",
    "compares", "synthesizes",
})
NOT_OBSERVED = frozenset({"modelled", "synthetic", "illustrative", "literature-derived"})
PROVENANCE_REQUIRED = (
    "sourceRefs", "scientificClassification", "uncertainty", "artifactSha256",
    "verification", "reviewStatus",
)
COMPUTED_REQUIRED = ("runtime", "codeVersion", "parameterSha256", "executionReceipt")
MAX_BUNDLE_OBJECTS = 64
MAX_BUNDLE_RELATIONS = 256


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class OutputProvenance(ws.Base):
    """Immutable provenance for one Research object revision (one per object)."""
    __tablename__ = "research_output_provenance"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ws.new_id)
    object_id: Mapped[str] = mapped_column(
        ForeignKey("research_metadata_objects.id"), unique=True, nullable=False, index=True,
    )
    schema: Mapped[str] = mapped_column(String(80), nullable=False)
    record: Mapped[dict] = mapped_column(JSON, nullable=False)
    record_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc)


class PublicationApproval(ws.Base):
    """A Workspace human decision recorded against an exact provenance hash."""
    __tablename__ = "research_publication_approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ws.new_id)
    object_id: Mapped[str] = mapped_column(
        ForeignKey("research_metadata_objects.id"), nullable=False, index=True,
    )
    dimension: Mapped[str] = mapped_column(String(24), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    workspace_approval_id: Mapped[str] = mapped_column(String(120), nullable=False)
    workspace_decision_id: Mapped[str] = mapped_column(String(120), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(160), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(80), nullable=False)
    record: Mapped[dict] = mapped_column(JSON, nullable=False)
    record_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc)
    __table_args__ = (UniqueConstraint("object_id", "dimension", name="uq_research_publication_approval"),)


# ---------------------------------------------------------------------------
# Validation helpers


def _sha(value: Any, where: str) -> str:
    if not isinstance(value, str) or not md.SHA256.fullmatch(value):
        raise ValueError(f"{where} must be a lowercase SHA-256")
    return value


DEFAULT_TOLERANCE = {"relative": 1e-9, "absolute": 1e-12}
MAX_TOLERANCE = {"relative": 0.05, "absolute": 1.0}


class ToleranceRelaxed(ValueError):
    """A reproduction request asked for a looser tolerance than was pre-registered."""


Z_BOUNDS = (1.0, 6.0)


def normalize_tolerance(spec: Any) -> dict | None:
    """A declared {relative, absolute[, statistical]} tolerance within the global bounds, or None.

    ``statistical`` ({method: z-score, z}) is the pre-specified agreement test for independent
    reproductions of stochastic values: |a - b| <= z * sqrt(se_a^2 + se_b^2).
    """
    if not isinstance(spec, dict) or not spec or set(spec) - {"relative", "absolute", "statistical"}:
        return None
    out: dict[str, Any] = {}
    for key in ("relative", "absolute"):
        v = spec.get(key, 0.0)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= float(v) <= MAX_TOLERANCE[key]:
            return None
        out[key] = float(v)
    if "statistical" in spec:
        st = spec["statistical"]
        z = st.get("z") if isinstance(st, dict) else None
        if (not isinstance(st, dict) or set(st) != {"method", "z"} or st.get("method") != "z-score"
                or isinstance(z, bool) or not isinstance(z, (int, float)) or not Z_BOUNDS[0] <= float(z) <= Z_BOUNDS[1]):
            return None
        out["statistical"] = {"method": "z-score", "z": float(z)}
    return out


def normalize_standard_errors(values: Any, names: Any) -> dict | None:
    """Per-value Monte Carlo standard errors (finite, non-negative) for declared result values only."""
    if not isinstance(values, dict) or not values or not isinstance(names, dict) or not set(values) <= set(names):
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0 for v in values.values()):
        return None
    return {str(k): float(v) for k, v in sorted(values.items())}


# SC-MATH-01 M1: agreement of two implementations at finitely many declared points is evidence for a
# symbolic identity, never a proof of it.
IDENTITY_METHOD = "numerical-identity-at-declared-points"
IDENTITY_ACTIONS = {"evaluate", "simplify", "differentiate"}
MAX_IDENTITY_POINTS = 256


def normalize_identity_check(value: Any) -> dict | None:
    if not isinstance(value, dict) or value.get("method") != IDENTITY_METHOD or value.get("proof") is not False:
        return None
    n, action = value.get("pointCount"), value.get("action")
    if isinstance(n, bool) or not isinstance(n, int) or not 0 < n <= MAX_IDENTITY_POINTS or action not in IDENTITY_ACTIONS:
        return None
    variable = value.get("variable") or ""
    if not isinstance(variable, str) or len(variable) > 32:
        return None
    return {"method": IDENTITY_METHOD, "proof": False, "action": action, "pointCount": n, "variable": variable}


def normalize_ordering(value: Any) -> dict | None:
    """SC-MATH-01 M2: the canonical order complex results were put in before export."""
    if not isinstance(value, dict) or value.get("method") != "real-then-imaginary":
        return None
    tie = value.get("realTieRelative")
    if isinstance(tie, bool) or not isinstance(tie, (int, float)) or not 0 <= tie <= 1e-6:
        return None
    return {"method": "real-then-imaginary", "realTieRelative": float(tie)}


def normalize_numerical_libraries(value: Any) -> dict | None:
    """SC-MATH-01 M4: library identity a runtime reported (flat or one level of nesting, bounded)."""
    if not isinstance(value, dict) or not value or len(value) > 32:
        return None
    try:
        if len(json.dumps(value, sort_keys=True, allow_nan=False)) > 4096:
            return None
    except (TypeError, ValueError):
        return None
    return {str(k): v for k, v in sorted(value.items())}


def _library_comparison(original: dict, reproduced: dict) -> dict | None:
    a, b = original.get("numericalLibraries"), reproduced.get("numericalLibraries")
    if a is None and b is None:
        return None
    # Informational: different libraries are expected for an independent implementation.
    return {"original": a, "reproduced": b, "identical": a is not None and a == b}


def validate_provenance(kind: str, classification: str, record: Any) -> dict:
    """Require the SC-MODEL-01 fields; computed outputs also need runtime and receipt."""
    if not isinstance(record, dict):
        raise ValueError("provenance must be an object")
    required = PROVENANCE_REQUIRED + (COMPUTED_REQUIRED if kind in COMPUTED_KINDS else ())
    missing = [k for k in required if k not in record]
    if missing:
        raise ValueError("provenance is missing: " + ", ".join(missing))
    if record["scientificClassification"] != classification:
        raise ValueError("provenance scientificClassification must equal the object classification")
    refs = record["sourceRefs"]
    if not isinstance(refs, list) or (kind != "research-question" and not refs):
        raise ValueError("provenance sourceRefs must list the original sources")
    _sha(record["artifactSha256"], "artifactSha256")
    unc = record["uncertainty"]
    if not isinstance(unc, dict) or not unc.get("status"):
        raise ValueError("uncertainty must be explicit, e.g. {'status': 'not-quantified', 'reason': ...}")
    if not isinstance(record["verification"], dict) or not record["verification"].get("status"):
        raise ValueError("verification must state a status")
    if kind in COMPUTED_KINDS:
        rt = record["runtime"]
        if not isinstance(rt, dict) or not all(rt.get(k) for k in ("language", "service", "version", "environmentFingerprint")):
            raise ValueError("runtime needs language, service, version and environmentFingerprint")
        _sha(record["parameterSha256"], "parameterSha256")
        receipt = record["executionReceipt"]
        if not isinstance(receipt, dict) or not receipt.get("receiptId"):
            raise ValueError("executionReceipt needs a receiptId")
        _sha(receipt.get("sha256"), "executionReceipt.sha256")
        if not record["codeVersion"]:
            raise ValueError("codeVersion is required")
    if "declaredTolerance" in record and normalize_tolerance(record["declaredTolerance"]) is None:
        raise ValueError("declaredTolerance must be {relative, absolute[, statistical]} within the reproduction bounds")
    if "standardErrors" in record and normalize_standard_errors(record["standardErrors"], record.get("resultValues")) is None:
        raise ValueError("standardErrors must be finite, non-negative and only for declared result values")
    if "identityCheck" in record and normalize_identity_check(record["identityCheck"]) is None:
        raise ValueError("identityCheck must be a numerical identity check at declared points with proof=false")
    if "ordering" in record and normalize_ordering(record["ordering"]) is None:
        raise ValueError("ordering must name the canonical real-then-imaginary order")
    rt_libs = (record.get("runtime") or {}).get("numericalLibraries") if isinstance(record.get("runtime"), dict) else None
    if rt_libs is not None and normalize_numerical_libraries(rt_libs) is None:
        raise ValueError("runtime.numericalLibraries must be a bounded object")
    values = record.get("resultValues")
    if values is not None:
        if not isinstance(values, dict) or not values or len(values) > 256:
            raise ValueError("resultValues must be a non-empty name -> number map")
        for name, v in values.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError(f"resultValues[{name!r}] must be a finite number")
    return record


def verify_bundle_manifest(bundle: dict) -> str:
    if not isinstance(bundle, dict) or bundle.get("schema") != BUNDLE_SCHEMA:
        raise ValueError("Unsupported convergence bundle schema")
    declared = _sha(bundle.get("manifestSha256"), "manifestSha256")
    body = {k: v for k, v in bundle.items() if k != "manifestSha256"}
    if not hmac.compare_digest(canonical_sha256(body), declared):
        raise ValueError("Bundle manifest hash does not match its content")
    return declared


def seal_bundle(bundle: dict) -> dict:
    """Producer helper: add manifestSha256 over the canonical bundle body."""
    body = {k: v for k, v in bundle.items() if k != "manifestSha256"}
    return {**body, "manifestSha256": canonical_sha256(body)}


def _provenance(session: Session, obj: md.ResearchObject) -> OutputProvenance | None:
    return session.scalar(select(OutputProvenance).where(OutputProvenance.object_id == obj.id))


def _attach_provenance(session: Session, obj: md.ResearchObject, record: dict) -> OutputProvenance:
    digest = canonical_sha256(record)
    existing = _provenance(session, obj)
    if existing is not None:
        if existing.record_sha256 != digest:
            raise ValueError(
                f"{obj.source_id}@{obj.source_version} already has different provenance; "
                "register a new sourceVersion instead of rewriting a revision")
        return existing
    row = OutputProvenance(object_id=obj.id, schema=PROVENANCE_SCHEMA, record=record, record_sha256=digest)
    session.add(row)
    session.flush()
    return row


def _owned_object(session: Session, owner_scope: str, object_id: str) -> md.ResearchObject:
    obj = session.get(md.ResearchObject, object_id)
    if obj is None or md._workspace_owner(session, obj.workspace_id) != owner_scope:
        # Same answer for absent and foreign objects.
        raise LookupError("Research object not found")
    return obj


# ---------------------------------------------------------------------------
# Ingest


def _guard_input_lineage(record: dict, attested: bool) -> None:
    if attested:
        return
    if record.get("inputLineage") == VERIFIED_INPUT_LINEAGE:
        raise ValueError("Verified input lineage can only be attested by a signed Workspace export")
    if (record.get("notebookCell") or {}).get("verified") or (record.get("verification") or {}).get("status") == "workspace-notebook-revision":
        raise ValueError("Verified notebook lineage can only be attested by a signed Workspace export")
    if "figure" in record:
        # SC-FIG-03A: a figure binding is written only after Research verified the bytes against a signed export.
        raise ValueError("A figure binding can only come from a signed Workspace export with verified bundle bytes")


def ingest_bundle(session: Session, *, owner_scope: str, bundle: dict, attested_input_lineage: bool = False) -> dict:
    """Register a verified bundle of upstream outputs and their relations, all private.

    Idempotent: replaying the same bundle returns the same objects; a changed
    record for an existing source version is refused. Only the signed Workspace export
    path may set attested_input_lineage.
    """
    manifest = verify_bundle_manifest(bundle)
    project = bundle.get("project") or {}
    workspace = ws.create_entry_workspace(
        session, area_slug=str(project.get("researchArea") or ""),
        project_key=str(project.get("projectKey") or ""), owner_scope=owner_scope,
        entry_slug=str(project.get("entrySlug") or ""), title=str(project.get("title") or ""),
    )
    ws_ref = str(project.get("workspaceProjectRef") or "")
    if not ws_ref or len(ws_ref) > 255:
        raise ValueError("workspaceProjectRef (the Workspace-owned project) is required")
    entry = session.get(ws.ResearchEntry, workspace.entry_id)
    rproject = session.get(ws.ResearchProject, entry.project_id)
    if rproject.external_workspace_ref is None:
        rproject.external_workspace_ref = ws_ref
    elif rproject.external_workspace_ref != ws_ref:
        raise PermissionError("Research project is bound to a different Workspace project")

    objects = bundle.get("objects") or []
    relations = bundle.get("relations") or []
    if not objects or len(objects) > MAX_BUNDLE_OBJECTS or len(relations) > MAX_BUNDLE_RELATIONS:
        raise ValueError("Bundle object/relation counts are out of bounds")
    created: dict[str, md.ResearchObject] = {}
    for item in objects:
        local = str(item.get("localId") or "")
        if not local or local in created:
            raise ValueError("Each bundle object needs a unique localId")
        kind = str(item.get("kind") or "")
        classification = str(item.get("dataClassification") or "")
        record = validate_provenance(kind, classification, item.get("provenance"))
        _guard_input_lineage(record, attested_input_lineage)
        obj = md.register_metadata_object(
            session, workspace=workspace, owner_scope=owner_scope,
            authority=str(item.get("authority") or ""), source_id=str(item.get("sourceId") or ""),
            source_version=str(item.get("sourceVersion") or ""), kind=kind,
            title=str(item.get("title") or ""), public_summary=str(item.get("publicSummary") or ""),
            doi=item.get("doi"), concepts=item.get("concepts"),
            source_sha256=record["artifactSha256"], data_classification=classification,
            source_license_ref=item.get("licenseRef"),
        )
        if obj.source_sha256 != record["artifactSha256"] or obj.kind != kind or obj.data_classification != classification:
            raise ValueError(f"{local}: source version already registered with different identity")
        _attach_provenance(session, obj, record)
        created[local] = obj

    edges = []
    for rel in relations:
        subject, target = created.get(rel.get("subject")), created.get(rel.get("object"))
        predicate = str(rel.get("predicate") or "")
        if subject is None or target is None:
            raise ValueError("Relations may only join objects in the same bundle")
        if predicate not in CONVERGENCE_PREDICATES:
            raise ValueError(f"Unsupported relation predicate {predicate!r}")
        if (predicate in DERIVATION_PREDICATES and subject.data_classification == "observed"
                and target.data_classification in NOT_OBSERVED):
            raise ValueError(
                f"{rel.get('subject')} cannot be 'observed' when derived from "
                f"{target.data_classification} {rel.get('object')}")
        row = md.propose_relation(
            session, workspace=workspace, owner_scope=owner_scope, subject=subject,
            target=target, predicate=predicate, private_basis=str(rel.get("basis") or ""),
        )
        edges.append(row.id)
    return {
        "schema": "sc-research-convergence-ingest/1.0",
        "manifestSha256": manifest,
        "workspaceId": workspace.id,
        "objects": {k: {"id": v.id, "kind": v.kind, "visibility": v.visibility,
                        "provenanceSha256": _provenance(session, v).record_sha256}
                    for k, v in created.items()},
        "relationIds": edges,
        "visibility": "private",
        "publicationPerformed": False,
    }


# ---------------------------------------------------------------------------
# Reproduction


def compare_values(original: dict, reproduced: dict, *, rel_tol: float, abs_tol: float,
                   se_original: dict | None = None, se_reproduced: dict | None = None, z: float | None = None) -> dict:
    """Numeric agreement per value. When a z-score test was pre-specified and both sides report a Monte Carlo
    standard error for a value, that value may also agree statistically: |a - b| <= z * sqrt(se_a^2 + se_b^2)."""
    se_original, se_reproduced = se_original or {}, se_reproduced or {}
    names = sorted(original)
    rows, worst_abs, worst_rel, ok = [], 0.0, 0.0, set(original) == set(reproduced)
    for name in names:
        a, b = float(original[name]), reproduced.get(name)
        if b is None:
            rows.append({"name": name, "original": a, "reproduced": None, "within": False})
            ok = False
            continue
        b = float(b)
        diff = abs(a - b)
        rel = diff / abs(a) if a else (0.0 if diff == 0 else math.inf)
        within = math.isclose(a, b, rel_tol=rel_tol, abs_tol=abs_tol)
        row = {"name": name, "original": a, "reproduced": b, "absDiff": diff, "within": within, "agreement": "numeric"}
        if z is not None and name in se_original and name in se_reproduced:
            combined = math.sqrt(se_original[name] ** 2 + se_reproduced[name] ** 2)
            score = diff / combined if combined > 0 else (0.0 if diff == 0 else math.inf)
            row.update({"combinedStandardError": combined, "zScore": score, "zLimit": z})
            if not within and score <= z:
                row.update({"within": True, "agreement": "statistical"})
                within = True
        ok = ok and within
        worst_abs, worst_rel = max(worst_abs, diff), max(worst_rel, rel)
        rows.append(row)
    return {"reproduced": ok, "maxAbsDiff": worst_abs, "maxRelDiff": worst_rel, "values": rows}


def _input_key(record: dict) -> list:
    """Identity of the verified inputs: dataset bytes, or for an upstream-cell binding the upstream results
    (by cell and result SHA-256), the derived payload and the datasets beneath the chain."""
    out = []
    for b in record.get("inputBindings") or []:
        if b.get("kind") == "parameter-set":
            out.append(("parameter-set", str(b.get("parameterSetId")), int(b.get("parameterSetRevision") or 0),
                        str(b.get("parameterSetFingerprint")), str(b.get("parametersSha256"))))
        elif b.get("kind") == "upstream-cell":
            out.append(("upstream-cell", str(b.get("derivedPayloadSha256")),
                        tuple(sorted((str(u.get("cellId")), str(u.get("resultSha256"))) for u in b.get("upstream") or [])),
                        tuple(sorted((str(d.get("datasetId")), int(d.get("datasetRevision") or 0), str(d.get("artifactSha256")))
                                     for d in b.get("datasets") or []))))
        else:
            out.append((str(b.get("datasetId")), int(b.get("datasetRevision") or 0), str(b.get("artifactSha256")),
                        str(b.get("rowsSha256"))))
    return sorted(out, key=repr)


def notebook_claim_status(session: Session, obj: md.ResearchObject, record: dict) -> dict:
    """Whether a notebook-cell computation still stands. Stale when Workspace reported at export that the
    cell or an upstream cell had changed at the notebook head; superseded when a later registered revision of
    the same notebook changed, added or removed the cell or any upstream cell. Either blocks publication
    until the affected cells are rerun and registered again."""
    cell = record.get("notebookCell") or {}
    if not cell.get("verified"):
        return {"status": "not-a-notebook-claim"}
    at_export = cell.get("currencyAtExport")
    if at_export and not at_export.get("current"):
        return {"status": "stale", "reasons": at_export.get("reasons") or [],
                "changedUpstreamCells": at_export.get("changedUpstreamCells") or []}
    mine = (cell.get("cellSha256"), cell.get("upstreamCellSha256"))
    later = []
    for other in session.scalars(select(md.ResearchObject).where(
            md.ResearchObject.workspace_id == obj.workspace_id, md.ResearchObject.kind == "computation",
            md.ResearchObject.authority == "workspace", md.ResearchObject.id != obj.id)).all():
        prov = _provenance(session, other)
        oc = (prov.record.get("notebookCell") or {}) if prov else {}
        if not (oc.get("verified") and oc.get("notebookId") == cell.get("notebookId")
                and isinstance(oc.get("notebookRevision"), int) and oc["notebookRevision"] > cell["notebookRevision"]):
            continue
        if oc.get("cellId") == cell.get("cellId"):
            # Legacy claims without recorded upstream identity are compared on their own cell only.
            upstream_known = mine[1] is not None and oc.get("upstreamCellSha256") is not None
            changed = oc.get("cellSha256") != mine[0] or (upstream_known and oc.get("upstreamCellSha256") != mine[1])
        else:
            # A later run of a downstream cell recorded this cell's hash at that revision.
            seen = (oc.get("upstreamCellSha256") or {}).get(cell.get("cellId"))
            changed = seen is not None and seen != mine[0]
        if changed:
            later.append({"objectId": other.id, "notebookRevision": oc["notebookRevision"]})
    if later:
        return {"status": "superseded", "by": sorted(later, key=lambda x: (x["notebookRevision"], x["objectId"]))}
    return {"status": "current", "asOf": "registered-revisions"}


def compare_inputs(original: dict, reproduction: dict) -> dict:
    """Whether both executions consumed the same, authority-verified input bytes."""
    lo, ln = original.get("inputLineage") or "unrecorded", reproduction.get("inputLineage") or "unrecorded"
    if lo != VERIFIED_INPUT_LINEAGE or ln != VERIFIED_INPUT_LINEAGE:
        return {"status": "unverified", "original": lo, "reproduction": ln}
    ka, kb = _input_key(original), _input_key(reproduction)
    if not ka or ka != kb:
        return {"status": "inputs-differ", "original": ka, "reproduction": kb}
    return {"status": "identical-verified", "bindings": ka}


def compare_code(original: dict, reproduction: dict) -> dict:
    """Which notebook cell code each side ran. Informational: an independent implementation
    (e.g. R vs Julia) is expected to differ; the verdict rests on inputs and numbers."""
    a, b = original.get("notebookCell") or {}, reproduction.get("notebookCell") or {}
    if not (a.get("verified") and b.get("verified")):
        return {"status": "unrecorded"}
    same = a.get("cellSha256") == b.get("cellSha256")
    return {"status": "identical" if same else "changed",
            "original": {k: a.get(k) for k in ("notebookId", "notebookRevision", "cellId", "cellSha256", "language")},
            "reproduction": {k: b.get(k) for k in ("notebookId", "notebookRevision", "cellId", "cellSha256", "language")}}


def reproduction_tolerance(original: dict, rel_tol: float | None, abs_tol: float | None) -> dict:
    """SC-MATH-01 M3: the tolerance a reproduction is judged at.

    The ceiling is the tolerance pre-registered on the original (its model revision's declaration), or the
    strict default when none was declared. A request may tighten it, never loosen it.
    """
    declared = normalize_tolerance(original.get("declaredTolerance"))
    ceiling = {k: v for k, v in (declared or DEFAULT_TOLERANCE).items() if k in ("relative", "absolute")}
    requested = {"relative": ceiling["relative"] if rel_tol is None else float(rel_tol),
                 "absolute": ceiling["absolute"] if abs_tol is None else float(abs_tol)}
    if not all(0 <= requested[k] <= MAX_TOLERANCE[k] for k in requested):
        raise ValueError("Reproduction tolerances out of bounds")
    if any(requested[k] > ceiling[k] for k in requested):
        raise ToleranceRelaxed(
            "A reproduction request cannot loosen the tolerance "
            + ("declared on the model revision" if declared else "(none was pre-registered, so the strict default applies)"))
    source = "model-revision-declared" if declared else "default-strict"
    if requested != ceiling:
        source += "-tightened-by-request"
    out = {**requested, "source": source, "ceiling": ceiling}
    if declared and declared.get("statistical"):
        # Pre-specified with the model revision; a request cannot change it.
        out["statistical"] = declared["statistical"]
    return out


def record_reproduction(
    session: Session, *, owner_scope: str, original_id: str, reproduction: dict,
    rel_tol: float | None = None, abs_tol: float | None = None, attested_input_lineage: bool = False,
) -> dict:
    """Compare an independent re-execution with the original and record the verdict.

    Research computes the verification; a client-supplied verdict is ignored. When
    either side has no numeric resultValues the record says replay was not possible.
    """
    original = _owned_object(session, owner_scope, original_id)
    prov = _provenance(session, original)
    if prov is None:
        raise ValueError("Original object has no provenance")
    tolerance = reproduction_tolerance(prov.record, rel_tol, abs_tol)
    rel_tol, abs_tol = tolerance["relative"], tolerance["absolute"]
    workspace = session.get(ws.EntryWorkspace, original.workspace_id)
    record = dict(reproduction.get("provenance") or {})
    _guard_input_lineage(record, attested_input_lineage)
    orig_rt, new_rt = prov.record.get("runtime") or {}, record.get("runtime") or {}
    if orig_rt.get("environmentFingerprint") and orig_rt.get("environmentFingerprint") == new_rt.get("environmentFingerprint") \
            and (prov.record.get("executionReceipt") or {}).get("receiptId") == (record.get("executionReceipt") or {}).get("receiptId"):
        raise ValueError("A reproduction must be a separate execution, not the original receipt")
    independence = ("independent-implementation"
                    if (orig_rt.get("language"), orig_rt.get("solver")) != (new_rt.get("language"), new_rt.get("solver"))
                    else "replay")
    a, b = prov.record.get("resultValues"), record.get("resultValues")
    inputs = compare_inputs(prov.record, record)
    if not a or not b:
        verdict = {"status": "not-reproducible", "reason": "numeric resultValues absent on original or reproduction"}
    else:
        stochastic = bool(prov.record.get("stochastic"))
        stat = tolerance.get("statistical") if stochastic else None
        cmp = compare_values(a, b, rel_tol=rel_tol, abs_tol=abs_tol,
                             se_original=prov.record.get("standardErrors"), se_reproduced=record.get("standardErrors"),
                             z=stat["z"] if stat else None)
        same_seed = (stochastic and (prov.record.get("stochastic") or {}).get("seed") is not None
                     and (prov.record.get("stochastic") or {}).get("seed") == (record.get("stochastic") or {}).get("seed"))
        # Numbers agreeing is not enough: only identical, verified input bytes make it a reproduction.
        if not cmp["reproduced"]:
            status = "not-reproduced"
        elif _identity_mismatch(prov.record, record):
            # The same names at different points, or a different action, check a different statement.
            status = "not-reproduced"
        elif inputs["status"] == "identical-verified":
            if stochastic and independence == "replay":
                # SC-SIM-01: the same implementation with the same seed shows determinism; with another seed it
                # replicates Monte Carlo error but does not test the implementation. Neither is a reproduction.
                status = "replayed-deterministic" if same_seed else "replicated-same-implementation"
            else:
                status = "reproduced"
        elif inputs["status"] == "inputs-differ":
            status = "inputs-differ"
        else:
            status = "reproduced-unverified-inputs"
        verdict = {**cmp, "status": status, "numericStatus": "within-tolerance" if cmp["reproduced"] else "outside-tolerance",
                   "tolerance": tolerance}
    # The boolean follows the verdict, never the numeric comparison alone.
    verdict["reproduced"] = verdict["status"] == "reproduced"
    verdict["inputs"] = inputs
    verdict["code"] = compare_code(prov.record, record)
    verdict["unavailableDependencies"] = sorted(set(a or {}) - set(b or {}))
    libs = _library_comparison(orig_rt, new_rt)
    if libs is not None:
        verdict["numericalLibraries"] = libs
    check = normalize_identity_check(prov.record.get("identityCheck"))
    if check is not None:
        verdict["claim"] = {**check, "statement": (f"Agreement at {check['pointCount']} declared points is evidence "
                                                   "for the identity, not a symbolic proof.")}
    record["verification"] = {
        "schema": REPRODUCTION_SCHEMA, "verifiedBy": "research", "independence": independence,
        "originalObjectId": original.id, "originalProvenanceSha256": prov.record_sha256, **verdict,
    }
    record.setdefault("scientificClassification", original.data_classification)
    record.setdefault("reviewStatus", "unreviewed")
    validate_provenance("reproduction", original.data_classification, record)
    obj = md.register_metadata_object(
        session, workspace=workspace, owner_scope=owner_scope,
        authority=str(reproduction.get("authority") or ""), source_id=str(reproduction.get("sourceId") or ""),
        source_version=str(reproduction.get("sourceVersion") or ""), kind="reproduction",
        title=str(reproduction.get("title") or f"Reproduction of {original.title}")[:350],
        source_sha256=record["artifactSha256"], data_classification=original.data_classification,
    )
    _attach_provenance(session, obj, record)
    md.propose_relation(session, workspace=workspace, owner_scope=owner_scope, subject=obj,
                        target=original, predicate="reproduces",
                        private_basis=f"{independence}; {verdict['status']}")
    return {"reproductionObjectId": obj.id, **record["verification"]}


def _identity_mismatch(original: dict, reproduction: dict) -> bool:
    a = normalize_identity_check(original.get("identityCheck"))
    if a is None:
        return False
    b = normalize_identity_check(reproduction.get("identityCheck"))
    return b is None or (a["action"], a["pointCount"], a["variable"]) != (b["action"], b["pointCount"], b["variable"])


def reproduction_status(session: Session, obj: md.ResearchObject) -> str:
    rows = session.scalars(
        select(OutputProvenance)
        .join(md.ResearchObjectRelation, md.ResearchObjectRelation.subject_id == OutputProvenance.object_id)
        .where(md.ResearchObjectRelation.object_id == obj.id,
               md.ResearchObjectRelation.predicate == "reproduces")
    ).all()
    current = _provenance(session, obj)
    statuses = [r.record.get("verification", {}).get("status") for r in rows
                if current and r.record.get("verification", {}).get("originalProvenanceSha256") == current.record_sha256]
    if "reproduced" in statuses:
        return "reproduced"
    if obj.kind == "computation" and not statuses:
        # SC-ED-01: a run has no numbers of its own; it counts as reproduced when a numeric result
        # it produced was independently reproduced (identical verified inputs) and is not withdrawn.
        produced = session.scalars(
            select(md.ResearchObject)
            .join(md.ResearchObjectRelation, md.ResearchObjectRelation.subject_id == md.ResearchObject.id)
            .where(md.ResearchObjectRelation.object_id == obj.id, md.ResearchObjectRelation.predicate == "produced-by",
                   md.ResearchObject.review_state != "revoked")).all()
        derived = [reproduction_status(session, o) for o in produced if o.kind != "computation"]
        if "reproduced" in derived:
            return "reproduced"
        return derived[-1] if derived else "not-attempted"
    return statuses[-1] if statuses else "not-attempted"


# ---------------------------------------------------------------------------
# Follow-up requests through Workspace (never dispatched by Research)


def build_followup_request(
    session: Session, *, owner_scope: str, object_ids: list[str], destination: str,
    intent: str, note: str = "",
) -> dict:
    if destination not in WS_HANDOFF_PRODUCTS or intent not in WS_HANDOFF_INTENTS:
        raise ValueError("Unsupported destination product or intent")
    if not object_ids or len(object_ids) > 32 or len(note) > 2000:
        raise ValueError("Select 1-32 objects")
    objs = [_owned_object(session, owner_scope, oid) for oid in object_ids]
    workspaces = {o.workspace_id for o in objs}
    if len(workspaces) != 1:
        raise ValueError("A follow-up request is scoped to one Research workspace")
    entry = session.get(ws.ResearchEntry, session.get(ws.EntryWorkspace, workspaces.pop()).entry_id)
    project = session.get(ws.ResearchProject, entry.project_id)
    if not project.external_workspace_ref:
        raise ValueError("Research project is not bound to a Workspace project")
    # Each Research object is one immutable revision; the fingerprint pins it.
    refs = [{"kind": o.kind, "objectId": f"{o.authority}:{o.source_id}@{o.source_version}", "revision": 1,
             "fingerprint": _provenance(session, o).record_sha256 if _provenance(session, o) else o.source_sha256}
            for o in objs]
    body = {
        "schema": "sc-research-followup-request/1.0",
        # Payload accepted by Workspace cross-product-research-handoff-consolidation `plan`.
        "workspacePlanRequest": {
            "sourceProduct": "workspace", "destinationProduct": destination, "intent": intent,
            "projectRef": project.external_workspace_ref, "objects": refs,
        },
        "requestedFrom": "research", "researchEntry": entry.slug, "note": note,
        "requiresWorkspaceAuthorization": True,
        "dispatchPerformed": False, "executionPerformedByResearch": False,
    }
    return {**body, "requestSha256": canonical_sha256(body)}


# ---------------------------------------------------------------------------
# Approvals and public projection


def subject_ref(obj: md.ResearchObject, prov: OutputProvenance) -> str:
    return f"research-object:{obj.id}#{prov.record_sha256}"


def record_workspace_approval(
    session: Session, *, owner_scope: str, object_id: str, dimension: str,
    approval: dict, decision: dict, signed_record: dict | None = None,
) -> PublicationApproval:
    """Record one Workspace human-governance decision for one approval dimension."""
    if dimension not in APPROVAL_ACTIONS:
        raise ValueError("Unknown approval dimension")
    obj = _owned_object(session, owner_scope, object_id)
    prov = _provenance(session, obj)
    if prov is None:
        raise ValueError("Object has no provenance to approve")
    if approval.get("schema") != WS_APPROVAL_SCHEMA or decision.get("schema") != WS_DECISION_SCHEMA:
        raise ValueError("Workspace human-governance approval and decision records are required")
    if approval.get("action") != APPROVAL_ACTIONS[dimension]:
        raise ValueError(f"{dimension} approval must be for action {APPROVAL_ACTIONS[dimension]}")
    if approval.get("subjectRef") != subject_ref(obj, prov):
        raise ValueError("Approval subject does not match the current provenance revision")
    if approval.get("status") != "approved" or decision.get("decision") != "approve":
        raise ValueError("Only an approve decision can be recorded")
    if decision.get("approvalId") != approval.get("approvalId") or approval.get("decisionRef") != decision.get("decisionId"):
        raise ValueError("Decision does not belong to this approval")
    actor = decision.get("actor") or {}
    if actor.get("actorType") != "human" or not actor.get("actorId") or not actor.get("role"):
        raise ValueError("A human Workspace actor is required")
    if actor.get("actorId") == (approval.get("requester") or {}).get("actorId"):
        raise ValueError("Self-approval is prohibited")
    if session.scalar(select(PublicationApproval).where(
            PublicationApproval.object_id == obj.id, PublicationApproval.dimension == dimension)):
        raise ValueError(f"{dimension} approval already recorded for this object")
    record = {"approval": approval, "decision": decision}
    if signed_record is not None:
        record["signedRecord"] = signed_record
    row = PublicationApproval(
        object_id=obj.id, dimension=dimension, subject_ref=approval["subjectRef"],
        workspace_approval_id=str(approval["approvalId"]), workspace_decision_id=str(decision["decisionId"]),
        actor_id=str(actor["actorId"]), actor_role=str(actor["role"]),
        record=record, record_sha256=canonical_sha256(record),
    )
    session.add(row)
    session.flush()
    return row


def verify_signed_approval(record: Any, key=None) -> dict:
    """Verify a Workspace-signed approval record; returns its body. Research cannot mint these."""
    from .workspace_ingest import load_public_key, verify_document
    body = verify_document(key or load_public_key(), WS_APPROVAL_RECORD_SCHEMA, record)
    approval, decision = body.get("approval") or {}, body.get("decision") or {}
    if (body.get("subjectRef"), body.get("action")) != (approval.get("subjectRef"), approval.get("action")):
        raise ValueError("Signed approval record is internally inconsistent")
    if decision.get("approvalId") != approval.get("approvalId"):
        raise ValueError("Signed approval record is internally inconsistent")
    return body


STEP_UP_POLICY_ENV = "RESEARCH_APPROVAL_STEP_UP"
STEP_UP_DECIDER_METHOD = "sc-identity-oidc-session"
STEP_UP_MAX_AGE_SECONDS = 300


def step_up_policy() -> str:
    """`required` or `off`. Production fails closed: without an explicit setting it is `required`."""
    value = os.getenv(STEP_UP_POLICY_ENV, "").strip().lower()
    if value in ("required", "off"):
        return value
    return "required" if os.getenv("RESEARCH_ENV", "").strip().lower() == "production" or value else "off"


def require_step_up_evidence(body: dict) -> None:
    """SC-PUB-M1: with the policy on, a decision counts only if the decider re-authenticated
    (password + second factor) with Sustainable Catalyst Identity shortly before deciding.
    The evidence is inside the Workspace-signed record, so it cannot be added afterwards."""
    if step_up_policy() != "required":
        return
    auth = body.get("authentication") if isinstance(body.get("authentication"), dict) else {}
    if auth.get("decider") != STEP_UP_DECIDER_METHOD or auth.get("stepUp") is not True:
        raise PermissionError("The approval was not decided with a fresh Sustainable Catalyst Identity sign-in (step-up)")
    try:
        issued = datetime.fromisoformat(str(body.get("issuedAt"))).timestamp()
        auth_time = int(auth.get("deciderAuthTime"))
    except (TypeError, ValueError) as exc:
        raise PermissionError("The approval carries no verifiable step-up time") from exc
    if not -60 <= issued - auth_time <= STEP_UP_MAX_AGE_SECONDS:
        raise PermissionError("The decider's step-up sign-in was not fresh when the approval was decided")


def record_signed_approval(
    session: Session, *, owner_scope: str, object_id: str, dimension: str, record: Any, key=None,
) -> PublicationApproval:
    body = verify_signed_approval(record, key)
    require_step_up_evidence(body)
    if body.get("ownerScope") != owner_scope:
        raise PermissionError("The approval was issued for another Workspace owner")
    if body.get("dimension") != dimension:
        raise ValueError(f"The signed record approves {body.get('dimension')!r}, not {dimension!r}")
    return record_workspace_approval(session, owner_scope=owner_scope, object_id=object_id, dimension=dimension,
                                     approval=body["approval"], decision=body["decision"], signed_record=record)


def approval_state(session: Session, obj: md.ResearchObject, *, signed_only: bool = False) -> dict:
    prov = _provenance(session, obj)
    current = subject_ref(obj, prov) if prov else None
    rows = session.scalars(select(PublicationApproval).where(PublicationApproval.object_id == obj.id)).all()
    if signed_only:
        rows = [r for r in rows if "signedRecord" in (r.record or {})]
    return {d: any(r.dimension == d and r.subject_ref == current for r in rows) for d in APPROVAL_ACTIONS}


def promote_public_projection(session: Session, *, owner_scope: str, object_id: str, signed_only: bool = False) -> dict:
    """Explicit, never automatic: all four approvals, plus reproduction for results."""
    obj = _owned_object(session, owner_scope, object_id)
    if obj.review_state == "revoked":
        raise PermissionError("The source authority withdrew this object")
    prov = _provenance(session, obj)
    if prov is None or (prov.record.get("verification") or {}).get("status") in UNVERIFIED_STATUSES:
        raise PermissionError("Only verified outputs can be published")
    if obj.kind == "figure" and not isinstance(prov.record.get("figure"), dict):
        # SC-FIG-03A: approvals must have covered the exact bundle bytes, artifacts and source lineage.
        raise PermissionError("Figures are published only with a verified figure binding; re-ingest with the bundle bytes")
    if obj.kind == "figure":
        from . import figure_releases as fr  # figure_releases imports this module
        rights = fr.source_rights_errors(session, owner_scope, prov.record["figure"])
        if rights:
            raise PermissionError("Figure source rights are not cleared: " + "; ".join(rights))
    state = approval_state(session, obj, signed_only=signed_only)
    missing = [d for d, ok in state.items() if not ok]
    if missing:
        raise PermissionError(("Missing signed Workspace approvals: " if signed_only else "Missing Workspace approvals: ")
                              + ", ".join(missing))
    claim = notebook_claim_status(session, obj, prov.record)
    if claim["status"] in ("stale", "superseded"):
        raise PermissionError(f"Notebook claim is {claim['status']}: the cell or an upstream cell changed; rerun it before publication")
    if obj.kind in REPRODUCTION_REQUIRED_KINDS and prov.record.get("inputLineage") != VERIFIED_INPUT_LINEAGE:
        raise PermissionError("Input lineage is not verified: inputs were not bound to authority-pinned dataset bytes")
    if obj.kind in REPRODUCTION_REQUIRED_KINDS and reproduction_status(session, obj) != "reproduced":
        raise PermissionError("Results need a successful independent reproduction before publication")
    obj.visibility = md.APPROVED
    obj.review_state = md.APPROVED
    obj.rights_cleared = True
    session.flush()
    return {"objectId": obj.id, "publicKey": obj.public_key, "visibility": obj.visibility,
            "approvals": state, "note": "Visible publicly only while its Research entry and area are also approved."}


# ---------------------------------------------------------------------------
# Graphs


def private_knowledge_graph(session: Session, *, owner_scope: str, area_slug: str = "") -> dict:
    """Owner-only graph across all studies of one owner, optionally one Research area."""
    query = (
        select(md.ResearchObject, ws.ResearchEntry.slug, ws.ResearchArea.slug)
        .join(ws.EntryWorkspace, ws.EntryWorkspace.id == md.ResearchObject.workspace_id)
        .join(ws.ResearchEntry, ws.ResearchEntry.id == ws.EntryWorkspace.entry_id)
        .join(ws.ResearchProject, ws.ResearchProject.id == ws.ResearchEntry.project_id)
        .join(ws.ResearchArea, ws.ResearchArea.id == ws.ResearchProject.area_id)
        .where(ws.ResearchProject.owner_scope == owner_scope)
    )
    if area_slug:
        query = query.where(ws.ResearchArea.slug == area_slug)
    rows = session.execute(query.order_by(md.ResearchObject.created_at, md.ResearchObject.id).limit(500)).all()
    ids = {obj.id for obj, _, _ in rows}
    nodes = []
    for obj, entry, area in rows:
        prov = _provenance(session, obj)
        rec = prov.record if prov else {}
        nodes.append({
            "id": obj.id, "kind": obj.kind, "title": obj.title, "authority": obj.authority,
            "source": f"{obj.source_id}@{obj.source_version}", "entry": entry, "area": area,
            "classification": obj.data_classification, "visibility": obj.visibility,
            "reviewState": obj.review_state, "artifactSha256": obj.source_sha256,
            "provenanceSha256": prov.record_sha256 if prov else None,
            "runtime": rec.get("runtime"), "uncertainty": rec.get("uncertainty"),
            "verification": (rec.get("verification") or {}).get("status"),
            "reproduction": reproduction_status(session, obj) if obj.kind in REPRODUCTION_REQUIRED_KINDS else None,
            "approvals": approval_state(session, obj),
        })
    edges = [{"id": r.id, "source": r.subject_id, "target": r.object_id, "relation": r.predicate,
              "reviewState": r.review_state, "visibility": r.visibility}
             for r in session.scalars(select(md.ResearchObjectRelation).where(
                 md.ResearchObjectRelation.subject_id.in_(ids)).order_by(md.ResearchObjectRelation.id)).all()
             if r.object_id in ids]
    return {"schema": GRAPH_SCHEMA, "ownerScope": owner_scope, "area": area_slug or None,
            "nodes": nodes, "edges": edges, "truncatedAt": 500,
            "interpretation": "Private provenance and dependency graph; relations are recorded lineage, not scientific proof."}


def public_provenance(obj: md.ResearchObject, prov: OutputProvenance | None) -> dict:
    """Explicit allow-list; never emits receipts, paths, owner scopes or private notes."""
    rec = prov.record if prov else {}
    rt = rec.get("runtime") or {}
    return {
        "schema": PUBLIC_PROVENANCE_SCHEMA, "id": obj.public_key, "kind": obj.kind,
        "scientificClassification": obj.data_classification,
        "artifactSha256": obj.source_sha256,
        "provenanceSha256": prov.record_sha256 if prov else None,
        "runtime": {k: rt.get(k) for k in ("language", "service", "version", "solver") if rt.get(k)} or None,
        "codeVersion": _public_code_version(rec.get("codeVersion")), "parameterSha256": rec.get("parameterSha256"),
        "executionReceiptSha256": (rec.get("executionReceipt") or {}).get("sha256"),
        "uncertainty": rec.get("uncertainty"),
        "verification": (rec.get("verification") or {}).get("status"),
        "units": rec.get("units"),
        "limitations": rec.get("limitations"),
        "figure": _public_figure_binding(rec.get("figure")),
        "review_status": "approved-public",
    }


def _public_code_version(value):
    """A Workspace model is named by its owner: publish its revision fingerprint, never its identifier."""
    if isinstance(value, str) and value.startswith("workspace-model:"):
        return "workspace-model#" + value.rsplit("#", 1)[-1] if "#" in value else "workspace-model"
    return value


def _public_figure_binding(b) -> dict | None:
    """SC-FIG-03A: the approved figure revision's hashes; never Workspace project, owner or run identifiers."""
    if not isinstance(b, dict):
        return None
    calc = b.get("calculationRefs") or {}
    # SC-FIG-05.3: the runtime uncertainty records released with the figure (absent for earlier figures).
    records = {"runtimeRecordSha256": [r.get("sha256") for r in b["runtimeRecords"]]} if b.get("runtimeRecords") else {}
    return {**records, "bundleSha256": b.get("bundleSha256"), "figureId": b.get("figureId"), "version": b.get("version"),
            "evidenceClass": b.get("evidenceClass"), "measurementStatus": b.get("measurementStatus"),
            "artifacts": [{k: a.get(k) for k in ("sha256", "mediaType")} for a in b.get("artifacts") or []],
            "sourceSha256": [s.get("sha256") for s in b.get("sourceLineage") or []],
            "runtimeResultSha256": [r.get("resultSha256") for r in calc.get("runtimeReceipts") or []],
            "notebookCellSha256": (calc.get("notebookCell") or {}).get("cellSha256")}


# ---------------------------------------------------------------------------
# HTTP


def _flag() -> str:
    return os.getenv("RESEARCH_CONVERGENCE_WRITES", "disabled").strip()


def writes_enabled() -> bool:
    # Production stays closed until SC-UP-01 replaces the staging service credential.
    return (_flag() == "staging-service-token"
            and os.getenv("RESEARCH_ENV") in {"staging", "development"}
            and bool(_token()))


def _token() -> str:
    path = os.getenv("RESEARCH_CONVERGENCE_TOKEN_FILE")
    try:
        return Path(path).read_text().strip() if path else ""
    except OSError:
        return ""


def internal_principal(
    authorization: str | None = Header(default=None),
    x_sc_owner_scope: str | None = Header(default=None),
) -> str:
    if not writes_enabled():
        raise HTTPException(status_code=503, detail=(
            "Research convergence writes are disabled; they require SC-UP-01 identity and ownership acceptance "
            "(staging may enable a service credential)."))
    if not authorization or not hmac.compare_digest(authorization.strip(), "Bearer " + _token()):
        raise HTTPException(status_code=401, detail="Research convergence authentication failed")
    scope = (x_sc_owner_scope or "").strip()
    if not scope or len(scope) > 160:
        raise HTTPException(status_code=400, detail="X-SC-Owner-Scope is required")
    return scope


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _commit(session: Session, fn, *args, **kwargs):
    try:
        result = _call(fn, session, *args, **kwargs)
        session.commit()
        return result
    except BaseException:
        session.rollback()
        raise


@router.get("/convergence/status")
def convergence_status():
    return {
        "schema": "sc-research-convergence-status/1.0", "build": "SC-MODEL-01",
        "privateWritesEnabled": writes_enabled(),
        "gate": "SC-UP-01 identity and ownership acceptance" if not writes_enabled() else "staging service credential",
        "ownershipAuthority": "workspace", "computationAuthorities": ["workbench", "workspace-runtimes", "platform-core"],
        "decisionAuthority": "decision-studio", "visualizationAuthority": "lab",
        "automaticPublication": False, "executionPerformedByResearch": False,
    }


@router.post("/convergence/bundles")
def ingest_route(bundle: dict = Body(...), owner: str = Depends(internal_principal),
                 session: Session = Depends(ws.get_session)):
    return _commit(session, ingest_bundle, owner_scope=owner, bundle=bundle)


@router.post("/convergence/objects/{object_id}/reproductions")
def reproduction_route(object_id: str, body: dict = Body(...), owner: str = Depends(internal_principal),
                       session: Session = Depends(ws.get_session)):
    return _commit(session, record_reproduction, owner_scope=owner, original_id=object_id,
                   reproduction=body.get("reproduction") or {},
                   rel_tol=_optional_float(body.get("relTol")), abs_tol=_optional_float(body.get("absTol")))


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="tolerances must be numbers") from exc


@router.post("/convergence/follow-up-requests")
def followup_route(body: dict = Body(...), owner: str = Depends(internal_principal),
                   session: Session = Depends(ws.get_session)):
    return _call(build_followup_request, session, owner_scope=owner, object_ids=list(body.get("objectIds") or []),
                 destination=str(body.get("destination") or ""), intent=str(body.get("intent") or ""),
                 note=str(body.get("note") or ""))


@router.get("/convergence/objects/{object_id}/subject-ref")
def subject_ref_route(object_id: str, owner: str = Depends(internal_principal),
                      session: Session = Depends(ws.get_session)):
    obj = _call(_owned_object, session, owner, object_id)
    prov = _provenance(session, obj)
    if prov is None:
        raise HTTPException(status_code=404, detail="Object has no provenance")
    return {"subjectRef": subject_ref(obj, prov), "approvalActions": APPROVAL_ACTIONS}


@router.post("/convergence/objects/{object_id}/approvals/{dimension}")
def approval_route(object_id: str, dimension: str, body: dict = Body(...),
                   owner: str = Depends(internal_principal), session: Session = Depends(ws.get_session)):
    # SC-ED-01: only records signed by Workspace's authenticated approval service are accepted here.
    if "record" not in body:
        raise HTTPException(status_code=422, detail=(
            "A Workspace-signed approval record is required; unsigned human-governance payloads are not accepted"))
    row = _commit(session, record_signed_approval, owner_scope=owner, object_id=object_id,
                  dimension=dimension, record=body["record"])
    return {"dimension": row.dimension, "recordSha256": row.record_sha256, "actorRole": row.actor_role,
            "actorId": row.actor_id, "signed": True}


@router.post("/convergence/objects/{object_id}/public-projection")
def projection_route(object_id: str, owner: str = Depends(internal_principal),
                     session: Session = Depends(ws.get_session)):
    return _commit(session, promote_public_projection, owner_scope=owner, object_id=object_id, signed_only=True)


@router.get("/convergence/graph")
def private_graph_route(area: str = Query(default="", max_length=128), owner: str = Depends(internal_principal),
                        session: Session = Depends(ws.get_session)):
    return private_knowledge_graph(session, owner_scope=owner, area_slug=area)


@router.get("/objects/{public_key}/provenance")
def public_provenance_route(public_key: str, session: Session = Depends(ws.get_session)):
    if not md.UUID.fullmatch(public_key):
        raise HTTPException(status_code=404, detail="Research object not found")
    row = session.execute(md.public_query(include_unlisted=True).where(md.ResearchObject.public_key == public_key)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Research object not found")
    return public_provenance(row[0], _provenance(session, row[0]))
