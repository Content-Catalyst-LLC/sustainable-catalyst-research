"""SC-FIG-02B / SC-FIG-03A — released scientific figures on the public Research API (read-only).

A public figure is a Research ``figure`` object whose exact revision Workspace approved in all four
signed dimensions (scientific, ownership, rights, publication) and which Research then released with
an article revision (``figure_releases``). This module only reads: it never approves, promotes,
releases or writes anything.

The figure's bytes are served from the read-only, content-addressed release store

    $RESEARCH_PUBLIC_FIGURE_STORE/<bundle sha256>/<file sha256>

that only Research's release step writes. Every request re-checks that:

* the object is still public (object, entry and area approved, not revoked);
* the article revision that released it is the live revision (published or unlisted: an unlisted
  article's figure is reachable by link but never listed);
* the stored bytes hash to the approved binding in the object's immutable provenance, view by view;
* the bundle still passes the disclosure policy (``release_errors``).

Anything that fails answers 404, and the reason stays server-side. The Lab renderer in the browser
verifies the hashes again before drawing.

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

from . import figure_releases as fr
from . import object_registry as md
from . import workspaces as ws

PREFIX = "/api/research/v1"
router = APIRouter(prefix=PREFIX, tags=["SC-FIG-03A public figures"])
SCHEMA = "sc-research-public-figure/2.0"
STATUS_SCHEMA = "sc-research-public-figure-status/1.0"
SHA = fr.SHA
APPROVAL_DIMENSIONS = ("scientific", "ownership", "rights", "publication")
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
    (re.compile(r"(?i)\bworkspace-(?:project|user|dataset|run|artifact|notebook):"), "a Workspace identifier"),
]


class NotServed(Exception):
    """The figure is not available publicly; ``reason`` stays server-side."""


def channel() -> str | None:
    return fr.channel()


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
    """Disclosure policy for a bundle whose exact bytes Workspace already approved.

    Release authority is the four signed Workspace approvals of the binding, checked by Research.
    The producer's own ``review.*`` and artifact ``status`` fields are its notes and are not read.
    Accessibility: the approvers sign off the caption and alt text in these exact bytes; Research
    requires both and the renderer adds an accessible data table.
    """
    errors = []
    project, publication = bundle.get("project") or {}, bundle.get("publication") or {}
    if project.get("projectRef") != "research-entry:" + article:
        errors.append("projectRef must bind the bundle to its public Research article")
    rendering = bundle.get("rendering") or {}
    if not str(rendering.get("altText") or "").strip() or not str(rendering.get("caption") or "").strip():
        errors.append("caption and alt text are required")
    if release_channel == "staging-synthetic":
        if (project.get("ownerScope"), project.get("environment"), publication.get("bindingStatus")) != \
                ("isolated-synthetic", "staging", "synthetic-test"):
            errors.append("the staging channel serves synthetic test bundles only")
    if bundle.get("figureType") == "scene-3d" or "scene" in bundle or \
            any(str(a.get("format") or "") == "glb" for a in bundle.get("artifacts") or [] if isinstance(a, dict)):
        errors.append(fr.SCENE_HOLD)
    if any(k in bundle for k in ("dimensions", "dependentFields", "viewProjection")):
        errors.append(fr.ND_HOLD)
    errors.extend(private_content(bundle))
    return errors


def load_release(session: Session, public_key: str) -> dict:
    """Return the verified public package for one released figure object, or raise NotServed."""
    release_channel, root = channel(), store()
    if release_channel is None or root is None:
        raise NotServed("public figures are not enabled")
    if not md.UUID.fullmatch(public_key):
        raise NotServed("invalid key")
    # A direct link is not discovery: figures of unlisted articles stay reachable by key.
    row = session.execute(md.public_query(kind="figure", include_unlisted=True)
                          .where(md.ResearchObject.public_key == public_key)).first()
    if row is None:
        raise NotServed("not an approved public figure")
    obj, _entry, area = row
    prov, binding = fr.binding_of(session, obj)
    if binding is None or binding["bundleSha256"] != obj.source_sha256:
        raise NotServed("no approved figure binding for this revision")
    live = fr.live_release(session, obj)
    if live is None:
        raise NotServed("not released by a live article revision")
    release, article = live
    if release.provenance_sha256 != prov.record_sha256:
        raise NotServed("the release names a different provenance revision")
    if release_channel == "staging-synthetic" and obj.data_classification != "synthetic":
        raise NotServed("the staging channel serves synthetic figures only")
    bundle, files, errors = fr.verified_files(root / binding["bundleSha256"], binding)
    if bundle is None:
        raise NotServed("; ".join(errors))
    errors += release_errors(bundle, article=article, release_channel=release_channel)
    errors += fr.source_rights_errors(session, md._workspace_owner(session, obj.workspace_id), binding)
    for f in files[1:]:
        if f["mediaType"].endswith("json") or f["hashMode"] == "canonical-json":
            errors.extend(private_content(json.loads(f["data"].decode("utf-8"))))
    if errors:
        raise NotServed("; ".join(errors))
    return {"object": obj, "article": article, "area": area, "bundle": bundle, "bundleSha256": binding["bundleSha256"],
            "channel": release_channel, "files": files, "release": release, "provenanceSha256": prov.record_sha256}


def public_package(release: dict) -> dict:
    """Explicit public projection: no database ids, owner scopes, store paths or approval records."""
    obj, b, rel = release["object"], release["bundle"], release["release"]
    base = f"{PREFIX}/objects/{obj.public_key}/figure/files/"
    citations = [{k: s[k] for k in ("sourceId", "provider", "doi", "citationId", "identityStatus", "qualityStatus", "licenceStatus")
                  if isinstance(s.get(k), str)} for s in b["sourceRefs"]]
    label = CHANNEL_LABEL[release["channel"]]
    released_at = rel.created_at.isoformat() if rel.created_at else None
    return {
        "schema": SCHEMA, "id": obj.public_key, "article": release["article"], "area": release["area"],
        "title": obj.title, "figureId": b["figureId"], "version": b.get("version", 1),
        "evidenceClass": b["evidenceClass"], "measurementStatus": b["scientificContext"]["measurementStatus"],
        "dataClassification": obj.data_classification, "classificationNotice": NOTICE.get(b["evidenceClass"]),
        "release": {"channel": release["channel"], "label": label, "authority": "sc-research",
                    "bundleSha256": release["bundleSha256"], "provenanceSha256": release["provenanceSha256"],
                    "registryVersion": obj.source_version, "articleRevision": rel.revision_number,
                    "releasedAt": released_at, "releaseSha256": rel.record_sha256,
                    "approvals": {"dimensions": list(APPROVAL_DIMENSIONS), "authority": "Sustainable Catalyst Workspace",
                                  "signed": True, "boundTo": "provenanceSha256"},
                    "approval": "Approved in Workspace (scientific, ownership, rights and publication; signed and bound to "
                                "this exact revision) and released by Sustainable Catalyst Research.",
                    "reproducibility": b["provenance"].get("reproducibility")},
        # What the Lab renderer shows as release state. Lab renders; Research is the release authority.
        "releaseContext": {"authority": "sc-research", "state": "released", "label": label,
                           "bundleSha256": release["bundleSha256"], "articleRevision": rel.revision_number,
                           "releasedAt": released_at},
        "units": b["scientificContext"].get("units"),
        "uncertainty": b["scientificContext"].get("uncertaintyNotation"),
        "citations": citations,
        "files": [{"role": f["role"], "sha256": f["sha256"], "mediaType": f["mediaType"], "bytes": f["bytes"],
                   "url": base + f["sha256"]} for f in release["files"]],
        "missingViews": [],
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
