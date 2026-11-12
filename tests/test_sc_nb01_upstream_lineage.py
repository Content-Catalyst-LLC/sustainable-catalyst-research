"""SC-NB-01: Research carries upstream-cell lineage and refuses stale or superseded notebook claims.

Signed Workspace exports are built by the SC-MODEL-01 test helpers (synthetic values only).
"""
import pytest

from tests.test_sc_model01_workspace_ingest import BINDING, PUB, export, grant, ingest, session  # noqa: F401 (fixture)
from tests.test_sc_nb01_notebook_reference import PKG1, PKG2, cell
from app import convergence as cv  # noqa: E402
from app import object_registry as md  # noqa: E402
from app import workspace_ingest as wi  # noqa: E402

FIT_SHA, REPLAY_SHA, SC_SHA = "c1" * 32, "d1" * 32, "e1" * 32
UP = {"fit": FIT_SHA, "replay": REPLAY_SHA}


def upstream_binding(fit_result="b" * 64, derived="5" * 64):
    return {"schema": "sc-workspace-upstream-cell-binding/1.0", "kind": "upstream-cell", "lineage": "artifact-bound-verified",
            "planId": "nbxp_1", "notebookId": "nb-erw", "notebookRevision": 1, "cellId": "scenario", "cellSha256": SC_SHA,
            "upstream": [{"cellId": "fit", "role": "primary", "runId": "run-1", "cellSha256": FIT_SHA, "language": "r",
                          "resultArtifactId": "polyglot-result-job-1", "resultArtifactRevision": 1, "resultSha256": fit_result,
                          "environmentFingerprint": "e" * 64, "modelRef": {"modelId": "m-r", "revision": 1},
                          "inputLineage": "artifact-bound-verified", "inputBindings": [BINDING]},
                         {"cellId": "replay", "role": "cross-check", "runId": "run-2", "cellSha256": REPLAY_SHA,
                          "resultSha256": "6" * 64, "inputLineage": "artifact-bound-verified"}],
            "crossChecks": [{"cellId": "replay", "values": ["intercept", "slope"], "maxAbsDifference": 1e-12, "tolerance": 1e-6}],
            "transform": {"kind": "linear-scenario-scores", "rates": [10, 25, 40]},
            "upstreamValues": {"fit": {"intercept": 1.5, "slope": 0.25}},
            "derivedPayloadSha256": derived, "chainSha256": "4" * 64,
            "datasets": [{"datasetId": "ds-1", "datasetRevision": 1, "artifactId": "rows", "artifactRevision": 1,
                          "artifactSha256": BINDING["artifactSha256"]}]}


def sc_cell(revision=1, upstream=None, current=True, head=1, changed=()):
    c = cell(cell_id="scenario", revision=revision, code=SC_SHA, pkg=PKG1 if revision == 1 else PKG2)
    c["upstreamCellSha256"] = dict(upstream or UP)
    c["currency"] = {"headRevision": head, "current": current,
                     "reasons": [] if current else ["upstream-changed"], **({"changedUpstreamCells": list(changed)} if changed else {})}
    return c


def scenario(session, run="run-s", gid="gs", **kw):
    binding = kw.pop("binding", None) or upstream_binding()
    return ingest(session, grant(run=run, grant_id=gid), export(run=run, grant_id=gid, bindings=[binding], cell=sc_cell(**kw)))


def record(session, oid):
    return cv._provenance(session, session.get(md.ResearchObject, oid)).record


def test_upstream_binding_is_recorded_as_hashes_only(session):
    out = scenario(session)
    rec = record(session, out["objects"]["run"]["id"])
    b = rec["inputBindings"][0]
    assert rec["inputLineage"] == "artifact-bound-verified" and b["kind"] == "upstream-cell"
    assert [(u["cellId"], u["role"], u["resultSha256"]) for u in b["upstream"]] == [("fit", "primary", "b" * 64), ("replay", "cross-check", "6" * 64)]
    assert "inputBindings" not in b["upstream"][0] and "upstreamValues" not in b  # values stay in Workspace
    assert b["datasets"] == [{"datasetId": "ds-1", "datasetRevision": 1, "artifactSha256": BINDING["artifactSha256"]}]
    assert rec["notebookCell"]["upstreamCellSha256"] == UP and rec["notebookCell"]["currencyAtExport"]["current"] is True
    assert cv.notebook_claim_status(session, session.get(md.ResearchObject, out["objects"]["run"]["id"]), rec)["status"] == "current"


def test_input_key_compares_upstream_results_and_datasets():
    a = {"inputLineage": "artifact-bound-verified", "inputBindings": [wi._binding(upstream_binding())]}
    same = {"inputLineage": "artifact-bound-verified", "inputBindings": [wi._binding(upstream_binding())]}
    other = {"inputLineage": "artifact-bound-verified", "inputBindings": [wi._binding(upstream_binding(fit_result="7" * 64))]}
    assert cv.compare_inputs(a, same)["status"] == "identical-verified"
    assert cv.compare_inputs(a, other)["status"] == "inputs-differ"
    dataset_only = {"inputLineage": "artifact-bound-verified", "inputBindings": [BINDING]}
    assert cv.compare_inputs(a, dataset_only)["status"] == "inputs-differ"


def test_stale_at_export_claim_is_recorded_and_versioned(session):
    fresh = scenario(session)
    stale = scenario(session, current=False, head=2, changed=["fit"])
    assert stale["objects"]["run"]["id"] != fresh["objects"]["run"]["id"]  # a new revision, not a conflict
    run = session.get(md.ResearchObject, stale["objects"]["run"]["id"])
    assert run.source_version.endswith("-stale-r2")
    status = cv.notebook_claim_status(session, run, record(session, run.id))
    assert status == {"status": "stale", "reasons": ["upstream-changed"], "changedUpstreamCells": ["fit"]}


def test_later_revision_with_changed_upstream_supersedes(session):
    first = scenario(session)
    # Revision 2: the scenario cell is unchanged, but the fit cell above it changed.
    scenario(session, run="run-s2", gid="gs2", revision=2, head=2, upstream={"fit": "c2" * 32, "replay": REPLAY_SHA})
    old = session.get(md.ResearchObject, first["objects"]["run"]["id"])
    status = cv.notebook_claim_status(session, old, record(session, old.id))
    assert status["status"] == "superseded" and status["by"][0]["notebookRevision"] == 2
    ref = wi.notebook_reference(session, owner_scope="workspace-user:a", notebook_id="nb-erw")
    by_rev = {r["notebookRevision"]: r["runs"][0]["claimStatus"]["status"] for r in ref["revisions"]}
    assert by_rev == {1: "superseded", 2: "current"}


def test_downstream_run_of_later_revision_supersedes_changed_upstream_cell(session):
    fit = ingest(session, grant(), export(cell=dict(cell(), upstreamCellSha256={})))
    # Only the revision-2 scenario is registered; it records a different fit hash as its upstream.
    scenario(session, run="run-s2", gid="gs2", revision=2, head=2, upstream={"fit": "c2" * 32, "replay": REPLAY_SHA})
    old = session.get(md.ResearchObject, fit["objects"]["run"]["id"])
    status = cv.notebook_claim_status(session, old, record(session, old.id))
    assert status["status"] == "superseded" and status["by"][0]["notebookRevision"] == 2


def test_later_revision_with_identical_lineage_does_not_supersede(session):
    first = scenario(session)
    scenario(session, run="run-s2", gid="gs2", revision=2, head=2)
    old = session.get(md.ResearchObject, first["objects"]["run"]["id"])
    assert cv.notebook_claim_status(session, old, record(session, old.id))["status"] == "current"


def test_publication_refuses_stale_and_superseded_claims(session):
    from tests.test_sc_model01_convergence import governance
    first = scenario(session)
    scenario(session, run="run-s2", gid="gs2", revision=2, head=2, upstream={"fit": "c2" * 32, "replay": REPLAY_SHA})
    oid = first["objects"]["output:polyglot-result"]["id"]
    obj = session.get(md.ResearchObject, oid)
    subject = cv.subject_ref(obj, cv._provenance(session, obj))
    for dim, action in cv.APPROVAL_ACTIONS.items():
        a, d = governance(subject, action)
        cv.record_workspace_approval(session, owner_scope="workspace-user:a", object_id=oid, dimension=dim, approval=a, decision=d)
    with pytest.raises(PermissionError, match="Notebook claim is superseded"):
        cv.promote_public_projection(session, owner_scope="workspace-user:a", object_id=oid)


def test_generic_bundles_cannot_self_attest_upstream_lineage(session):
    out = scenario(session)
    rec = record(session, out["objects"]["run"]["id"])
    with pytest.raises(ValueError, match="attested"):
        cv._guard_input_lineage(rec, attested=False)
