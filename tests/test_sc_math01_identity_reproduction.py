"""SC-MATH-01 M1/M2/M4: symbolic identity checks reproduced at declared points (never a proof), canonical
ordering and numerical library identity in provenance. Synthetic fixtures only (public CI)."""
from copy import deepcopy

import pytest

from tests.test_sc_sim01_simulation_provenance import (  # noqa: F401  (fixture re-export)
    cv, grant, ingest, result_of, session, sim_export, wi, PUB,
)

CHECK = {"method": "numerical-identity-at-declared-points", "proof": False, "action": "differentiate", "pointCount": 3,
         "variable": "x"}
VALUES = {"d0": 7.333244722889034, "d1": 11.955613417263281, "d2": -0.6203}
SYMPY_LIBS = {"source": "build-configuration", "python": "3.12.12", "numpy": "2.3.5", "sympy": "1.14.0",
              "blas": {"name": "openblas", "version": "0.3.29"}, "precision": "ieee754-binary64"}
JULIA_LIBS = {"source": "runtime-loaded", "julia": "1.11.5", "blasVendor": "lbt", "precision": "ieee754-binary64"}
TOL = {"relative": 1e-12, "absolute": 1e-14}


def math_export(run, language="python", check=CHECK, values=VALUES, libs=SYMPY_LIBS, version="sympy 1.14.0", **kw):
    extra = {"identityCheck": check} if check is not None else {}
    return sim_export(run=run, grant_id=f"g-{run}", language=language, seed=None, tolerance=TOL, values=values,
                      model_kind="mathematical", extra_values=extra,
                      receipt_extra={"numericalLibraries": libs, "runtimeVersion": version}, **kw)


def original(session):
    out = ingest(session, grant(run="run-sympy", grant_id="g-run-sympy"), math_export("run-sympy"), entry="ws-math")
    return result_of(session, out)


def reproduce(session, rid, run, **kw):
    return wi.ingest_reproduction(session, key=PUB, original_id=rid, grant=grant(run=run, grant_id=f"g-{run}"),
                                  fetch=lambda _: math_export(run, **kw))


def test_identity_check_and_library_identity_are_recorded(session):
    obj, rec = original(session)
    assert rec["identityCheck"] == CHECK and rec["declaredTolerance"] == TOL
    assert rec["runtime"]["numericalLibraries"] == SYMPY_LIBS and rec["runtime"]["version"] == "sympy 1.14.0"
    assert obj.data_classification == "synthetic"


def test_independent_julia_evaluation_reproduces_but_is_never_a_proof(session):
    obj, _ = original(session)
    rep = reproduce(session, obj.id, "run-julia", language="julia", libs=JULIA_LIBS, version="1.11.5",
                    values={k: v * (1 + 3e-16) for k, v in VALUES.items()})
    assert rep["status"] == "reproduced" and rep["independence"] == "independent-implementation"
    assert rep["claim"]["proof"] is False and rep["claim"]["pointCount"] == 3 and "not a symbolic proof" in rep["claim"]["statement"]
    assert rep["numericalLibraries"] == {"original": SYMPY_LIBS, "reproduced": JULIA_LIBS, "identical": False}


@pytest.mark.parametrize("check", [None, {**CHECK, "pointCount": 4}, {**CHECK, "action": "evaluate"}, {**CHECK, "variable": "y"}])
def test_a_different_identity_check_does_not_reproduce(session, check):
    obj, _ = original(session)
    rep = reproduce(session, obj.id, "run-julia", language="julia", libs=JULIA_LIBS, check=check)
    assert rep["status"] == "not-reproduced" and rep["reproduced"] is False


def test_disagreement_beyond_the_declared_tolerance_is_not_reproduced(session):
    obj, _ = original(session)
    rep = reproduce(session, obj.id, "run-julia", language="julia", libs=JULIA_LIBS, values={**VALUES, "d2": -0.6204})
    assert rep["status"] == "not-reproduced"


def test_proof_claims_and_malformed_math_fields_are_refused():
    assert cv.normalize_identity_check({**CHECK, "proof": True}) is None
    assert cv.normalize_identity_check({**CHECK, "pointCount": 0}) is None
    assert cv.normalize_identity_check({**CHECK, "action": "integrate"}) is None
    assert cv.normalize_ordering({"method": "real-then-imaginary", "realTieRelative": 1e-9}) is not None
    assert cv.normalize_ordering({"method": "magnitude", "realTieRelative": 1e-9}) is None
    assert cv.normalize_numerical_libraries({"k": "v" * 5000}) is None


def test_validate_provenance_rejects_a_proof_claim(session):
    _, rec = original(session)
    with pytest.raises(ValueError, match="identityCheck"):
        cv.validate_provenance("result", "synthetic", {**deepcopy(rec), "identityCheck": {**CHECK, "proof": True}})
    with pytest.raises(ValueError, match="ordering"):
        cv.validate_provenance("result", "synthetic", {**deepcopy(rec), "ordering": {"method": "magnitude"}})


def test_canonical_ordering_is_recorded_for_roots(session):
    out = ingest(session, grant(run="run-roots", grant_id="g-run-roots"),
                 math_export("run-roots", check=None, values={"r0": -1.0, "r1": 1.0}), entry="ws-roots")
    _, rec = result_of(session, out)
    assert "identityCheck" not in rec and "ordering" not in rec
    out = ingest(session, grant(run="run-roots2", grant_id="g-run-roots2"),
                 sim_export(run="run-roots2", grant_id="g-run-roots2", seed=None, values={"r0": -1.0},
                            extra_values={"ordering": {"method": "real-then-imaginary", "realTieRelative": 1e-9}}),
                 entry="ws-roots2")
    _, rec = result_of(session, out)
    assert rec["ordering"] == {"method": "real-then-imaginary", "realTieRelative": 1e-9}
