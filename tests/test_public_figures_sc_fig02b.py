"""SC-FIG-02B: approved figures on the public Research API. Synthetic fixtures only."""
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research-api"))

from app.main import app  # noqa: E402
from app import figure_objects as fo  # noqa: E402
from app import object_registry as md  # noqa: E402
from app import public_figures as pf  # noqa: E402
from app import workspaces as ws  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "sc_fig02b_public"
INDEX = json.loads((FIX / "FIXTURES.json").read_text(encoding="utf-8"))
ARTICLE = INDEX["article"]
SOIL, PLOT = INDEX["figures"]
P = "/api/research/v1"


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = tmp_path / "store"
    shutil.copytree(FIX / "store", store)
    monkeypatch.setenv("RESEARCH_PUBLIC_FIGURES", "staging-synthetic")
    monkeypatch.setenv("RESEARCH_PUBLIC_FIGURE_STORE", str(store))
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "pub.sqlite"))
    ws.Base.metadata.create_all(engine)

    def session_override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[ws.get_session] = session_override
    with Session(engine) as s:
        w = ws.create_entry_workspace(s, area_slug="soil-systems", project_key="soil-carbon", owner_scope="owner-a",
                                      entry_slug=ARTICLE, title="Synthetic soil carbon scenarios")
        keys = {f["figureId"]: register(s, w, store, f["bundleSha256"]) for f in INDEX["figures"]}
        approve_article(s, w)
        s.commit()
    yield {"engine": engine, "store": store, "keys": keys}
    app.dependency_overrides.clear()


def register(session, workspace, store, digest):
    raw = (store / digest / digest).read_bytes()
    return fo.register_figure_bundle(session, workspace=workspace, owner_scope="owner-a", raw=raw)["object"].public_key


def approve_article(session, workspace, objects=True):
    # Stands in for the governed flow (Workspace-signed approvals, then promotion), which is not part of this module.
    entry = session.get(ws.ResearchEntry, workspace.entry_id)
    entry.visibility, entry.rights_cleared, entry.state = md.APPROVED, True, "published"
    session.get(ws.ResearchArea, session.get(ws.ResearchProject, entry.project_id).area_id).visibility = md.APPROVED
    if objects:
        for obj in session.query(md.ResearchObject).filter_by(workspace_id=workspace.id):
            obj.visibility, obj.review_state, obj.rights_cleared = md.APPROVED, md.APPROVED, True


def set_object(env, figure_id, **values):
    with Session(env["engine"]) as s:
        obj = s.query(md.ResearchObject).filter_by(public_key=env["keys"][figure_id]).one()
        for k, v in values.items():
            setattr(obj, k, v)
        s.commit()


def rewrite_bundle(env, fixture, change):
    """Write a changed bundle into the store and point the registered object at its new hash."""
    old = fixture["bundleSha256"]
    b = json.loads((env["store"] / old / old).read_bytes())
    change(b)
    raw = (json.dumps(b, indent=1, ensure_ascii=False) + "\n").encode()
    new = hashlib.sha256(raw).hexdigest()
    shutil.copytree(env["store"] / old, env["store"] / new)
    (env["store"] / new / old).unlink()
    (env["store"] / new / new).write_bytes(raw)
    set_object(env, fixture["figureId"], source_sha256=new, source_version="v%d+%s" % (b.get("version", 1), new[:16]))
    return new


def figure(env, fixture):
    return TestClient(app).get(f"{P}/objects/{env['keys'][fixture['figureId']]}/figure")


def test_status_reports_the_channel(env, monkeypatch):
    c = TestClient(app)
    assert c.get(P + "/public/figures/status").json() == {
        "schema": "sc-research-public-figure-status/1.0", "enabled": True, "channel": "staging-synthetic",
        "label": "Synthetic staging release. Not published research."}
    monkeypatch.setenv("RESEARCH_PUBLIC_FIGURES", "production")  # not authorized: treated as off
    assert c.get(P + "/public/figures/status").json()["enabled"] is False


def test_approved_figures_are_listed_and_served_as_exact_bytes(env):
    c = TestClient(app)
    listed = c.get(P + "/objects", params={"article": ARTICLE, "kind": "figure"}).json()["items"]
    assert sorted(i["id"] for i in listed) == sorted(env["keys"].values())
    for fixture in (SOIL, PLOT):
        r = figure(env, fixture)
        assert r.status_code == 200
        pkg = r.json()
        assert pkg["schema"] == "sc-research-public-figure/1.0" and pkg["article"] == ARTICLE
        assert pkg["release"]["bundleSha256"] == fixture["bundleSha256"] and pkg["release"]["channel"] == "staging-synthetic"
        assert pkg["release"]["registryVersion"] == "v1+" + fixture["bundleSha256"][:16]
        assert pkg["dataClassification"] == "synthetic" and pkg["evidenceClass"] == fixture["evidenceClass"]
        assert "Not a measurement" in pkg["classificationNotice"]
        assert [(f["role"], f["sha256"]) for f in pkg["files"]] == [("bundle", fixture["bundleSha256"]), ("view", fixture["viewSha256"])]
        assert pkg["missingViews"] == []
        for f in pkg["files"]:
            body = c.get(f["url"])
            assert body.status_code == 200 and body.headers["content-type"] == "application/octet-stream"
            assert body.headers["content-security-policy"] == "default-src 'none'; sandbox"
            assert body.content == (env["store"] / fixture["bundleSha256"] / f["sha256"]).read_bytes()
        text = json.dumps(pkg)
        assert "owner-a" not in text and str(env["store"]) not in text  # no owner scope or store path


def test_citations_units_and_uncertainty_are_public(env):
    pkg = figure(env, SOIL).json()
    assert pkg["units"] == "t C ha-1" and "25%" in pkg["uncertainty"]
    assert {"citationId": "SYNTHETIC-CITATION-1", "doi": "10.0000/synthetic.0001"}.items() <= pkg["citations"][1].items()
    assert pkg["citations"][1]["licenceStatus"] == "pending"  # never upgraded by Research


def test_nothing_is_served_when_disabled_or_unconfigured(env, monkeypatch):
    for name, value in (("RESEARCH_PUBLIC_FIGURES", "off"), ("RESEARCH_PUBLIC_FIGURES", "production"),
                        ("RESEARCH_PUBLIC_FIGURE_STORE", str(env["store"] / "missing"))):
        monkeypatch.setenv(name, value)
        assert figure(env, SOIL).status_code == 404
        monkeypatch.undo()
        monkeypatch.setenv("RESEARCH_PUBLIC_FIGURES", "staging-synthetic")
        monkeypatch.setenv("RESEARCH_PUBLIC_FIGURE_STORE", str(env["store"]))


@pytest.mark.parametrize("values", [
    {"visibility": "private"}, {"review_state": "unreviewed"}, {"rights_cleared": False}, {"review_state": "revoked"},
])
def test_unapproved_objects_are_not_served(env, values):
    set_object(env, SOIL["figureId"], **values)
    assert figure(env, SOIL).status_code == 404
    assert figure(env, PLOT).status_code == 200


def test_unpublished_article_hides_its_figures(env):
    with Session(env["engine"]) as s:
        s.query(ws.ResearchEntry).filter_by(slug=ARTICLE).one().visibility = "private"
        s.commit()
    assert figure(env, SOIL).status_code == 404


def test_tampered_or_substituted_bytes_are_refused(env):
    d = env["store"] / SOIL["bundleSha256"]
    (d / SOIL["bundleSha256"]).write_bytes((d / SOIL["bundleSha256"]).read_bytes().replace(b"SYNTHETIC soil", b"SYNTHETIC Soil"))
    assert figure(env, SOIL).status_code == 404
    # A view whose bytes no longer match is left out; with no verified view the figure is not served.
    view = env["store"] / PLOT["bundleSha256"] / PLOT["viewSha256"]
    view.write_bytes(view.read_bytes().replace(b"1.8", b"1.9"))
    assert figure(env, PLOT).status_code == 404


def test_registered_hash_must_name_the_exact_revision(env):
    set_object(env, SOIL["figureId"], source_sha256=PLOT["bundleSha256"])
    assert figure(env, SOIL).status_code == 404  # bytes exist but belong to another figure identity


def test_symlinked_store_files_are_refused(env, tmp_path):
    d = env["store"] / SOIL["bundleSha256"]
    target = tmp_path / "outside"
    target.write_bytes((d / SOIL["viewSha256"]).read_bytes())
    (d / SOIL["viewSha256"]).unlink()
    (d / SOIL["viewSha256"]).symlink_to(target)
    assert figure(env, SOIL).status_code == 404


@pytest.mark.parametrize("change,reason", [
    (lambda b: b["project"].update(projectRef="research-entry:another-article"), "projectRef"),
    (lambda b: b["project"].update(projectRef="workspace-project:p-123"), "projectRef"),
    (lambda b: b["review"].update(publication="pending"), "review.publication"),
    (lambda b: b["review"].update(rights="pending"), "review.rights"),
    (lambda b: b["rendering"].update(accessibilityReview="pending"), "accessibility"),
    (lambda b: b["project"].update(ownerScope="authenticated", environment="production"), "synthetic test bundles"),
    (lambda b: b["artifacts"][0].update(status="local-review"), "staging-verified"),
    (lambda b: b["scientificContext"].update(interpretation="see https://example.org/x"), "a link"),
    (lambda b: b["scientificContext"].update(interpretation="data at /Users/someone/x.csv"), "a filesystem path"),
    (lambda b: b["scientificContext"].update(interpretation="ask person@example.org"), "an email"),
    (lambda b: b["scientificContext"].update(interpretation="host 203.0.113.9"), "an IP address"),
    (lambda b: b["scientificContext"].update(interpretation="api_key=abc"), "credential"),
])
def test_release_policy_refusals(env, change, reason):
    b = json.loads((env["store"] / SOIL["bundleSha256"] / SOIL["bundleSha256"]).read_bytes())
    change(b)
    assert any(reason in e for e in pf.release_errors(b, article=ARTICLE, release_channel="staging-synthetic"))
    rewrite_bundle(env, SOIL, change)
    assert figure(env, SOIL).status_code == 404


def test_release_policy_accepts_fixtures_and_doi_links():
    for fixture in (SOIL, PLOT):
        b = json.loads((FIX / "store" / fixture["bundleSha256"] / fixture["bundleSha256"]).read_bytes())
        assert pf.release_errors(b, article=ARTICLE, release_channel="staging-synthetic") == []
    assert pf.private_content({"doi": "https://doi.org/10.0000/x", "v": "vega-lite 6.5.0; vega 6.4.0", "lab": "0.163.1.6"}) == []


def test_staging_channel_never_serves_a_non_synthetic_object(env):
    set_object(env, SOIL["figureId"], data_classification="modelled")
    assert figure(env, SOIL).status_code == 404


def test_file_route_serves_only_this_figures_files(env):
    c = TestClient(app)
    soil = env["keys"][SOIL["figureId"]]
    assert c.get(f"{P}/objects/{soil}/figure/files/{PLOT['viewSha256']}").status_code == 404
    assert c.get(f"{P}/objects/{soil}/figure/files/..%2F..%2Fetc").status_code == 404
    assert c.get(f"{P}/objects/not-a-uuid/figure").status_code == 404
