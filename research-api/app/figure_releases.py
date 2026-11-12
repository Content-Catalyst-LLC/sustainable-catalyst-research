"""SC-FIG-03A — governed scientific figure revisions and their public release.

A figure reaches the public site in three steps, and only Research writes any of them:

1. **Binding (ingest).** A Workspace run output with role ``figure`` and media type
   ``application/vnd.sc.figure-bundle.v1+json`` arrives in a Workspace-signed export as a hash. The
   caller may supply the exact bundle bytes and the view files the bundle references. Research
   accepts them only if the bundle bytes hash to the signed output hash, every artifact the bundle
   declares is supplied and matches its declared hash, and every source the bundle cites is one of
   the run's verified input hashes. The result is a ``figure`` binding inside the object's immutable
   provenance record (bundle revision, artifact hashes, source lineage, calculation references and
   the owning Workspace project). Workspace approvals name ``research-object:<id>#<provenance hash>``,
   so all four signed approvals cover exactly that binding. The bytes go to a private, content-addressed
   candidate store that only the writer service mounts.
2. **Release (editorial publish).** When an article revision that references the figure is published,
   Research re-reads every byte from the candidate store, re-checks it against the approved binding,
   re-applies the public disclosure policy, and only then copies the files into the release store
   (``<bundle sha256>/<file sha256>``). An immutable release record names the article revision, the
   provenance hash and the signed approval records.
3. **Serving (public reader).** ``public_figures`` serves a figure only while the article revision
   that released it is the live revision, the figure object is still public, and the stored bytes
   still match. Withdrawal, retraction, rollback away from the figure and Workspace withdrawal of the
   run remove it from the API immediately; ``purge_release_store`` then deletes the bytes.

Nothing here approves anything. Producer-side fields inside a bundle (``review.*``, artifact status)
are the producer's own notes and never count as release authority.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, aliased, mapped_column

from . import convergence as cv
from . import figure_objects as fo
from . import object_registry as md
from . import workspaces as ws

BINDING_SCHEMA = "sc-research-figure-binding/1.0"
RELEASE_SCHEMA = "sc-research-figure-release/1.0"
POLICY = "sc-research-figure-release-policy/2.0"
SHA = re.compile(r"^[0-9a-f]{64}$")
MAX_FILES = 12  # the Lab renderer's per-package limit, bundle included
MAX_FILE_BYTES = 8 * 1024 * 1024
CHANNELS = frozenset({"staging-synthetic"})
LIVE = ("published", "unlisted")
# sc.figure-bundle.v1 licenceStatus: only a source verified for use may be released.
CLEARED_LICENCE = "verified-for-use"
WITHDRAWN_LICENCE = "restricted"
# SC-FIG-05.3: runtime uncertainty records a bundle's calculation references name by hash. The Lab renderer
# draws a band, quantile fan or map uncertainty layer only from these exact records, so Research binds them
# with the views and releases them alongside; nothing is drawn from a record Research did not verify.
RUNTIME_RECORD_KEYS = ("bandsSha256", "quantileBandsSha256", "uncertaintySha256")
# SC-FIG-05.0: the scene-3d contract is a proposal under owner review. Scenes may be bound privately (their
# provenance is recorded and re-checkable) but are never released until the contract is approved.
SCENE_CONTRACT = "sc.scene-3d.v0-proposed"
SCENE_HOLD = "scene-3d contract proposed, not approved"
# SC-FIG-05.0 (nD draft v0.1, FIG-TEST-025): independent axes, dependent fields and a selected slice. Bound privately
# with the slice hash recomputed; never released while the dimension contract is a proposal.
ND_HOLD = "nD dimension and projection contract proposed, not approved"


class FigureRelease(ws.Base):
    """Immutable: Research verified and released these exact bytes for one article revision."""
    __tablename__ = "research_figure_releases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ws.new_id)
    object_id: Mapped[str] = mapped_column(ForeignKey("research_metadata_objects.id"), nullable=False, index=True)
    entry_id: Mapped[str] = mapped_column(ForeignKey("research_entries.id"), nullable=False, index=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    bundle_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provenance_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    record: Mapped[dict] = mapped_column(JSON, nullable=False)
    record_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ws.now_utc, nullable=False)
    __table_args__ = (UniqueConstraint("object_id", "entry_id", "revision_number", name="uq_research_figure_release"),)


# ---------------------------------------------------------------------------
# Configuration


def channel() -> str | None:
    value = os.getenv("RESEARCH_PUBLIC_FIGURES", "off").strip().lower()
    return value if value in CHANNELS else None


def _dir(name: str) -> Path | None:
    raw = os.getenv(name, "").strip()
    path = Path(raw) if raw else None
    return path if path is not None and path.is_dir() and not path.is_symlink() else None


def candidate_store() -> Path | None:
    """Private bytes awaiting approval (writer service only; never mounted into the public reader)."""
    return _dir("RESEARCH_FIGURE_CANDIDATE_STORE")


def release_store() -> Path | None:
    """The writer's read-write view of the release store the public reader mounts read-only."""
    return _dir("RESEARCH_FIGURE_RELEASE_STORE")


# ---------------------------------------------------------------------------
# Hashing


def artifact_match(data: bytes, digest: str) -> str | None:
    """How ``data`` matches ``digest``: exact bytes, or the canonical JSON the producer hashed."""
    if hashlib.sha256(data).hexdigest() == digest:
        return "raw-bytes"
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return "canonical-json" if cv.canonical_sha256(value) == digest else None


def decode_files(items: Any) -> dict[str, bytes]:
    """``[{sha256, contentBase64}]`` from an ingest request -> {sha256: bytes}, each hash re-checked."""
    if items in (None, []):
        return {}
    if not isinstance(items, list) or len(items) > MAX_FILES:
        raise ValueError(f"figureFiles must be a list of at most {MAX_FILES} files")
    out: dict[str, bytes] = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {"sha256", "contentBase64"}:
            raise ValueError("Each figure file is {sha256, contentBase64}")
        digest = item["sha256"]
        if not isinstance(digest, str) or not SHA.fullmatch(digest) or digest in out:
            raise ValueError("Each figure file needs a distinct lowercase SHA-256")
        try:
            data = base64.b64decode(str(item["contentBase64"]), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("figure file content is not base64") from exc
        if not data or len(data) > MAX_FILE_BYTES:
            raise ValueError("Each figure file must be 1 byte to 8 MiB")
        if artifact_match(data, digest) is None:
            raise ValueError("A figure file does not match its declared SHA-256")
        out[digest] = data
    return out


# ---------------------------------------------------------------------------
# 1. Binding at ingest


def _input_hashes(verification: dict, datasets: list[dict]) -> set[str]:
    """Every hash Workspace attested as an input of this run (pinned bytes, rows, derived payloads)."""
    found: set[str] = set()

    def add(v):
        if isinstance(v, str) and SHA.fullmatch(v):
            found.add(v)

    for b in verification.get("inputBindings") or []:
        for k in ("artifactSha256", "rowsSha256", "derivedPayloadSha256"):
            add(b.get(k))
        for d in b.get("datasets") or []:
            add(d.get("artifactSha256"))
    for d in datasets:
        add(d.get("artifactSha256"))
    return found


def figure_binding(export: dict, output: dict, files: dict[str, bytes], datasets: list[dict]) -> dict:
    """The immutable binding for one figure output, or ValueError. ``files`` holds the supplied bytes."""
    sha = output["sha256"]
    raw = files.get(sha)
    if raw is None:
        raise ValueError(f"figure output {output.get('outputId')!r}: bundle bytes were not supplied")
    bundle, digest = fo.parse_bundle(raw)
    if digest != sha:
        raise ValueError("figure bundle bytes do not hash to the Workspace-signed output")
    verification = export["verification"]
    if verification.get("status") != "executed-verified":
        raise ValueError("figures are bound only to executed-verified Workspace runs")
    artifacts, used = [], {sha}
    for a in bundle["artifacts"]:
        digest_a = str(a.get("sha256") or "")
        data = files.get(digest_a) if SHA.fullmatch(digest_a) else None
        mode = artifact_match(data, digest_a) if data is not None else None
        if mode is None:
            raise ValueError(f"figure artifact {digest_a[:12] or '?'} was not supplied with matching bytes")
        used.add(digest_a)
        artifacts.append({"sha256": digest_a, "mediaType": str(a.get("mediaType") or ""), "bytes": len(data),
                          "hashMode": mode})
    runtime_records = []
    for i, c in enumerate(bundle.get("calculationRefs") or []):
        for k in RUNTIME_RECORD_KEYS if isinstance(c, dict) else ():
            if k not in c:
                continue
            digest_r = str(c[k] or "")
            data = files.get(digest_r) if SHA.fullmatch(digest_r) else None
            mode = artifact_match(data, digest_r) if data is not None else None
            if mode is None or not _is_record(_json(data)):
                raise ValueError(f"figure calculationRefs[{i}].{k}: the runtime record was not supplied as matching JSON "
                                 "(uncertainty is drawn only from the runtime's own record)")
            used.add(digest_r)
            runtime_records.append({"kind": k[:-len("Sha256")], "calculationIndex": i, "sha256": digest_r,
                                    "bytes": len(data), "hashMode": mode})
    scene = scene_binding(bundle)
    projection = projection_binding(bundle)
    inputs = _input_hashes(verification, datasets)
    sources, citations = [], []
    for s in bundle["sourceRefs"]:
        if not isinstance(s, dict):
            raise ValueError("figure sourceRefs must be objects")
        digest_s = s.get("sha256")
        if digest_s is None and (s.get("doi") or s.get("citationId")):
            # A literature citation: displayed with its recorded status, never treated as data lineage.
            citations.append({k: s.get(k) for k in ("sourceId", "doi", "citationId", "licenceStatus", "qualityStatus")})
            continue
        if not (isinstance(digest_s, str) and SHA.fullmatch(digest_s)):
            raise ValueError("every figure data source must carry the SHA-256 of the input it was drawn from")
        if digest_s not in inputs:
            raise ValueError("a figure source is not one of the run's verified inputs (source lineage unbound)")
        sources.append({"sourceId": str(s.get("sourceId") or ""), "sha256": digest_s,
                        "licenceStatus": s.get("licenceStatus"), "identityStatus": s.get("identityStatus")})
    if not sources:
        raise ValueError("a figure needs at least one source drawn from the run's verified inputs")
    receipts = verification.get("runtimeReceipts") or []
    cell = export.get("notebookCell") if isinstance(export.get("notebookCell"), dict) else {}
    run = export["provenance"]["run"]
    calc = {"run": {"runId": export["runId"], "reproducibilityFingerprint": run.get("reproducibilityFingerprint"),
                    "inputLineage": verification.get("inputLineage") or "unrecorded"},
            "runtimeReceipts": [{"receiptId": r.get("receiptId"), "resultSha256": r.get("resultSha256")} for r in receipts],
            "notebookCell": ({k: cell.get(k) for k in ("notebookId", "notebookRevision", "cellId", "cellSha256")}
                             if cell.get("verified") is True else None),
            "producerCalculationRefs": bundle.get("calculationRefs") or []}
    project = bundle.get("project") or {}
    extra = {}
    # Only bundles that carry these get the keys, so every earlier binding (and its provenance hash) is unchanged.
    if runtime_records:
        extra["runtimeRecords"] = runtime_records
    if scene is not None:
        extra["scene"] = scene
    if projection is not None:
        extra["projection"] = projection
    return {
        **extra,
        "schema": BINDING_SCHEMA, "bundleSha256": sha, "figureId": bundle["figureId"], "version": bundle.get("version", 1),
        "producer": bundle["provenance"]["authorityOwner"], "evidenceClass": bundle["evidenceClass"],
        "measurementStatus": bundle["scientificContext"]["measurementStatus"],
        "artifacts": artifacts, "sourceLineage": sources, "citations": citations, "calculationRefs": calc,
        "ownership": {"workspaceProject": f"workspace-project:{export['projectId']}",
                      "ownerScopeSha256": cv.canonical_sha256(export["ownerScope"]),
                      "bundleProjectRef": project.get("projectRef"), "bundleOwnerScope": project.get("ownerScope")},
    }


def _is_record(value) -> bool:
    """A runtime record is a JSON object, or a non-empty list of objects (the Workspace R export carries the
    confidence and prediction bands of one fit as a list)."""
    return isinstance(value, dict) or (isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value))


def _json(data: bytes):
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def scene_binding(bundle: dict) -> dict | None:
    """SC-FIG-05.0 private provenance for a proposed scene-3d figure, or None for any other figure.

    The scene hash is recomputed from the scene record itself, so the binding names the geometry, scalar
    field, camera, interpolation and renderer limits Workspace approved. Mesh bytes stay with the producer;
    their declared hashes are recorded, not re-read.
    """
    scene = bundle.get("scene")
    if bundle.get("figureType") != "scene-3d" and scene is None:
        return None
    if not isinstance(scene, dict) or scene.get("sceneSchema") != SCENE_CONTRACT or bundle.get("figureType") != "scene-3d":
        raise ValueError(f"a scene-3d figure needs figureType scene-3d and a {SCENE_CONTRACT} scene record")
    if scene.get("sceneSha256") != cv.canonical_sha256({k: v for k, v in scene.items() if k != "sceneSha256"}):
        raise ValueError("scene.sceneSha256 does not match the canonical scene record")
    meshes = scene.get("meshes")
    if not isinstance(meshes, list) or not meshes or not all(
            isinstance(m, dict) and SHA.fullmatch(str(m.get("verticesSha256") or "")) and SHA.fullmatch(str(m.get("facesSha256") or ""))
            for m in meshes):
        raise ValueError("every scene mesh needs vertex and face SHA-256 hashes")
    if not any(str(a.get("mediaType") or "") == "image/png" for a in bundle["artifacts"]):
        raise ValueError("a scene-3d figure needs a static PNG view")
    renderer = scene.get("renderer") if isinstance(scene.get("renderer"), dict) else {}
    return {"contract": SCENE_CONTRACT, "contractStatus": "proposed-owner-review", "sceneSha256": scene["sceneSha256"],
            "meshes": [{k: m.get(k) for k in ("meshId", "vertexCount", "faceCount", "verticesSha256", "facesSha256", "scalarsSha256")}
                       for m in meshes],
            "scalar": scene.get("scalar"), "interpolation": scene.get("interpolation"),
            "verticalExaggeration": scene.get("verticalExaggeration"),
            "renderer": {k: renderer.get(k) for k in ("name", "version", "offscreen", "network")},
            "budgets": scene.get("budgets"), "meshHashMode": "declared-by-producer"}


MAX_AXES = 16  # the proposal's bound on declared independent axes


def projection_errors(bundle: dict) -> list[str]:
    """Cross-field rules for declared axes and a selected slice (the Data proposal's validator applies the same)."""
    errs, dims = [], bundle.get("dimensions") or []
    if not isinstance(dims, list) or not all(isinstance(d, dict) and d.get("independent") is True for d in dims):
        return ["dimensions must be a list of independent axes"]
    if len(dims) > MAX_AXES:
        return [f"at most {MAX_AXES} independent axes may be declared"]
    ids = [d.get("id") for d in dims]
    axes = dict(zip(ids, dims))
    if len(axes) != len(ids):
        errs.append("duplicate axis id")
    for d in dims:
        a, role = d.get("id"), d.get("role")
        if role == "spatial" and not (d.get("unit") and (d.get("crs") or d.get("verticalReference"))):
            errs.append(f"spatial axis {a!r} needs a unit and a crs or verticalReference")
        if role == "time" and (d.get("kind") != "discrete" or not d.get("samples")):
            errs.append(f"time axis {a!r} must be discrete with listed samples")
        if d.get("measured") is True and d.get("evidenceClass") != "observed":
            errs.append(f"axis {a!r} is marked measured but is not observed evidence")
    if "isActual5d" in (bundle.get("temporal") or {}):
        errs.append("isActual5d is not a field: temporal.isActual4d gates stay authoritative")
    fields = [f.get("name") for f in bundle.get("dependentFields") or [] if isinstance(f, dict)]
    if set(fields) & set(ids):
        errs.append("a dependent output is declared as an independent axis")
    vp = bundle.get("viewProjection")
    if vp is None:
        return errs
    if not isinstance(vp, dict) or not dims:
        return errs + ["a view projection needs declared dimensions"]
    visible = vp.get("visibleAxes") or []
    if len(visible) > 3 or any(a not in axes for a in visible):
        errs.append("visible axes must be at most three declared independent axes")
    fixed = {}
    for f in vp.get("fixed") or []:
        a, d = f.get("axis"), axes.get(f.get("axis"))
        if d is None or a in fixed or a in visible:
            errs.append(f"fixed axis {a!r} is undefined, repeated or visible")
            continue
        fixed[a] = f
        if d.get("unit") and f.get("unit") != d["unit"]:
            errs.append(f"fixed axis {a!r} unit does not match the axis")
        if "samples" in d and f.get("value") not in d["samples"]:
            errs.append(f"fixed axis {a!r} value is not a listed sample (no interpolation)")
        dom, v = d.get("domain"), f.get("value")
        if "samples" not in d and isinstance(dom, dict):
            lo, hi, num = dom.get("min"), dom.get("max"), (int, float)
            if not (isinstance(lo, num) and isinstance(hi, num) and isinstance(v, num) and lo <= v <= hi):
                errs.append(f"fixed axis {a!r} value is outside the declared domain")
    agg = vp.get("aggregation") or {}
    if agg.get("kind") == "declared-method" and not (agg.get("method") and isinstance(agg.get("calculationIndex"), int)):
        errs.append("a declared aggregation needs its method and calculationIndex")
    if agg.get("kind") == "none-exact-slice":
        errs += [f"axis {a!r} is neither visible nor fixed" for a in ids if a not in fixed and a not in visible]
    body = {"dimensions": dims, "dependentFields": bundle.get("dependentFields") or [],
            "viewProjection": {k: v for k, v in vp.items() if k != "sliceSha256"}}
    if vp.get("sliceSha256") != cv.canonical_sha256(body):
        errs.append("viewProjection.sliceSha256 does not match the canonical axes, fields and selection")
    return errs


def projection_binding(bundle: dict) -> dict | None:
    """SC-FIG-05.0 private provenance for declared axes and a selected slice, or None when the bundle has neither."""
    if not any(k in bundle for k in ("dimensions", "dependentFields", "viewProjection")):
        return None
    errs = projection_errors(bundle)
    if errs:
        raise ValueError("figure dimensions refused: " + "; ".join(errs))
    vp = bundle.get("viewProjection") or {}
    return {"contractStatus": "proposed-owner-review",
            "axes": [{k: d.get(k) for k in ("id", "role", "kind", "unit", "crs", "verticalReference", "evidenceClass", "measured")}
                     for d in bundle["dimensions"]],
            "dependentFields": [{k: f.get(k) for k in ("name", "unit", "evidenceClass")} for f in bundle.get("dependentFields") or []],
            "sliceSha256": vp.get("sliceSha256"), "visibleAxes": vp.get("visibleAxes"), "fixed": vp.get("fixed"),
            "aggregation": vp.get("aggregation")}


def bound_hashes(binding: dict) -> set[str]:
    """Every file hash a binding names: the bundle, its views and its runtime uncertainty records."""
    return {binding["bundleSha256"], *(a["sha256"] for a in binding["artifacts"]),
            *(r["sha256"] for r in binding.get("runtimeRecords") or [])}


def store_candidates(files: dict[str, bytes], bundle_sha256: str) -> None:
    root = candidate_store()
    if root is None:
        raise ValueError("Figure ingest needs RESEARCH_FIGURE_CANDIDATE_STORE (private, writer only)")
    _write_all(root, bundle_sha256, files)


def _write_all(root: Path, bundle_sha256: str, files: dict[str, bytes]) -> None:
    target = root / bundle_sha256
    if target.is_symlink():
        raise ValueError("figure store entry is a symlink")
    target.mkdir(mode=0o755, exist_ok=True)
    for digest, data in files.items():
        path = target / digest
        if path.is_file() and not path.is_symlink() and path.read_bytes() == data:
            continue
        tmp = root / f".tmp-{uuid4().hex}"
        tmp.write_bytes(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)


def _read(directory: Path, digest: str) -> bytes | None:
    path = directory / digest
    if not SHA.fullmatch(digest) or path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        return None
    return path.read_bytes()


# ---------------------------------------------------------------------------
# 2. Release at editorial publish


def binding_of(session: Session, obj: md.ResearchObject) -> tuple[cv.OutputProvenance | None, dict | None]:
    prov = cv._provenance(session, obj)
    binding = (prov.record.get("figure") if prov else None)
    return prov, binding if isinstance(binding, dict) and binding.get("schema") == BINDING_SCHEMA else None


def verified_files(directory: Path | None, binding: dict) -> tuple[dict | None, list[dict], list[str]]:
    """Read every bound file from ``directory`` and re-check it. Returns (bundle, files, problems)."""
    problems: list[str] = []
    if directory is None or not directory.is_dir() or directory.is_symlink():
        return None, [], ["figure bytes are not in the store"]
    raw = _read(directory, binding["bundleSha256"])
    if raw is None or hashlib.sha256(raw).hexdigest() != binding["bundleSha256"]:
        return None, [], ["bundle bytes are missing or differ from the approved revision"]
    try:
        bundle, _ = fo.parse_bundle(raw)
    except ValueError as exc:
        return None, [], [str(exc)]
    declared = [str(a.get("sha256") or "") for a in bundle["artifacts"]]
    if declared != [a["sha256"] for a in binding["artifacts"]]:
        problems.append("bundle artifacts differ from the approved binding")
    files = [{"role": "bundle", "sha256": binding["bundleSha256"], "mediaType": "application/json", "bytes": len(raw),
              "hashMode": "raw-bytes", "data": raw}]
    for a in binding["artifacts"]:
        data = _read(directory, a["sha256"])
        mode = artifact_match(data, a["sha256"]) if data is not None else None
        if mode is None:
            problems.append(f"view {a['sha256'][:12]} is missing or altered")
            continue
        if any(f["sha256"] == a["sha256"] for f in files):
            continue
        files.append({"role": "view", "sha256": a["sha256"], "mediaType": a["mediaType"], "bytes": len(data),
                      "hashMode": mode, "data": data})
    for r in binding.get("runtimeRecords") or []:
        data = _read(directory, r["sha256"])
        if data is None or artifact_match(data, r["sha256"]) is None or not _is_record(_json(data)):
            problems.append(f"runtime {r['kind']} record {r['sha256'][:12]} is missing or altered")
            continue
        if any(f["sha256"] == r["sha256"] for f in files):
            continue
        files.append({"role": "runtime-record", "sha256": r["sha256"], "mediaType": "application/json", "bytes": len(data),
                      "hashMode": artifact_match(data, r["sha256"]), "data": data})
    if len(files) > MAX_FILES:
        problems.append("too many files for one figure package")
    if len(files) == 1:
        problems.append("no verified view file")
    return bundle, files, problems


def release_figures(session: Session, *, entry: ws.ResearchEntry, revision_number: int, references: list[dict]) -> list[dict]:
    """Release every figure an article revision references. Raises PermissionError if any fails.

    Called by editorial publish and rollback after the signed approvals and reference checks passed.
    Files are written only after every figure has been re-verified, so a refusal leaves nothing behind.
    """
    from .public_figures import release_errors  # the same policy the reader applies on every request

    figures = [r for r in references if r.get("role") == "figure"]
    release_channel = channel()
    if not figures or release_channel is None:
        return []
    source, target = candidate_store(), release_store()
    if source is None or target is None:
        raise PermissionError("Figure release needs the private candidate store and the release store")
    planned = []
    for ref in figures:
        obj = session.get(md.ResearchObject, ref["objectId"])
        prov, binding = binding_of(session, obj)
        if binding is None:
            raise PermissionError(f"Figure {obj.title!r} has no verified figure binding; re-ingest it with its bytes")
        if binding["bundleSha256"] != obj.source_sha256:
            raise PermissionError("Figure binding does not match the registered revision")
        state = cv.approval_state(session, obj, signed_only=True)
        if not all(state.values()):
            raise PermissionError("Figure is missing signed Workspace approvals: " + ", ".join(d for d, ok in state.items() if not ok))
        bundle, files, problems = verified_files(source / binding["bundleSha256"], binding)
        if bundle is not None:
            problems += release_errors(bundle, article=entry.slug, release_channel=release_channel)
            for f in files[1:]:
                if f["mediaType"].endswith("json"):
                    from .public_figures import private_content
                    problems += private_content(json.loads(f["data"].decode("utf-8")))
        if release_channel == "staging-synthetic" and obj.data_classification != "synthetic":
            problems.append("the staging channel releases synthetic figures only")
        problems += source_rights_errors(session, md._workspace_owner(session, obj.workspace_id), binding)
        if problems:
            raise PermissionError(f"Figure {obj.title!r} cannot be released: " + "; ".join(problems))
        planned.append((obj, prov, binding, files, state))
    out = []
    from . import source_rights_sync as rs  # noqa: E402
    for obj, prov, binding, files, state in planned:
        _write_all(target, binding["bundleSha256"], {f["sha256"]: f["data"] for f in files})
        rs.record_release_sources(session, obj, binding)
        approvals = {r.dimension: r.record_sha256 for r in session.scalars(select(cv.PublicationApproval).where(
            cv.PublicationApproval.object_id == obj.id)) if "signedRecord" in (r.record or {})}
        record = {"schema": RELEASE_SCHEMA, "policy": POLICY, "channel": release_channel, "article": entry.slug,
                  "revision": revision_number, "objectId": obj.id, "bundleSha256": binding["bundleSha256"],
                  "subjectRef": cv.subject_ref(obj, prov), "provenanceSha256": prov.record_sha256,
                  "approvalRecords": approvals,
                  "files": [{k: f[k] for k in ("role", "sha256", "mediaType", "bytes", "hashMode")} for f in files]}
        row = session.scalar(select(FigureRelease).where(
            FigureRelease.object_id == obj.id, FigureRelease.entry_id == entry.id,
            FigureRelease.revision_number == revision_number))
        if row is None:
            row = FigureRelease(object_id=obj.id, entry_id=entry.id, revision_number=revision_number,
                                bundle_sha256=binding["bundleSha256"], provenance_sha256=prov.record_sha256,
                                record=record, record_sha256=cv.canonical_sha256(record))
            session.add(row)
            session.flush()
        elif row.provenance_sha256 != prov.record_sha256 or row.bundle_sha256 != binding["bundleSha256"]:
            raise PermissionError("An earlier release of this revision names different bytes")
        out.append({"objectId": obj.id, "bundleSha256": row.bundle_sha256, "releaseSha256": row.record_sha256})
    return out


# ---------------------------------------------------------------------------
# 3. What the reader may serve, and store cleanup


def live_release(session: Session, obj: md.ResearchObject, *, apply_hold: bool = True) -> tuple[FigureRelease, str] | None:
    """The release of ``obj`` by the live revision of a published or unlisted article, if any.

    SC-RIGHTS-02: with ``apply_hold`` (every reader path), a figure whose Workspace source rights are not
    confirmed as current is not live. Store cleanup passes False, so a hold never deletes released bytes.
    """
    from .editorial import EditorialArticle  # editorial imports this module

    rows = session.execute(
        select(FigureRelease, ws.ResearchEntry.slug)
        .join(EditorialArticle, EditorialArticle.entry_id == FigureRelease.entry_id)
        .join(ws.ResearchEntry, ws.ResearchEntry.id == FigureRelease.entry_id)
        .where(FigureRelease.object_id == obj.id, FigureRelease.bundle_sha256 == obj.source_sha256,
               EditorialArticle.publication_status.in_(LIVE),
               EditorialArticle.published_revision == FigureRelease.revision_number,
               ws.ResearchEntry.visibility == md.APPROVED)
        .order_by(FigureRelease.created_at.desc())).all()
    for row, slug in rows:
        if ws.approved_workspace_view(session, slug) is not None:
            if apply_hold and obj.authority == "workspace":
                from . import source_rights_sync as rs  # noqa: E402
                _prov, binding = binding_of(session, obj)
                if rs.binding_problems(session, md._workspace_owner(session, obj.workspace_id), binding or {}):
                    return None
            return row, slug
    return None


def live_release_object_ids(*, apply_hold: bool = True):
    """Subquery: ids of figure objects whose exact bundle a live article revision released.

    SC-RIGHTS-02: figures held for unconfirmed source rights are left out unless ``apply_hold`` is False.
    """
    from .editorial import EditorialArticle  # editorial imports this module
    from . import source_rights_sync as rs  # noqa: E402

    obj = aliased(md.ResearchObject)
    query = (select(FigureRelease.object_id)
             .join(obj, obj.id == FigureRelease.object_id)
             .join(EditorialArticle, EditorialArticle.entry_id == FigureRelease.entry_id)
             .where(FigureRelease.bundle_sha256 == obj.source_sha256,
                    EditorialArticle.publication_status.in_(LIVE),
                    EditorialArticle.published_revision == FigureRelease.revision_number))
    held = rs.held_condition(FigureRelease.object_id, obj.authority) if apply_hold else None
    return query if held is None else query.where(~held)


# ---------------------------------------------------------------------------
# 2b. Source rights (SC-FIG-03B)
#
# Source rights enter Research through the signed figure binding (sourceLineage[].licenceStatus), so the
# rights approval covers the rights recorded in those exact bytes. When a later signed binding of the same
# owner records a source as restricted, every approval that relied on the earlier "verified-for-use" is
# stale: those figures are revoked and their released bytes removed, and no figure citing that source can
# be released again by the owner until a newer binding records it as verified for use.


def _owner_figures(session: Session, owner_scope: str) -> list[tuple[md.ResearchObject, cv.OutputProvenance, dict]]:
    rows = session.execute(
        select(md.ResearchObject, cv.OutputProvenance)
        .join(cv.OutputProvenance, cv.OutputProvenance.object_id == md.ResearchObject.id)
        .where(md.ResearchObject.kind == "figure")).all()
    out = []
    for obj, prov in rows:
        b = prov.record.get("figure")
        if isinstance(b, dict) and b.get("schema") == BINDING_SCHEMA and md._workspace_owner(session, obj.workspace_id) == owner_scope:
            out.append((obj, prov, b))
    return out


def source_rights(session: Session, owner_scope: str) -> dict[str, str]:
    """The owner's current recorded licence status per source SHA-256 (latest signed binding wins)."""
    latest: dict[str, tuple] = {}
    for _obj, prov, b in _owner_figures(session, owner_scope):
        for s in b.get("sourceLineage") or []:
            when = (prov.created_at, prov.record_sha256)
            if s.get("sha256") and (s["sha256"] not in latest or when > latest[s["sha256"]][0]):
                latest[s["sha256"]] = (when, s.get("licenceStatus"))
    out = {sha: status for sha, (_when, status) in latest.items()}
    # SC-FIG-04.1: the owner's signed Workspace notices are the rights registry and override bundle declarations.
    from .workspace_ingest import WorkspaceSourceRightsNotice as N  # noqa: E402  (circular at import time)
    for row in session.scalars(select(N).where(N.owner_scope == owner_scope).order_by(N.source_sha256, N.sequence)):
        out[row.source_sha256] = row.licence_status
    return out


def source_rights_errors(session: Session, owner_scope: str, binding: dict) -> list[str]:
    current = source_rights(session, owner_scope)
    errors = []
    for s in binding.get("sourceLineage") or []:
        if s.get("licenceStatus") != CLEARED_LICENCE:
            errors.append(f"source {s.get('sourceId') or s['sha256'][:12]} is not verified for use ({s.get('licenceStatus')})")
        elif current.get(s["sha256"], CLEARED_LICENCE) != CLEARED_LICENCE:
            errors.append(f"source {s.get('sourceId') or s['sha256'][:12]} rights were withdrawn after approval")
    return errors


def revoke_for_withdrawn_rights(session: Session, owner_scope: str, binding: dict) -> list[str]:
    """Called when a new signed binding records restricted sources: revoke every figure relying on them."""
    withdrawn = {s["sha256"] for s in binding.get("sourceLineage") or [] if s.get("licenceStatus") == WITHDRAWN_LICENCE}
    if not withdrawn:
        return []
    revoked = []
    for obj, _prov, b in _owner_figures(session, owner_scope):
        if obj.review_state != "revoked" and withdrawn & {s.get("sha256") for s in b.get("sourceLineage") or []}:
            obj.visibility, obj.review_state, obj.rights_cleared = "private", "revoked", False
            revoked.append(obj.id)
    session.flush()
    return revoked


def servable_bundles(session: Session) -> set[str]:
    public = {o.id for o, *_ in session.execute(md.public_query(include_unlisted=True, kind="figure", apply_hold=False))}
    out = set()
    for row in session.scalars(select(FigureRelease)):
        obj = session.get(md.ResearchObject, row.object_id)
        if obj.id in public and live_release(session, obj, apply_hold=False) is not None:
            out.add(row.bundle_sha256)
    return out


def purge_release_store(session: Session) -> list[str]:
    """Delete released bytes that no live release serves any more (withdrawal, retraction, rollback)."""
    root = release_store()
    if root is None:
        return []
    keep = servable_bundles(session)
    removed = []
    for child in sorted(root.iterdir()):
        if SHA.fullmatch(child.name) and child.name not in keep and child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
            removed.append(child.name)
    return removed
