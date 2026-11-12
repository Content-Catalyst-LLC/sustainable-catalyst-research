"""SC-RIGHTS-02: Workspace signing-key rotation, rights attestations, reconciliation state and the freshness hold.

Synthetic fixtures only. A local Ed25519 key plays Workspace's signing key; a second one plays the rotated key.
"""
import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import select

from tests.test_sc_ed01_editorial import OWNER, OWNER_ACTOR
from tests.test_sc_fig03a_governed_figures import (  # noqa: F401 (fixtures)
    ARTICLE, P, PLOT, SOIL, client, graph_keys, key, live, source_sha, stores, world,
)
from tests.test_sc_fig041_source_rights_notices import notice
from tests.test_sc_model01_workspace_ingest import KEY, OTHER, PUB, kid, session, sign  # noqa: F401

from app import convergence as cv  # noqa: E402
from app import editorial as ed  # noqa: E402
from app import figure_releases as fr  # noqa: E402
from app import object_registry as md  # noqa: E402
from app import source_rights_sync as rs  # noqa: E402
from app import workspace_ingest as wi  # noqa: E402

NEW = Ed25519PrivateKey.generate()


def raw(k):
    return k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def keyring(*entries):
    return wi.parse_keyring(json.dumps({"schema": wi.KEYRING_SCHEMA, "keys": [
        {"keyId": kid(k), "publicKey": base64.b64encode(raw(k)).decode(), **extra} for k, extra in entries]}))


def soil_src():
    return source_sha(SOIL["bundleSha256"])


def plot_src():
    return source_sha(PLOT["bundleSha256"])


def attestation(sources, owner=OWNER, issued=None, k=KEY, att_id=None):
    body = {"schema": rs.ATTESTATION_SCHEMA, "issuer": wi.ISSUER, "audience": wi.AUDIENCE,
            "attestationId": att_id or f"sra_{uuid.uuid4().hex}", "ownerScope": owner,
            "issuedAt": (issued or datetime.now(timezone.utc)).isoformat(), "sources": sources}
    return sign(rs.ATTESTATION_SCHEMA, body, k)


def src(sha, seq=0, digest=None, status=None, known=True):
    return {"sourceSha256": sha, "sequence": seq, "noticeSha256": digest, "licenceStatus": status, "known": known}


def confirm_all(session, issued=None):
    out = rs.apply_attestation(session, key=PUB, attestation=attestation([src(soil_src()), src(plot_src())], issued=issued))
    session.commit()
    return out


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setenv("RESEARCH_ENV", "staging")
    monkeypatch.setenv("RESEARCH_RIGHTS_FRESHNESS_SECONDS", "600")


# --- Freshness policy ---------------------------------------------------------------------------------

@pytest.mark.parametrize("env,value,expected", [
    ("production", "", rs.DEFAULT_WINDOW), ("staging", "", rs.DEFAULT_WINDOW), ("", "", None),
    ("development", "off", None), ("production", "off", 0), ("staging", "off", 0), ("staging", "5", rs.MIN_WINDOW),
    ("staging", "99999999", rs.MAX_WINDOW), ("staging", "900", 900),
])
def test_freshness_window_policy_fails_closed(monkeypatch, env, value, expected):
    monkeypatch.setenv("RESEARCH_ENV", env)
    monkeypatch.setenv("RESEARCH_RIGHTS_FRESHNESS_SECONDS", value)
    assert rs.freshness_window() == expected


def test_unconfirmed_figures_are_held_everywhere_and_served_once_confirmed(session, world, client, enforced):
    live(session, world)
    session.commit()
    soil, plot = key(session, world["soil"]), key(session, world["plot"])
    # Released before any attestation: held from every public path, but nothing is revoked or purged.
    assert client.get(f"{P}/objects/{soil}/figure").status_code == 404
    assert client.get(f"{P}/objects/{soil}").status_code == 404 and soil not in graph_keys(client)
    assert (world["release"] / SOIL["bundleSha256"]).exists()
    assert session.get(md.ResearchObject, world["soil"]).review_state not in ("revoked", "proposed")
    events = len(ed.audit_log(session, OWNER, ARTICLE))
    out = confirm_all(session)
    assert {r["status"] for r in out["results"]} == {rs.IN_SYNC} and out["missing"] == []
    assert client.get(f"{P}/objects/{soil}/figure").status_code == 200
    assert client.get(f"{P}/objects/{plot}/figure").status_code == 200 and soil in graph_keys(client)
    assert len(ed.audit_log(session, OWNER, ARTICLE)) == events  # holds and confirmations are not editorial events


def test_confirmation_expires_after_the_window(session, world, client, enforced):
    live(session, world)
    confirm_all(session, issued=datetime.now(timezone.utc) - timedelta(seconds=601))
    assert client.get(f"{P}/objects/{key(session, world['soil'])}/figure").status_code == 404
    confirm_all(session)
    assert client.get(f"{P}/objects/{key(session, world['soil'])}/figure").status_code == 200


def test_a_hold_never_deletes_released_bytes(session, world, enforced):
    live(session, world)
    session.commit()
    assert fr.purge_release_store(session) == []
    assert (world["release"] / SOIL["bundleSha256"]).exists() and (world["release"] / PLOT["bundleSha256"]).exists()


def test_freshness_off_in_development_serves_without_attestation(session, world, client, monkeypatch):
    monkeypatch.setenv("RESEARCH_ENV", "development")
    monkeypatch.setenv("RESEARCH_RIGHTS_FRESHNESS_SECONDS", "off")
    live(session, world)
    session.commit()
    assert client.get(f"{P}/objects/{key(session, world['soil'])}/figure").status_code == 200


def test_a_workspace_figure_with_no_recorded_sources_is_held(session, world, client, enforced):
    live(session, world)
    confirm_all(session)
    for row in session.scalars(select(rs.FigureRightsSource).where(rs.FigureRightsSource.object_id == world["soil"])):
        session.delete(row)
    session.commit()
    assert key(session, world["soil"]) not in graph_keys(client)


# --- Attestations -----------------------------------------------------------------------------------

def test_research_behind_holds_at_once_and_reconciles_after_delivery(session, world, client, enforced):
    live(session, world)
    confirm_all(session)
    n1 = notice(soil_src())
    digest = cv.canonical_sha256(n1)
    # Workspace issued notice 1 for soil; Research has not received it yet.
    out = rs.apply_attestation(session, key=PUB, attestation=attestation(
        [src(soil_src(), 1, digest, "restricted"), src(plot_src())]))
    session.commit()
    assert [r["status"] for r in out["results"]] == ["research-behind", rs.IN_SYNC]
    assert client.get(f"{P}/objects/{key(session, world['soil'])}/figure").status_code == 404
    assert client.get(f"{P}/objects/{key(session, world['plot'])}/figure").status_code == 200
    st = {s["sourceSha256"]: s for o in rs.state(session)["owners"] for s in o["sources"]}
    assert st[soil_src()]["appliedSequence"] == 0 and st[soil_src()]["confirmation"]["status"] == "research-behind"
    wi.apply_source_rights_notice(session, key=PUB, notice=n1)
    out = rs.apply_attestation(session, key=PUB, attestation=attestation([src(soil_src(), 1, digest, "restricted")]))
    assert out["results"][0]["status"] == rs.IN_SYNC


def test_divergent_older_unknown_and_gap_statuses(session, world, enforced):
    live(session, world)
    n1 = notice(soil_src())
    wi.apply_source_rights_notice(session, key=PUB, notice=n1)
    out = rs.apply_attestation(session, key=PUB, attestation=attestation([
        src(soil_src(), 1, "f" * 64, "restricted"), src(plot_src(), 0, known=False)]))
    assert [r["status"] for r in out["results"]] == ["divergent", "unknown-to-workspace"]
    out = rs.apply_attestation(session, key=PUB, attestation=attestation([src(soil_src(), 0)]))
    assert out["results"][0]["status"] == "attestation-older"
    # A restriction crossed a gap (sequence 3 applied without 2): only a restricted status stands on it.
    n3 = notice(plot_src(), seq=3, prev="2" * 64)
    wi.apply_source_rights_notice(session, key=PUB, notice=n3)
    d3 = cv.canonical_sha256(n3)
    out = rs.apply_attestation(session, key=PUB, attestation=attestation([src(plot_src(), 3, d3, "restricted")]))
    assert out["results"][0]["status"] == rs.IN_SYNC
    gaps = {s["sourceSha256"]: s["chainGaps"] for o in rs.state(session)["owners"] for s in o["sources"]}
    assert gaps[plot_src()] == [1, 2]


def test_gap_overtaken_by_restriction_then_reverified_is_in_sync(session, world, enforced):
    """Staging finding: the notices a restriction overtook are stale for ever (409), so once a later
    verified-for-use notice follows the restriction, Research and Workspace agree and the source is in-sync."""
    live(session, world)
    n3 = notice(plot_src(), seq=3, prev="2" * 64)
    wi.apply_source_rights_notice(session, key=PUB, notice=n3)
    n4 = notice(plot_src(), status="verified-for-use", seq=4, prev=cv.canonical_sha256(n3))
    out4 = wi.apply_source_rights_notice(session, key=PUB, notice=n4)
    assert out4["chainGap"] is False
    out = rs.apply_attestation(session, key=PUB, attestation=attestation(
        [src(plot_src(), 4, cv.canonical_sha256(n4), "verified-for-use")]))
    assert out["results"][0]["status"] == rs.IN_SYNC
    gaps = {s["sourceSha256"]: s["chainGaps"] for o in rs.state(session)["owners"] for s in o["sources"]}
    assert gaps[plot_src()] == [1, 2]


def test_attestation_replay_order_and_tampering(session, world, enforced):
    live(session, world)
    a = attestation([src(soil_src())])
    first = rs.apply_attestation(session, key=PUB, attestation=a)
    again = rs.apply_attestation(session, key=PUB, attestation=a)
    assert again["replayed"] and again["results"] == first["results"]
    assert len(session.scalars(select(rs.SourceRightsAttestationLog)).all()) == 1
    clash = attestation([src(plot_src())], att_id=a["attestationId"])
    with pytest.raises(PermissionError):
        rs.apply_attestation(session, key=PUB, attestation=clash)
    with pytest.raises(wi.StaleNotice):
        rs.apply_attestation(session, key=PUB, attestation=attestation(
            [src(soil_src())], issued=datetime.now(timezone.utc) - timedelta(hours=1)))
    with pytest.raises(ValueError, match="future"):
        rs.apply_attestation(session, key=PUB, attestation=attestation(
            [src(soil_src())], issued=datetime.now(timezone.utc) + timedelta(hours=1)))
    forged = attestation([src(soil_src())], k=OTHER)
    with pytest.raises(PermissionError):
        rs.apply_attestation(session, key=PUB, attestation=forged)
    tampered = attestation([src(soil_src())])
    tampered["sources"][0]["sequence"] = 5
    with pytest.raises(PermissionError):
        rs.apply_attestation(session, key=PUB, attestation=tampered)


@pytest.mark.parametrize("change", [{"ownerScope": "a"}, {"issuedAt": "yesterday"}, {"sources": "all"},
                                    {"sources": [{"sourceSha256": "x", "sequence": 0}]},
                                    {"sources": [{"sourceSha256": "a" * 64, "sequence": -1}]},
                                    {"sources": [{"sourceSha256": "a" * 64, "sequence": True}]}])
def test_malformed_attestations_are_refused(session, change):
    body = {k: v for k, v in attestation([src("a" * 64)]).items() if k != "signature"}
    body.update(change)
    with pytest.raises((ValueError, PermissionError)):
        rs.apply_attestation(session, key=PUB, attestation=sign(rs.ATTESTATION_SCHEMA, body))


def test_another_owners_attestation_never_confirms_my_sources(session, world, client, enforced):
    live(session, world)
    rs.apply_attestation(session, key=PUB, attestation=attestation([src(soil_src()), src(plot_src())],
                                                                    owner="workspace-user:b"))
    session.commit()
    assert client.get(f"{P}/objects/{key(session, world['soil'])}/figure").status_code == 404


def test_state_lists_sources_live_figures_need(session, world, enforced):
    live(session, world)
    st = rs.state(session)
    mine = next(o for o in st["owners"] if o["ownerScope"] == OWNER)
    assert {s["sourceSha256"] for s in mine["sources"]} == {soil_src(), plot_src()}
    assert all(s["liveFigures"] == 1 and s["appliedSequence"] == 0 and s["confirmation"] is None for s in mine["sources"])
    assert st["freshnessWindowSeconds"] == 600


# --- Key rotation -----------------------------------------------------------------------------------

def test_rotation_keeps_queued_notices_verifiable_and_refuses_the_retired_key_after(session, world):
    live(session, world)
    cut = datetime.now(timezone.utc)
    ring = keyring((KEY, {"notAfter": cut.isoformat()}), (NEW, {}))
    old = notice(soil_src(), effectiveAt=(cut - timedelta(minutes=5)).isoformat())
    assert wi.apply_source_rights_notice(session, key=ring, notice=old)["replayed"] is False
    late = notice(plot_src(), effectiveAt=(cut + timedelta(minutes=5)).isoformat())
    with pytest.raises(PermissionError, match="retired"):
        wi.apply_source_rights_notice(session, key=ring, notice=late)
    fresh = notice(plot_src(), _key=NEW)
    assert wi.apply_source_rights_notice(session, key=ring, notice=fresh)["replayed"] is False
    # Attestations are fresh statements: only a current key may sign them.
    with pytest.raises(PermissionError, match="retired|current key"):
        rs.apply_attestation(session, key=ring, attestation=attestation([src(soil_src())], k=KEY))
    assert rs.apply_attestation(session, key=ring, attestation=attestation([src(soil_src())], k=NEW))["results"]


def test_revoked_and_unknown_keys_verify_nothing(session, world):
    live(session, world)
    ring = keyring((KEY, {"revoked": True}), (NEW, {}))
    with pytest.raises(PermissionError, match="revoked"):
        wi.apply_source_rights_notice(session, key=ring, notice=notice(soil_src()))
    with pytest.raises(PermissionError, match="does not trust"):
        wi.apply_source_rights_notice(session, key=keyring((NEW, {})), notice=notice(soil_src()))


def test_keyring_file_formats():
    legacy = wi.parse_keyring(base64.b64encode(raw(KEY)).decode())
    assert list(legacy.entries) == [kid(KEY)]
    with pytest.raises(RuntimeError, match="wrong key id"):
        wi.parse_keyring(json.dumps({"schema": wi.KEYRING_SCHEMA, "keys": [
            {"keyId": kid(NEW), "publicKey": base64.b64encode(raw(KEY)).decode()}]}))
    with pytest.raises(RuntimeError, match="time zone"):
        wi.parse_keyring(json.dumps({"schema": wi.KEYRING_SCHEMA, "keys": [
            {"publicKey": base64.b64encode(raw(KEY)).decode(), "notAfter": "2026-11-11T00:00:00"}]}))
    with pytest.raises(RuntimeError):
        wi.parse_keyring(json.dumps({"schema": "other", "keys": []}))


# --- HTTP -----------------------------------------------------------------------------------------

def test_delivery_token_works_on_rights_routes_only(session, world, client, monkeypatch, tmp_path, enforced):
    live(session, world)
    session.commit()
    pub = tmp_path / "ring.json"
    pub.write_text(json.dumps({"schema": wi.KEYRING_SCHEMA, "keys": [
        {"keyId": kid(KEY), "publicKey": base64.b64encode(raw(KEY)).decode()}]}))
    tok = tmp_path / "delivery"
    tok.write_text("rights-delivery-test\n")
    for k, v in {"RESEARCH_WORKSPACE_PUBLIC_KEY_FILE": str(pub), "RESEARCH_RIGHTS_DELIVERY_TOKEN_FILE": str(tok)}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(wi, "workspace_configured", lambda: True)
    monkeypatch.setattr(cv, "writes_enabled", lambda: True)
    monkeypatch.setattr(cv, "_token", lambda: "convergence-test")
    h = {"Authorization": "Bearer rights-delivery-test"}
    assert client.get(f"{P}/convergence/workspace/source-rights-state").status_code == 401
    st = client.get(f"{P}/convergence/workspace/source-rights-state", headers=h)
    assert st.status_code == 200 and st.json()["schema"] == rs.STATE_SCHEMA
    a = client.post(f"{P}/convergence/workspace/source-rights-attestations", headers=h,
                    json={"attestation": attestation([src(soil_src()), src(plot_src())])})
    assert a.status_code == 200 and {r["status"] for r in a.json()["results"]} == {rs.IN_SYNC}
    assert client.get(f"{P}/objects/{key(session, world['soil'])}/figure").status_code == 200
    n = client.post(f"{P}/convergence/workspace/source-rights-notices", headers=h, json={"notice": notice(soil_src())})
    assert n.status_code == 200 and n.json()["revokedObjectIds"] == [world["soil"]]
    forged = client.post(f"{P}/convergence/workspace/source-rights-attestations", headers=h,
                         json={"attestation": attestation([src(soil_src())], k=OTHER)})
    assert forged.status_code == 403
    # The delivery credential is not a convergence credential.
    assert client.post(f"{P}/convergence/workspace/execution-runs", headers=h, json={"grant": "x"}).status_code == 401
    assert client.post(f"{P}/convergence/workspace/withdrawals", headers=h, json={"notice": {}}).status_code == 401
