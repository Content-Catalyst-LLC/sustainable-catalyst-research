"""SC-FIG-04.1: Workspace-signed source rights notices applied by Research, and runtime trend intervals.

A notice is the owner's signed record of a licence status for exact dataset bytes. Research applies it to that
owner's figures only: restricted revokes and purges, and is recorded in the editorial audit chain; a later
verified-for-use notice records the new status without republishing anything. Synthetic data only.
"""
import uuid

import pytest
from sqlalchemy import select

from tests.test_sc_ed01_editorial import OWNER, OWNER_ACTOR
from tests.test_sc_fig03a_governed_figures import (  # noqa: F401 (fixtures)
    ARTICLE, P, PLOT, SOIL, client, graph_keys, key, live, publish, source_sha, stores, world,
)
from tests.test_sc_model01_workspace_ingest import OTHER, PUB, session, sign  # noqa: F401

from app import convergence as cv  # noqa: E402
from app import editorial as ed  # noqa: E402
from app import figure_releases as fr  # noqa: E402
from app import object_registry as md  # noqa: E402
from app import workspace_ingest as wi  # noqa: E402

def notice(sha, status="restricted", seq=1, prev=None, owner=OWNER, _key=None, **over):
    body = {"schema": wi.SOURCE_RIGHTS_SCHEMA, "issuer": wi.ISSUER, "audience": wi.AUDIENCE,
            "noticeId": f"srn_{uuid.uuid4().hex}", "ownerScope": owner, "projectId": "proj-1", "datasetId": "ds-1",
            "datasetRevision": 1, "sourceSha256": sha, "licenceStatus": status, "sequence": seq,
            "previousNoticeSha256": prev, "effectiveAt": "2026-11-11T20:00:00+00:00", "reason": "synthetic drill"}
    body.update(over)
    return sign(wi.SOURCE_RIGHTS_SCHEMA, body, **({"key": _key} if _key is not None else {}))


def apply(session, n, key=PUB):
    return wi.apply_source_rights_notice(session, key=key, notice=n)


def soil_src():
    return source_sha(SOIL["bundleSha256"])


def test_restricted_notice_revokes_purges_and_is_audited(session, world, client):
    live(session, world)
    session.commit()
    soil, plot = key(session, world["soil"]), key(session, world["plot"])
    assert client.get(f"{P}/objects/{soil}/figure").status_code == 200
    out = apply(session, notice(soil_src()))
    session.commit()
    assert out["revokedObjectIds"] == [world["soil"]] and SOIL["bundleSha256"] in out["figureBytesRemoved"]
    assert out["articles"] == [ARTICLE] and len(out["auditEvents"]) == 1
    # Public figure, files, listing and graph: gone. The other source's figure is untouched.
    assert client.get(f"{P}/objects/{soil}/figure").status_code == 404
    assert client.get(f"{P}/objects/{soil}/figure/files/{SOIL['bundleSha256']}").status_code == 404
    assert client.get(f"{P}/objects/{soil}").status_code == 404 and soil not in graph_keys(client)
    assert not (world["release"] / SOIL["bundleSha256"]).exists()
    assert client.get(f"{P}/objects/{plot}/figure").status_code == 200
    # Audit: a source-rights-changed event on the article; the chain stays valid and explains public state.
    log = ed.audit_log(session, OWNER, ARTICLE)
    ev = [e for e in log if e["action"] == "source-rights-changed"]
    assert len(ev) == 1 and ev[0]["detail"]["sourceSha256"] == soil_src() and ev[0]["detail"]["licenceStatus"] == "restricted"
    integ = ed.editorial_integrity(session)
    assert integ["chain"]["valid"] and not integ["findings"]
    # The applied notice is kept, immutable in PostgreSQL (trigger) and append-only here.
    row = session.scalar(select(wi.WorkspaceSourceRightsNotice))
    assert row.source_sha256 == soil_src() and row.revoked_object_ids == [world["soil"]]


def test_stale_rights_block_reapproval_repinning_and_rollback(session, world):
    live(session, world)
    apply(session, notice(soil_src()))
    session.commit()
    obj = session.get(md.ResearchObject, world["soil"])
    assert obj.review_state == "revoked"
    binding = fr.binding_of(session, obj)[1]
    assert any("withdrawn after approval" in e for e in fr.source_rights_errors(session, OWNER, binding))
    with pytest.raises(PermissionError):
        publish(session, world, n=2)  # re-pinning the revoked figure
    session.rollback()
    publish(session, world, n=2, figures=("plot",))  # a revision without it is fine
    with pytest.raises((PermissionError, ed.Conflict)) as exc:
        ed.rollback_article(session, OWNER_ACTOR, slug=ARTICLE, revision=1, reason="drill")
    assert "already the published" not in str(exc.value)


def test_reinstated_rights_do_not_republish(session, world, client):
    live(session, world)
    first = apply(session, notice(soil_src()))
    session.commit()
    n1 = session.scalar(select(wi.WorkspaceSourceRightsNotice))
    second = apply(session, notice(soil_src(), "verified-for-use", seq=2, prev=n1.notice_sha256))
    session.commit()
    assert second["revokedObjectIds"] == [] and second["licenceStatus"] == "verified-for-use"
    assert fr.source_rights(session, OWNER)[soil_src()] == "verified-for-use"
    obj = session.get(md.ResearchObject, world["soil"])
    assert obj.review_state == "revoked" and obj.visibility == "private"
    assert client.get(f"{P}/objects/{key(session, world['soil'])}/figure").status_code == 404
    assert first["revokedObjectIds"] == [world["soil"]]


def test_duplicate_notice_is_idempotent(session, world):
    live(session, world)
    n = notice(soil_src())
    a, b = apply(session, n), apply(session, n)
    assert not a["replayed"] and b["replayed"] and b["revokedObjectIds"] == a["revokedObjectIds"]
    assert len(session.scalars(select(wi.WorkspaceSourceRightsNotice)).all()) == 1
    assert sum(e["action"] == "source-rights-changed" for e in ed.audit_log(session, OWNER, ARTICLE)) == 1


def test_stale_and_out_of_order_notices_are_refused(session, world):
    live(session, world)
    apply(session, notice(soil_src(), seq=2))
    with pytest.raises(wi.StaleNotice, match="newer"):
        apply(session, notice(soil_src(), "verified-for-use", seq=1))
    with pytest.raises(wi.StaleNotice, match="newer"):
        apply(session, notice(soil_src(), "verified-for-use", seq=2))
    with pytest.raises(wi.StaleNotice, match="does not follow"):
        apply(session, notice(soil_src(), "verified-for-use", seq=3, prev="0" * 64))


def test_a_relaxing_notice_that_skips_the_chain_is_refused(session, world):
    """Staging found this: notice 4 arrived before 3 and was applied. Only a restriction may cross a gap."""
    live(session, world)
    apply(session, notice(soil_src()))
    n1 = session.scalar(select(wi.WorkspaceSourceRightsNotice))
    with pytest.raises(wi.StaleNotice, match="does not follow"):
        apply(session, notice(soil_src(), "verified-for-use", seq=3, prev="0" * 64))
    with pytest.raises(wi.StaleNotice, match="does not follow"):
        apply(session, notice(soil_src(), "verified-for-use", seq=2, prev=None))
    ok = apply(session, notice(soil_src(), "verified-for-use", seq=2, prev=n1.notice_sha256))
    assert ok["chainGap"] is False and ok["sequence"] == 2


def test_a_first_relaxing_notice_must_start_the_chain(session, world):
    live(session, world)
    with pytest.raises(wi.StaleNotice, match="does not follow"):
        apply(session, notice(soil_src(), "verified-for-use", seq=2, prev="1" * 64))
    with pytest.raises(wi.StaleNotice, match="does not follow"):
        apply(session, notice(soil_src(), "verified-for-use", seq=1, prev="1" * 64))
    assert apply(session, notice(soil_src(), "verified-for-use"))["chainGap"] is False


def test_a_restriction_across_a_gap_applies_and_records_the_gap(session, world):
    live(session, world)
    session.commit()
    out = apply(session, notice(soil_src(), seq=3, prev="2" * 64))
    session.commit()
    assert out["chainGap"] is True and out["revokedObjectIds"] == [world["soil"]]
    ev = [e for e in ed.audit_log(session, OWNER, ARTICLE) if e["action"] == "source-rights-changed"]
    assert len(ev) == 1 and ev[0]["detail"]["chainGap"] is True
    # The missed notice 2 is now older than the applied one: refused, whatever it says.
    with pytest.raises(wi.StaleNotice, match="newer"):
        apply(session, notice(soil_src(), "verified-for-use", seq=2, prev="1" * 64))
    replay = apply(session, notice(soil_src(), seq=3, prev="2" * 64, noticeId=out["noticeId"]))
    assert replay["replayed"] is True and replay["sequence"] == 3


@pytest.mark.parametrize("change", [
    {"issuer": "someone-else"}, {"audience": "sc-workbench"}, {"licenceStatus": "pending"}, {"sequence": 0},
    {"sequence": True}, {"sourceSha256": "not-a-sha"}, {"ownerScope": "a"},
])
def test_malformed_or_misaddressed_notices_are_refused(session, world, change):
    live(session, world)
    with pytest.raises((ValueError, PermissionError)):
        apply(session, notice(soil_src(), **change))
    assert session.get(md.ResearchObject, world["soil"]).review_state != "revoked"


def test_forged_and_tampered_notices_are_refused(session, world):
    live(session, world)
    forged = notice(soil_src(), _key=OTHER)
    with pytest.raises(PermissionError):
        apply(session, forged)
    tampered = notice(soil_src(), "verified-for-use")
    tampered["licenceStatus"] = "restricted"
    with pytest.raises(PermissionError):
        apply(session, tampered)
    assert session.get(md.ResearchObject, world["soil"]).review_state != "revoked"


def test_another_owners_notice_never_touches_my_figures(session, world, client):
    live(session, world)
    session.commit()
    out = apply(session, notice(soil_src(), owner="workspace-user:b"))
    session.commit()
    assert out["revokedObjectIds"] == [] and out["articles"] == []
    assert session.get(md.ResearchObject, world["soil"]).review_state != "revoked"
    assert client.get(f"{P}/objects/{key(session, world['soil'])}/figure").status_code == 200
    assert fr.source_rights(session, OWNER).get(soil_src()) != "restricted"
    # Recorded for owner b, in b's audit trail only (no article of a's names it).
    assert not [e for e in ed.audit_log(session, OWNER) if e["action"] == "source-rights-changed"]
    assert [e for e in ed.audit_log(session, "workspace-user:b") if e["action"] == "source-rights-changed"]


def test_notice_for_a_source_no_figure_uses_is_recorded_without_effect(session, world):
    live(session, world)
    out = apply(session, notice("e" * 64))
    assert out["revokedObjectIds"] == [] and out["figureBytesRemoved"] == [] and out["articles"] == []
    assert session.get(md.ResearchObject, world["soil"]).review_state != "revoked"


def test_notice_overrides_an_earlier_cleared_binding_and_blocks_promotion(session, world):
    """Before release: a restricted notice makes the figure's cleared declaration stale."""
    apply(session, notice(soil_src()))
    obj = session.get(md.ResearchObject, world["soil"])
    with pytest.raises(PermissionError):
        cv.promote_public_projection(session, owner_scope=OWNER, object_id=obj.id, signed_only=True)


def test_http_route_is_authenticated_and_maps_conflicts(session, world, client, monkeypatch):
    live(session, world)
    session.commit()
    monkeypatch.setattr(wi, "load_public_key", lambda *a: PUB)
    monkeypatch.setattr(wi, "workspace_configured", lambda: True)
    monkeypatch.setattr(cv, "writes_enabled", lambda: True)
    monkeypatch.setattr(cv, "_token", lambda: "convergence-test")
    route = f"{P}/convergence/workspace/source-rights-notices"
    n = notice(soil_src(), seq=2)
    assert client.post(route, json={"notice": n}).status_code == 401
    assert client.post(route, json={"notice": n}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.post(route, json={"notice": n}, headers={"Authorization": "Bearer convergence-test"})
    assert ok.status_code == 200 and ok.json()["revokedObjectIds"] == [world["soil"]]
    stale = client.post(route, json={"notice": notice(soil_src(), "verified-for-use", seq=1)},
                        headers={"Authorization": "Bearer convergence-test"})
    assert stale.status_code == 409
    forged = client.post(route, json={"notice": notice(soil_src(), _key=OTHER)}, headers={"Authorization": "Bearer convergence-test"})
    assert forged.status_code == 403


# --- Trend intervals (recorded as the runtime computed them; never derived here) ---------------------

INTERVALS = {"semantics": "confidence-interval", "level": 0.95, "method": "stats::confint.lm", "distribution": "Student t",
             "degreesOfFreedom": 22.0, "computedBy": "runtime", "values": {"slope": [0.1, 1.5], "intercept": [27.0, 31.0]}}
VALUES = {"slope": 0.83, "intercept": 29.1}


def test_runtime_intervals_are_recorded_as_computed():
    assert wi.normalize_intervals(INTERVALS, VALUES)["values"] == {"intercept": [27.0, 31.0], "slope": [0.1, 1.5]}


@pytest.mark.parametrize("change", [
    {"computedBy": "research"}, {"semantics": "credible-interval"}, {"level": 1.0}, {"level": 0.3}, {"degreesOfFreedom": 0},
    {"values": {"slope": [1.0, 1.5]}}, {"values": {"other": [0.0, 1.0]}}, {"values": {"slope": [0.1]}}, {"method": None},
    {"values": {}},
])
def test_intervals_that_are_inconsistent_or_not_from_the_runtime_are_dropped(change):
    assert wi.normalize_intervals({**INTERVALS, **change}, VALUES) is None


def test_assumptions_are_recorded_only_as_declared_unchecked():
    ok = {"declaredBy": "model-revision", "checked": False, "statements": ["Independent errors"]}
    assert wi.normalize_assumptions(ok) == ok
    assert wi.normalize_assumptions({**ok, "checked": True}) is None
    assert wi.normalize_assumptions({**ok, "statements": []}) is None
