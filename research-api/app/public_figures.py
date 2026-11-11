"""SC-FIG-02B — approved scientific figures on the public Research API (read-only).

A public figure is an existing Research ``figure`` object (``figure_objects``) that the governed
approval flow has already made public at every level: the object, its article and its area are
``public-approved`` and rights-cleared. Workspace keeps publication authority; this module only
reads the result and never approves, promotes or writes anything.

The figure's bytes are served from a read-only, content-addressed release store:

    $RESEARCH_PUBLIC_FIGURE_STORE/<bundle sha256>/<file sha256>

holding the exact ``sc.figure-bundle.v1`` bytes and the view files the bundle references. Every
request re-checks that the bundle bytes hash to the object's registered ``source_sha256`` (the exact
approved revision), that each view file matches its declared artifact hash, and that the bundle is a
public release (reviews recorded, article binding, no private identifiers, hosts, paths or
credentials). Anything that fails is not served. The Lab renderer in the browser verifies the same
hashes again before drawing.

``RESEARCH_PUBLIC_FIGURES`` selects the release channel. Only ``staging-synthetic`` exists so far: it
serves synthetic bundles only and labels them as such. Unset, ``off`` or any other value (including
``production``, which is not yet authorized) serves nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from . import figure_objects as fo
from . import object_registry as md
from . import workspaces as ws

PREFIX = "/api/research/v1"
router = APIRouter(prefix=PREFIX, tags=["SC-FIG-02B public figures"])
SCHEMA = "sc-research-public-figure/1.0"
STATUS_SCHEMA = "sc-research-public-figure-status/1.0"
CHANNELS = frozenset({"staging-synthetic"})
SHA = re.compile(r"^[0-9a-f]{64}$")
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_FILES = 12  # the Lab renderer's per-package limit, bundle included
APPROVED_REVIEW = {"scientific": {"approved"}, "rights": {"approved"}, "sourceIntegrity": {"approved"},
                   "publication": {"approved"}, "visual": {"approved", "not-applicable"}}
NOTICE = {
    "modelled": "Modelled output computed from specified inputs. Not a measurement.",
    "hypothetical": "Hypothetical scenario. Not a measurement.",
    "conceptual": "Conceptual illustration. Not a measurement.",
    "literature-derived": "Values derived from published literature. Not new measurements.",
    "status-only": "Status display only. Not a measurement.",
}
CHANNEL_LABEL = {"staging-synthetic": "Synthetic staging release. Not published research."}
# Strings that must never appear in a public bundle or JSON view. The publication approval covers the
# exact bytes, so these are refused rather than redacted (redaction would break the hash binding).
_PRIVATE = [
    (re.compile(r"(?i)\b(?:https?|ftp|file|sftp|ssh)://(?!doi\.org/|vega\.github\.io/schema/)"), "a link other than doi.org"),
    (re.compile(r"(?i)\bfile:"), "a file: reference"),
    (re.compile(r"(?:^|[\s\"'(=])(?:/(?:Users|home|root|srv|var|opt|etc|tmp|private|mnt|Volumes)/|[A-Za-z]:\\)"), "a filesystem path"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "an email address"),
    (re.compile(r"(?<![\d.])(?:[1-9]\d{0,2})(?:\.\d{1,3}){3}(?![\d.])"), "an IP address"),
    (re.compile(r"(?i)\blocalhost\b|\.local\b|\.internal\b"), "an internal host name"),
    (re.compile(r"(?i)password|passwd|secret|api[_-]?key|bearer\s|private key|BEGIN [A-Z ]+KEY"), "credential-like text"),
    (re.compile(r"(?i)\bworkspace-project:"), "a Workspace project identifier"),
]


class NotServed(Exception):
    """The figure is not available publicly; ``reason`` stays server-side."""


def channel() -> str | None:
    value = os.getenv("RESEARCH_PUBLIC_FIGURES", "off").strip().lower()
    return value if value in CHANNELS else None


def store() -> Path | None:
    raw = os.getenv("RESEARCH_PUBLIC_FIGURE_STORE", "").strip()
    path = Path(raw) if raw else None
    return path if path is not None and path.is_dir() else None


def canonical_sha256(value) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strings(value, path=""):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(k, path + "{key}")
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _strings(v, f"{path}[{i}]")


def private_content(value) -> list[str]:
    found = []
    for path, text in _strings(value):
        for pattern, what in _PRIVATE:
            if pattern.search(text):
                found.append(f"{what} at {path or '$'}")
    return found


def release_errors(bundle: dict, *, article: str, release_channel: str) -> list[str]:
    """Checks that make a registered, approved figure bundle servable on the public site."""
    errors = []
    project, publication = bundle.get("project") or {}, bundle.get("publication") or {}
    if project.get("projectRef") != "research-entry:" + article:
        errors.append("projectRef must bind the bundle to its public Research article")
    review = bundle.get("review") or {}
    for dimension, allowed in APPROVED_REVIEW.items():
        if review.get(dimension) not in allowed:
            errors.append(f"review.{dimension} is {review.get(dimension)!r}")
    rendering = bundle.get("rendering") or {}
    if rendering.get("accessibilityReview") != "passed":
        errors.append("accessibility review has not passed")
    if not str(rendering.get("altText") or "").strip() or not str(rendering.get("caption") or "").strip():
        errors.append("caption and alt text are required")
    if release_channel == "staging-synthetic":
        if (project.get("ownerScope"), project.get("environment"), publication.get("bindingStatus")) != \
                ("isolated-synthetic", "staging", "synthetic-test"):
            errors.append("the staging channel serves synthetic test bundles only")
        if any(a.get("status") != "staging-verified" for a in bundle.get("artifacts") or []):
            errors.append("every artifact must be staging-verified")
    errors.extend(private_content(bundle))
    return errors


def _read(directory: Path, digest: str) -> bytes | None:
    path = directory / digest
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        return None
    return path.read_bytes()


def _artifact_matches(data: bytes, digest: str) -> str | None:
    if hashlib.sha256(data).hexdigest() == digest:
        return "raw-bytes"
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return "canonical-json" if canonical_sha256(value) == digest else None


def load_release(session: Session, public_key: str) -> dict:
    """Return the verified public package for one approved figure object, or raise NotServed."""
    release_channel, root = channel(), store()
    if release_channel is None or root is None:
        raise NotServed("public figures are not enabled")
    if not md.UUID.fullmatch(public_key):
        raise NotServed("invalid key")
    row = session.execute(md.public_query(kind="figure").where(md.ResearchObject.public_key == public_key)).first()
    if row is None:
        raise NotServed("not an approved public figure")
    obj, article, area = row
    if not SHA.fullmatch(obj.source_sha256 or ""):
        raise NotServed("no registered bundle hash")
    directory = root / obj.source_sha256
    raw = _read(directory, obj.source_sha256) if directory.is_dir() and not directory.is_symlink() else None
    if raw is None or hashlib.sha256(raw).hexdigest() != obj.source_sha256:
        raise NotServed("release store does not hold the approved bundle bytes")
    try:
        bundle, digest = fo.parse_bundle(raw)
    except ValueError as exc:
        raise NotServed(str(exc)) from None
    version = bundle.get("version", 1)
    identity = (bundle["provenance"]["authorityOwner"], "figure:" + bundle["figureId"], f"v{version}+{digest[:16]}")
    if identity != (obj.authority, obj.source_id, obj.source_version):
        raise NotServed("bundle identity differs from the registered figure revision")
    if release_channel == "staging-synthetic" and obj.data_classification != "synthetic":
        raise NotServed("the staging channel serves synthetic figures only")
    errors = release_errors(bundle, article=article, release_channel=release_channel)
    files, missing = [{"role": "bundle", "sha256": digest, "mediaType": "application/json", "bytes": len(raw), "data": raw}], []
    for artifact in bundle["artifacts"]:
        sha = str(artifact.get("sha256") or "")
        data = _read(directory, sha) if SHA.fullmatch(sha) else None
        mode = _artifact_matches(data, sha) if data is not None else None
        if mode is None:
            missing.append(sha)
            continue
        if any(f["sha256"] == sha for f in files):
            continue
        if mode == "canonical-json" or artifact.get("mediaType", "").endswith("json"):
            errors.extend(private_content(json.loads(data.decode("utf-8"))))
        files.append({"role": "view", "sha256": sha, "mediaType": artifact.get("mediaType"), "bytes": len(data),
                      "hashMode": mode, "data": data})
    if len(files) > MAX_FILES:
        errors.append("too many files for one figure package")
    if len(files) == 1:
        errors.append("no verified view file in the release store")
    if errors:
        raise NotServed("; ".join(errors))
    return {"object": obj, "article": article, "area": area, "bundle": bundle, "bundleSha256": digest,
            "channel": release_channel, "files": files, "missing": missing}


def public_package(release: dict) -> dict:
    """Explicit public projection: no database ids, owner scopes, store paths or approval records."""
    obj, b = release["object"], release["bundle"]
    base = f"{PREFIX}/objects/{obj.public_key}/figure/files/"
    citations = [{k: s[k] for k in ("sourceId", "provider", "doi", "citationId", "identityStatus", "qualityStatus", "licenceStatus")
                  if isinstance(s.get(k), str)} for s in b["sourceRefs"]]
    return {
        "schema": SCHEMA, "id": obj.public_key, "article": release["article"], "area": release["area"],
        "title": obj.title, "figureId": b["figureId"], "version": b.get("version", 1),
        "evidenceClass": b["evidenceClass"], "measurementStatus": b["scientificContext"]["measurementStatus"],
        "dataClassification": obj.data_classification, "classificationNotice": NOTICE.get(b["evidenceClass"]),
        "release": {"channel": release["channel"], "label": CHANNEL_LABEL[release["channel"]],
                    "bundleSha256": release["bundleSha256"], "registryVersion": obj.source_version,
                    "approval": "Approved for public viewing in Research under Workspace publication authority.",
                    "reproducibility": b["provenance"]["reproducibility"]},
        "units": b["scientificContext"].get("units"),
        "uncertainty": b["scientificContext"].get("uncertaintyNotation"),
        "citations": citations,
        "files": [{"role": f["role"], "sha256": f["sha256"], "mediaType": f["mediaType"], "bytes": f["bytes"],
                   "url": base + f["sha256"]} for f in release["files"]],
        "missingViews": release["missing"],
    }


def _not_found():
    return HTTPException(status_code=404, detail="Research figure not found")


@router.get("/public/figures/status")
def figures_status():
    release_channel = channel()
    return {"schema": STATUS_SCHEMA, "enabled": release_channel is not None and store() is not None,
            "channel": release_channel, "label": CHANNEL_LABEL.get(release_channel or "", None)}


@router.get("/objects/{public_key}/figure")
def figure_route(public_key: str, session: Session = Depends(ws.get_session)):
    try:
        return public_package(load_release(session, public_key))
    except NotServed:
        raise _not_found() from None


@router.get("/objects/{public_key}/figure/files/{sha256}")
def figure_file_route(public_key: str, sha256: str, session: Session = Depends(ws.get_session)):
    if not SHA.fullmatch(sha256):
        raise _not_found()
    try:
        release = load_release(session, public_key)
    except NotServed:
        raise _not_found() from None
    for f in release["files"]:
        if f["sha256"] == sha256:
            # Bytes for the renderer only: never rendered by the browser as a document.
            return Response(f["data"], media_type="application/octet-stream", headers={
                "Content-Disposition": "attachment", "Content-Security-Policy": "default-src 'none'; sandbox",
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
    raise _not_found()
