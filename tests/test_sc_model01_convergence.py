"""SC-MODEL-01 Research convergence: synthetic fixtures only (public CI)."""
import sys
from uuid import uuid4
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research-api"))

from app.main import app
from app import convergence as cv
from app import object_registry as md
from app import workspaces as ws

OWNER = "workspace-user:synthetic-owner"
H = lambda c: c * 64  # noqa: E731


@pytest.fixture
def session(tmp_path):
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "cv.sqlite"))
    ws.Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def runtime(language, solver, fp):
    return {"language": language, "service": f"workspace-{language}-runtime", "version": "2.13.0",
            "solver": solver, "environmentFingerprint": fp}


def computed(classification, values=None, *, lang="r", solver="qr", fp=H("e"), receipt="rcpt-1"):
    rec = {
        "sourceRefs": [{"authority": "library", "sourceId": "syn-src"}],
        "scientificClassification": classification,
        "uncertainty": {"status": "standard-error", "values": {"slope": 0.01}},
        "artifactSha256": H("b"), "verification": {"status": "executed"},
        "reviewStatus": "unreviewed", "runtime": runtime(lang, solver, fp),
        "codeVersion": "runner.R@sha256:" + H("c"), "parameterSha256": H("d"),
        "executionReceipt": {"receiptId": receipt, "sha256": H("f")},
        "units": {"slope": "mmol/kg per t/ha"}, "limitations": ["Synthetic fixture."],
    }
    if values is not None:
        rec["resultValues"] = values
    return rec


def bundle(*, area="metrics-of-sustainable-development", entry="syn-study-a", result_class="synthetic",
           values=None, extra_relations=(), dataset_class="synthetic", result_version="v1"):
    plain = {"sourceRefs": [{"authority": "library", "sourceId": "syn-src"}],
             "uncertainty": {"status": "not-quantified", "reason": "fixture"},
             "artifactSha256": H("a"), "verification": {"status": "hash-verified"}, "reviewStatus": "unreviewed"}
    return cv.seal_bundle({
        "schema": cv.BUNDLE_SCHEMA,
        "project": {"researchArea": area, "projectKey": "syn-project", "entrySlug": entry,
                    "title": "Synthetic study", "workspaceProjectRef": "workspace-project:syn-1"},
        "objects": [
            {"localId": "q", "authority": "research", "sourceId": "syn-question", "sourceVersion": "v1",
             "kind": "research-question", "title": "Does dose change flux?", "dataClassification": "illustrative",
             "provenance": {**plain, "sourceRefs": [], "scientificClassification": "illustrative"}},
            {"localId": "ds", "authority": "library", "sourceId": "syn-dataset", "sourceVersion": "v1",
             "kind": "dataset", "title": "Synthetic dataset", "dataClassification": dataset_class,
             "provenance": {**plain, "scientificClassification": dataset_class}},
            {"localId": "res", "authority": "workspace", "sourceId": "syn-ols", "sourceVersion": result_version,
             "kind": "result", "title": "OLS fit", "dataClassification": result_class,
             "provenance": computed(result_class, values if values is not None else {"intercept": 1.5, "slope": 0.25})},
            {"localId": "fig", "authority": "workbench", "sourceId": "syn-fig", "sourceVersion": "v1",
             "kind": "figure", "title": "Fit figure", "dataClassification": result_class,
             "provenance": computed(result_class, lang="python", solver="plotly", fp=H("1"), receipt="fig-1")},
        ],
        "relations": [
            {"subject": "res", "predicate": "uses-dataset", "object": "ds"},
            {"subject": "res", "predicate": "answers", "object": "q"},
            {"subject": "fig", "predicate": "visualizes", "object": "res"},
            *extra_relations,
        ],
    })


def ingest(session, b=None, owner=OWNER):
    out = cv.ingest_bundle(session, owner_scope=owner, bundle=b or bundle())
    session.flush()
    return out


def julia_reproduction(values=None, fp=H("9")):
    return {"authority": "workspace", "sourceId": "syn-ols-julia", "sourceVersion": "v1",
            "provenance": computed("synthetic", values or {"intercept": 1.5, "slope": 0.25},
                                   lang="julia", solver="cholesky", fp=fp, receipt="rcpt-julia")}


def governance(subject, action, approver="workspace-user:reviewer", requester=OWNER):
    approval = {"schema": cv.WS_APPROVAL_SCHEMA, "approvalId": "approval_" + action, "action": action,
                "subjectRef": subject, "status": "approved", "decisionRef": "decision_" + action,
                "requester": {"actorId": requester, "actorType": "human"}}
    decision = {"schema": cv.WS_DECISION_SCHEMA, "decisionId": "decision_" + action,
                "approvalId": "approval_" + action, "decision": "approve",
                "actor": {"actorId": approver, "actorType": "human", "role": "research_owner"}}
    return approval, decision


def signed_record(subject, dimension, owner=OWNER, approver="workspace-user:reviewer", requester=None, key=None):
    """Shaped exactly like Workspace's sc-workspace-research-approval-record/1.0 (signed by a test key)."""
    from tests.test_sc_model01_workspace_ingest import KEY, sign
    a, d = governance(subject, cv.APPROVAL_ACTIONS.get(dimension, "research_area_publication"),
                      approver=approver, requester=requester or owner)
    # Workspace issues a fresh approval/decision id for every request.
    unique = uuid4().hex[:12]
    a["approvalId"] = d["approvalId"] = f"approval_{unique}"
    a["decisionRef"] = d["decisionId"] = f"decision_{unique}"
    return sign(cv.WS_APPROVAL_RECORD_SCHEMA, {
        "schema": cv.WS_APPROVAL_RECORD_SCHEMA, "issuer": "sustainable-catalyst-workspace", "audience": "sc-research",
        "ownerScope": owner, "projectId": "proj-1", "dimension": dimension, "action": a["action"],
        "subjectRef": subject, "grantId": "rag_test", "authentication": {"requester": "test", "decider": "test"},
        "approval": a, "decision": d, "issuedAt": "2026-11-10T00:00:00+00:00"}, key or KEY)


def approve_all(session, oid):
    obj = session.get(md.ResearchObject, oid)
    subject = cv.subject_ref(obj, cv._provenance(session, obj))
    for dim, action in cv.APPROVAL_ACTIONS.items():
        a, d = governance(subject, action)
        cv.record_workspace_approval(session, owner_scope=OWNER, object_id=oid, dimension=dim, approval=a, decision=d)


def test_bundle_is_private_hash_verified_and_idempotent(session):
    out = ingest(session)
    assert out["visibility"] == "private" and out["publicationPerformed"] is False
    assert {o["visibility"] for o in out["objects"].values()} == {"private"}
    again = ingest(session)
    assert again["objects"]["res"]["id"] == out["objects"]["res"]["id"]
    tampered = bundle()
    tampered["objects"][2]["title"] = "changed"
    with pytest.raises(ValueError, match="manifest hash"):
        ingest(session, tampered)


def test_revisions_are_immutable(session):
    ingest(session)
    with pytest.raises(ValueError):
        ingest(session, bundle(values={"intercept": 9.0, "slope": 9.0}))
    out = ingest(session, bundle(values={"intercept": 9.0, "slope": 9.0}, result_version="v2"))
    assert out["objects"]["res"]["provenanceSha256"]


def test_provenance_fields_are_required(session):
    b = bundle()
    del b["objects"][2]["provenance"]["executionReceipt"]
    with pytest.raises(ValueError, match="executionReceipt"):
        ingest(session, cv.seal_bundle(b))
    b = bundle()
    b["objects"][2]["provenance"]["scientificClassification"] = "observed"
    with pytest.raises(ValueError, match="classification"):
        ingest(session, cv.seal_bundle(b))


def test_modelled_or_literature_values_cannot_become_observed(session):
    b = bundle(result_class="observed", dataset_class="literature-derived")
    with pytest.raises(ValueError, match="cannot be 'observed'"):
        ingest(session, b)


def test_project_ownership_stays_with_workspace_owner(session):
    ingest(session)
    with pytest.raises(PermissionError):
        ingest(session, owner="workspace-user:other")
    b = bundle()
    b["project"]["workspaceProjectRef"] = "workspace-project:different"
    with pytest.raises(PermissionError):
        ingest(session, cv.seal_bundle(b))


def test_independent_reproduction_is_computed_by_research(session):
    out = ingest(session)
    rid = out["objects"]["res"]["id"]
    rep = julia_reproduction({"intercept": 1.5 + 1e-13, "slope": 0.25})
    rep["provenance"]["verification"] = {"status": "reproduced-by-client-claim"}
    v = cv.record_reproduction(session, owner_scope=OWNER, original_id=rid, reproduction=rep)
    # Numbers agree, but a generic bundle cannot attest its inputs: never a verified reproduction.
    assert v["status"] == "reproduced-unverified-inputs" and v["numericStatus"] == "within-tolerance"
    assert v["independence"] == "independent-implementation" and v["inputs"]["status"] == "unverified"
    assert cv.reproduction_status(session, session.get(md.ResearchObject, rid)) == "reproduced-unverified-inputs"


def test_failed_and_impossible_reproductions_are_recorded(session):
    rid = ingest(session)["objects"]["res"]["id"]
    bad = julia_reproduction({"intercept": 1.6, "slope": 0.25})
    assert cv.record_reproduction(session, owner_scope=OWNER, original_id=rid, reproduction=bad)["status"] == "not-reproduced"
    none = julia_reproduction()
    none["sourceId"] = "syn-ols-none"
    del none["provenance"]["resultValues"]
    assert cv.record_reproduction(session, owner_scope=OWNER, original_id=rid, reproduction=none)["status"] == "not-reproducible"
    same = julia_reproduction(fp=H("e"))
    same["provenance"]["executionReceipt"]["receiptId"] = "rcpt-1"
    with pytest.raises(ValueError, match="separate execution"):
        cv.record_reproduction(session, owner_scope=OWNER, original_id=rid, reproduction=same)


def test_publication_requires_four_separate_workspace_approvals_and_reproduction(session):
    rid = ingest(session)["objects"]["res"]["id"]
    obj = session.get(md.ResearchObject, rid)
    subject = cv.subject_ref(obj, cv._provenance(session, obj))
    a, d = governance(subject, "research_scientific_review", approver=OWNER)
    with pytest.raises(ValueError, match="Self-approval"):
        cv.record_workspace_approval(session, owner_scope=OWNER, object_id=rid, dimension="scientific", approval=a, decision=d)
    a, d = governance(subject, "research_publication")
    with pytest.raises(ValueError, match="action"):
        cv.record_workspace_approval(session, owner_scope=OWNER, object_id=rid, dimension="scientific", approval=a, decision=d)
    a, d = governance("research-object:x#" + H("0"), "research_scientific_review")
    with pytest.raises(ValueError, match="provenance revision"):
        cv.record_workspace_approval(session, owner_scope=OWNER, object_id=rid, dimension="scientific", approval=a, decision=d)
    a, d = governance(subject, "research_scientific_review")
    cv.record_workspace_approval(session, owner_scope=OWNER, object_id=rid, dimension="scientific", approval=a, decision=d)
    with pytest.raises(PermissionError, match="rights, ownership, publication"):
        cv.promote_public_projection(session, owner_scope=OWNER, object_id=rid)
    for dim in ("rights", "ownership", "publication"):
        a, d = governance(subject, cv.APPROVAL_ACTIONS[dim])
        cv.record_workspace_approval(session, owner_scope=OWNER, object_id=rid, dimension=dim, approval=a, decision=d)
    with pytest.raises(PermissionError, match="Input lineage is not verified"):
        cv.promote_public_projection(session, owner_scope=OWNER, object_id=rid)
    cv.record_reproduction(session, owner_scope=OWNER, original_id=rid, reproduction=julia_reproduction())
    # Results from generic bundles carry no attested input lineage, so they can never be published.
    with pytest.raises(PermissionError, match="Input lineage is not verified"):
        cv.promote_public_projection(session, owner_scope=OWNER, object_id=rid)


def test_followup_request_goes_through_workspace_without_dispatch(session):
    out = ingest(session)
    req = cv.build_followup_request(session, owner_scope=OWNER, object_ids=[out["objects"]["res"]["id"]],
                                    destination="decision-studio", intent="compare")
    plan = req["workspacePlanRequest"]
    assert plan["projectRef"] == "workspace-project:syn-1" and plan["sourceProduct"] == "workspace"
    assert req["dispatchPerformed"] is False and req["executionPerformedByResearch"] is False
    with pytest.raises(LookupError):
        cv.build_followup_request(session, owner_scope="workspace-user:other",
                                  object_ids=[out["objects"]["res"]["id"]], destination="workbench", intent="analyze")
    with pytest.raises(ValueError):
        cv.build_followup_request(session, owner_scope=OWNER, object_ids=[out["objects"]["res"]["id"]],
                                  destination="research", intent="publish")


def test_area_graph_accumulates_studies_privately(session):
    ingest(session)
    b = bundle(entry="syn-study-b")
    b["project"]["projectKey"] = "syn-project"
    for o in b["objects"]:
        o["sourceId"] += "-b"
    ingest(session, cv.seal_bundle(b))
    ingest(session, bundle(area="example-site-enhanced-rock-weathering", entry="syn-example-site"))
    g = cv.private_knowledge_graph(session, owner_scope=OWNER, area_slug="metrics-of-sustainable-development")
    assert {n["entry"] for n in g["nodes"]} == {"syn-study-a", "syn-study-b"}
    assert len(g["nodes"]) == 8 and len(g["edges"]) == 6
    res = next(n for n in g["nodes"] if n["kind"] == "result")
    assert res["runtime"]["language"] == "r" and res["reproduction"] == "not-attempted"
    assert cv.private_knowledge_graph(session, owner_scope="workspace-user:other")["nodes"] == []


def test_http_writes_disabled_by_default_and_in_production(monkeypatch, tmp_path):
    client = TestClient(app)
    assert client.get("/api/research/v1/convergence/status").json()["privateWritesEnabled"] is False
    assert client.post("/api/research/v1/convergence/bundles", json={}).status_code == 503
    token = tmp_path / "t"
    token.write_text("staging-secret")
    monkeypatch.setenv("RESEARCH_CONVERGENCE_TOKEN_FILE", str(token))
    monkeypatch.setenv("RESEARCH_CONVERGENCE_WRITES", "staging-service-token")
    monkeypatch.setenv("RESEARCH_ENV", "production")
    assert client.post("/api/research/v1/convergence/bundles", json={}).status_code == 503
    monkeypatch.setenv("RESEARCH_ENV", "staging")
    assert client.post("/api/research/v1/convergence/bundles", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/research/v1/convergence/graph", headers={"Authorization": "Bearer staging-secret"}).status_code == 400


def test_http_staging_round_trip_and_public_provenance(monkeypatch, tmp_path):
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "http.sqlite"))
    ws.Base.metadata.create_all(engine)
    monkeypatch.setattr(ws, "_session_factory", lambda: Session(engine, expire_on_commit=False))
    token = tmp_path / "t"
    token.write_text("staging-secret")
    monkeypatch.setenv("RESEARCH_CONVERGENCE_TOKEN_FILE", str(token))
    monkeypatch.setenv("RESEARCH_CONVERGENCE_WRITES", "staging-service-token")
    monkeypatch.setenv("RESEARCH_ENV", "staging")
    hdr = {"Authorization": "Bearer staging-secret", "X-SC-Owner-Scope": OWNER}
    from tests.test_sc_model01_workspace_ingest import OTHER, PUB, pub_file
    monkeypatch.setenv("RESEARCH_WORKSPACE_PUBLIC_KEY_FILE", pub_file(tmp_path, PUB))
    client = TestClient(app)
    out = client.post("/api/research/v1/convergence/bundles", json=bundle(), headers=hdr)
    assert out.status_code == 200, out.text
    rid = out.json()["objects"]["res"]["id"]
    rep = client.post(f"/api/research/v1/convergence/objects/{rid}/reproductions",
                      json={"reproduction": julia_reproduction()}, headers=hdr)
    assert rep.json()["status"] == "reproduced-unverified-inputs"
    fid = out.json()["objects"]["fig"]["id"]
    did = out.json()["objects"]["ds"]["id"]
    for oid in (rid, fid, did):
        subject = client.get(f"/api/research/v1/convergence/objects/{oid}/subject-ref", headers=hdr).json()["subjectRef"]
        assert client.post(f"/api/research/v1/convergence/objects/{oid}/public-projection", headers=hdr).status_code == 403
        a, d = governance(subject, cv.APPROVAL_ACTIONS["scientific"])
        # Unsigned (simulated) governance payloads are refused over HTTP.
        r = client.post(f"/api/research/v1/convergence/objects/{oid}/approvals/scientific",
                        json={"approval": a, "decision": d}, headers=hdr)
        assert r.status_code == 422 and "signed" in r.text
        forged = signed_record(subject, "scientific", key=OTHER)
        assert client.post(f"/api/research/v1/convergence/objects/{oid}/approvals/scientific",
                           json={"record": forged}, headers=hdr).status_code == 422
        foreign = signed_record(subject, "scientific", owner="workspace-user:someone-else")
        assert client.post(f"/api/research/v1/convergence/objects/{oid}/approvals/scientific",
                           json={"record": foreign}, headers=hdr).status_code == 403
        for dim in cv.APPROVAL_ACTIONS:
            r = client.post(f"/api/research/v1/convergence/objects/{oid}/approvals/{dim}",
                            json={"record": signed_record(subject, dim)}, headers=hdr)
            assert r.status_code == 200 and r.json()["signed"] is True, r.text
    # The result has no attested input lineage, so even four approvals cannot publish it.
    refused = client.post(f"/api/research/v1/convergence/objects/{rid}/public-projection", headers=hdr)
    assert refused.status_code == 403 and "Input lineage" in refused.text
    # SC-FIG-03A: a figure without a verified binding (bytes checked against a signed export) is never public.
    unbound = client.post(f"/api/research/v1/convergence/objects/{fid}/public-projection", headers=hdr)
    assert unbound.status_code == 403 and "figure binding" in unbound.text
    forged = bundle(entry="syn-study-forged")
    forged["objects"][3]["provenance"]["figure"] = {"schema": "sc-research-figure-binding/1.0", "bundleSha256": H("a")}
    forged.pop("manifestSha256", None)
    r = client.post("/api/research/v1/convergence/bundles", json=cv.seal_bundle(forged), headers=hdr)
    assert r.status_code == 422 and "figure binding" in r.text
    pub = client.post(f"/api/research/v1/convergence/objects/{did}/public-projection", headers=hdr).json()
    key = pub["publicKey"]
    # Object approved, but its Research entry/area are not: still not public.
    assert client.get(f"/api/research/v1/objects/{key}/provenance").status_code == 404
    with Session(engine) as s:
        entry = s.get(ws.ResearchEntry, s.get(ws.EntryWorkspace, s.get(md.ResearchObject, did).workspace_id).entry_id)
        entry.visibility, entry.rights_cleared = ws.APPROVED, True
        s.get(ws.ResearchArea, s.get(ws.ResearchProject, entry.project_id).area_id).visibility = ws.APPROVED
        s.commit()
    prov = client.get(f"/api/research/v1/objects/{key}/provenance").json()
    assert prov["scientificClassification"] == "synthetic" and prov["kind"] == "dataset" and prov["figure"] is None
    text = str(prov)
    assert OWNER not in text and "rcpt-1" not in text and "environmentFingerprint" not in text
    with Session(engine) as s:
        res_key = s.get(md.ResearchObject, rid).public_key
    assert client.get(f"/api/research/v1/objects/{res_key}/provenance").status_code == 404
