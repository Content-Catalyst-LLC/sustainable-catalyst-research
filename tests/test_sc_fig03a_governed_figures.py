"""SC-FIG-03A governed figure approval and publication (synthetic fixtures only; public CI).

Figures reach the public API only through the governed path: a Workspace-signed export whose figure
output bytes Research verifies, four Workspace-signed approvals of that exact binding, editorial
publication of an article revision that references the figure, and Research's release step. No test
here sets an approval or visibility column directly. A local Ed25519 key plays Workspace's signing key;
real Workspace-issued, step-up-bound records are exercised in the isolated staging acceptance.
"""
import base64
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.test_sc_ed01_editorial import AREA, OWNER, OWNER_ACTOR, approve, publish_area, signed_record
from tests.test_sc_model01_workspace_ingest import (  # noqa: F401 (session is a fixture)
    BINDING, KEY, OTHER, PUB, export, grant, ingest, session, sign,
)
from app import convergence as cv  # noqa: E402
from app import editorial as ed  # noqa: E402
from app import figure_releases as fr  # noqa: E402
from app import object_registry as md  # noqa: E402
from app import public_figures as pf  # noqa: E402
from app import workspace_ingest as wi  # noqa: E402
from app import workspaces as ws  # noqa: E402
from app.main import app  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "sc_fig02b_public"
INDEX = json.loads((FIX / "FIXTURES.json").read_text(encoding="utf-8"))
ARTICLE = INDEX["article"]
SOIL, PLOT = INDEX["figures"]
P = "/api/research/v1"
DIMS = tuple(cv.APPROVAL_ACTIONS)


def raw_of(sha):
    return (FIX / "store" / sha / sha).read_bytes()


def bundle_of(sha):
    return json.loads(raw_of(sha))


def source_sha(sha):
    return next(s["sha256"] for s in bundle_of(sha)["sourceRefs"] if "sha256" in s)


def files_of(sha, raw=None):
    """Request entries for a fixture bundle (or a variant ``raw`` of it) and its view files."""
    raw = raw or raw_of(sha)
    views = [f for f in sorted((FIX / "store" / sha).iterdir()) if f.name != sha]
    return [{"sha256": hashlib.sha256(raw).hexdigest(), "contentBase64": base64.b64encode(raw).decode()}] + \
        [{"sha256": f.name, "contentBase64": base64.b64encode(f.read_bytes()).decode()} for f in views]


def variant(sha, change):
    """A changed revision of a fixture bundle (same views): new bytes, new hash."""
    b = bundle_of(sha)
    change(b)
    raw = (json.dumps(b, indent=1, ensure_ascii=False) + "\n").encode()
    return raw, hashlib.sha256(raw).hexdigest()


def fig_output(sha, output_id):
    return {"outputId": output_id, "role": "figure", "sha256": sha, "mediaType": wi.FIGURE_MEDIA, "label": "Synthetic figure"}


def fig_export(run="run-1", grant_id="rxg_1", figures=((SOIL["bundleSha256"], "soil"), (PLOT["bundleSha256"], "plot")),
               owner="a", **kw):
    outputs = [{"outputId": "polyglot-result", "role": "result", "sha256": "b" * 64, "mediaType": "application/json",
                "label": "R result"}] + [fig_output(sha, oid) for sha, oid in figures]
    bindings = [dict(BINDING, artifactSha256=source_sha(SOIL["bundleSha256"])),
                dict(BINDING, datasetId="ds-2", artifactSha256=source_sha(PLOT["bundleSha256"]))]
    return export(owner=owner, run=run, grant_id=grant_id, outputs=outputs, bindings=bindings, **kw)


@pytest.fixture
def stores(tmp_path, monkeypatch):
    release = tmp_path / "figure-releases"
    release.mkdir()
    monkeypatch.setenv("RESEARCH_PUBLIC_FIGURES", "staging-synthetic")
    monkeypatch.setenv("RESEARCH_FIGURE_RELEASE_STORE", str(release))
    monkeypatch.setenv("RESEARCH_PUBLIC_FIGURE_STORE", str(release))
    return {"release": release, "candidates": Path(tmp_path / "figure-candidates")}


@pytest.fixture
def world(session, stores):
    out = ingest(session, grant(), fig_export(), entry=ARTICLE,
                 figure_files=files_of(SOIL["bundleSha256"]) + files_of(PLOT["bundleSha256"]))
    o = {k.split(":")[-1] if k.startswith("output:") else k: v["id"] for k, v in out["objects"].items()}
    ed.create_article(session, OWNER_ACTOR, area_slug=AREA, project_key="unused", workspace_project_id="proj-1",
                      slug=ARTICLE, title="Synthetic soil carbon scenarios")
    return {**o, "ingest": out, **stores}


def reproduce(session, rid):
    """Independent Julia replay over the same pinned inputs (required before the result can be public)."""
    return wi.ingest_reproduction(session, key=PUB, original_id=rid, grant=grant(run="run-julia", grant_id="gj"), rel_tol=1e-9,
                                  abs_tol=1e-12, fetch=lambda _: fig_export(run="run-julia", grant_id="gj"))


def make_publishable(session, w, figures=("soil", "plot")):
    assert reproduce(session, w["polyglot-result"])["status"] == "reproduced"
    for oid in (w["polyglot-result"], w["run"], *(w[f] for f in figures)):
        approve(session, oid)
        cv.promote_public_projection(session, owner_scope=OWNER, object_id=oid, signed_only=True)


def refs(session, w, figures=("soil", "plot")):
    pin = lambda oid: ed.current_pin(session, session.get(md.ResearchObject, oid))  # noqa: E731
    out = [{"objectId": w["polyglot-result"], "role": "result", "pin": pin(w["polyglot-result"])},
           {"objectId": w["run"], "role": "study-package", "pin": pin(w["run"]), "packageId": "study_1"}]
    return out + [{"objectId": w[f], "role": "figure", "pin": pin(w[f]), "caption": "Synthetic figure."} for f in figures]


def publish(session, w, n=1, figures=("soil", "plot"), title="Synthetic soil carbon scenarios"):
    ed.add_revision(session, OWNER_ACTOR, slug=ARTICLE, base_revision=n - 1, title=title, summary="Synthetic data only.",
                    sections=[{"heading": "Overview", "body": "Modelled and hypothetical synthetic values."}],
                    references=refs(session, w, figures))
    sub = ed.submit_revision(session, OWNER_ACTOR, slug=ARTICLE, revision=n)
    for dim in DIMS:
        ed.record_article_approval(session, OWNER_ACTOR, slug=ARTICLE, revision=n, dimension=dim,
                                   record=signed_record(sub["subjectRef"], dim), key=PUB)
    return ed.publish_revision(session, OWNER_ACTOR, slug=ARTICLE, revision=n)


def live(session, w, **kw):
    make_publishable(session, w)
    publish_area(session)
    return publish(session, w, **kw)


@pytest.fixture
def client(session, monkeypatch):
    session.commit()
    monkeypatch.setattr(ws, "_session_factory", lambda: session)
    return TestClient(app)


def key(session, oid):
    return session.get(md.ResearchObject, oid).public_key


def stored(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


# --- 1. Binding at ingest --------------------------------------------------------------------


def test_ingest_binds_exact_revision_artifacts_lineage_calculations_and_ownership(session, world):
    soil = session.get(md.ResearchObject, world["soil"])
    prov, b = fr.binding_of(session, soil)
    assert b["bundleSha256"] == SOIL["bundleSha256"] == soil.source_sha256
    assert [a["sha256"] for a in b["artifacts"]] == [SOIL["viewSha256"]] and b["artifacts"][0]["hashMode"] == "canonical-json"
    assert [s["sha256"] for s in b["sourceLineage"]] == [source_sha(SOIL["bundleSha256"])]
    assert b["citations"][0]["doi"] == "10.0000/synthetic.0001"  # literature citation, not data lineage
    calc = b["calculationRefs"]
    assert calc["run"]["runId"] == "run-1" and calc["run"]["inputLineage"] == "artifact-bound-verified"
    assert calc["runtimeReceipts"] == [{"receiptId": "pgr-run-1", "resultSha256": "b" * 64}]
    assert b["ownership"]["workspaceProject"] == "workspace-project:proj-1"
    assert b["ownership"]["ownerScopeSha256"] == cv.canonical_sha256(OWNER)
    # The approval subject names the provenance hash, which covers the whole binding.
    assert cv.subject_ref(soil, prov) == f"research-object:{soil.id}#{cv.canonical_sha256(prov.record)}"
    assert world["ingest"]["figureBindings"]["output:soil"]["artifacts"] == 1
    # Bytes wait in the private candidate store; nothing is released yet.
    assert stored(world["candidates"]) == sorted(
        f"{s['bundleSha256']}/{n}" for s in (SOIL, PLOT) for n in (s["bundleSha256"], s["viewSha256"]))
    assert stored(world["release"]) == []


def _ingest_with(session, files, exp=None, entry="other-article", **kw):
    return ingest(session, grant(grant_id="rxg_x", **kw), exp or fig_export(grant_id="rxg_x", **kw), entry=entry,
                  figure_files=files)


@pytest.mark.parametrize("files, match", [
    (lambda: files_of(SOIL["bundleSha256"])[:1] + files_of(PLOT["bundleSha256"]), "artifact .* was not supplied"),
    (lambda: files_of(SOIL["bundleSha256"]) + files_of(PLOT["bundleSha256"]) +
     [{"sha256": hashlib.sha256(b"x").hexdigest(), "contentBase64": base64.b64encode(b"x").decode()}], "no figure output"),
    (lambda: [dict(files_of(SOIL["bundleSha256"])[1], sha256="0" * 64)], "does not match"),
    (lambda: [{"sha256": "1" * 64, "contentBase64": "not base64!"}], "base64"),
    (lambda: [{"sha256": "1" * 64}], "sha256, contentBase64"),
    (lambda: files_of(SOIL["bundleSha256"]) * 7, "at most 12|distinct"),
])
def test_figure_bytes_must_match_the_signed_output_and_the_bundle(session, stores, files, match):
    with pytest.raises(ValueError, match=match):
        _ingest_with(session, files())
    assert stored(stores["release"]) == []


def test_figure_sources_must_be_verified_run_inputs(session, stores):
    exp = export(grant_id="rxg_x", outputs=[fig_output(SOIL["bundleSha256"], "soil")], bindings=[BINDING])
    with pytest.raises(ValueError, match="source lineage unbound"):
        _ingest_with(session, files_of(SOIL["bundleSha256"]), exp=exp)
    raw, sha = variant(SOIL["bundleSha256"], lambda b: b.update(sourceRefs=[s for s in b["sourceRefs"] if "doi" in s]))
    exp = export(grant_id="rxg_x", outputs=[fig_output(sha, "soil")], bindings=[dict(BINDING, artifactSha256=source_sha(SOIL["bundleSha256"]))])
    with pytest.raises(ValueError, match="needs at least one source"):
        _ingest_with(session, files_of(SOIL["bundleSha256"], raw), exp=exp)


def test_unverified_runs_never_bind_figures(session, stores):
    exp = fig_export(grant_id="rxg_x", status="failed-or-unverified")
    out = _ingest_with(session, None, exp=exp)
    assert out["figureBindings"] == {} and "output:soil" not in out["objects"]


def test_a_figure_without_its_bytes_is_registered_but_can_never_be_published(session, stores):
    out = ingest(session, grant(), fig_export(), entry=ARTICLE, figure_files=None)
    fid = out["objects"]["output:soil"]["id"]
    approve(session, fid)
    with pytest.raises(PermissionError, match="figure binding"):
        cv.promote_public_projection(session, owner_scope=OWNER, object_id=fid, signed_only=True)


# --- 2. Signed approvals of the exact revision -------------------------------------------------


def _subject(session, oid):
    obj = session.get(md.ResearchObject, oid)
    return cv.subject_ref(obj, cv._provenance(session, obj))


def test_approvals_are_signed_owned_and_bound_to_the_exact_binding(session, world):
    fid = world["soil"]
    subject = _subject(session, fid)
    for record, err in ((signed_record(subject, "scientific", key=OTHER), ValueError),
                        (signed_record(subject, "scientific", owner="workspace-user:b"), PermissionError),
                        (signed_record(_subject(session, world["plot"]), "scientific"), ValueError)):
        with pytest.raises(err):
            cv.record_signed_approval(session, owner_scope=OWNER, object_id=fid, dimension="scientific", record=record, key=PUB)
    approve(session, fid, dims=("scientific", "rights", "ownership"))
    with pytest.raises(PermissionError, match="publication"):
        cv.promote_public_projection(session, owner_scope=OWNER, object_id=fid, signed_only=True)
    # Another owner cannot promote or even see the object.
    with pytest.raises((LookupError, PermissionError)):
        cv.promote_public_projection(session, owner_scope="workspace-user:b", object_id=fid, signed_only=True)


def test_step_up_policy_applies_to_figure_approvals(session, world, monkeypatch):
    from tests.test_sc_pub_m1_release_lifecycle import stepped_record
    monkeypatch.setenv(cv.STEP_UP_POLICY_ENV, "required")
    subject = _subject(session, world["soil"])
    with pytest.raises(PermissionError):
        cv.record_signed_approval(session, owner_scope=OWNER, object_id=world["soil"], dimension="scientific",
                                  record=stepped_record(subject, "scientific", step_up=False), key=PUB)
    cv.record_signed_approval(session, owner_scope=OWNER, object_id=world["soil"], dimension="scientific",
                              record=stepped_record(subject, "scientific"), key=PUB)


def test_approvals_of_an_earlier_figure_revision_do_not_transfer(session, world):
    """A changed bundle from a new run state is a new revision; the approved one becomes superseded."""
    make_publishable(session, world)
    old_subject = _subject(session, world["soil"])
    raw, sha = variant(SOIL["bundleSha256"], lambda b: b["rendering"].update(caption=b["rendering"]["caption"] + " Revised."))
    new = ingest(session, grant(grant_id="rxg_2"), fig_export(grant_id="rxg_2", figures=((sha, "soil"),), repro="d" * 64),
                 entry=ARTICLE, figure_files=files_of(SOIL["bundleSha256"], raw))
    fid2 = new["objects"]["output:soil"]["id"]
    assert fid2 != world["soil"] and session.get(md.ResearchObject, fid2).source_sha256 == sha
    assert cv.approval_state(session, session.get(md.ResearchObject, fid2), signed_only=True) == {d: False for d in DIMS}
    with pytest.raises(ValueError):  # the old revision's approval names the old provenance hash
        cv.record_signed_approval(session, owner_scope=OWNER, object_id=fid2, dimension="scientific",
                                  record=signed_record(old_subject, "scientific"), key=PUB)
    old_ref = next(r for r in refs(session, world) if r["objectId"] == world["soil"])
    assert ed.verify_reference(session, OWNER, old_ref)["status"] == "superseded"
    publish_area(session)
    with pytest.raises(PermissionError, match="superseded"):
        publish(session, world)


# --- 2b. Source rights (SC-FIG-03B) ---------------------------------------------------------------


def _licence(status):
    def change(b):
        for src in b["sourceRefs"]:
            if "sha256" in src:
                src["licenceStatus"] = status
    return change


def test_restricted_source_makes_earlier_rights_approvals_stale(session, world, client):
    """A later signed binding records the source as restricted: figures relying on it are pulled at once."""
    live(session, world)
    session.commit()
    soil, plot = key(session, world["soil"]), key(session, world["plot"])
    assert client.get(f"{P}/objects/{soil}/figure").status_code == 200
    raw, sha = variant(SOIL["bundleSha256"], _licence("restricted"))
    out = ingest(session, grant(run="run-2", grant_id="rxg_r"), fig_export(run="run-2", grant_id="rxg_r", figures=((sha, "soil"),)),
                 entry=ARTICLE, figure_files=files_of(SOIL["bundleSha256"], raw))
    session.commit()
    assert world["soil"] in out["sourceRightsRevokedObjectIds"] and world["plot"] not in out["sourceRightsRevokedObjectIds"]
    assert SOIL["bundleSha256"] in out["figureBytesRemoved"] and not (world["release"] / SOIL["bundleSha256"]).exists()
    assert client.get(f"{P}/objects/{soil}/figure").status_code == 404
    assert soil not in graph_keys(client) and client.get(f"{P}/objects/{soil}").status_code == 404
    assert client.get(f"{P}/objects/{plot}/figure").status_code == 200  # a different source is unaffected
    # The restricted revision is revoked as well and cannot be promoted however many approvals it gets.
    new = out["objects"]["output:soil"]["id"]
    assert new in out["sourceRightsRevokedObjectIds"]
    approve(session, new)
    with pytest.raises(PermissionError, match="withdrew"):
        cv.promote_public_projection(session, owner_scope=OWNER, object_id=new, signed_only=True)
    # The revoked revision stays revoked: its approvals do not bring it back, and rollback cannot re-release it.
    with pytest.raises(PermissionError, match="withdrew"):
        cv.promote_public_projection(session, owner_scope=OWNER, object_id=world["soil"], signed_only=True)
    publish(session, world, n=2, figures=("plot",), title="Without the restricted figure")
    with pytest.raises(PermissionError):
        ed.rollback_article(session, OWNER_ACTOR, slug=ARTICLE, revision=1, reason="drill")


def test_a_figure_citing_a_source_withdrawn_later_cannot_be_released(session, world):
    """Even a figure ingested earlier with a cleared licence is refused once a later binding restricts it."""
    raw, sha = variant(SOIL["bundleSha256"], _licence("restricted"))
    ingest(session, grant(run="run-2", grant_id="rxg_r"), fig_export(run="run-2", grant_id="rxg_r", figures=((sha, "soil"),)),
           entry=ARTICLE, figure_files=files_of(SOIL["bundleSha256"], raw))
    obj = session.get(md.ResearchObject, world["soil"])
    assert obj.review_state == "revoked"
    binding = fr.binding_of(session, obj)[1]
    assert any("withdrawn after approval" in e for e in fr.source_rights_errors(session, OWNER, binding))


@pytest.mark.parametrize("status", ["pending", "restricted"])
def test_uncleared_source_licences_are_never_promoted(session, stores, status):
    raw, sha = variant(SOIL["bundleSha256"], _licence(status))
    out = ingest(session, grant(), fig_export(figures=((sha, "soil"),)), entry=ARTICLE,
                 figure_files=files_of(SOIL["bundleSha256"], raw))
    fid = out["objects"]["output:soil"]["id"]
    approve(session, fid)
    with pytest.raises(PermissionError, match="source rights are not cleared" if status == "pending" else "withdrew"):
        cv.promote_public_projection(session, owner_scope=OWNER, object_id=fid, signed_only=True)


def test_another_owners_rights_record_does_not_revoke_my_figures(session, world):
    raw, sha = variant(SOIL["bundleSha256"], _licence("restricted"))
    out = ingest(session, grant(owner="b", run="run-b", grant_id="rxg_b"),
                 fig_export(owner="b", run="run-b", grant_id="rxg_b", figures=((sha, "soil"),)),
                 area="metrics-of-sustainable-development", entry="ws-study-b", figure_files=files_of(SOIL["bundleSha256"], raw))
    assert world["soil"] not in out["sourceRightsRevokedObjectIds"]
    assert session.get(md.ResearchObject, world["soil"]).review_state != "revoked"


# --- 3. Release only at editorial publication ----------------------------------------------------


def test_nothing_is_released_or_served_before_publication(session, world, client):
    make_publishable(session, world)
    session.commit()
    assert stored(world["release"]) == []
    for f in ("soil", "plot"):
        assert client.get(f"{P}/objects/{key(session, f and world[f])}/figure").status_code == 404


def test_publication_releases_the_exact_approved_bytes(session, world, client):
    out = live(session, world)
    session.commit()
    assert out["publicationStatus"] == "published"
    assert stored(world["release"]) == stored(world["candidates"])
    rows = session.scalars(select(fr.FigureRelease)).all()
    assert len(rows) == 2 and all(r.record["approvalRecords"].keys() == set(DIMS) and r.revision_number == 1 for r in rows)
    for fixture, f in ((SOIL, "soil"), (PLOT, "plot")):
        r = client.get(f"{P}/objects/{key(session, world[f])}/figure")
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        pkg = r.json()
        assert pkg["schema"] == "sc-research-public-figure/2.0" and pkg["article"] == ARTICLE
        rel = pkg["release"]
        assert rel["bundleSha256"] == fixture["bundleSha256"] and rel["articleRevision"] == 1 and rel["authority"] == "sc-research"
        assert pkg["releaseContext"] == {"authority": "sc-research", "state": "released", "label": pf.CHANNEL_LABEL["staging-synthetic"],
                                         "bundleSha256": fixture["bundleSha256"], "articleRevision": 1,
                                         "releasedAt": rel["releasedAt"]}
        text = json.dumps(pkg)
        for private in (OWNER, "proj-1", "run-1", "workspace-", world[f], "approval_", "signature", "rxg_", "pgr-"):
            assert private not in text, (private, text[max(0, text.find(private) - 80):text.find(private) + 40])
        for item in pkg["files"]:
            body = client.get(item["url"])
            assert body.status_code == 200 and body.content == (FIX / "store" / fixture["bundleSha256"] / item["sha256"]).read_bytes()
            assert body.headers["content-type"] == "application/octet-stream" and body.headers["cache-control"] == "no-store"
            assert body.headers["content-security-policy"] == "default-src 'none'; sandbox"
    listed = client.get(f"{P}/objects", params={"article": ARTICLE, "kind": "figure"}).json()["items"]
    assert sorted(i["id"] for i in listed) == sorted(key(session, world[f]) for f in ("soil", "plot"))


def test_release_receipt_records_the_figures_in_the_audit_chain(session, world):
    out = live(session, world)
    event = session.scalar(select(ed.EditorialEvent).where(ed.EditorialEvent.event_sha256 == out["receipt"]["eventSha256"]))
    released = event.body["detail"]["figureReleases"]
    assert {r["bundleSha256"] for r in released} == {SOIL["bundleSha256"], PLOT["bundleSha256"]}
    assert out["receipt"]["figureReleases"] == released  # the receipt the owner keeps names them too
    assert ed.verify_chain(session)["valid"]


def test_release_rechecks_candidate_bytes_and_leaves_nothing_behind(session, world):
    make_publishable(session, world)
    publish_area(session)
    view = world["candidates"] / SOIL["bundleSha256"] / SOIL["viewSha256"]
    view.write_bytes(view.read_bytes().replace(b"synthetic", b"SYNTHETIC", 1))
    with pytest.raises(PermissionError, match="missing or altered"):
        publish(session, world)
    assert stored(world["release"]) == []
    assert session.scalars(select(fr.FigureRelease)).all() == []


@pytest.mark.parametrize("change, match", [
    (lambda b: b["project"].update(projectRef="research-entry:another-article"), "projectRef"),
    (lambda b: b["rendering"].update(caption=b["rendering"]["caption"] + " Contact someone@example.org."), "email"),
    (lambda b: b["scientificContext"].update(interpretation="Run in workspace-user:abc on /home/x"), "Workspace identifier"),
    (lambda b: b["publication"].update(bindingStatus="bound"), "synthetic test bundles only"),
    (lambda b: b["rendering"].update(altText=" "), "alt text"),
])
def test_release_reapplies_the_disclosure_policy(session, stores, change, match):
    raw, sha = variant(SOIL["bundleSha256"], change)
    out = ingest(session, grant(), fig_export(figures=((sha, "soil"),)), entry=ARTICLE, figure_files=files_of(SOIL["bundleSha256"], raw))
    w = {k.split(":")[-1] if k.startswith("output:") else k: v["id"] for k, v in out["objects"].items()}
    ed.create_article(session, OWNER_ACTOR, area_slug=AREA, project_key="unused", workspace_project_id="proj-1",
                      slug=ARTICLE, title="Synthetic")
    make_publishable(session, w, figures=("soil",))
    publish_area(session)
    with pytest.raises(PermissionError, match=match):
        publish(session, w, figures=("soil",))
    assert stored(stores["release"]) == []


def test_channel_off_releases_and_serves_nothing(session, world, client, monkeypatch):
    monkeypatch.setenv("RESEARCH_PUBLIC_FIGURES", "production")  # not an authorized channel yet
    live(session, world)
    session.commit()
    assert stored(world["release"]) == [] and client.get(f"{P}/public/figures/status").json()["enabled"] is False
    assert client.get(f"{P}/objects/{key(session, world['soil'])}/figure").status_code == 404


# --- 4. Lifecycle after release ------------------------------------------------------------------


def graph_keys(client):
    return {n["id"] for n in client.get(f"{P}/graph/metadata").json()["nodes"]}


def test_unlisted_figures_stay_reachable_by_link_but_leave_discovery_and_the_graph(session, world, client):
    live(session, world)
    session.commit()
    k = key(session, world["soil"])
    assert k in graph_keys(client)
    ed.unlist_article(session, OWNER_ACTOR, slug=ARTICLE, reason="hold")
    session.commit()
    assert client.get(f"{P}/objects/{k}/figure").status_code == 200
    assert k not in {i["id"] for i in client.get(f"{P}/objects", params={"kind": "figure"}).json()["items"]}
    assert k not in graph_keys(client)
    ed.relist_article(session, OWNER_ACTOR, slug=ARTICLE, reason="ok")
    session.commit()
    assert k in {i["id"] for i in client.get(f"{P}/objects", params={"kind": "figure"}).json()["items"]} and k in graph_keys(client)


def test_withdrawal_removes_package_files_graph_nodes_and_released_bytes(session, world, client):
    live(session, world)
    session.commit()
    k = key(session, world["soil"])
    urls = [f["url"] for f in client.get(f"{P}/objects/{k}/figure").json()["files"]]
    out = ed.withdraw_article(session, OWNER_ACTOR, slug=ARTICLE, reason="drill (private)", public_notice="Withdrawn by the author.")
    session.commit()
    assert out["receipt"]["action"] == "article-withdrawn" and out["receipt"]["figureBytesRemoved"]
    assert client.get(f"{P}/objects/{k}/figure").status_code == 404
    assert all(client.get(u).status_code == 404 for u in urls)
    assert k not in graph_keys(client) and client.get(f"{P}/objects/{k}").status_code == 404
    assert stored(world["release"]) == []  # bytes removed, not just hidden
    assert client.get(f"{P}/articles/{ARTICLE}/content").status_code == 410


def test_update_and_rollback_serve_only_the_live_revisions_figures(session, world, client):
    live(session, world)
    publish(session, world, n=2, figures=("soil",), title="Second")
    session.commit()
    soil, plot = key(session, world["soil"]), key(session, world["plot"])
    assert client.get(f"{P}/objects/{soil}/figure").json()["release"]["articleRevision"] == 2
    assert client.get(f"{P}/objects/{plot}/figure").status_code == 404
    assert not (world["release"] / PLOT["bundleSha256"]).exists()
    # Staging finding: the dropped figure also leaves listings, search, the graph and its object links.
    listed = lambda **q: {i["id"] for i in client.get(f"{P}/objects", params=q).json()["items"]}  # noqa: E731
    assert plot not in listed() | listed(kind="figure") | listed(article=ARTICLE) | graph_keys(client)
    assert soil in listed(kind="figure") and soil in graph_keys(client)
    assert client.get(f"{P}/objects/{plot}").status_code == 404
    assert client.get(f"{P}/objects/{plot}/provenance").status_code == 404
    ed.rollback_article(session, OWNER_ACTOR, slug=ARTICLE, revision=1, reason="drill")
    session.commit()
    assert client.get(f"{P}/objects/{plot}/figure").json()["release"]["articleRevision"] == 1  # re-released, re-verified
    assert (world["release"] / PLOT["bundleSha256"] / PLOT["viewSha256"]).is_file()
    assert plot in listed(kind="figure") and plot in graph_keys(client)


def test_promoted_figures_are_not_discoverable_before_release(session, world, client):
    make_publishable(session, world)
    session.commit()
    soil = key(session, world["soil"])
    assert soil not in {i["id"] for i in client.get(f"{P}/objects").json()["items"]} | graph_keys(client)
    assert client.get(f"{P}/objects/{soil}").status_code == 404


def test_retraction_and_workspace_withdrawal_remove_released_figures(session, world, client):
    live(session, world)
    session.commit()
    k = key(session, world["soil"])
    notice = sign(wi.WITHDRAWAL_SCHEMA, {"schema": wi.WITHDRAWAL_SCHEMA, "issuer": wi.ISSUER, "audience": wi.AUDIENCE,
                                          "ownerScope": OWNER, "projectId": "proj-1", "runId": "run-1",
                                          "withdrawnAt": "2026-11-11T00:00:00Z", "reason": "drill"})
    out = wi.apply_withdrawal(session, key=PUB, notice=notice)
    session.commit()
    assert world["soil"] in out["revokedObjectIds"] and set(out["figureBytesRemoved"]) == {SOIL["bundleSha256"], PLOT["bundleSha256"]}
    assert client.get(f"{P}/objects/{k}/figure").status_code == 404 and stored(world["release"]) == []


def test_retraction_removes_released_figures(session, world, client):
    live(session, world)
    ed.retract_article(session, OWNER_ACTOR, slug=ARTICLE, reason="silent removal")
    session.commit()
    assert client.get(f"{P}/objects/{key(session, world['soil'])}/figure").status_code == 404 and stored(world["release"]) == []


def test_tampered_or_missing_release_bytes_are_not_served(session, world, client):
    live(session, world)
    session.commit()
    k = key(session, world["plot"])
    view = world["release"] / PLOT["bundleSha256"] / PLOT["viewSha256"]
    original = view.read_bytes()
    view.write_bytes(original.replace(b"Plot A", b"Plot Z", 1))
    assert client.get(f"{P}/objects/{k}/figure").status_code == 404
    view.unlink()
    assert client.get(f"{P}/objects/{k}/figure").status_code == 404
    view.write_bytes(original)
    assert client.get(f"{P}/objects/{k}/figure").status_code == 200


def test_release_records_are_immutable_per_revision(session, world):
    live(session, world)
    row = session.scalars(select(fr.FigureRelease)).first()
    assert row.record_sha256 == cv.canonical_sha256(row.record)
    with pytest.raises(PermissionError, match="different bytes"):
        clone = deepcopy(row.record)
        session.add(fr.FigureRelease(object_id=row.object_id, entry_id=row.entry_id, revision_number=99,
                                     bundle_sha256=row.bundle_sha256, provenance_sha256="0" * 64, record=clone,
                                     record_sha256=cv.canonical_sha256(clone)))
        session.flush()
        entry = session.get(ws.ResearchEntry, row.entry_id)
        fr.release_figures(session, entry=entry, revision_number=99,
                           references=[{"objectId": row.object_id, "role": "figure"}])


# --- 5. Public surface ----------------------------------------------------------------------------


def test_public_provenance_shows_the_approved_binding_without_workspace_identifiers(session, world, client):
    live(session, world)
    session.commit()
    prov = client.get(f"{P}/objects/{key(session, world['soil'])}/provenance").json()
    fig = prov["figure"]
    assert fig["bundleSha256"] == SOIL["bundleSha256"] and fig["artifacts"] == [
        {"sha256": SOIL["viewSha256"], "mediaType": "application/vnd.vegalite.v5+json"}]
    assert fig["sourceSha256"] == [source_sha(SOIL["bundleSha256"])] and fig["runtimeResultSha256"] == ["b" * 64]
    text = json.dumps(prov)
    for private in (OWNER, "proj-1", "run-1", "workspace-project", "ownerScope", "pgr-run-1"):
        assert private not in text, private


def test_public_provenance_never_names_the_workspace_model(session, world, client):
    """Staging finding: codeVersion carried the owner-chosen Workspace model id onto the public API."""
    live(session, world)
    session.commit()
    named = 0
    for oid in (world["polyglot-result"], world["run"], world["soil"]):
        private = cv._provenance(session, session.get(md.ResearchObject, oid)).record
        prov = client.get(f"{P}/objects/{key(session, oid)}/provenance").json()
        assert "workspace-model:" not in json.dumps(prov) and "m-1" not in json.dumps(prov)
        if str(private.get("codeVersion", "")).startswith("workspace-model:m-1@"):
            named += 1
            assert prov["codeVersion"] == "workspace-model#" + "d" * 64
    assert named  # the private record does name the model; only the public projection drops it


@pytest.mark.parametrize("path", ["not-a-key/figure", "00000000-0000-4000-8000-000000000000/figure",
                                  "00000000-0000-4000-8000-000000000000/figure/files/" + "a" * 64,
                                  "00000000-0000-4000-8000-000000000000/figure/files/../../etc"])
def test_unknown_keys_and_paths_are_404(client, stores, path):
    assert client.get(f"{P}/objects/{path}").status_code == 404


def test_release_policy_unit():
    b = bundle_of(SOIL["bundleSha256"])
    assert pf.release_errors(b, article=ARTICLE, release_channel="staging-synthetic") == []
    # Producer-side review fields are not release authority either way.
    b["review"] = {"scientific": "pending", "rights": "pending"}
    b["rendering"]["accessibilityReview"] = "pending"
    assert pf.release_errors(b, article=ARTICLE, release_channel="staging-synthetic") == []
    for text, what in (("see http://intranet.example/x", "link"), ("10.1.2.3", "IP address"), ("/Users/alice/x", "filesystem"),
                       ("api_key", "credential"), ("db.internal", "internal host"), ("workspace-dataset:ds-1", "Workspace identifier")):
        c = deepcopy(b)
        c["scientificContext"]["interpretation"] = text
        assert any(what in e for e in pf.release_errors(c, article=ARTICLE, release_channel="staging-synthetic")), text


def test_status_citations_units_and_uncertainty(session, world, client):
    assert client.get(f"{P}/public/figures/status").json() == {
        "schema": pf.STATUS_SCHEMA, "enabled": True, "channel": "staging-synthetic", "label": pf.CHANNEL_LABEL["staging-synthetic"]}
    live(session, world)
    session.commit()
    pkg = client.get(f"{P}/objects/{key(session, world['soil'])}/figure").json()
    assert pkg["units"] == "t C ha-1" and "25%" in pkg["uncertainty"] and pkg["classificationNotice"].startswith("Modelled")
    assert {"citationId": "SYNTHETIC-CITATION-1", "doi": "10.0000/synthetic.0001"}.items() <= pkg["citations"][1].items()
    assert pkg["citations"][1]["licenceStatus"] == "pending"  # never upgraded by Research


def test_reader_serves_nothing_when_disabled_after_release(session, world, client, monkeypatch, tmp_path):
    live(session, world)
    session.commit()
    k = key(session, world["soil"])
    for name, value in (("RESEARCH_PUBLIC_FIGURES", "off"), ("RESEARCH_PUBLIC_FIGURES", "production"),
                        ("RESEARCH_PUBLIC_FIGURE_STORE", str(tmp_path / "missing"))):
        with monkeypatch.context() as m:
            m.setenv(name, value)
            assert client.get(f"{P}/objects/{k}/figure").status_code == 404
    assert client.get(f"{P}/objects/{k}/figure").status_code == 200


def test_symlinked_store_entries_and_other_figures_files_are_refused(session, world, client, tmp_path):
    live(session, world)
    session.commit()
    soil, plot = key(session, world["soil"]), key(session, world["plot"])
    assert client.get(f"{P}/objects/{soil}/figure/files/{PLOT['viewSha256']}").status_code == 404
    view = world["release"] / SOIL["bundleSha256"] / SOIL["viewSha256"]
    outside = tmp_path / "outside"
    outside.write_bytes(view.read_bytes())
    view.unlink()
    view.symlink_to(outside)
    assert client.get(f"{P}/objects/{soil}/figure").status_code == 404
    assert client.get(f"{P}/objects/{plot}/figure").status_code == 200
