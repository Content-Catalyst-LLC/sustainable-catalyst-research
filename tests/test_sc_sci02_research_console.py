"""SC-SCI-02: the Research Console serves signed-in researchers their own records only. Offline: synthetic keys,
people, exports and figures; SQLite."""
import base64
import hashlib
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tests.test_sc_ed01_editorial import attestation
from tests.test_sc_model01_workspace_ingest import FIG_SHA, PUB, export, figure_files, grant, pub_file
from tests.test_sc_up01_workspace_sessions import SESSIONS, auth, b64, session_doc
from app import user_sessions as us
from app import workspace_ingest as wi
from app import workspaces as ws

A, B, EDITOR = "workspace-user:a", "workspace-user:b", "workspace-user:ed"
C = "/api/research/v1/console"
AREA = "synthetic-console-area"


@pytest.fixture
def console(monkeypatch, tmp_path):
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "console.sqlite"))
    ws.Base.metadata.create_all(engine)
    monkeypatch.setattr(ws, "_session_factory", lambda: Session(engine, expire_on_commit=False))
    (tmp_path / "candidates").mkdir()
    token = tmp_path / "t"
    token.write_text("staging-secret")
    for k, v in {**SESSIONS, "RESEARCH_WORKSPACE_PUBLIC_KEY_FILE": pub_file(tmp_path, PUB),
                 "RESEARCH_WORKSPACE_EXPORT_URL": "https://workspace.example.org/v1/research-exports",
                 "RESEARCH_WORKSPACE_CLIENT_TOKEN_FILE": str(token),
                 "RESEARCH_CONVERGENCE_WRITES": "staging-service-token", "RESEARCH_CONVERGENCE_TOKEN_FILE": str(token),
                 "RESEARCH_FIGURE_CANDIDATE_STORE": str(tmp_path / "candidates")}.items():
        monkeypatch.setenv(k, v)
    exports = {}
    monkeypatch.setattr(wi, "http_fetch_export", lambda g: deepcopy(exports[g]))
    from app.main import app
    return TestClient(app), exports


def _import(client, exports, owner="a", run="run-1", entry="console-study-a", headers=None, **kw):
    g = grant(owner=owner, run=run, grant_id="g-" + run)
    exports[g] = export(owner=owner, run=run, grant_id="g-" + run, **kw)
    return client.post(C + "/imports", headers=headers or auth(f"workspace-user:{owner}"),
                       json={"grant": g, "researchArea": AREA, "entrySlug": entry, "title": "Synthetic console study",
                             "figureFiles": figure_files()})


def test_status_reports_identity_and_never_accepts_the_service_credential(console):
    client, _ = console
    status = client.get(C + "/status").json()
    assert status["identity"]["mode"] == "workspace-session" and status["importsEnabled"] is True
    assert status["serviceCredentialAccepted"] is False and status["automaticPublication"] is False
    for headers in ({}, {"Authorization": "Bearer staging-secret", "X-SC-Owner-Scope": A},
                    {"Authorization": "SC-Workspace " + b64({k: v for k, v in session_doc().items() if k != "signature"})},
                    auth(seconds=60, issued_ago=120)):
        for method, path in (("GET", "/projects"), ("GET", "/graph"), ("POST", "/imports")):
            r = client.request(method, C + path, headers=headers, json={} if method == "POST" else None)
            assert r.status_code == 401 and r.json()["detail"]["code"] == "SC_SIGN_IN_REQUIRED", (headers, path)


def test_a_researcher_imports_inspects_and_renders_their_own_verified_run(console):
    client, exports = console
    r = _import(client, exports)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ownerScope"] == A and out["verificationStatus"] == "executed-verified"
    projects = client.get(C + "/projects", headers=auth()).json()
    assert projects["owner"] == A and projects["role"] == "owner"
    assert [(p["area"], p["workspaceProjectId"], [e["slug"] for e in p["entries"]]) for p in projects["items"]] == \
        [(AREA, "proj-1", ["console-study-a"])]
    graph = client.get(C + "/graph", headers=auth()).json()
    assert {n["kind"] for n in graph["nodes"]} >= {"dataset", "computation", "result", "figure"} and graph["edges"]
    fig_id = out["objects"]["output:fig"]["id"]
    detail = client.get(f"{C}/objects/{fig_id}", headers=auth()).json()
    assert detail["figure"]["bundleSha256"] == FIG_SHA and detail["subjectRef"].startswith("research-object:" + fig_id)
    assert detail["visibility"] == "private" and not any(detail["approvals"].values())
    files = client.get(f"{C}/objects/{fig_id}/figure-files", headers=auth()).json()
    assert files["bundleSha256"] == FIG_SHA
    for f in files["files"]:
        data = base64.b64decode(f["contentBase64"])
        assert f["hashMode"] != "raw-bytes" or hashlib.sha256(data).hexdigest() == f["sha256"]
    result = out["objects"]["output:polyglot-result"]["id"]
    assert client.get(f"{C}/objects/{result}", headers=auth()).json()["reproduction"] == "not-attempted"
    # An independent Julia run of the same inputs reproduces the R result.
    g = grant(run="run-julia", grant_id="g-julia")
    exports[g] = export(run="run-julia", grant_id="g-julia", values={"intercept": 1.5, "slope": 0.25 + 1e-15})
    r = client.post(f"{C}/objects/{result}/reproductions", headers=auth(), json={"grant": g, "relTol": 1e-9, "absTol": 1e-12})
    assert r.status_code == 200 and r.json()["status"] == "reproduced", r.text
    assert client.get(f"{C}/objects/{result}", headers=auth()).json()["reproduction"] == "reproduced"


def test_a_second_researcher_sees_and_takes_nothing_of_the_first(console):
    client, exports = console
    out = _import(client, exports).json()
    fig_id, result = out["objects"]["output:fig"]["id"], out["objects"]["output:polyglot-result"]["id"]
    assert client.get(C + "/projects", headers=auth(B)).json()["items"] == []
    assert client.get(C + "/graph", headers=auth(B)).json()["nodes"] == []
    for path in (f"/objects/{fig_id}", f"/objects/{fig_id}/figure-files", f"/objects/{result}"):
        assert client.get(C + path, headers=auth(B)).status_code == 404
    # Naming the owner without their attestation is refused; so is importing someone else's run.
    assert client.get(C + "/projects", headers={**auth(B), "X-SC-Owner-Scope": A}).status_code == 403
    r = _import(client, exports, owner="a", run="run-2", entry="console-steal", headers=auth(B))
    assert r.status_code == 403
    assert client.get(C + "/projects", headers=auth(B)).json()["items"] == []
    assert [e["slug"] for e in client.get(C + "/projects", headers=auth()).json()["items"][0]["entries"]] == ["console-study-a"]
    # B cannot record a reproduction against A's result, even with B's own run.
    g = grant(owner="b", run="run-b", grant_id="g-b")
    exports[g] = export(owner="b", run="run-b", grant_id="g-b")
    assert client.post(f"{C}/objects/{result}/reproductions", headers=auth(B), json={"grant": g}).status_code == 404
    # B's import into an entry slug A already holds is refused, and leaves nothing behind.
    assert _import(client, exports, owner="b", run="run-b2", entry="console-study-a", headers=auth(B)).status_code in (403, 409, 422)
    assert client.get(C + "/projects", headers=auth(B)).json()["items"] == []


def test_an_editor_reads_with_the_owners_attestation_but_cannot_import_or_approve(console):
    client, exports = console
    _import(client, exports)
    as_editor = {**auth(EDITOR), "X-SC-Owner-Scope": A, "X-SC-Editor-Attestation": b64(attestation(grantee=EDITOR))}
    projects = client.get(C + "/projects", headers=as_editor).json()
    assert projects["role"] == "editor" and projects["owner"] == A and projects["items"]
    assert _import(client, exports, run="run-3", headers=as_editor).status_code == 403
    stolen = {**auth(B), "X-SC-Owner-Scope": A, "X-SC-Editor-Attestation": b64(attestation(grantee=EDITOR))}
    assert client.get(C + "/projects", headers=stolen).status_code == 403
    expired = {**auth(EDITOR), "X-SC-Owner-Scope": A,
               "X-SC-Editor-Attestation": b64(attestation(grantee=EDITOR, issued_ago=60, minutes=30))}
    assert client.get(C + "/projects", headers=expired).status_code == 403


def test_imports_are_closed_without_workspace_configuration_and_sessions_off_closes_everything(console, monkeypatch):
    client, exports = console
    monkeypatch.delenv("RESEARCH_WORKSPACE_CLIENT_TOKEN_FILE")
    assert client.get(C + "/status").json()["importsEnabled"] is False
    assert _import(client, exports).status_code == 503
    monkeypatch.setenv("RESEARCH_USER_IDENTITY", "disabled")
    assert client.get(C + "/projects", headers=auth()).status_code == 503
    assert client.get(C + "/projects", headers={"Authorization": "Bearer staging-secret", "X-SC-Owner-Scope": A}).status_code == 503


def test_production_activation_is_configured_not_hard_coded():
    base = {**SESSIONS, "RESEARCH_WORKSPACE_PUBLIC_KEY_FILE": "/k", "RESEARCH_ENV": "production"}
    assert us.session_state(base).reason == "production-not-authorized"
    assert us.session_state({**base, us.PRODUCTION_ENV: "yes"}).reason == "production-not-authorized"
    on = {**base, us.PRODUCTION_ENV: "authorized"}
    assert us.session_state(on) == us.SessionState(True, "active")
    assert us.session_state({**on, "RESEARCH_WORKSPACE_PUBLIC_URL": "http://127.0.0.1:8094"}).reason == "production-needs-https-workspace"
    assert us.session_state({**on, us.METHODS_ENV: "erw-account-session"}).enabled
    for bad in ("signed-http-only-session-cookie", "erw-account-session,service-bearer"):
        assert us.session_state({**on, us.METHODS_ENV: bad}).reason == "unknown-session-method"


def test_workspace_account_sessions_are_admitted_only_where_configured(monkeypatch):
    header = auth(authentication="erw-account-session")["Authorization"]
    with pytest.raises(PermissionError, match="not a service"):
        us.verify_session(header, key=PUB, env={})
    assert us.verify_session(header, key=PUB, env={us.METHODS_ENV: "erw-account-session"})["subject"] == A
    legacy = auth(authentication="signed-http-only-session-cookie")["Authorization"]
    with pytest.raises(PermissionError):
        us.verify_session(legacy, key=PUB, env={us.METHODS_ENV: "erw-account-session"})
    status = us.public_status({**SESSIONS, "RESEARCH_WORKSPACE_PUBLIC_KEY_FILE": "/k", us.METHODS_ENV: "erw-account-session"})
    assert status["authentication"] == ["erw-account-session", "sc-identity-access-token", "sc-identity-oidc-session"]
