"""SC-SIM-01 / SC-MATH-01 M3: simulation provenance from attested ParameterSet revisions, and
pre-registered reproduction tolerances. Synthetic fixtures only (public CI)."""
from copy import deepcopy

import pytest

from tests.test_sc_model01_workspace_ingest import (  # noqa: F401  (fixture re-export)
    BINDING, KEY, PUB, cv, export, grant, ingest, md, provenance, session, sign, wi,
)

PARAMS = {"kind": "parameter-set", "lineage": "artifact-bound-verified", "parameterSetId": "ps-1", "parameterSetRevision": 1,
          "parameterSetFingerprint": "5" * 64, "modelId": "m-1", "parametersSha256": "6" * 64,
          "scientificClassification": "synthetic", "schema": "sc-workspace-parameter-input-binding/1.0"}
TOL = {"relative": 1e-6, "absolute": 1e-9}


def sim_export(run="run-sim", grant_id="g-sim", params=None, values=None, seed=11, tolerance=TOL, datasets=False,
               lineage="artifact-bound-verified", language="julia", dataset_class="synthetic", model_kind="simulation",
               se=None, extra_values=None, receipt_extra=None):
    prov = provenance(run=run, dataset_class=dataset_class)
    prov.pop("provenanceFingerprint")
    if not datasets:
        prov["inputs"]["datasets"] = []
    prov["inputs"]["model"]["resolved"].update({"modelKind": model_kind, "framework": "Julia"})
    prov["provenanceFingerprint"] = cv.canonical_sha256(prov)
    bindings = [dict(PARAMS, **(params or {}))] + ([BINDING] if datasets else [])
    rv = {"values": values or {"sampleMean": 2.0, "sampleStdDev": 0.5}, "sourceArtifactSha256": "b" * 64}
    if seed is not None:
        rv["stochastic"] = {"seed": seed}
    if tolerance is not None:
        rv["tolerance"] = tolerance
    if se is not None:
        rv["standardErrors"] = {"semantics": "monte-carlo-standard-error", "values": se}
    rv.update(extra_values or {})
    body = {"schema": wi.EXPORT_SCHEMA, "issuer": wi.ISSUER, "audience": wi.AUDIENCE, "grantId": grant_id,
            "ownerScope": "workspace-user:a", "projectId": "proj-1", "runId": run, "exportedAt": "2026-11-10T00:00:00Z",
            "provenance": prov,
            "verification": {"status": "executed-verified", "runStatus": "succeeded", "jobId": "job-1",
                             "runtimeReceipts": [{"receiptId": f"pgr-{run}", "language": language, "runtime": f"{language}-numerical",
                                                  "resultSha256": "b" * 64, **(receipt_extra or {})}],
                             "inputLineage": lineage, "inputBindings": bindings},
            "resultValues": rv, "studyPackages": [], "boundaries": {"artifactBytesIncluded": False}}
    return sign(wi.EXPORT_SCHEMA, body, KEY)


def result_of(session, out):
    obj = session.get(md.ResearchObject, out["objects"]["output:polyglot-result"]["id"])
    return obj, cv._provenance(session, obj).record


def reproduce(session, rid, run, *, rel=None, abs_=None, **kw):
    return wi.ingest_reproduction(session, key=PUB, original_id=rid, grant=grant(run=run, grant_id=f"g-{run}"),
                                  rel_tol=rel, abs_tol=abs_, fetch=lambda _: sim_export(run=run, grant_id=f"g-{run}", **kw))


def test_parameter_only_simulation_converges_as_modelled_or_synthetic(session):
    out = ingest(session, grant(run="run-sim", grant_id="g-sim"), sim_export(), entry="ws-sim")
    obj, rec = result_of(session, out)
    assert obj.data_classification == "synthetic"
    assert {"parameterSetId": "ps-1", "revision": 1, "fingerprint": "5" * 64, "authority": "workspace"} in rec["sourceRefs"]
    assert {"authority": "workspace", "modelId": "m-1", "revision": 2, "fingerprint": "d" * 64} in rec["sourceRefs"]
    assert rec["method"] == {"kind": "simulation", "execution": "stochastic", "seed": 11}
    assert rec["declaredTolerance"] == TOL and rec["inputLineage"] == "artifact-bound-verified"
    assert rec["inputBindings"][0]["parameterSetFingerprint"] == "5" * 64
    # Undeclared parameter classification is 'modelled'; a deterministic simulation records no seed.
    out = ingest(session, grant(run="run-sim2", grant_id="g-sim2"),
                 sim_export(run="run-sim2", grant_id="g-sim2", params={"scientificClassification": ""}, seed=None), entry="ws-sim2")
    obj, rec = result_of(session, out)
    assert obj.data_classification == "modelled" and rec["method"] == {"kind": "simulation", "execution": "deterministic", "seed": None}


@pytest.mark.parametrize("param_class,dataset_class", [("observed", None), ("", "observed"), ("literature-derived", "literature-derived")])
def test_simulations_are_never_observed(session, param_class, dataset_class):
    kw = {"datasets": dataset_class is not None, "dataset_class": dataset_class or "synthetic"}
    out = ingest(session, grant(run="run-o", grant_id="g-o"),
                 sim_export(run="run-o", grant_id="g-o", params={"scientificClassification": param_class}, **kw), entry="ws-obs")
    obj, _ = result_of(session, out)
    assert obj.data_classification == "modelled"


def test_parameter_bindings_without_verified_lineage_are_not_sources(session):
    with pytest.raises(ValueError, match="sourceRefs"):
        ingest(session, grant(run="run-u", grant_id="g-u"), sim_export(run="run-u", grant_id="g-u", lineage="inline-unverified"),
               entry="ws-u")


def test_simulation_reproduction_compares_parameter_revisions(session):
    out = ingest(session, grant(run="run-sim", grant_id="g-sim"), sim_export(), entry="ws-sim")
    obj, _ = result_of(session, out)
    # Same pinned parameters, same implementation, same seed: determinism only.
    rep = reproduce(session, obj.id, "run-replay")
    assert rep["inputs"]["status"] == "identical-verified" and rep["status"] == "replayed-deterministic"
    assert rep["tolerance"]["source"] == "model-revision-declared" and rep["tolerance"]["relative"] == 1e-6
    # A different ParameterSet revision is a different input, whatever the numbers say.
    rep = reproduce(session, obj.id, "run-other-params", params={"parameterSetRevision": 2, "parameterSetFingerprint": "7" * 64})
    assert rep["inputs"]["status"] == "inputs-differ" and rep["reproduced"] is False
    # An independent implementation on the same pinned parameters, within the declared tolerance.
    rep = reproduce(session, obj.id, "run-r", language="r", values={"sampleMean": 2.0 + 1e-7, "sampleStdDev": 0.5})
    assert rep["status"] == "reproduced" and rep["independence"] == "independent-implementation"
    assert cv.reproduction_status(session, obj) == "reproduced"


def test_declared_tolerance_cannot_be_relaxed_by_the_request(session):
    out = ingest(session, grant(run="run-sim", grant_id="g-sim"), sim_export(), entry="ws-sim")
    obj, _ = result_of(session, out)
    close = {"sampleMean": 2.0 + 1e-7, "sampleStdDev": 0.5}
    with pytest.raises(cv.ToleranceRelaxed, match="declared on the model revision"):
        reproduce(session, obj.id, "run-loose", language="r", rel=1e-3, values=close)
    with pytest.raises(cv.ToleranceRelaxed):
        reproduce(session, obj.id, "run-loose-abs", language="r", abs_=1e-3, values=close)
    # Tightening is allowed and recorded; here it turns the same numbers into a non-reproduction.
    rep = reproduce(session, obj.id, "run-tight", language="r", rel=1e-9, abs_=1e-12, values=close)
    assert rep["tolerance"]["source"] == "model-revision-declared-tightened-by-request" and rep["status"] == "not-reproduced"
    assert rep["tolerance"]["ceiling"] == TOL


def test_undeclared_tolerance_falls_back_to_the_strict_default(session):
    out = ingest(session, grant(run="run-sim", grant_id="g-sim"), sim_export(tolerance=None), entry="ws-sim")
    obj, rec = result_of(session, out)
    assert "declaredTolerance" not in rec
    with pytest.raises(cv.ToleranceRelaxed, match="strict default"):
        reproduce(session, obj.id, "run-loose", language="r", rel=1e-8)
    rep = reproduce(session, obj.id, "run-r", language="r", values={"sampleMean": 2.0 + 1e-7, "sampleStdDev": 0.5})
    assert rep["tolerance"]["source"] == "default-strict" and rep["status"] == "not-reproduced"


def test_invalid_declared_tolerance_is_ignored_and_strict_default_applies(session):
    out = ingest(session, grant(run="run-sim", grant_id="g-sim"), sim_export(tolerance={"relative": 0.5}), entry="ws-sim")
    _, rec = result_of(session, out)
    assert "declaredTolerance" not in rec
    assert cv.normalize_tolerance({"relative": 0.5}) is None and cv.normalize_tolerance({"relative": True}) is None
    with pytest.raises(ValueError, match="declaredTolerance"):
        cv.validate_provenance("result", "synthetic", {**deepcopy(rec), "declaredTolerance": {"relative": 2}})


# ---------------------------------------------------------------------------
# G4: pre-specified statistical agreement for independent stochastic reproductions

STAT = {"relative": 0.0, "absolute": 0.0, "statistical": {"method": "z-score", "z": 4.0}}
SE = {"sampleMean": 0.0035}


def stat_original(session, **kw):
    out = ingest(session, grant(run="run-sim", grant_id="g-sim"), sim_export(tolerance=STAT, se=SE, **kw), entry="ws-sim")
    return result_of(session, out)


def test_independent_stochastic_reproduction_agrees_statistically(session):
    obj, rec = stat_original(session)
    assert rec["standardErrors"] == SE and rec["declaredTolerance"]["statistical"] == {"method": "z-score", "z": 4.0}
    # Different implementation, different random draws: 0.002 apart with combined se 0.00495 (z = 0.40).
    rep = reproduce(session, obj.id, "run-py", language="python", seed=7, se=SE,
                    values={"sampleMean": 2.002, "sampleStdDev": 0.5})
    row = next(r for r in rep["values"] if r["name"] == "sampleMean")
    assert rep["status"] == "reproduced" and row["agreement"] == "statistical" and row["zScore"] < 0.5
    assert rep["tolerance"]["statistical"] == {"method": "z-score", "z": 4.0}
    # sampleStdDev has no standard error, so it still needs numeric agreement (here exact).
    assert next(r for r in rep["values"] if r["name"] == "sampleStdDev")["agreement"] == "numeric"


def test_statistical_disagreement_is_not_reproduced(session):
    obj, _ = stat_original(session)
    rep = reproduce(session, obj.id, "run-py", language="python", seed=7, se=SE, values={"sampleMean": 2.05, "sampleStdDev": 0.5})
    row = next(r for r in rep["values"] if r["name"] == "sampleMean")
    assert rep["status"] == "not-reproduced" and row["zScore"] > 4 and row["within"] is False


def test_same_implementation_other_seed_is_only_a_replication(session):
    obj, _ = stat_original(session)
    rep = reproduce(session, obj.id, "run-seed12", seed=12, se=SE, values={"sampleMean": 2.002, "sampleStdDev": 0.5})
    assert rep["independence"] == "replay" and rep["status"] == "replicated-same-implementation" and rep["reproduced"] is False
    assert "replicated-same-implementation" in cv.UNVERIFIED_STATUSES


@pytest.mark.parametrize("case", ["no-declaration", "no-reproduction-se", "deterministic-original"])
def test_statistical_agreement_needs_every_precondition(session, case):
    kw = {"tolerance": TOL if case == "no-declaration" else STAT, "se": SE}
    if case == "deterministic-original":
        kw["seed"] = None
    out = ingest(session, grant(run="run-sim", grant_id="g-sim"), sim_export(**kw), entry="ws-sim")
    obj, _ = result_of(session, out)
    rep = reproduce(session, obj.id, "run-py", language="python", seed=7, se=None if case == "no-reproduction-se" else SE,
                    values={"sampleMean": 2.002, "sampleStdDev": 0.5})
    assert rep["status"] == "not-reproduced"
    assert all(r["agreement"] == "numeric" for r in rep["values"])


def test_invalid_standard_errors_are_not_recorded(session):
    for se in ({"sampleMean": -1.0}, {"other": 0.1}, {"sampleMean": float("inf")}):
        out = ingest(session, grant(run="run-sim", grant_id="g-sim"), sim_export(tolerance=STAT, se=se), entry="ws-sim")
        _, rec = result_of(session, out)
        assert "standardErrors" not in rec
    assert cv.normalize_tolerance({"statistical": {"method": "z-score", "z": 9}}) is None
    assert cv.normalize_tolerance({"statistical": {"method": "z-score", "z": 3, "extra": 1}}) is None
