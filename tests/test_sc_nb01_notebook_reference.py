"""SC-NB-01: Research registers and reopens Workspace notebooks by reference (synthetic fixtures).

The notebook identity stays in Workspace. Research records a reference object per notebook
revision only when the signed Workspace export says the cell was verified against the immutable
revision, links runs to it, compares revisions and never copies notebook content.
"""
import pytest
from fastapi.testclient import TestClient

from tests.test_sc_model01_workspace_ingest import PUB, export, grant, ingest, session  # noqa: F401 (fixture)
from app import convergence as cv  # noqa: E402
from app import object_registry as md  # noqa: E402
from app import workspace_ingest as wi  # noqa: E402

PKG1, PKG2 = "1a" * 32, "2b" * 32


def cell(cell_id="fit", revision=1, code="c1" * 32, language="r", verified=True, pkg=PKG1, nb="nb-erw"):
    return {"schema": "sc-workspace-notebook-cell-run/1.0", "planId": "nbxp_1", "notebookId": nb,
            "notebookRevision": revision, "cellId": cell_id, "cellSha256": code, "language": language,
            "notebookPackageSha256": pkg, "notebookTitle": "Synthetic dose-response", "verified": verified}


def test_verified_cell_registers_notebook_reference_and_links_run(session):
    out = ingest(session, grant(), export(cell=cell()))
    assert out["objects"]["notebook"]["kind"] == "notebook"
    nb = session.get(md.ResearchObject, out["objects"]["notebook"]["id"])
    assert (nb.authority, nb.source_id, nb.source_version, nb.source_sha256) == ("workspace", "notebook:nb-erw", "r1", PKG1)
    assert nb.data_classification == "unknown"
    run = cv._provenance(session, session.get(md.ResearchObject, out["objects"]["run"]["id"])).record
    assert run["notebookCell"]["verified"] is True and run["notebookCell"]["cellSha256"] == "c1" * 32
    ref = wi.notebook_reference(session, owner_scope="workspace-user:a", notebook_id="nb-erw")
    assert ref["contentCopied"] is False and ref["open"] == {"authority": "workspace", "route": "/v1/notebooks/nb-erw"}
    assert [r["notebookRevision"] for r in ref["revisions"]] == [1]
    runs = ref["revisions"][0]["runs"]
    assert [(r["workspaceRunId"], r["cell"]["cellId"], r["inputLineage"]) for r in runs] == [("run-1", "fit", "artifact-bound-verified")]
    # The reference carries no cell source or notebook content.
    assert "source" not in str(ref) and "cells" not in str(ref["revisions"])


def test_one_identity_per_revision_and_revision_comparison(session):
    ingest(session, grant(), export(cell=cell()))
    # A second cell of the same revision reuses the same notebook object (no duplicate identity).
    again = ingest(session, grant(run="run-2", grant_id="g2"), export(run="run-2", grant_id="g2", cell=cell(cell_id="replay", code="d1" * 32, language="julia")))
    first = wi.notebook_reference(session, owner_scope="workspace-user:a", notebook_id="nb-erw")
    assert len(first["revisions"]) == 1 and first["revisions"][0]["objectId"] == again["objects"]["notebook"]["id"]
    # Revision 2 changes the fit cell's code.
    ingest(session, grant(run="run-3", grant_id="g3"), export(run="run-3", grant_id="g3", cell=cell(revision=2, code="c2" * 32, pkg=PKG2)))
    ref = wi.notebook_reference(session, owner_scope="workspace-user:a", notebook_id="nb-erw")
    assert [r["notebookRevision"] for r in ref["revisions"]] == [1, 2]
    assert ref["revisionComparison"] == [{"from": 1, "to": 2, "changedCells": ["fit"], "addedCells": [], "removedCells": ["replay"]}]


def test_runs_with_different_classifications_share_one_notebook_reference(session):
    a = ingest(session, grant(), export(cell=cell()))
    # A cell whose run has no dataset (e.g. a scenario comparison) has a different run classification.
    b = ingest(session, grant(run="run-s", grant_id="gs"),
               export(run="run-s", grant_id="gs", cell=cell(cell_id="scenario", code="e1" * 32),
                      dataset_class="modelled", dataset_id="ds-scenario"))
    assert a["objects"]["notebook"]["id"] == b["objects"]["notebook"]["id"]


def test_unverified_cell_is_recorded_but_not_registered(session):
    out = ingest(session, grant(), export(cell=cell(verified=False)))
    assert "notebook" not in out["objects"]
    run = cv._provenance(session, session.get(md.ResearchObject, out["objects"]["run"]["id"])).record
    assert run["notebookCell"]["verified"] is False
    with pytest.raises(LookupError):
        wi.notebook_reference(session, owner_scope="workspace-user:a", notebook_id="nb-erw")
    # A malformed "verified" claim (bad hash) is downgraded, not trusted.
    out = ingest(session, grant(run="run-x", grant_id="gx"), export(run="run-x", grant_id="gx", cell=cell(code="not-a-hash")))
    assert "notebook" not in out["objects"]


def test_owner_isolation(session):
    ingest(session, grant(), export(cell=cell()))
    with pytest.raises(LookupError):
        wi.notebook_reference(session, owner_scope="workspace-user:b", notebook_id="nb-erw")


def test_reproduction_records_code_comparison(session):
    out = ingest(session, grant(), export(cell=cell()))
    rid = out["objects"]["output:polyglot-result"]["id"]
    rep = wi.ingest_reproduction(session, key=PUB, original_id=rid, grant=grant(run="run-julia", grant_id="gj"),
                                 rel_tol=1e-9, abs_tol=1e-12,
                                 fetch=lambda _: export(run="run-julia", grant_id="gj",
                                                        cell=cell(cell_id="replay", code="d1" * 32, language="julia")))
    assert rep["status"] == "reproduced" and rep["code"]["status"] == "changed"
    assert (rep["code"]["original"]["language"], rep["code"]["reproduction"]["language"]) == ("r", "julia")
    rep = wi.ingest_reproduction(session, key=PUB, original_id=rid, grant=grant(run="run-r2", grant_id="gr"),
                                 rel_tol=1e-9, abs_tol=1e-12, fetch=lambda _: export(run="run-r2", grant_id="gr", cell=cell()))
    assert rep["code"]["status"] == "identical"
    rep = wi.ingest_reproduction(session, key=PUB, original_id=rid, grant=grant(run="run-plain", grant_id="gq"),
                                 rel_tol=1e-9, abs_tol=1e-12, fetch=lambda _: export(run="run-plain", grant_id="gq"))
    assert rep["code"]["status"] == "unrecorded"


def test_generic_bundles_cannot_self_attest_notebook_lineage(session):
    from tests.test_sc_model01_convergence import OWNER, bundle
    b = bundle()
    b["objects"][0]["provenance"]["notebookCell"] = cell()
    with pytest.raises(ValueError, match="notebook lineage"):
        cv.ingest_bundle(session, owner_scope=OWNER, bundle=cv.seal_bundle({k: v for k, v in b.items() if k != "manifestSha256"}))
    b = bundle()
    b["objects"][0]["provenance"]["verification"] = {"status": wi.NOTEBOOK_VERIFIED}
    with pytest.raises(ValueError, match="notebook lineage"):
        cv.ingest_bundle(session, owner_scope=OWNER, bundle=cv.seal_bundle({k: v for k, v in b.items() if k != "manifestSha256"}))


def test_reopen_route_requires_internal_principal(session, monkeypatch):
    from app import main as research_main
    from app import workspaces as ws
    ingest(session, grant(), export(cell=cell()))
    session.commit()
    research_main.app.dependency_overrides[ws.get_session] = lambda: session
    try:
        client = TestClient(research_main.app)
        assert client.get("/api/research/v1/convergence/workspace/notebooks/nb-erw").status_code in (401, 403, 503)
        research_main.app.dependency_overrides[cv.internal_principal] = lambda: "workspace-user:a"
        r = client.get("/api/research/v1/convergence/workspace/notebooks/nb-erw")
        assert r.status_code == 200 and r.json()["revisions"][0]["notebookRevision"] == 1
        research_main.app.dependency_overrides[cv.internal_principal] = lambda: "workspace-user:b"
        assert client.get("/api/research/v1/convergence/workspace/notebooks/nb-erw").status_code == 404
    finally:
        research_main.app.dependency_overrides.clear()
