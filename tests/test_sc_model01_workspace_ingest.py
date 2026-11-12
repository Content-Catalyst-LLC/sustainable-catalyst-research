"""SC-MODEL-01 Workspace ExecutionRun ingestion (synthetic fixtures, public CI).

A local Ed25519 key plays Workspace's signing key and shapes exports exactly like
Workspace's ``sc-workspace-research-export/1.0``. Real Workspace-issued exports are
exercised in the isolated staging acceptance.
"""
import base64
import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research-api"))

from app import convergence as cv  # noqa: E402
from app import main as research_main  # noqa: E402
from app import object_registry as md  # noqa: E402
from app import workspace_ingest as wi  # noqa: E402
from app import workspaces as ws  # noqa: E402

KEY = Ed25519PrivateKey.generate()
PUB = KEY.public_key()
OTHER = Ed25519PrivateKey.generate()

# SC-FIG-03A: the run's figure output is a real sc.figure-bundle.v1 (synthetic Plotly fixture) whose bytes
# accompany the ingest; its data source is the run's pinned dataset revision.
FIG_STORE = ROOT / "tests" / "fixtures" / "sc_fig02b_public" / "store"
FIG_SHA = json.loads((FIG_STORE.parent / "FIXTURES.json").read_text())["figures"][1]["bundleSha256"]
FIG_SOURCE_SHA = json.loads((FIG_STORE / FIG_SHA / FIG_SHA).read_bytes())["sourceRefs"][0]["sha256"]


def figure_files(bundle_sha=FIG_SHA, store=FIG_STORE):
    return [{"sha256": f.name, "contentBase64": base64.b64encode(f.read_bytes()).decode()}
            for f in sorted((store / bundle_sha).iterdir())]


def b64e(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def canon(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def kid(key=KEY):
    return wi.key_id(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def sign(domain, body, key=KEY):
    return {**body, "signature": {"alg": "Ed25519", "keyId": kid(key),
                                  "value": b64e(key.sign(domain.encode() + b"\n" + canon(body)))}}


def pub_file(tmp_path, pub=PUB):
    path = tmp_path / "workspace-pub"
    path.write_text(base64.b64encode(pub.public_bytes(Encoding.Raw, PublicFormat.Raw)).decode())
    return str(path)


def grant(owner="a", run="run-1", project="proj-1", grant_id="rxg_1", exp=None, key=KEY):
    claims = {"schema": wi.GRANT_SCHEMA, "grantId": grant_id, "iss": wi.ISSUER, "aud": wi.AUDIENCE, "sub": owner,
              "ownerScope": f"workspace-user:{owner}", "projectId": project, "runId": run,
              "iat": int(time.time()), "exp": exp or int(time.time()) + 300}
    enc = b64e(canon(claims))
    return enc + "." + b64e(key.sign(wi.GRANT_SCHEMA.encode() + b"\n" + enc.encode()))


def provenance(run="run-1", project="proj-1", status="succeeded", values_sha="b" * 64, dataset_class="synthetic",
               repro="c" * 64, outputs=None, dataset_id="ds-1"):
    item = {
        "schema": "sc-workspace-scientific-execution-provenance-run/1.0", "projectId": project,
        "run": {"runId": run, "projectId": project, "name": "OLS run", "status": status, "jobId": "job-1",
                "targetProduct": "workspace", "operation": "workspace.polyglot.r.linear-model",
                "environmentFingerprint": "e" * 64, "inputFingerprint": "f" * 64, "reproducibilityFingerprint": repro},
        "inputs": {
            "datasets": [{"ref": {"datasetId": dataset_id}, "resolved": {"datasetId": dataset_id, "revision": 1, "fingerprint": "a" * 64,
                                                                     "artifactSha256": FIG_SOURCE_SHA, "name": "Synthetic",
                                                                     "metadata": {"scientificClassification": dataset_class}}}],
            "model": {"ref": {}, "resolved": {"modelId": "m-1", "revision": 2, "fingerprint": "d" * 64, "name": "OLS",
                                              "framework": "R", "algorithm": "lm", "metadata": {"researchUnits": {"slope": "u"}}}},
            "parameterSet": {"ref": {}, "resolved": None}, "environment": {"ref": {}, "resolved": None},
            "runtimeAdapter": {"ref": {}, "resolved": None}},
        "outputs": outputs if outputs is not None else [
            {"outputId": "polyglot-result", "role": "result", "sha256": values_sha, "mediaType": "application/json", "label": "R result"},
            {"outputId": "fig", "role": "figure", "sha256": FIG_SHA, "mediaType": wi.FIGURE_MEDIA, "label": "Fit figure"}],
        "events": [], "scientificReceipts": [],
    }
    item["provenanceFingerprint"] = cv.canonical_sha256(item)
    return item


BINDING = {"datasetId": "ds-1", "datasetRevision": 1, "artifactSha256": "a1" * 32, "rowsSha256": "r" * 64,
           "transform": None, "derivedPayloadSha256": None}


def export(owner="a", run="run-1", project="proj-1", grant_id="rxg_1", status="executed-verified",
           values=None, key=KEY, lineage="artifact-bound-verified", bindings=None, cell=None, stochastic=None, **prov_kw):
    prov = provenance(run=run, project=project, **prov_kw)
    receipts = [{"receiptId": f"pgr-{run}", "language": "r" if "julia" not in run else "julia",
                 "runtime": "r-statistical" if "julia" not in run else "julia-numerical", "resultSha256": "b" * 64}] \
        if status == "executed-verified" else []
    vals = values if values is not None else {"intercept": 1.5, "slope": 0.25}
    body = {"schema": wi.EXPORT_SCHEMA, "issuer": wi.ISSUER, "audience": wi.AUDIENCE, "grantId": grant_id,
            "ownerScope": f"workspace-user:{owner}", "projectId": project, "runId": run, "exportedAt": "2026-11-10T00:00:00Z",
            "provenance": prov,
            "verification": {"status": status, "runStatus": prov["run"]["status"], "jobId": "job-1", "runtimeReceipts": receipts,
                             "inputLineage": lineage if status == "executed-verified" else "not-executed",
                             "inputBindings": (bindings if bindings is not None else [BINDING]) if status == "executed-verified" else []},
            "resultValues": ({"values": vals, "sourceArtifactSha256": "b" * 64,
                              "uncertainty": {"semantics": "standard-error", "values": {"slope": 0.01}}}
                             if status == "executed-verified" else {"values": {}}),
            "studyPackages": [{"schema": "sc-workspace-scientific-study-package/1.0", "packageId": "study_1",
                               "manifestFingerprint": "7" * 64, "bundleSha256": "8" * 64, "verified": True, "errors": []}],
            "boundaries": {"artifactBytesIncluded": False}}
    if cell is not None:
        body["notebookCell"] = cell
    if stochastic is not None:
        body["resultValues"]["stochastic"] = stochastic
    return sign(wi.EXPORT_SCHEMA, body, key)


@pytest.fixture
def session(tmp_path, monkeypatch):
    (tmp_path / "figure-candidates").mkdir()
    monkeypatch.setenv("RESEARCH_FIGURE_CANDIDATE_STORE", str(tmp_path / "figure-candidates"))
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "wi.sqlite"))
    ws.Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def ingest(session, g, exp, **kw):
    args = {"area": "metrics-of-sustainable-development", "entry": "ws-study-a", "title": "Workspace study"}
    outputs = exp["provenance"].get("outputs") or []
    if exp["verification"]["status"] == "executed-verified" and any(o.get("sha256") == FIG_SHA for o in outputs) \
            and FIG_SOURCE_SHA in json.dumps(exp):  # exports without the pinned dataset leave the figure unbound
        args["figure_files"] = figure_files()
    args.update(kw)
    return wi.ingest_workspace_run(session, key=PUB, grant=g, fetch=lambda _: deepcopy(exp), **args)


def test_verified_run_maps_into_existing_registry_owned_by_signed_owner(session):
    out = ingest(session, grant(), export())
    assert out["ownerScope"] == "workspace-user:a" and out["verificationStatus"] == "executed-verified"
    kinds = {k: v["kind"] for k, v in out["objects"].items()}
    assert kinds == {"dataset:ds-1:r1": "dataset", "model": "model", "run": "computation",
                     "output:polyglot-result": "result", "output:fig": "figure"}
    res = session.get(md.ResearchObject, out["objects"]["output:polyglot-result"]["id"])
    rec = cv._provenance(session, res).record
    assert rec["resultValues"] == {"intercept": 1.5, "slope": 0.25} and rec["scientificClassification"] == "synthetic"
    assert rec["runtime"]["environmentFingerprint"] == "e" * 64 and rec["verification"]["status"] == "executed-verified"
    assert rec["uncertainty"]["values"] == {"slope": 0.01}
    fig = cv._provenance(session, session.get(md.ResearchObject, out["objects"]["output:fig"]["id"])).record
    assert fig["verification"]["status"] == "owner-registered-output"
    run = cv._provenance(session, session.get(md.ResearchObject, out["objects"]["run"]["id"])).record
    assert run["studyPackages"][0]["verified"] is True
    g = cv.private_knowledge_graph(session, owner_scope="workspace-user:a")
    assert len(g["nodes"]) == 5 and len(g["edges"]) == 4


def test_retries_with_new_grants_are_idempotent_and_versions_are_new_objects(session):
    first = ingest(session, grant(), export())
    again = ingest(session, grant(grant_id="rxg_2"), export(grant_id="rxg_2"))
    assert again["objects"]["run"]["id"] == first["objects"]["run"]["id"]
    changed = ingest(session, grant(grant_id="rxg_3"), export(grant_id="rxg_3", repro="1" * 64))
    assert changed["objects"]["run"]["id"] != first["objects"]["run"]["id"]
    assert changed["objects"]["model"]["id"] == first["objects"]["model"]["id"]


def test_failed_incomplete_and_receiptless_runs_never_become_results(session):
    for i, (status, run_status) in enumerate([("failed", "failed"), ("incomplete", "running"),
                                              ("succeeded-without-runtime-receipt", "succeeded")]):
        rid = f"run-x{i}"
        out = ingest(session, grant(run=rid, grant_id=f"g{i}"), _unverified(rid, f"g{i}", status, run_status))
        assert set(out["objects"]) == {"dataset:ds-1:r1", "model", "run"}
        with pytest.raises(PermissionError, match="verified"):
            cv.promote_public_projection(session, owner_scope="workspace-user:a", object_id=out["objects"]["run"]["id"])


def _unverified(rid, gid, status, run_status):
    doc = export(run=rid, grant_id=gid, status=status)
    body = {k: v for k, v in doc.items() if k != "signature"}
    body["provenance"] = provenance(run=rid, status=run_status)
    body["verification"]["runStatus"] = run_status
    return sign(wi.EXPORT_SCHEMA, body)


def test_forged_tampered_mismatched_and_cross_owner_exports_are_rejected(session):
    with pytest.raises(ValueError, match="grant signature"):
        ingest(session, grant(key=OTHER), export())
    with pytest.raises(ValueError, match="research-export/1.0 signature"):
        ingest(session, grant(), export(key=OTHER))
    tampered = export()
    tampered["resultValues"]["values"]["slope"] = 9.9
    with pytest.raises(ValueError, match="signature"):
        ingest(session, grant(), tampered)
    with pytest.raises(ValueError, match="does not correspond"):
        ingest(session, grant(owner="a"), export(owner="b"))
    with pytest.raises(PermissionError, match="expired"):
        ingest(session, grant(exp=int(time.time()) - 5), export())
    bad_fp = export()
    body = {k: v for k, v in bad_fp.items() if k != "signature"}
    body["provenance"]["run"]["status"] = "succeeded-edited"
    with pytest.raises(ValueError, match="fingerprint"):
        ingest(session, grant(), sign(wi.EXPORT_SCHEMA, body))
    ingest(session, grant(), export())
    # Owner b cannot file its run into owner a's Research entry.
    with pytest.raises((PermissionError, ValueError)):
        ingest(session, grant(owner="b", run="run-b", grant_id="gb"), export(owner="b", run="run-b", grant_id="gb"))


def test_two_owners_stay_isolated(session):
    ingest(session, grant(), export())
    ingest(session, grant(owner="b", run="run-b", grant_id="gb"), export(owner="b", run="run-b", grant_id="gb"), entry="ws-study-b")
    a = cv.private_knowledge_graph(session, owner_scope="workspace-user:a")
    b = cv.private_knowledge_graph(session, owner_scope="workspace-user:b")
    assert {n["entry"] for n in a["nodes"]} == {"ws-study-a"} and {n["entry"] for n in b["nodes"]} == {"ws-study-b"}
    assert not {n["id"] for n in a["nodes"]} & {n["id"] for n in b["nodes"]}


def test_independent_workspace_reproduction(session):
    out = ingest(session, grant(), export())
    rid = out["objects"]["output:polyglot-result"]["id"]
    rep = wi.ingest_reproduction(session, key=PUB, original_id=rid, grant=grant(run="run-julia", grant_id="gj"),
                                 rel_tol=1e-9, abs_tol=1e-12,
                                 fetch=lambda _: export(run="run-julia", grant_id="gj", values={"intercept": 1.5, "slope": 0.25 + 1e-15}))
    assert rep["status"] == "reproduced" and rep["reproduced"] is True and rep["independence"] == "independent-implementation"
    assert rep["inputs"]["status"] == "identical-verified" and rep["unavailableDependencies"] == []
    # Same numbers, but the replay's inputs were sent inline: not a verified reproduction.
    rep = wi.ingest_reproduction(session, key=PUB, original_id=rid, grant=grant(run="run-julia-inline", grant_id="gi"),
                                 rel_tol=1e-9, abs_tol=1e-12,
                                 fetch=lambda _: export(run="run-julia-inline", grant_id="gi", lineage="inline-unverified", bindings=[]))
    assert rep["status"] == "reproduced-unverified-inputs" and rep["numericStatus"] == "within-tolerance" and rep["reproduced"] is False
    # Same numbers from different dataset bytes (a modified dataset): never a reproduction.
    rep = wi.ingest_reproduction(session, key=PUB, original_id=rid, grant=grant(run="run-julia-mod", grant_id="gm"),
                                 rel_tol=1e-9, abs_tol=1e-12,
                                 fetch=lambda _: export(run="run-julia-mod", grant_id="gm", bindings=[dict(BINDING, artifactSha256="b2" * 32)]))
    assert rep["status"] == "inputs-differ" and rep["reproduced"] is False
    # A reproduction missing a value records the unavailable dependency.
    rep = wi.ingest_reproduction(session, key=PUB, original_id=rid, grant=grant(run="run-julia-part", grant_id="gp"),
                                 rel_tol=1e-9, abs_tol=1e-12,
                                 fetch=lambda _: export(run="run-julia-part", grant_id="gp", values={"intercept": 1.5}))
    assert rep["status"] == "not-reproduced" and rep["unavailableDependencies"] == ["slope"]
    assert {k: rep["tolerance"][k] for k in ("relative", "absolute", "source")} == {"relative": 1e-9, "absolute": 1e-12, "source": "default-strict"}
    with pytest.raises(LookupError):
        wi.ingest_reproduction(session, key=PUB, original_id=rid, grant=grant(owner="b", run="run-j2", grant_id="gb2"),
                               rel_tol=1e-9, abs_tol=1e-12, fetch=lambda _: export(owner="b", run="run-j2", grant_id="gb2"))
    with pytest.raises(ValueError, match="different Workspace run"):
        wi.ingest_reproduction(session, key=PUB, original_id=rid, grant=grant(grant_id="g-same"), rel_tol=1e-9, abs_tol=1e-12,
                               fetch=lambda _: export(grant_id="g-same"))


def test_same_seed_replay_of_stochastic_output_is_not_a_reproduction(session):
    """SC-SIM-01: seeded pseudo-random draws replayed by the same implementation prove determinism only."""
    out = ingest(session, grant(), export(stochastic={"seed": 7}))
    rid = out["objects"]["output:polyglot-result"]["id"]
    assert cv._provenance(session, session.get(md.ResearchObject, rid)).record["stochastic"] == {"seed": 7}
    rep = wi.ingest_reproduction(session, key=PUB, original_id=rid, grant=grant(run="run-replay", grant_id="gr"),
                                 rel_tol=1e-9, abs_tol=1e-12,
                                 fetch=lambda _: export(run="run-replay", grant_id="gr", stochastic={"seed": 7}))
    assert rep["independence"] == "replay" and rep["inputs"]["status"] == "identical-verified"
    assert rep["numericStatus"] == "within-tolerance"
    assert rep["status"] == "replayed-deterministic" and rep["reproduced"] is False
    assert cv.reproduction_status(session, session.get(md.ResearchObject, rid)) == "replayed-deterministic"
    # An independent implementation on identical verified inputs still counts, at the declared tolerance.
    rep = wi.ingest_reproduction(session, key=PUB, original_id=rid, grant=grant(run="run-julia", grant_id="gj"),
                                 rel_tol=1e-9, abs_tol=1e-12, fetch=lambda _: export(run="run-julia", grant_id="gj"))
    assert rep["status"] == "reproduced" and rep["independence"] == "independent-implementation"


def test_deterministic_replay_still_counts_and_bad_stochastic_markers_are_normalised(session):
    out = ingest(session, grant(), export())
    rid = out["objects"]["output:polyglot-result"]["id"]
    assert "stochastic" not in cv._provenance(session, session.get(md.ResearchObject, rid)).record
    rep = wi.ingest_reproduction(session, key=PUB, original_id=rid, grant=grant(run="run-replay", grant_id="gr"),
                                 rel_tol=1e-9, abs_tol=1e-12, fetch=lambda _: export(run="run-replay", grant_id="gr"))
    assert rep["status"] == "reproduced" and rep["independence"] == "replay"
    out = ingest(session, grant(run="run-s", grant_id="gs"), export(run="run-s", grant_id="gs", stochastic={"seed": True, "x": 1}),
                 entry="ws-study-s")
    assert cv._provenance(session, session.get(md.ResearchObject, out["objects"]["output:polyglot-result"]["id"])).record["stochastic"] == {"seed": None}


def test_withdrawal_revokes_and_blocks_reingest(session):
    out = ingest(session, grant(), export())
    notice = sign(wi.WITHDRAWAL_SCHEMA, {"schema": wi.WITHDRAWAL_SCHEMA, "issuer": wi.ISSUER, "audience": wi.AUDIENCE,
                                         "ownerScope": "workspace-user:a", "projectId": "proj-1", "runId": "run-1",
                                         "withdrawnAt": "2026-11-10T00:00:00Z", "reason": "test"})
    forged = deepcopy(notice)
    forged["runId"] = "run-other"
    with pytest.raises(ValueError):
        wi.apply_withdrawal(session, key=PUB, notice=forged)
    res = wi.apply_withdrawal(session, key=PUB, notice=notice)
    assert set(res["revokedObjectIds"]) == {out["objects"]["run"]["id"], out["objects"]["output:polyglot-result"]["id"],
                                            out["objects"]["output:fig"]["id"]}
    assert wi.apply_withdrawal(session, key=PUB, notice=notice)["replayed"] is True
    obj = session.get(md.ResearchObject, out["objects"]["output:polyglot-result"]["id"])
    assert obj.review_state == "revoked"
    with pytest.raises(PermissionError, match="withdrew"):
        cv.promote_public_projection(session, owner_scope="workspace-user:a", object_id=obj.id)
    with pytest.raises(PermissionError, match="withdrawn"):
        ingest(session, grant(grant_id="g9"), export(grant_id="g9"))


def test_http_gate_closed_by_default_and_owner_header_ignored(monkeypatch, tmp_path):
    client = TestClient(research_main.app)
    assert client.post("/api/research/v1/convergence/workspace/execution-runs", json={}).status_code == 503
    key_file = tmp_path / "pub"
    key_file.write_text(base64.b64encode(PUB.public_bytes(Encoding.Raw, PublicFormat.Raw)).decode())
    tok = tmp_path / "tok"
    tok.write_text("x")
    for k, v in {"RESEARCH_CONVERGENCE_WRITES": "staging-service-token", "RESEARCH_CONVERGENCE_TOKEN_FILE": str(tok),
                 "RESEARCH_WORKSPACE_EXPORT_URL": "http://127.0.0.1:9", "RESEARCH_WORKSPACE_CLIENT_TOKEN_FILE": str(tok),
                 "RESEARCH_WORKSPACE_PUBLIC_KEY_FILE": str(key_file)}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("RESEARCH_ENV", "production")
    assert client.post("/api/research/v1/convergence/workspace/execution-runs", json={}).status_code == 503
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "http.sqlite"))
    ws.Base.metadata.create_all(engine)
    monkeypatch.setattr(ws, "_session_factory", lambda: Session(engine, expire_on_commit=False))
    monkeypatch.setenv("RESEARCH_ENV", "staging")
    monkeypatch.setattr(wi, "http_fetch_export", lambda g: export())
    body = {"grant": grant(), "researchArea": "example-site-enhanced-rock-weathering", "entrySlug": "ws-http", "title": "T"}
    assert client.post("/api/research/v1/convergence/workspace/execution-runs", json=body).status_code == 401
    assert client.post("/api/research/v1/convergence/workspace/execution-runs", json=body,
                       headers={"Authorization": "Bearer y"}).status_code == 401
    r = client.post("/api/research/v1/convergence/workspace/execution-runs",
                    headers={"Authorization": "Bearer x", "X-SC-Owner-Scope": "workspace-user:mallory"},
                    json={"grant": grant(), "researchArea": "example-site-enhanced-rock-weathering", "entrySlug": "ws-http", "title": "T"})
    assert r.status_code == 200, r.text
    assert r.json()["ownerScope"] == "workspace-user:a"


ALEMBIC_ENV = {**os.environ, "PYTHONPATH": str(ROOT / "research-api")}


def alembic(url, *args):
    subprocess.run([sys.executable, "-m", "alembic", "-c", str(ROOT / "research-api/alembic.ini"), *args],
                   env={**ALEMBIC_ENV, "RESEARCH_DATABASE_URL": url}, check=True, capture_output=True)


def test_schema_head_matches_migrations_and_readiness_tracks_upgrade_and_rollback(tmp_path, monkeypatch):
    import re
    heads = {}
    for f in (ROOT / "research-api/alembic/versions").glob("*.py"):
        text = f.read_text()
        rev = re.search(r"^revision\s*=\s*['\"]([^'\"]+)", text, re.M).group(1)
        down = re.search(r"^down_revision\s*=\s*['\"]([^'\"]+)", text, re.M)
        heads[rev] = down.group(1) if down else None
    head = (set(heads) - set(heads.values())).pop()
    assert research_main.SCHEMA_HEAD == head
    url = "sqlite+pysqlite:///" + str(tmp_path / "ready.sqlite")
    client = TestClient(research_main.app)
    monkeypatch.setattr(ws, "_engine", create_engine(url))
    alembic(url, "upgrade", "head")
    assert client.get("/api/research/v1/ready").status_code == 200
    alembic(url, "downgrade", "-1")
    assert client.get("/api/research/v1/ready").status_code == 503
    alembic(url, "upgrade", "head")
    assert client.get("/api/research/v1/ready").status_code == 200



def test_models_match_migrations_at_head(tmp_path):
    """alembic check: the models and the migrated schema agree, so autogenerate proposes nothing."""
    url = "sqlite+pysqlite:///" + str(tmp_path / "check.sqlite")
    alembic(url, "upgrade", "head")
    alembic(url, "check")


def test_timestamp_migration_refuses_rows_without_created_at(tmp_path):
    url = "sqlite+pysqlite:///" + str(tmp_path / "nulls.sqlite")
    alembic(url, "upgrade", "0008_editorial_publishing")
    with create_engine(url).begin() as c:
        c.exec_driver_sql("INSERT INTO research_workspace_withdrawals(id, owner_scope, project_id, run_id, notice_sha256,"
                          " revoked_object_ids, created_at) VALUES ('w', 'workspace-user:x', 'p', 'r', '0', '[]', NULL)")
    with pytest.raises(subprocess.CalledProcessError) as exc:
        alembic(url, "upgrade", "head")
    assert b"refusing to invent timestamps" in exc.value.stderr

def test_publication_requires_verified_input_lineage_and_public_view_is_redacted(session):
    from tests.test_sc_model01_convergence import governance
    ok = ingest(session, grant(), export())
    rid = ok["objects"]["output:polyglot-result"]["id"]
    inline = ingest(session, grant(run="run-inline", grant_id="g-inl"),
                    export(run="run-inline", grant_id="g-inl", lineage="inline-unverified", bindings=[]))
    iid = inline["objects"]["output:polyglot-result"]["id"]
    for oid in (rid, iid):
        obj = session.get(md.ResearchObject, oid)
        subject = cv.subject_ref(obj, cv._provenance(session, obj))
        for dim, action in cv.APPROVAL_ACTIONS.items():
            a, d = governance(subject, action)
            cv.record_workspace_approval(session, owner_scope="workspace-user:a", object_id=oid, dimension=dim, approval=a, decision=d)
    with pytest.raises(PermissionError, match="Input lineage is not verified"):
        cv.promote_public_projection(session, owner_scope="workspace-user:a", object_id=iid)
    with pytest.raises(PermissionError, match="reproduction"):
        cv.promote_public_projection(session, owner_scope="workspace-user:a", object_id=rid)
    wi.ingest_reproduction(session, key=PUB, original_id=rid, grant=grant(run="run-julia", grant_id="gj"), rel_tol=1e-9,
                           abs_tol=1e-12, fetch=lambda _: export(run="run-julia", grant_id="gj"))
    pub = cv.promote_public_projection(session, owner_scope="workspace-user:a", object_id=rid)
    obj = session.get(md.ResearchObject, rid)
    view = cv.public_provenance(obj, cv._provenance(session, obj))
    text = str(view)
    assert pub["visibility"] == md.APPROVED and view["scientificClassification"] == "synthetic"
    assert "workspace-user:a" not in text and "pgr-run-1" not in text and "rxg_" not in text


def test_generic_bundles_cannot_self_attest_verified_input_lineage(session):
    from tests.test_sc_model01_convergence import OWNER, bundle, julia_reproduction
    b = bundle()
    for o in b["objects"]:
        o["provenance"]["inputLineage"] = "artifact-bound-verified"
    with pytest.raises(ValueError, match="signed Workspace export"):
        cv.ingest_bundle(session, owner_scope=OWNER, bundle=cv.seal_bundle({k: v for k, v in b.items() if k != "manifestSha256"}))
    rid = cv.ingest_bundle(session, owner_scope=OWNER, bundle=bundle())["objects"]["res"]["id"]
    rep = julia_reproduction()
    rep["provenance"]["inputLineage"] = "artifact-bound-verified"
    with pytest.raises(ValueError, match="signed Workspace export"):
        cv.record_reproduction(session, owner_scope=OWNER, original_id=rid, reproduction=rep)
