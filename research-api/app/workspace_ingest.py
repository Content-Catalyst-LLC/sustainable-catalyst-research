"""SC-MODEL-01 — Research ingestion of Workspace ExecutionRun exports.

The authoritative inputs are Workspace execution runs, their registered outputs, the
model and dataset revisions they resolved, runtime receipts and verified scientific
study packages, delivered as a Workspace-signed export. Research:

* never touches the Workspace database and never accepts a caller-supplied owner. The
  owner is the Workspace-signed ``ownerScope``; the caller only presents the owner's
  Workspace grant, which Research forwards with its own least-privilege client
  credential to Workspace, where ownership is enforced at the source;
* holds only Workspace's Ed25519 public key, so it can verify but not forge grants,
  exports or withdrawal notices;
* maps the export into the existing convergence registry (objects, relations,
  immutable provenance), copying hashes and bounded numeric values, never artifact bytes;
* only creates result objects for ``executed-verified`` runs. Failed, incomplete or
  manually-marked runs are recorded as computations carrying that status, and stay
  unpublishable.
"""
from __future__ import annotations

import base64
import hmac
import json
import math
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Body, Depends, Header, HTTPException
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import convergence as cv
from . import figure_releases as fr
from . import object_registry as md
from . import source_rights_sync as rs
from . import workspaces as ws

PREFIX = "/api/research/v1"
router = APIRouter(prefix=PREFIX, tags=["SC-MODEL-01 Workspace execution ingest"])

GRANT_SCHEMA = "sc-workspace-research-export-grant/1.0"
EXPORT_SCHEMA = "sc-workspace-research-export/1.0"
WITHDRAWAL_SCHEMA = "sc-workspace-research-export-withdrawal/1.0"
SOURCE_RIGHTS_SCHEMA = "sc-workspace-source-rights-notice/1.0"
LICENCE_STATUSES = ("restricted", "verified-for-use")
ISSUER = "sustainable-catalyst-workspace"
AUDIENCE = "sc-research"
SHA = re.compile(r"^[0-9a-f]{64}$")
FIGURE_MEDIA = "application/vnd.sc.figure-bundle.v1+json"


class WorkspaceExportReceipt(ws.Base):
    """Append-only record of every verified Workspace export Research accepted."""
    __tablename__ = "research_workspace_exports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ws.new_id)
    owner_scope: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    grant_id: Mapped[str] = mapped_column(String(96), nullable=False)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    run_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    export_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    verification_status: Mapped[str] = mapped_column(String(48), nullable=False)
    computation_object_id: Mapped[str] = mapped_column(ForeignKey("research_metadata_objects.id"), nullable=False)
    record: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc)


class WorkspaceWithdrawal(ws.Base):
    """Append-only record of applied Workspace withdrawal notices."""
    __tablename__ = "research_workspace_withdrawals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ws.new_id)
    owner_scope: Mapped[str] = mapped_column(String(160), nullable=False)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    run_id: Mapped[str] = mapped_column(String(96), nullable=False)
    notice_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    revoked_object_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc)
    __table_args__ = (UniqueConstraint("owner_scope", "run_id", name="uq_research_workspace_withdrawal"),)


class WorkspaceSourceRightsNotice(ws.Base):
    """SC-FIG-04.1: append-only record of applied, Workspace-signed source rights notices (one row per notice)."""
    __tablename__ = "research_workspace_source_rights"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ws.new_id)
    owner_scope: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(160), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    licence_status: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    notice_id: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    notice_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    effective_at: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_object_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    figure_bytes_removed: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc)
    __table_args__ = (UniqueConstraint("owner_scope", "source_sha256", "sequence", name="uq_research_source_rights_sequence"),)


class StaleNotice(Exception):
    """A notice older than (or conflicting with) one already applied for the same owner and source."""


class UpstreamRefused(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


# ---------------------------------------------------------------------------
# Signatures


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


KEYRING_SCHEMA = "sc-research-workspace-keyring/1.0"
# The signed time a document states, used to decide whether a retired key may still verify it.
DOCUMENT_TIME_FIELDS = ("effectiveAt", "issuedAt", "exportedAt", "withdrawnAt", "generatedAt", "createdAt")


def key_id(raw: bytes) -> str:
    """Workspace's key id: the first 16 hex characters of SHA-256 over the raw public key."""
    import hashlib
    return hashlib.sha256(raw).hexdigest()[:16]


def _instant(value: Any) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else None


class Keyring:
    """SC-RIGHTS-02: the Workspace signing keys Research trusts, so the Workspace key can be rotated.

    Each key has an id (Workspace's key id), an optional ``notAfter`` (retired: it still verifies documents
    signed no later than that instant, such as queued notices issued before a rotation) and ``revoked``
    (verifies nothing, for a compromised key). A document names its key in ``signature.keyId``.
    """

    def __init__(self, entries: dict[str, tuple[Ed25519PublicKey, datetime | None, bool]]):
        if not entries:
            raise RuntimeError("the Workspace keyring has no keys")
        self.entries = entries

    def select(self, kid: Any, body: dict) -> Ed25519PublicKey:
        entry = self.entries.get(str(kid or ""))
        if entry is None:
            raise ValueError("signed by a Workspace key Research does not trust")
        pub, not_after, revoked = entry
        if revoked:
            raise ValueError("signed by a revoked Workspace key")
        if not_after is not None:
            when = next((t for t in (_instant(body.get(f)) for f in DOCUMENT_TIME_FIELDS if body.get(f)) if t), None)
            if when is None or when > not_after:
                raise ValueError("signed by a retired Workspace key after its retirement")
        return pub

    def current(self) -> list[Ed25519PublicKey]:
        now = datetime.now(timezone.utc)
        return [pub for pub, not_after, revoked in self.entries.values()
                if not revoked and (not_after is None or now <= not_after)]

    def require_current(self, kid: Any) -> None:
        """Fresh statements (attestations) must be signed by a key that is neither retired nor revoked."""
        entry = self.entries.get(str(kid or ""))
        if entry is None or entry[2] or (entry[1] is not None and datetime.now(timezone.utc) > entry[1]):
            raise ValueError("a fresh Workspace statement must be signed by a current key")

    def verify(self, signature: bytes, data: bytes) -> None:
        """Signatures that name no key (grants): any current key."""
        for pub in self.current():
            try:
                pub.verify(signature, data)
                return
            except InvalidSignature:
                continue
        raise InvalidSignature()


def parse_keyring(text: str) -> Keyring:
    text = text.strip()
    if not text.startswith("{"):
        raw = base64.b64decode(text)
        return Keyring({key_id(raw): (Ed25519PublicKey.from_public_bytes(raw), None, False)})
    doc = json.loads(text)
    if doc.get("schema") != KEYRING_SCHEMA or not isinstance(doc.get("keys"), list):
        raise RuntimeError("the Workspace keyring file is malformed")
    entries = {}
    for k in doc["keys"]:
        raw = base64.b64decode(str(k.get("publicKey") or ""))
        kid = key_id(raw)
        if k.get("keyId") not in (None, kid):
            raise RuntimeError("a Workspace keyring entry names the wrong key id")
        not_after = _instant(k["notAfter"]) if k.get("notAfter") else None
        if k.get("notAfter") and not_after is None:
            raise RuntimeError("a Workspace keyring notAfter must be an ISO instant with a time zone")
        entries[kid] = (Ed25519PublicKey.from_public_bytes(raw), not_after, bool(k.get("revoked")))
    return Keyring(entries)


def load_public_key(path: str | None = None) -> Keyring:
    path = path or os.getenv("RESEARCH_WORKSPACE_PUBLIC_KEY_FILE", "")
    if not path:
        raise RuntimeError("Workspace export public key is not configured")
    return parse_keyring(Path(path).read_text())


def verify_document(key: "Ed25519PublicKey | Keyring", domain: str, doc: Any) -> dict:
    if not isinstance(doc, dict) or doc.get("schema") != domain:
        raise ValueError(f"Expected a signed {domain} document")
    sig = doc.get("signature") or {}
    body = {k: v for k, v in doc.items() if k != "signature"}
    if isinstance(key, Keyring):
        key = key.select(sig.get("keyId"), body)
    try:
        key.verify(_b64d(str(sig.get("value") or "")), domain.encode() + b"\n" + _canonical(body))
    except (InvalidSignature, ValueError) as exc:
        raise ValueError(f"{domain} signature is invalid") from exc
    if body.get("issuer") != ISSUER or body.get("audience") != AUDIENCE:
        raise ValueError("Signed document has the wrong issuer or audience")
    return body


def verify_grant(key: Ed25519PublicKey, token: str) -> dict:
    parts = str(token or "").split(".")
    if len(parts) != 2 or len(token) > 4096:
        raise ValueError("Malformed Workspace grant")
    try:
        key.verify(_b64d(parts[1]), GRANT_SCHEMA.encode() + b"\n" + parts[0].encode("ascii"))
        claims = json.loads(_b64d(parts[0]))
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("Workspace grant signature is invalid") from exc
    if claims.get("schema") != GRANT_SCHEMA or claims.get("iss") != ISSUER or claims.get("aud") != AUDIENCE:
        raise ValueError("Workspace grant has the wrong issuer or audience")
    if int(claims.get("exp") or 0) <= int(datetime.now(timezone.utc).timestamp()):
        raise PermissionError("Workspace grant has expired")
    return claims


# ---------------------------------------------------------------------------
# Workspace client (least-privilege credential, fetch route only)


def workspace_configured() -> bool:
    return all(os.getenv(k) for k in ("RESEARCH_WORKSPACE_EXPORT_URL", "RESEARCH_WORKSPACE_CLIENT_TOKEN_FILE",
                                      "RESEARCH_WORKSPACE_PUBLIC_KEY_FILE"))


def http_fetch_export(grant: str) -> dict:
    url = os.environ["RESEARCH_WORKSPACE_EXPORT_URL"].rstrip("/") + "/fetch"
    token = Path(os.environ["RESEARCH_WORKSPACE_CLIENT_TOKEN_FILE"]).read_text().strip()
    req = urllib.request.Request(url, data=json.dumps({"grant": grant}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail")
        except Exception:
            detail = ""
        raise UpstreamRefused(exc.code, f"Workspace refused the export: {detail or exc.code}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise UpstreamRefused(502, "Workspace export service unavailable") from exc


# ---------------------------------------------------------------------------
# Mapping (Workspace project ids are unique per owner, so Research keys include the owner)


def _sha(value: Any, fallback: Any) -> str:
    return value if isinstance(value, str) and SHA.fullmatch(value) else cv.canonical_sha256(fallback)


def _classification(datasets: list[dict], parameter_sets: list[dict] | None = None) -> str:
    """Computed outputs are never 'observed'; synthetic inputs keep them synthetic.

    SC-SIM-01: a simulation driven by a server-attested ParameterSet revision is 'modelled' (or 'synthetic'
    when its parameters are declared synthetic), even without datasets and even when its datasets were
    observed. Without datasets or parameter sets the classification stays 'unknown'.
    """
    classes = {d["classification"] for d in datasets}
    param_classes = {p.get("scientificClassification") or "modelled" for p in parameter_sets or []}
    if "synthetic" in classes | param_classes:
        return "synthetic"
    if (not classes and not param_classes) or "unknown" in classes:
        return "unknown"
    return "modelled"


PARAMETER_KIND = "parameter-set"
PARAMETER_BINDING_KEYS = ("kind", "parameterSetId", "parameterSetRevision", "parameterSetFingerprint", "modelId",
                          "parametersSha256", "scientificClassification")


NOTEBOOK_VERIFIED = "workspace-notebook-revision"
NOTEBOOK_REFERENCE_SCHEMA = "sc-research-notebook-reference/1.0"
CELL_KEYS = ("notebookId", "notebookRevision", "cellId", "cellSha256", "language", "notebookPackageSha256", "notebookTitle")
UPSTREAM_KIND = "upstream-cell"
DATASET_BINDING_KEYS = ("datasetId", "datasetRevision", "artifactSha256", "rowsSha256", "transform", "derivedPayloadSha256")
UPSTREAM_KEYS = ("cellId", "role", "runId", "cellSha256", "language", "operation", "modelRef", "environmentFingerprint",
                 "runtimeReceiptId", "resultArtifactId", "resultArtifactRevision", "resultSha256", "valuesSha256", "inputLineage")


def _sha_map(value) -> dict | None:
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) and SHA.fullmatch(v) for k, v in value.items()):
        return None
    return dict(sorted(value.items()))


def _notebook_cell(cell) -> dict | None:
    if not isinstance(cell, dict):
        return None
    out = {k: cell.get(k) for k in CELL_KEYS}
    out["verified"] = (cell.get("verified") is True and isinstance(out["notebookRevision"], int)
                       and all(isinstance(out[k], str) and out[k] for k in ("notebookId", "cellId"))
                       and all(isinstance(out[k], str) and SHA.fullmatch(out[k]) for k in ("cellSha256", "notebookPackageSha256")))
    # Upstream cell identity (exports from before SC-NB-01 upstream binding carry none) and whether the
    # claim still described the Workspace notebook head when it was exported.
    out["upstreamCellSha256"] = _sha_map(cell.get("upstreamCellSha256"))
    cur = cell.get("currency") if isinstance(cell.get("currency"), dict) else {}
    out["currencyAtExport"] = {"current": cur.get("current") is True, "headRevision": cur.get("headRevision"),
                               "reasons": [str(r) for r in cur.get("reasons") or []][:8],
                               "changedUpstreamCells": [str(c) for c in cur.get("changedUpstreamCells") or []][:64]} if cur else None
    return out


def _binding(b: dict) -> dict:
    """Research's copy of a Workspace input binding: a pinned dataset, or an upstream-cell chain (hashes only)."""
    if b.get("kind") == PARAMETER_KIND:
        return {k: b.get(k) for k in PARAMETER_BINDING_KEYS}
    if b.get("kind") != UPSTREAM_KIND:
        return {k: b.get(k) for k in DATASET_BINDING_KEYS}
    return {"kind": UPSTREAM_KIND, "cellId": b.get("cellId"), "cellSha256": b.get("cellSha256"), "planId": b.get("planId"),
            "upstream": [{k: u.get(k) for k in UPSTREAM_KEYS} for u in b.get("upstream") or []],
            "crossChecks": b.get("crossChecks") or [], "transform": b.get("transform"),
            "derivedPayloadSha256": b.get("derivedPayloadSha256"), "chainSha256": b.get("chainSha256"),
            "datasets": [{k: d.get(k) for k in ("datasetId", "datasetRevision", "artifactSha256")} for d in b.get("datasets") or []]}


def _simulation_method(values: dict) -> dict:
    """SC-SIM-01: 'simulation' is a method subtype of a modelled (or synthetic) output, not a classification."""
    st = values.get("stochastic") if isinstance(values.get("stochastic"), dict) else None
    seed = st.get("seed") if st else None
    return {"kind": "simulation", "execution": "stochastic" if st else "deterministic",
            "seed": seed if isinstance(seed, int) and not isinstance(seed, bool) else None}


def normalize_intervals(value: Any, names: Any) -> dict | None:
    """Runtime confidence intervals for declared result values; each must bracket its estimate."""
    if not isinstance(value, dict) or value.get("semantics") != "confidence-interval" or value.get("computedBy") != "runtime":
        return None
    level, df, vals = value.get("level"), value.get("degreesOfFreedom"), value.get("values")
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)  # noqa: E731
    if not (num(level) and 0.5 <= level < 1 and num(df) and df >= 1 and isinstance(vals, dict) and vals
            and isinstance(names, dict) and set(vals) <= set(names) and isinstance(value.get("method"), str)):
        return None
    out = {}
    for k, pair in vals.items():
        if not (isinstance(pair, list) and len(pair) == 2 and all(num(x) for x in pair) and pair[0] <= names[k] <= pair[1]):
            return None
        out[str(k)] = [float(pair[0]), float(pair[1])]
    return {"semantics": "confidence-interval", "level": float(level), "method": value["method"][:240],
            "distribution": str(value.get("distribution") or "")[:64], "degreesOfFreedom": float(df),
            "computedBy": "runtime", "values": dict(sorted(out.items()))}


def normalize_assumptions(value: Any) -> dict | None:
    """Statistical assumptions as declared on the model revision: recorded as declared, never as checked."""
    if not isinstance(value, dict) or value.get("declaredBy") != "model-revision" or value.get("checked") is not False:
        return None
    st = value.get("statements")
    if not isinstance(st, list) or not 0 < len(st) <= 12 or not all(isinstance(s, str) and 0 < len(s) <= 240 for s in st):
        return None
    return {"declaredBy": "model-revision", "checked": False, "statements": list(st)}


def map_export(export: dict, *, area: str, entry: str, title: str, figure_files: dict[str, bytes] | None = None) -> dict:
    """Workspace export -> convergence bundle (deterministic; hashes, not bytes).

    SC-FIG-03A: ``figure_files`` (verified bytes keyed by SHA-256) bind figure outputs to their exact
    bundle revision; the binding becomes part of the immutable provenance that approvals name.
    """
    figure_files = figure_files or {}
    bound_files: set[str] = set()
    prov, verification, values = export["provenance"], export["verification"], export.get("resultValues") or {}
    run, inputs = prov["run"], prov["inputs"]
    status = verification["status"]
    run_id, project_id = export["runId"], export["projectId"]
    datasets = []
    for item in inputs.get("datasets") or []:
        d = item.get("resolved")
        if not d:
            raise ValueError("Execution run references a dataset revision Workspace could not resolve")
        cls = (d.get("metadata") or {}).get("scientificClassification", "unknown")
        datasets.append({**d, "classification": cls if cls in md.DATA_CLASSIFICATIONS else "unknown"})
    model = (inputs.get("model") or {}).get("resolved")
    params = (inputs.get("parameterSet") or {}).get("resolved")
    env = (inputs.get("environment") or {}).get("resolved") or {}
    adapter = (inputs.get("runtimeAdapter") or {}).get("resolved") or {}
    receipts = verification.get("runtimeReceipts") or []
    first = receipts[0] if receipts else {}
    # Parameter-set bindings exist only when Workspace's worker bound the run's pinned, re-hashed revision.
    parameter_sets = [b for b in verification.get("inputBindings") or []
                      if b.get("kind") == PARAMETER_KIND and verification.get("inputLineage") == cv.VERIFIED_INPUT_LINEAGE]
    classification = _classification(datasets, parameter_sets)
    source_refs = [{"authority": "workspace", "datasetId": d["datasetId"], "revision": d["revision"],
                    "fingerprint": d["fingerprint"]} for d in datasets]
    if parameter_sets:
        source_refs += [{"authority": "workspace", "parameterSetId": b["parameterSetId"], "revision": b["parameterSetRevision"],
                         "fingerprint": b["parameterSetFingerprint"]} for b in parameter_sets]
        if model:
            source_refs.append({"authority": "workspace", "modelId": model["modelId"], "revision": model["revision"],
                                "fingerprint": model.get("fingerprint")})
    # Grant ids and export times live on the export receipt, not in immutable provenance,
    # so retries with a fresh grant replay to the same revision.
    workspace_ref = {"projectId": project_id, "runId": run_id,
                     "inputFingerprint": run.get("inputFingerprint"),
                     "reproducibilityFingerprint": run.get("reproducibilityFingerprint"),
                     "provenanceFingerprint": prov.get("provenanceFingerprint")}
    runtime = {
        "language": first.get("language") or adapter.get("runtimeFamily") or "unrecorded",
        "service": first.get("runtime") or run.get("targetProduct") or "workspace",
        "version": (adapter.get("runtimeVersion") or (env.get("runtime") or {}).get("version")
                    or (first.get("runtimeVersion") if first.get("runtimeVersion") != "unrecorded" else None) or "unrecorded"),
        "operation": run.get("operation"),
        "environmentFingerprint": run.get("environmentFingerprint") or "unrecorded",
        "runtimeAdapterFingerprint": run.get("runtimeAdapterFingerprint") or None,
    }
    libs = cv.normalize_numerical_libraries(first.get("numericalLibraries"))
    if libs is not None:
        runtime["numericalLibraries"] = libs
    code_version = (f"workspace-model:{model['modelId']}@r{model['revision']}#{model['fingerprint']}"
                    if model else f"workspace-operation:{run.get('operation')}")
    receipt_doc = {"receipts": receipts, "jobId": verification.get("jobId"), "runStatus": verification.get("runStatus")}
    common = {
        "sourceRefs": source_refs, "scientificClassification": classification, "runtime": runtime,
        "codeVersion": code_version,
        "parameterSha256": _sha(params.get("fingerprint") if params else None, {"inputFingerprint": run.get("inputFingerprint")}),
        "executionReceipt": {"receiptId": first.get("receiptId") or f"workspace-run-state:{run_id}",
                             "sha256": cv.canonical_sha256(receipt_doc)},
        "uncertainty": ({"status": values["uncertainty"]["semantics"], **values["uncertainty"]}
                        if values.get("uncertainty") else {"status": "not-quantified", "reason": "no uncertainty declared by the run"}),
        "reviewStatus": "unreviewed", "workspace": workspace_ref,
        # Verified only when Workspace's worker read pinned dataset bytes; never inferred here.
        "inputLineage": verification.get("inputLineage") or "unrecorded",
        "inputBindings": [_binding(b) for b in verification.get("inputBindings") or []],
        # The notebook cell that ran, as Workspace verified it against the immutable notebook revision.
        "notebookCell": _notebook_cell(export.get("notebookCell")),
        **({"method": _simulation_method(values)} if (model or {}).get("modelKind") == "simulation" else {}),
        "units": (model or {}).get("metadata", {}).get("researchUnits"),
        "limitations": (model or {}).get("metadata", {}).get("limitations"),
    }
    plain = {"uncertainty": {"status": "not-quantified", "reason": "registry record"}, "reviewStatus": "unreviewed"}
    objects, relations = [], []
    for d in datasets:
        local = f"dataset:{d['datasetId']}:r{d['revision']}"
        objects.append({"localId": local, "authority": "workspace", "sourceId": f"dataset:{d['datasetId']}",
                        "sourceVersion": f"r{d['revision']}", "kind": "dataset", "title": d.get("name") or d["datasetId"],
                        "dataClassification": d["classification"],
                        "provenance": {**plain, "scientificClassification": d["classification"],
                                       "sourceRefs": [{"authority": "workspace", "datasetId": d["datasetId"],
                                                       "lineage": d.get("lineage") or {}, "externalUri": d.get("externalUri") or ""}],
                                       "artifactSha256": _sha(d.get("fingerprint"), d),
                                       "verification": {"status": "workspace-registered-revision"}}})
    if model:
        objects.append({"localId": "model", "authority": "workspace", "sourceId": f"model:{model['modelId']}",
                        "sourceVersion": f"r{model['revision']}", "kind": "model", "title": model.get("name") or model["modelId"],
                        "dataClassification": "modelled",
                        "provenance": {**plain, "scientificClassification": "modelled",
                                       "sourceRefs": [{"authority": "workspace", "modelId": model["modelId"],
                                                       "framework": model.get("framework"), "algorithm": model.get("algorithm"),
                                                       "executionOperation": model.get("executionOperation")}],
                                       "artifactSha256": _sha(model.get("fingerprint"), model),
                                       "verification": {"status": "workspace-registered-revision"}}})
    cell = common["notebookCell"]
    if cell and cell["verified"]:
        # A reference to the Workspace notebook revision; Research never copies or re-identifies it.
        objects.append({"localId": "notebook", "authority": "workspace", "sourceId": f"notebook:{cell['notebookId']}",
                        "sourceVersion": f"r{cell['notebookRevision']}", "kind": "notebook",
                        # Classification is per run: a notebook mixes cells with different evidence classes.
                        "title": cell.get("notebookTitle") or cell["notebookId"], "dataClassification": "unknown",
                        "provenance": {**plain, "scientificClassification": "unknown",
                                       "sourceRefs": [{"authority": "workspace", "notebookId": cell["notebookId"],
                                                       "notebookRevision": cell["notebookRevision"],
                                                       "projectId": project_id}],
                                       "artifactSha256": cell["notebookPackageSha256"],
                                       "verification": {"status": NOTEBOOK_VERIFIED}}})
    packages = [{k: p.get(k) for k in ("schema", "packageId", "manifestFingerprint", "bundleSha256", "verified", "errors")}
                for p in export.get("studyPackages") or []]
    # A new run state or a newly pinned study package is a new immutable revision.
    run_version = (f"{run.get('status')}-{str(run.get('reproducibilityFingerprint') or '')[:16]}"
                   f"-p{cv.canonical_sha256(packages)[:8]}")
    if cell and cell["currencyAtExport"] and not cell["currencyAtExport"]["current"]:
        # Re-exported after the notebook head moved on: a new revision that records the stale claim.
        run_version += "-stale-r" + str(cell["currencyAtExport"]["headRevision"])
    objects.append({"localId": "run", "authority": "workspace", "sourceId": f"run:{run_id}", "sourceVersion": run_version,
                    "kind": "computation", "title": run.get("name") or f"Workspace run {run_id}",
                    "dataClassification": classification,
                    "provenance": {**common, "studyPackages": packages,
                                   "artifactSha256": _sha(run.get("reproducibilityFingerprint"), run),
                                   "verification": {"status": status, "runStatus": verification.get("runStatus"),
                                                    "jobId": verification.get("jobId")}}})
    for d in datasets:
        relations.append({"subject": "run", "predicate": "uses-dataset", "object": f"dataset:{d['datasetId']}:r{d['revision']}"})
    if model:
        relations.append({"subject": "run", "predicate": "executes-model", "object": "model"})
    if cell and cell["verified"]:
        relations.append({"subject": "run", "predicate": "derived-from", "object": "notebook"})
    if status == "executed-verified":
        receipt_hashes = {r.get("resultSha256") for r in receipts}
        for out in prov.get("outputs") or []:
            sha = out.get("sha256")
            if not (isinstance(sha, str) and SHA.fullmatch(sha)):
                continue
            is_runtime_result = out.get("role") == "result" and sha in receipt_hashes
            is_figure = out.get("role") == "figure" and out.get("mediaType") == FIGURE_MEDIA
            if not (is_runtime_result or is_figure):
                continue
            local = f"output:{out['outputId']}"
            rec = {**common, "artifactSha256": sha,
                   "verification": {"status": "executed-verified" if is_runtime_result else "owner-registered-output",
                                    "outputId": out["outputId"], "mediaType": out.get("mediaType")}}
            if is_figure and sha in figure_files:
                rec["figure"] = fr.figure_binding(export, out, figure_files, datasets)
                bound_files |= fr.bound_hashes(rec["figure"])
            if is_runtime_result and values.get("values") and values.get("sourceArtifactSha256") == sha:
                rec["resultValues"] = values["values"]
                tol = cv.normalize_tolerance(values.get("tolerance"))
                if tol is not None:
                    # Pre-registered on the model revision before the run; reproductions cannot loosen it.
                    rec["declaredTolerance"] = tol
                iv = normalize_intervals(values.get("intervals"), values["values"])
                if iv is not None:
                    # SC-FIG-04.1: computed by the runtime (level, method, df from its own record), never here.
                    rec["intervals"] = iv
                ass = normalize_assumptions(values.get("assumptions"))
                if ass is not None:
                    rec["assumptions"] = ass
                se = values.get("standardErrors")
                se = cv.normalize_standard_errors(se.get("values"), values["values"]) if isinstance(se, dict) else None
                if se is not None:
                    rec["standardErrors"] = se
                st = values.get("stochastic")
                if isinstance(st, dict):
                    seed = st.get("seed")
                    rec["stochastic"] = {"seed": seed if isinstance(seed, int) and not isinstance(seed, bool) else None}
                for key, norm in (("identityCheck", cv.normalize_identity_check), ("ordering", cv.normalize_ordering)):
                    claim = norm(values.get(key))
                    if claim is not None:
                        rec[key] = claim
            objects.append({"localId": local, "authority": "workspace", "sourceId": f"run:{run_id}:output:{out['outputId']}",
                            "sourceVersion": f"{run_version}:{sha[:32]}", "kind": "result" if is_runtime_result else "figure",
                            "title": out.get("label") or out["outputId"], "dataClassification": classification,
                            "provenance": rec})
            relations.append({"subject": local, "predicate": "produced-by", "object": "run"})
    if set(figure_files) - bound_files:
        raise ValueError("figureFiles contains files that no figure output of this run references")
    body = {"schema": cv.BUNDLE_SCHEMA,
            "project": {"researchArea": area, "projectKey": _slug(f"ws-{cv.canonical_sha256(export['ownerScope'])[:10]}-{project_id}"), "entrySlug": entry, "title": title,
                        "workspaceProjectRef": f"workspace-project:{project_id}"},
            "objects": objects, "relations": relations}
    return cv.seal_bundle(body)


def _slug(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (s or "workspace")[:120]


# ---------------------------------------------------------------------------
# Operations


def _withdrawn(session: Session, owner_scope: str, run_id: str) -> bool:
    return session.scalar(select(WorkspaceWithdrawal.id).where(
        WorkspaceWithdrawal.owner_scope == owner_scope, WorkspaceWithdrawal.run_id == run_id)) is not None


def verified_export(key: Ed25519PublicKey, grant: str, fetch: Callable[[str], dict] | None) -> tuple[dict, dict]:
    claims = verify_grant(key, grant)
    export = verify_document(key, EXPORT_SCHEMA, (fetch or http_fetch_export)(grant))
    expected = (claims["grantId"], claims["ownerScope"], claims["projectId"], claims["runId"])
    if (export.get("grantId"), export.get("ownerScope"), export.get("projectId"), export.get("runId")) != expected:
        raise ValueError("Workspace export does not correspond to the presented grant")
    prov = export.get("provenance") or {}
    run = prov.get("run") or {}
    if prov.get("projectId") != claims["projectId"] or run.get("runId") != claims["runId"]:
        raise ValueError("Workspace export provenance is for a different run")
    declared = prov.get("provenanceFingerprint")
    if cv.canonical_sha256({k: v for k, v in prov.items() if k != "provenanceFingerprint"}) != declared:
        raise ValueError("Workspace provenance fingerprint does not match its content")
    return claims, export


def ingest_workspace_run(session: Session, *, key: Ed25519PublicKey, grant: str, area: str, entry: str,
                         title: str, fetch: Callable[[str], dict] | None = None, figure_files: Any = None) -> dict:
    claims, export = verified_export(key, grant, fetch)
    owner = export["ownerScope"]
    if _withdrawn(session, owner, export["runId"]):
        raise PermissionError("This run was withdrawn by its Workspace owner")
    files = fr.decode_files(figure_files)
    bundle = map_export(export, area=area, entry=entry, title=title, figure_files=files)
    result = cv.ingest_bundle(session, owner_scope=owner, bundle=bundle, attested_input_lineage=True)
    figures, rights_revoked = {}, []
    for item in bundle["objects"]:
        binding = item["provenance"].get("figure")
        if binding:
            mine = fr.bound_hashes(binding)
            fr.store_candidates({k: v for k, v in files.items() if k in mine}, binding["bundleSha256"])
            figures[item["localId"]] = {"bundleSha256": binding["bundleSha256"], "artifacts": len(binding["artifacts"])}
            # SC-FIG-03B: a source recorded as restricted makes earlier rights approvals stale.
            rights_revoked += fr.revoke_for_withdrawn_rights(session, owner, binding)
    purged = fr.purge_release_store(session) if rights_revoked else []
    export_sha = cv.canonical_sha256(export)
    if session.scalar(select(WorkspaceExportReceipt.id).where(WorkspaceExportReceipt.export_sha256 == export_sha)) is None:
        session.add(WorkspaceExportReceipt(
            owner_scope=owner, grant_id=export["grantId"], project_id=export["projectId"], run_id=export["runId"],
            export_sha256=export_sha, verification_status=export["verification"]["status"],
            computation_object_id=result["objects"]["run"]["id"],
            record={"objects": {k: v["id"] for k, v in result["objects"].items()},
                    "studyPackages": export.get("studyPackages") or [], "keyId": (export.get("signature") or {}).get("keyId")}))
        session.flush()
    return {**result, "ownerScope": owner, "runId": export["runId"], "exportSha256": export_sha,
            "verificationStatus": export["verification"]["status"], "figureBindings": figures,
            "sourceRightsRevokedObjectIds": rights_revoked, "figureBytesRemoved": purged,
            "inputLineage": export["verification"].get("inputLineage") or "unrecorded",
            "studyPackages": [{k: p.get(k) for k in ("packageId", "manifestFingerprint", "verified")}
                              for p in export.get("studyPackages") or []]}


def ingest_reproduction(session: Session, *, key: Ed25519PublicKey, original_id: str, grant: str,
                        rel_tol: float | None = None, abs_tol: float | None = None,
                        fetch: Callable[[str], dict] | None = None) -> dict:
    """Record a separate Workspace run as an independent reproduction of an ingested result."""
    _, export = verified_export(key, grant, fetch)
    owner = export["ownerScope"]
    if _withdrawn(session, owner, export["runId"]):
        raise PermissionError("This run was withdrawn by its Workspace owner")
    original = cv._owned_object(session, owner, original_id)
    orig_prov = cv._provenance(session, original)
    if orig_prov is None or (orig_prov.record.get("workspace") or {}).get("runId") == export["runId"]:
        raise ValueError("A reproduction must come from a different Workspace run")
    bundle = map_export(export, area="reproduction", entry="reproduction", title="reproduction")
    run_obj = next(o for o in bundle["objects"] if o["localId"] == "run")
    result_obj = next((o for o in bundle["objects"] if o["kind"] == "result" and o["provenance"].get("resultValues")), None)
    record = dict((result_obj or run_obj)["provenance"])
    record["scientificClassification"] = original.data_classification
    return cv.record_reproduction(session, owner_scope=owner, original_id=original_id, reproduction={
        "authority": "workspace", "sourceId": f"reproduction:run:{export['runId']}",
        "sourceVersion": run_obj["sourceVersion"], "title": f"Workspace reproduction run {export['runId']}",
        "provenance": record}, rel_tol=rel_tol, abs_tol=abs_tol, attested_input_lineage=True)


def notebook_reference(session: Session, *, owner_scope: str, notebook_id: str) -> dict:
    """Reopen a notebook from Research: the Workspace identity and revisions it was registered under,
    and the runs derived from each revision. Content stays in Workspace; nothing here is a copy."""
    rows = session.execute(
        select(md.ResearchObject)
        .join(ws.EntryWorkspace, ws.EntryWorkspace.id == md.ResearchObject.workspace_id)
        .join(ws.ResearchEntry, ws.ResearchEntry.id == ws.EntryWorkspace.entry_id)
        .join(ws.ResearchProject, ws.ResearchProject.id == ws.ResearchEntry.project_id)
        .where(ws.ResearchProject.owner_scope == owner_scope, md.ResearchObject.authority == "workspace",
               md.ResearchObject.kind == "notebook", md.ResearchObject.source_id == f"notebook:{notebook_id}")
        .order_by(md.ResearchObject.created_at, md.ResearchObject.id)).scalars().all()
    revisions = []
    for obj in rows:
        prov = cv._provenance(session, obj)
        rec = prov.record if prov else {}
        if (rec.get("verification") or {}).get("status") != NOTEBOOK_VERIFIED:
            continue
        ref = (rec.get("sourceRefs") or [{}])[0]
        runs = []
        for rel in session.scalars(select(md.ResearchObjectRelation).where(
                md.ResearchObjectRelation.object_id == obj.id, md.ResearchObjectRelation.predicate == "derived-from")).all():
            run = session.get(md.ResearchObject, rel.subject_id)
            run_prov = cv._provenance(session, run) if run else None
            if run is None or run_prov is None:
                continue
            r = run_prov.record
            runs.append({"objectId": run.id, "workspaceRunId": (r.get("workspace") or {}).get("runId"),
                         "cell": r.get("notebookCell"), "verification": (r.get("verification") or {}).get("status"),
                         "inputLineage": r.get("inputLineage"), "reviewState": run.review_state,
                         "claimStatus": cv.notebook_claim_status(session, run, r)})
        revisions.append({"objectId": obj.id, "notebookRevision": ref.get("notebookRevision"),
                          "packageSha256": obj.source_sha256, "visibility": obj.visibility, "reviewState": obj.review_state,
                          "runs": sorted(runs, key=lambda x: (x["cell"] or {}).get("cellId") or "")})
    if not revisions:
        raise LookupError("Notebook not registered in Research")
    # Revision comparison: which cells changed code between registered revisions.
    cells = {r["notebookRevision"]: {(x["cell"] or {}).get("cellId"): (x["cell"] or {}).get("cellSha256") for x in r["runs"]}
             for r in revisions}
    ordered = sorted(cells)
    changes = [{"from": a, "to": b,
                "changedCells": sorted(c for c in set(cells[a]) & set(cells[b]) if cells[a][c] != cells[b][c]),
                "addedCells": sorted(set(cells[b]) - set(cells[a])), "removedCells": sorted(set(cells[a]) - set(cells[b]))}
               for a, b in zip(ordered, ordered[1:])]
    return {"schema": NOTEBOOK_REFERENCE_SCHEMA, "authority": "workspace", "notebookId": notebook_id,
            "open": {"authority": "workspace", "route": f"/v1/notebooks/{notebook_id}"},
            "revisions": revisions, "revisionComparison": changes, "contentCopied": False}


def apply_withdrawal(session: Session, *, key: Ed25519PublicKey, notice: dict) -> dict:
    body = verify_document(key, WITHDRAWAL_SCHEMA, notice)
    owner, run_id = body["ownerScope"], body["runId"]
    digest = cv.canonical_sha256(notice)
    existing = session.scalar(select(WorkspaceWithdrawal).where(
        WorkspaceWithdrawal.owner_scope == owner, WorkspaceWithdrawal.run_id == run_id))
    if existing is not None:
        return {"runId": run_id, "revokedObjectIds": existing.revoked_object_ids, "replayed": True}
    rows = session.execute(
        select(md.ResearchObject, cv.OutputProvenance)
        .join(cv.OutputProvenance, cv.OutputProvenance.object_id == md.ResearchObject.id)
        .join(ws.EntryWorkspace, ws.EntryWorkspace.id == md.ResearchObject.workspace_id)
        .join(ws.ResearchEntry, ws.ResearchEntry.id == ws.EntryWorkspace.entry_id)
        .join(ws.ResearchProject, ws.ResearchProject.id == ws.ResearchEntry.project_id)
        .where(ws.ResearchProject.owner_scope == owner, md.ResearchObject.authority == "workspace")).all()
    revoked = []
    for obj, prov in rows:
        if (prov.record.get("workspace") or {}).get("runId") == run_id:
            obj.visibility, obj.review_state, obj.rights_cleared = "private", "revoked", False
            revoked.append(obj.id)
    session.add(WorkspaceWithdrawal(owner_scope=owner, project_id=body["projectId"], run_id=run_id,
                                    notice_sha256=digest, revoked_object_ids=revoked))
    session.flush()
    # SC-FIG-03A: released figure bytes of the withdrawn run leave the public store as well.
    purged = fr.purge_release_store(session)
    return {"runId": run_id, "revokedObjectIds": revoked, "replayed": False, "figureBytesRemoved": purged}


def _affected_entries(session: Session, object_ids: list[str]) -> list[ws.ResearchEntry]:
    if not object_ids:
        return []
    return list(session.scalars(
        select(ws.ResearchEntry).join(ws.EntryWorkspace, ws.EntryWorkspace.entry_id == ws.ResearchEntry.id)
        .join(md.ResearchObject, md.ResearchObject.workspace_id == ws.EntryWorkspace.id)
        .where(md.ResearchObject.id.in_(object_ids)).distinct().order_by(ws.ResearchEntry.slug)).all())


def apply_source_rights_notice(session: Session, *, key: Ed25519PublicKey, notice: dict) -> dict:
    """SC-FIG-04.1: apply an owner-signed source rights notice from Workspace.

    Only the signing owner's figures are touched. ``restricted`` revokes every figure of that owner whose
    source lineage names the exact bytes, removes their released files and records a source-rights-changed
    event in the editorial audit chain for each affected article. ``verified-for-use`` records the new
    status only: nothing is re-approved or re-published (revoked figures need new approvals).
    """
    try:
        body = verify_document(key, SOURCE_RIGHTS_SCHEMA, notice)
    except ValueError as exc:
        # A notice Workspace did not sign for Research (forged, altered or misaddressed) is refused, not parsed.
        raise PermissionError(f"Source rights notice refused: {exc}") from exc
    owner, sha, status, seq = body.get("ownerScope"), body.get("sourceSha256"), body.get("licenceStatus"), body.get("sequence")
    if not (isinstance(owner, str) and owner.startswith("workspace-user:") and isinstance(sha, str) and SHA.fullmatch(sha)
            and status in LICENCE_STATUSES and isinstance(seq, int) and not isinstance(seq, bool) and seq >= 1
            and isinstance(body.get("noticeId"), str) and isinstance(body.get("effectiveAt"), str)):
        raise ValueError("Source rights notice is malformed")
    digest = cv.canonical_sha256(notice)
    existing = session.scalar(select(WorkspaceSourceRightsNotice).where(WorkspaceSourceRightsNotice.notice_sha256 == digest))
    if existing is not None:
        return {"noticeId": existing.notice_id, "licenceStatus": existing.licence_status, "sequence": existing.sequence,
                "replayed": True, "revokedObjectIds": existing.revoked_object_ids, "figureBytesRemoved": []}
    last = session.scalar(select(WorkspaceSourceRightsNotice).where(
        WorkspaceSourceRightsNotice.owner_scope == owner, WorkspaceSourceRightsNotice.source_sha256 == sha)
        .order_by(WorkspaceSourceRightsNotice.sequence.desc()).limit(1).with_for_update())
    if last is not None and seq <= last.sequence:
        raise StaleNotice(f"A newer source rights notice (sequence {last.sequence}) was already applied for this source")
    # Workspace chains notices per source (sequence n names notice n-1's digest). A notice that does not directly
    # follow the last one applied here means Research missed one. A restriction still applies (withdrawing rights
    # is never held back) and the gap is recorded; any other status is refused until the chain is delivered in order.
    follows = (seq == 1 and body.get("previousNoticeSha256") is None) if last is None else \
        (seq == last.sequence + 1 and body.get("previousNoticeSha256") == last.notice_sha256)
    if not follows and status != fr.WITHDRAWN_LICENCE:
        raise StaleNotice("Source rights notice does not follow the last applied notice for this source; "
                          "deliver the earlier notices in order")
    revoked = (fr.revoke_for_withdrawn_rights(session, owner, {"sourceLineage": [{"sha256": sha, "licenceStatus": fr.WITHDRAWN_LICENCE}]})
               if status == fr.WITHDRAWN_LICENCE else [])
    row = WorkspaceSourceRightsNotice(owner_scope=owner, project_id=str(body.get("projectId") or ""),
                                      dataset_id=str(body.get("datasetId") or ""), source_sha256=sha, licence_status=status,
                                      sequence=seq, notice_id=body["noticeId"], notice_sha256=digest,
                                      effective_at=body["effectiveAt"], revoked_object_ids=revoked, figure_bytes_removed=[])
    session.add(row)
    session.flush()
    purged = fr.purge_release_store(session) if revoked else []
    from . import editorial as ed  # noqa: E402  (editorial imports this module)
    actor = ed.Actor(owner, "owner", owner, "*")
    detail = {"noticeId": body["noticeId"], "noticeSha256": digest, "sourceSha256": sha, "licenceStatus": status,
              "sequence": seq, "chainGap": not follows, "revokedObjectIds": revoked, "figureBytesRemoved": purged}
    entries = _affected_entries(session, revoked)
    events = [ed._event(session, actor, "source-rights-changed", entry=e, detail=detail).event_sha256 for e in entries] \
        or [ed._event(session, actor, "source-rights-changed", detail=detail).event_sha256]
    return {"noticeId": body["noticeId"], "licenceStatus": status, "sequence": seq, "replayed": False,
            "chainGap": not follows, "revokedObjectIds": revoked, "figureBytesRemoved": purged, "articles": [e.slug for e in entries],
            "auditEvents": events}


# ---------------------------------------------------------------------------
# HTTP (the Workspace grant is the caller's authority; no owner header is read)


def ingest_gate(authorization: str | None = Header(default=None)) -> Ed25519PublicKey:
    if not cv.writes_enabled() or not workspace_configured():
        raise HTTPException(status_code=503, detail=(
            "Workspace execution ingest is disabled; production requires SC-UP-01 acceptance."))
    # The staging credential identifies the calling service only; ownership comes from the signed export.
    if not authorization or not hmac.compare_digest(authorization.strip(), "Bearer " + cv._token()):
        raise HTTPException(status_code=401, detail="Research convergence authentication failed")
    return load_public_key()


def _run(session: Session, fn, **kwargs):
    try:
        result = fn(session, **kwargs)
        session.commit()
        return result
    except (StaleNotice, IntegrityError) as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc) if isinstance(exc, StaleNotice)
                            else "A concurrent source rights notice was applied first; resend after refreshing") from exc
    except UpstreamRefused as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status if exc.status in (403, 404, 410) else 502, detail=str(exc)) from exc
    except LookupError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/convergence/workspace/execution-runs")
def ingest_route(body: dict = Body(...), key: Ed25519PublicKey = Depends(ingest_gate),
                 session: Session = Depends(ws.get_session)):
    return _run(session, ingest_workspace_run, key=key, grant=str(body.get("grant") or ""),
                area=str(body.get("researchArea") or ""), entry=str(body.get("entrySlug") or ""),
                title=str(body.get("title") or ""), figure_files=body.get("figureFiles"))


@router.post("/convergence/workspace/reproductions")
def reproduction_route(body: dict = Body(...), key: Ed25519PublicKey = Depends(ingest_gate),
                       session: Session = Depends(ws.get_session)):
    return _run(session, ingest_reproduction, key=key, original_id=str(body.get("originalObjectId") or ""),
                grant=str(body.get("grant") or ""), rel_tol=cv._optional_float(body.get("relTol")),
                abs_tol=cv._optional_float(body.get("absTol")))


@router.get("/convergence/workspace/notebooks/{notebook_id}")
def notebook_reference_route(notebook_id: str, owner: str = Depends(cv.internal_principal),
                             session: Session = Depends(ws.get_session)):
    try:
        return notebook_reference(session, owner_scope=owner, notebook_id=notebook_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/convergence/workspace/withdrawals")
def withdrawal_route(body: dict = Body(...), key: Ed25519PublicKey = Depends(ingest_gate),
                     session: Session = Depends(ws.get_session)):
    return _run(session, apply_withdrawal, key=key, notice=body.get("notice") or {})


def rights_delivery_token() -> str:
    path = os.getenv("RESEARCH_RIGHTS_DELIVERY_TOKEN_FILE", "")
    try:
        return Path(path).read_text().strip() if path else ""
    except OSError:
        return ""


def rights_gate(authorization: str | None = Header(default=None)) -> Keyring:
    """SC-RIGHTS-02: Workspace's rights delivery credential works on the source-rights routes only.

    The convergence service credential is still accepted here (operators and the staging harness).
    """
    if not cv.writes_enabled() or not workspace_configured():
        raise HTTPException(status_code=503, detail=(
            "Workspace source-rights delivery is disabled; production requires SC-UP-01 acceptance."))
    supplied = (authorization or "").strip()
    accepted = [t for t in (rights_delivery_token(), cv._token()) if t]
    if not supplied or not any(hmac.compare_digest(supplied, "Bearer " + t) for t in accepted):
        raise HTTPException(status_code=401, detail="Research source-rights authentication failed")
    return load_public_key()


@router.post("/convergence/workspace/source-rights-notices")
def source_rights_route(body: dict = Body(...), key: Keyring = Depends(rights_gate),
                        session: Session = Depends(ws.get_session)):
    return _run(session, apply_source_rights_notice, key=key, notice=body.get("notice") or {})


@router.get("/convergence/workspace/source-rights-state")
def source_rights_state_route(_key: Keyring = Depends(rights_gate), session: Session = Depends(ws.get_session)):
    return rs.state(session)


@router.post("/convergence/workspace/source-rights-attestations")
def source_rights_attestation_route(body: dict = Body(...), key: Keyring = Depends(rights_gate),
                                    session: Session = Depends(ws.get_session)):
    return _run(session, rs.apply_attestation, key=key, attestation=body.get("attestation") or {})
