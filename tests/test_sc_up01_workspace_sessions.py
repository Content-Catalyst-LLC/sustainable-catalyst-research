"""SC-UP-01: Research takes the person from a Workspace-signed Sustainable Catalyst session, not from a shared
credential and a header the browser chooses. Offline: synthetic keys, people and articles; SQLite."""
import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tests.test_rh08d04_discovery_intake import cand
from tests.test_sc_ed01_editorial import attestation
from tests.test_sc_model01_workspace_ingest import KEY, PUB, pub_file, sign
from app import discovery_intake as di
from app import editorial as ed
from app import user_sessions as us
from app import workspace_ingest as wi
from app import workspaces as ws

OWNER, OTHER, EDITOR = "workspace-user:a", "workspace-user:b", "workspace-user:ed"
BASE = "/api/research/v1"
SESSIONS = {"RESEARCH_DISCOVERY_INTAKE": "staging", "RESEARCH_ENV": "staging",
            "RESEARCH_USER_IDENTITY": "workspace-session", "RESEARCH_WORKSPACE_PUBLIC_URL": "https://workspace.example.org"}


def session_doc(subject=OWNER, seconds=300, issued_ago=0.0, key=KEY, **over):
    now = datetime.now(timezone.utc) - timedelta(seconds=issued_ago)
    body = {"schema": us.SESSION_SCHEMA, "issuer": wi.ISSUER, "audience": wi.AUDIENCE, "subject": subject,
            "authentication": "sc-identity-oidc-session", "issuedAt": now.isoformat(),
            "expiresAt": (now + timedelta(seconds=seconds)).isoformat(), **over}
    return sign(us.SESSION_SCHEMA, body, key)


def b64(doc):
    return base64.urlsafe_b64encode(json.dumps(doc).encode()).decode().rstrip("=")


def auth(subject=OWNER, **kw):
    return {"Authorization": "SC-Workspace " + b64(session_doc(subject, **kw))}


# --- Verification ---------------------------------------------------------------------------------------------


def test_a_signed_current_sc_identity_session_names_the_person():
    assert us.verify_session(auth()["Authorization"], key=PUB)["subject"] == OWNER
    assert us.verify_session(auth(authentication="sc-identity-access-token")["Authorization"], key=PUB)["subject"] == OWNER


@pytest.mark.parametrize("header, reason", [
    (None, "Sign in"),
    ("Bearer staging-secret", "Sign in"),
    ("SC-Workspace !!!", "malformed"),
    ("SC-Workspace " + b64(["not", "a", "document"]), "malformed"),
    ("SC-Workspace " + "A" * 9000, "malformed"),
    ("SC-Workspace " + b64(session_doc(key=Ed25519PrivateKey.generate())), "could not be verified"),
    ("SC-Workspace " + b64({**session_doc(), "subject": OTHER}), "could not be verified"),  # altered after signing
    ("SC-Workspace " + b64(session_doc(audience="sc-library")), "could not be verified"),
    ("SC-Workspace " + b64(sign(ed.ATTESTATION_SCHEMA, attestation())), "could not be verified"),  # another document
    ("SC-Workspace " + b64(session_doc(subject="wp-user:7")), "no Workspace person"),
    ("SC-Workspace " + b64(session_doc(authentication="service-bearer+wordpress-user-context")), "not a service"),
    ("SC-Workspace " + b64(session_doc(authentication="signed-http-only-session-cookie")), "not a service"),
    ("SC-Workspace " + b64(session_doc(issuedAt="2026-11-13T10:00:00")), "no valid issue time"),
    ("SC-Workspace " + b64(session_doc(seconds=3600)), "too long"),
    ("SC-Workspace " + b64(session_doc(issued_ago=-600)), "future"),
    ("SC-Workspace " + b64(session_doc(seconds=60, issued_ago=120)), "expired"),
])
def test_anything_but_a_valid_session_is_refused(header, reason):
    with pytest.raises(PermissionError, match=reason):
        us.verify_session(header, key=PUB)


def test_a_retired_or_revoked_workspace_key_signs_no_new_session():
    raw = base64.b64encode(PUB.public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    for entry in ({"publicKey": raw, "notAfter": past}, {"publicKey": raw, "revoked": True}):
        ring = wi.parse_keyring(json.dumps({"schema": wi.KEYRING_SCHEMA, "keys": [entry]}))
        with pytest.raises(PermissionError, match="could not be verified"):
            us.verify_session(auth()["Authorization"], key=ring)


def test_sessions_are_off_by_default_and_closed_in_production():
    assert us.session_state({}) == us.SessionState(False, "mode-disabled")
    key_set = {**SESSIONS, "RESEARCH_WORKSPACE_PUBLIC_KEY_FILE": "/k"}
    assert us.session_state(key_set) == us.SessionState(True, "active")
    assert us.session_state({**key_set, "RESEARCH_USER_IDENTITY": "on"}).reason == "unknown-mode"
    assert us.session_state({**key_set, "RESEARCH_ENV": "production"}).reason == "production-not-authorized"
    assert us.session_state({**key_set, "RESEARCH_ENV": "preview"}).reason == "environment-not-staging"
    assert us.session_state(SESSIONS).reason == "workspace-key-not-configured"
    for url in ("http://workspace.example.org", "https://workspace.example.org/app", "javascript:x", ""):
        assert us.session_state({**key_set, "RESEARCH_WORKSPACE_PUBLIC_URL": url}).reason == "workspace-url-not-configured"
    assert us.workspace_url({"RESEARCH_WORKSPACE_PUBLIC_URL": "http://127.0.0.1:18965"}) == "http://127.0.0.1:18965"
    assert us.session_state({**key_set, "RESEARCH_ENV": "production", us.PRODUCTION_ENV: "authorized"}).enabled


# --- HTTP: the saved-sources workflow by signed person -----------------------------------------------------------


@pytest.fixture
def http(monkeypatch, tmp_path):
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "up01.sqlite"))
    ws.Base.metadata.create_all(engine)
    monkeypatch.setattr(ws, "_session_factory", lambda: Session(engine, expire_on_commit=False))
    token = tmp_path / "t"
    token.write_text("staging-secret")  # the legacy staging credential is configured, and must still be refused
    for k, v in {**SESSIONS, "RESEARCH_CONVERGENCE_WRITES": "staging-service-token",
                 "RESEARCH_CONVERGENCE_TOKEN_FILE": str(token),
                 "RESEARCH_WORKSPACE_PUBLIC_KEY_FILE": pub_file(tmp_path, PUB)}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(di, "HANDLES", di.RunHandles())
    with Session(engine) as s:
        for owner, slug, project in ((OWNER, "erw-study", "proj-1"), (OTHER, "other-study", "proj-9")):
            ed.create_article(s, ed.Actor(owner, "owner", owner, "*"), area_slug="synthetic-area-" + owner[-1],
                              project_key="k-" + slug, workspace_project_id=project, slug=slug, title="Synthetic " + slug)
        s.commit()
    from app.main import app
    return TestClient(app), engine


def test_status_tells_the_page_where_to_sign_in_and_that_no_service_credential_is_accepted(http):
    client, _ = http
    identity = client.get(BASE + "/discovery/intake/status").json()["identity"]
    assert identity == {"mode": "workspace-session", "reason": "active", "workspaceUrl": "https://workspace.example.org",
                        "sessionSchema": us.SESSION_SCHEMA, "maxLifetimeSeconds": 300, "serviceCredentialAccepted": False,
                        "authentication": ["sc-identity-access-token", "sc-identity-oidc-session"]}
    status = client.get(BASE + "/editorial/status").json()
    assert status["privateWritesEnabled"] is True and status["identity"]["mode"] == "workspace-session"


def test_a_signed_in_person_saves_links_and_proposes_their_own_sources_only(http):
    client, engine = http
    c = cand(retracted=None)
    r = client.post(BASE + "/discovery/intake/candidates", headers=auth(),
                    json={"run_handle": di.HANDLES.put([c]), "candidate_id": c.candidate_id})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert r.json()["savedBy"] == OWNER
    assert [a["slug"] for a in client.get(BASE + "/editorial/articles", headers=auth()).json()["items"]] == ["erw-study"]
    r = client.post(f"{BASE}/discovery/intake/candidates/{sid}/associations/erw-study", headers=auth())
    assert r.json()["associations"] == [{"entry": "erw-study", "relation": "related-candidate"}]
    r = client.post(f"{BASE}/discovery/intake/candidates/{sid}/library-proposal", headers=auth(),
                    json={"acknowledged_blocking_issues": ["retraction-status-unknown"]})
    assert r.status_code == 200 and r.json()["libraryProposal"]["sent"] is False

    # Another signed-in person sees nothing of it, and cannot claim the owner by naming them.
    assert client.get(BASE + "/discovery/intake/candidates", headers=auth(OTHER)).json() == {"items": []}
    assert client.get(f"{BASE}/discovery/intake/candidates/{sid}", headers=auth(OTHER)).status_code == 404
    assert [a["slug"] for a in client.get(BASE + "/editorial/articles", headers=auth(OTHER)).json()["items"]] == ["other-study"]
    claimed = {**auth(OTHER), "X-SC-Owner-Scope": OWNER}
    assert client.get(BASE + "/discovery/intake/candidates", headers=claimed).status_code == 403
    assert client.post(f"{BASE}/discovery/intake/candidates/{sid}/dismiss", headers=claimed).status_code == 403
    assert client.post(f"{BASE}/discovery/intake/candidates/{sid}/associations/other-study",
                       headers=auth(OTHER)).status_code == 404
    with Session(engine) as s:
        assert di.verify_chain(s)["events"] == 3  # save, link, proposal: all by the owner, chain intact


def test_the_staging_service_credential_and_unsigned_identity_are_refused_while_sessions_are_on(http):
    client, _ = http
    for headers in ({"Authorization": "Bearer staging-secret", "X-SC-Owner-Scope": OWNER},
                    {"X-SC-Owner-Scope": OWNER, "X-SC-Actor-Scope": OWNER},
                    {"Authorization": "SC-Workspace " + b64({k: v for k, v in session_doc().items() if k != "signature"})},
                    auth(seconds=60, issued_ago=120)):
        for method, path in (("GET", "/discovery/intake/candidates"), ("GET", "/editorial/articles"),
                             ("POST", "/editorial/articles")):
            r = client.request(method, BASE + path, headers=headers, json={} if method == "POST" else None)
            assert r.status_code == 401, (headers, path, r.text)
            assert r.json()["detail"]["code"] == "SC_SIGN_IN_REQUIRED" and r.headers["www-authenticate"] == "SC-Workspace"


def test_an_editor_acts_for_an_owner_only_with_that_owners_attestation_for_the_signed_person(http):
    client, _ = http
    c = cand()
    sid = client.post(BASE + "/discovery/intake/candidates", headers=auth(),
                      json={"run_handle": di.HANDLES.put([c]), "candidate_id": c.candidate_id}).json()["id"]
    as_editor = {**auth(EDITOR), "X-SC-Owner-Scope": OWNER, "X-SC-Editor-Attestation": b64(attestation(grantee=EDITOR))}
    assert [i["id"] for i in client.get(BASE + "/discovery/intake/candidates", headers=as_editor).json()["items"]] == [sid]
    r = client.post(f"{BASE}/discovery/intake/candidates/{sid}/associations/erw-study", headers=as_editor)
    assert r.status_code == 200, r.text
    assert client.post(f"{BASE}/discovery/intake/candidates/{sid}/dismiss", headers=as_editor).status_code == 403
    # Someone else presenting the editor's attestation is still themself: the grantee does not match.
    stolen = {**auth(OTHER), "X-SC-Owner-Scope": OWNER, "X-SC-Editor-Attestation": b64(attestation(grantee=EDITOR))}
    assert client.get(BASE + "/discovery/intake/candidates", headers=stolen).status_code == 403
    # The X-SC-Actor-Scope header no longer chooses the actor.
    spoof = {**auth(OTHER), "X-SC-Owner-Scope": OWNER, "X-SC-Actor-Scope": EDITOR,
             "X-SC-Editor-Attestation": b64(attestation(grantee=EDITOR))}
    assert client.get(BASE + "/discovery/intake/candidates", headers=spoof).status_code == 403


def test_without_the_workspace_key_sessions_stay_closed_and_nothing_falls_back(http, monkeypatch):
    client, _ = http
    monkeypatch.delenv("RESEARCH_WORKSPACE_PUBLIC_KEY_FILE")
    monkeypatch.delenv("RESEARCH_CONVERGENCE_WRITES")
    status = client.get(BASE + "/discovery/intake/status").json()
    assert status["enabled"] is False and status["identity"]["mode"] == "closed"
    assert client.get(BASE + "/discovery/intake/candidates", headers=auth()).status_code == 503


def test_an_unreadable_workspace_key_fails_closed(http, monkeypatch, tmp_path):
    client, _ = http
    monkeypatch.setenv("RESEARCH_WORKSPACE_PUBLIC_KEY_FILE", str(tmp_path / "missing"))
    assert client.get(BASE + "/discovery/intake/candidates", headers=auth()).status_code == 503
