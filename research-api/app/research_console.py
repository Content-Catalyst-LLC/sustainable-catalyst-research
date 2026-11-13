"""SC-SCI-02: the Research Console, the signed-in researcher's view of their own Research records.

Every route here takes the person from a Workspace-signed Sustainable Catalyst session (``user_sessions``); none accepts
the staging service credential or an owner the browser chooses. An editor reads another owner's records only with
that owner's Workspace editor attestation for the signed person (``editorial.resolve_actor``); imports, reproductions
and approvals are the owner's own.

Nothing is copied from Workspace. Imports go through the same signed grant -> Research fetch -> hash-verified export
path as the service ingest (``workspace_ingest``); the export's signed ``ownerScope`` must be the signed person, or
the import is rolled back. Figures render from the private candidate store, re-verified against the bound hashes on
every read. Article linking, review and publication stay with the editorial routes, which need Workspace-signed
approvals; the console never publishes.
"""
from __future__ import annotations

import base64
import json

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import convergence as cv
from . import editorial as ed
from . import figure_releases as fr
from . import object_registry as md
from . import user_sessions as us
from . import workspace_ingest as wi
from . import workspaces as ws

router = APIRouter(prefix="/api/research/v1/console", tags=["research-console"])
STATUS_SCHEMA = "sc-research-console-status/1.0"
OBJECT_SCHEMA = "sc-research-console-object/1.0"


def console_actor(
    request: Request,
    authorization: str | None = Header(default=None),
    x_sc_owner_scope: str | None = Header(default=None),
    x_sc_editor_attestation: str | None = Header(default=None),
) -> ed.Actor:
    person = us.signed_person(authorization)
    owner = (x_sc_owner_scope or "").strip() or person
    if len(owner) > 160:
        raise HTTPException(status_code=400, detail="X-SC-Owner-Scope is too long")
    attestation = None
    if x_sc_editor_attestation:
        try:
            attestation = json.loads(base64.urlsafe_b64decode(x_sc_editor_attestation + "=" * (-len(x_sc_editor_attestation) % 4)))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="X-SC-Editor-Attestation must be base64url JSON") from exc
    actor = cv._call(ed.resolve_actor, owner, person, attestation)
    if request.method not in ed.READ_METHODS:
        cv._call(ed.require_fresh_for_write, actor)
    return actor


def owner_only(actor: ed.Actor = Depends(console_actor)) -> ed.Actor:
    if actor.role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner imports, reproduces or approves their own results")
    return actor


def imports_enabled() -> bool:
    return us.session_state().enabled and wi.workspace_configured()


def import_gate(actor: ed.Actor = Depends(owner_only)) -> tuple[ed.Actor, wi.Keyring]:
    if not imports_enabled():
        raise HTTPException(status_code=503, detail="Workspace imports are not configured for this Research deployment")
    try:
        return actor, wi.load_public_key()
    except (RuntimeError, OSError) as exc:
        raise HTTPException(status_code=503, detail="Research cannot verify Workspace exports") from exc


# ---------------------------------------------------------------------------
# Reads


def projects(session: Session, owner: str) -> list[dict]:
    rows = session.execute(
        select(ws.ResearchProject, ws.ResearchArea)
        .join(ws.ResearchArea, ws.ResearchArea.id == ws.ResearchProject.area_id)
        .where(ws.ResearchProject.owner_scope == owner).order_by(ws.ResearchArea.slug, ws.ResearchProject.project_key)).all()
    articles = {a["slug"]: a for a in ed.owner_articles(session, owner)}
    out = []
    for project, area in rows:
        entries = []
        for entry in session.scalars(select(ws.ResearchEntry).where(ws.ResearchEntry.project_id == project.id)
                                     .order_by(ws.ResearchEntry.slug)).all():
            count = session.scalar(select(func.count(md.ResearchObject.id))
                                   .join(ws.EntryWorkspace, ws.EntryWorkspace.id == md.ResearchObject.workspace_id)
                                   .where(ws.EntryWorkspace.entry_id == entry.id))
            entries.append({"slug": entry.slug, "title": entry.title, "visibility": entry.visibility,
                            "objects": count or 0, "article": articles.get(entry.slug)})
        ref = project.external_workspace_ref or ""
        out.append({"area": area.slug, "projectKey": project.project_key,
                    "workspaceProjectId": ref.removeprefix("workspace-project:") if ref else None, "entries": entries})
    return out


def object_detail(session: Session, owner: str, object_id: str) -> dict:
    obj = cv._owned_object(session, owner, object_id)
    prov = cv._provenance(session, obj)
    rec = prov.record if prov else {}
    relations = [{"relation": r.predicate, "direction": "out" if r.subject_id == obj.id else "in",
                  "objectId": r.object_id if r.subject_id == obj.id else r.subject_id}
                 for r in session.scalars(select(md.ResearchObjectRelation).where(
                     (md.ResearchObjectRelation.subject_id == obj.id) | (md.ResearchObjectRelation.object_id == obj.id))
                     .order_by(md.ResearchObjectRelation.id)).all()]
    _, binding = fr.binding_of(session, obj)
    return {
        "schema": OBJECT_SCHEMA, "id": obj.id, "kind": obj.kind, "title": obj.title, "authority": obj.authority,
        "source": f"{obj.source_id}@{obj.source_version}", "artifactSha256": obj.source_sha256,
        "classification": obj.data_classification, "visibility": obj.visibility, "reviewState": obj.review_state,
        "provenance": rec or None, "provenanceSha256": prov.record_sha256 if prov else None,
        "subjectRef": cv.subject_ref(obj, prov) if prov else None,
        "verification": (rec.get("verification") or {}).get("status"),
        "reproduction": cv.reproduction_status(session, obj) if obj.kind in cv.REPRODUCTION_REQUIRED_KINDS else None,
        "claim": cv.notebook_claim_status(session, obj, rec) if rec.get("notebookCell") else None,
        "approvals": cv.approval_state(session, obj, signed_only=True) if prov else {},
        "approvalActions": cv.APPROVAL_ACTIONS,
        "pin": ed.current_pin(session, obj),
        "figure": {"bundleSha256": binding["bundleSha256"], "artifacts": len(binding["artifacts"])} if binding else None,
        "relations": relations,
    }


def figure_files(session: Session, owner: str, object_id: str) -> dict:
    obj = cv._owned_object(session, owner, object_id)
    _, binding = fr.binding_of(session, obj)
    if binding is None:
        raise LookupError("This object has no bound figure")
    root = fr.candidate_store()
    bundle, files, problems = fr.verified_files(root / binding["bundleSha256"] if root else None, binding)
    if problems:
        raise LookupError("Figure bytes are not available: " + "; ".join(problems))
    return {"bundleSha256": binding["bundleSha256"], "files": [
        {k: f[k] for k in ("role", "sha256", "mediaType", "bytes", "hashMode")} | {"contentBase64": base64.b64encode(f["data"]).decode()}
        for f in files]}


# ---------------------------------------------------------------------------
# Imports (the signed export names the owner; it must be the signed person)


def _owned_export(owner: str, fetch=None):
    """Fetch the export once, and refuse it before any write unless its signed owner is ``owner``."""
    cache: dict[str, dict] = {}

    def fetch_once(grant: str) -> dict:
        if grant not in cache:
            doc = (fetch or wi.http_fetch_export)(grant)
            if (doc or {}).get("ownerScope") != owner:
                raise PermissionError("This Workspace run belongs to another person")
            cache[grant] = doc
        return cache[grant]
    return fetch_once


def import_run(session: Session, *, owner: str, key, grant: str, area: str, entry: str, title: str,
               figure_files: object = None, fetch=None) -> dict:
    result = wi.ingest_workspace_run(session, key=key, grant=grant, area=area, entry=entry, title=title,
                                     fetch=_owned_export(owner, fetch), figure_files=figure_files)
    if result["ownerScope"] != owner:  # the signed export decides; checked again after verification
        raise PermissionError("This Workspace run belongs to another person")
    return result


def import_reproduction(session: Session, *, owner: str, key, original_id: str, grant: str,
                        rel_tol=None, abs_tol=None, fetch=None) -> dict:
    cv._owned_object(session, owner, original_id)
    return wi.ingest_reproduction(session, key=key, original_id=original_id, grant=grant, rel_tol=rel_tol,
                                  abs_tol=abs_tol, fetch=_owned_export(owner, fetch))


# ---------------------------------------------------------------------------
# HTTP


@router.get("/status")
def status_route():
    identity = us.public_status()
    return {"schema": STATUS_SCHEMA, "build": "SC-SCI-02", "identity": identity,
            "importsEnabled": imports_enabled(), "figureStore": fr.candidate_store() is not None,
            "serviceCredentialAccepted": False, "automaticPublication": False,
            "sourceOfTruth": {"identity": "workspace", "notebooks": "workspace", "datasets": "workspace",
                              "execution": "workspace", "figures": "lab-renderer", "publication": "research-editorial"}}


@router.get("/projects")
def projects_route(actor: ed.Actor = Depends(console_actor), session: Session = Depends(ws.get_session)):
    return {"owner": actor.owner, "actor": actor.scope, "role": actor.role, "items": projects(session, actor.owner)}


@router.get("/graph")
def graph_route(area: str = Query(default="", max_length=128), actor: ed.Actor = Depends(console_actor),
                session: Session = Depends(ws.get_session)):
    return cv.private_knowledge_graph(session, owner_scope=actor.owner, area_slug=area)


@router.get("/notebooks/{notebook_id}")
def notebook_route(notebook_id: str, actor: ed.Actor = Depends(console_actor), session: Session = Depends(ws.get_session)):
    return cv._call(wi.notebook_reference, session, owner_scope=actor.owner, notebook_id=notebook_id)


@router.get("/objects/{object_id}")
def object_route(object_id: str, actor: ed.Actor = Depends(console_actor), session: Session = Depends(ws.get_session)):
    return cv._call(object_detail, session, actor.owner, object_id)


@router.get("/objects/{object_id}/figure-files")
def figure_route(object_id: str, actor: ed.Actor = Depends(console_actor), session: Session = Depends(ws.get_session)):
    return cv._call(figure_files, session, actor.owner, object_id)


@router.post("/imports")
def import_route(body: dict = Body(...), gate=Depends(import_gate), session: Session = Depends(ws.get_session)):
    actor, key = gate
    return wi._run(session, import_run, owner=actor.owner, key=key, grant=str(body.get("grant") or ""),
                   area=str(body.get("researchArea") or ""), entry=str(body.get("entrySlug") or ""),
                   title=str(body.get("title") or ""), figure_files=body.get("figureFiles"))


@router.post("/objects/{object_id}/reproductions")
def reproduction_route(object_id: str, body: dict = Body(...), gate=Depends(import_gate),
                       session: Session = Depends(ws.get_session)):
    actor, key = gate
    return wi._run(session, import_reproduction, owner=actor.owner, key=key, original_id=object_id,
                   grant=str(body.get("grant") or ""), rel_tol=cv._optional_float(body.get("relTol")),
                   abs_tol=cv._optional_float(body.get("absTol")))


@router.post("/objects/{object_id}/approvals/{dimension}")
def approval_route(object_id: str, dimension: str, body: dict = Body(...), actor: ed.Actor = Depends(owner_only),
                   session: Session = Depends(ws.get_session)):
    """Record an approval Workspace's approval service signed; the console never creates one."""
    if "record" not in body:
        raise HTTPException(status_code=422, detail="A Workspace-signed approval record is required")
    row = cv._commit(session, cv.record_signed_approval, owner_scope=actor.owner, object_id=object_id,
                     dimension=dimension, record=body["record"])
    return {"dimension": row.dimension, "recordSha256": row.record_sha256, "signed": True}
