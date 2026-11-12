"""SC-FIG-05.0 / 05.3 Research bindings (synthetic fixtures only; public CI).

05.3: a figure whose calculation references name runtime uncertainty records (regression bands, ensemble
quantile fans, map uncertainty) is bound only with those exact records, and they are released with the views
so the Lab renderer can verify every band before drawing it. Earlier figures' bindings are unchanged.

05.0: a proposed scene-3d figure is bound privately with a recomputed scene hash, and is never released while
the scene-3d contract is a proposal.
"""
import base64
import hashlib
import json
from copy import deepcopy

import pytest
from sqlalchemy import select

from tests.test_sc_fig03a_governed_figures import (  # noqa: F401 (fixtures)
    ARTICLE, P, SOIL, client, files_of, fig_export, key, make_publishable, publish, raw_of, stored, stores, variant, world,
)
from tests.test_sc_ed01_editorial import OWNER_ACTOR, publish_area
from tests.test_sc_model01_workspace_ingest import grant, ingest, session  # noqa: F401
from app import convergence as cv  # noqa: E402
from app import editorial as ed  # noqa: E402
from app import figure_releases as fr  # noqa: E402
from app import object_registry as md  # noqa: E402
from app import public_figures as pf  # noqa: E402

BAND = {"semantics": "confidence-band", "predictor": "t", "level": 0.9, "method": "stats::predict.lm(interval='confidence')",
        "distribution": "Student t", "degreesOfFreedom": 10, "evaluatedAt": "observed predictor values", "computedBy": "runtime",
        "points": [[0, 2.0, 1.8, 2.2], [1, 2.5, 2.35, 2.65]]}
BAND_RAW = (json.dumps(BAND, indent=1) + "\n").encode()
BAND_SHA = cv.canonical_sha256(BAND)  # producers hash the canonical JSON; Research accepts either form
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic scene view"
PNG_SHA = hashlib.sha256(PNG).hexdigest()


def entry(data, sha=None):
    return {"sha256": sha or hashlib.sha256(data).hexdigest(), "contentBase64": base64.b64encode(data).decode()}


def with_bands(b):
    b["calculationRefs"][0]["bandsSha256"] = BAND_SHA


def ingest_variant(session, change, extra_files=(), drop_extra=False):
    raw, sha = variant(SOIL["bundleSha256"], change)
    files = files_of(SOIL["bundleSha256"], raw) + list(extra_files)
    out = ingest(session, grant(), fig_export(figures=((sha, "soil"),)), entry=ARTICLE, figure_files=files)
    return out, sha


def world_of(out):
    return {k.split(":")[-1] if k.startswith("output:") else k: v["id"] for k, v in out["objects"].items()}


# --- 05.3 runtime uncertainty records ---------------------------------------------------------------------------

def test_runtime_band_record_is_bound_and_stored_with_the_views(session, stores):
    out, sha = ingest_variant(session, with_bands, [entry(BAND_RAW, BAND_SHA)])
    obj = session.get(md.ResearchObject, out["objects"]["output:soil"]["id"])
    _, b = fr.binding_of(session, obj)
    assert b["runtimeRecords"] == [{"kind": "bands", "calculationIndex": 0, "sha256": BAND_SHA, "bytes": len(BAND_RAW),
                                    "hashMode": "canonical-json"}]
    assert b["calculationRefs"]["producerCalculationRefs"][0]["bandsSha256"] == BAND_SHA
    assert f"{sha}/{BAND_SHA}" in stored(stores["candidates"])
    assert cv._public_figure_binding(b)["runtimeRecordSha256"] == [BAND_SHA]


def test_a_band_list_record_as_exported_by_workspace_is_bound(session, stores):
    # Workspace research_exports exports a fit's bands as a list (confidence and prediction); Workbench binds it.
    bands = [BAND, dict(BAND, semantics="prediction-band", points=[[0, 2.0, 1.5, 2.5], [1, 2.5, 2.0, 3.0]])]
    raw = (json.dumps(bands, indent=1) + "\n").encode()
    digest = cv.canonical_sha256(bands)
    out, sha = ingest_variant(session, lambda b: b["calculationRefs"][0].update(bandsSha256=digest), [entry(raw, digest)])
    _, b = fr.binding_of(session, session.get(md.ResearchObject, out["objects"]["output:soil"]["id"]))
    assert [r["sha256"] for r in b["runtimeRecords"]] == [digest]
    assert f"{sha}/{digest}" in stored(stores["candidates"])


@pytest.mark.parametrize("files, match", [
    ((), "runtime record was not supplied"),
    ((entry(b"not json at all"),), "runtime record was not supplied|no figure output"),
    ((entry(b"[1, 2, 3]"),), "runtime record was not supplied|no figure output"),
])
def test_a_band_without_its_runtime_record_is_never_bound(session, stores, files, match):
    with pytest.raises(ValueError, match=match):
        ingest_variant(session, with_bands, files)
    assert stored(stores["release"]) == []


def test_a_band_record_that_is_not_json_is_refused_even_when_its_hash_is_named(session, stores):
    junk = b"[1, 2, 3]\n"
    with pytest.raises(ValueError, match="runtime record was not supplied as matching JSON"):
        ingest_variant(session, lambda b: b["calculationRefs"][0].update(bandsSha256=hashlib.sha256(junk).hexdigest()), [entry(junk)])


def test_earlier_figures_bind_exactly_as_before(session, world):
    _, b = fr.binding_of(session, session.get(md.ResearchObject, world["soil"]))
    assert "runtimeRecords" not in b and "scene" not in b
    assert "runtimeRecordSha256" not in cv._public_figure_binding(b)


def _publishable_band_figure(session):
    from tests.test_sc_ed01_editorial import AREA
    out, sha = ingest_variant(session, with_bands, [entry(BAND_RAW, BAND_SHA)])
    w = world_of(out)
    ed.create_article(session, OWNER_ACTOR, area_slug=AREA, project_key="unused", workspace_project_id="proj-1",
                      slug=ARTICLE, title="Synthetic")
    make_publishable(session, w, figures=("soil",))
    publish_area(session)
    return w, sha


def test_release_serves_the_runtime_record_with_the_views(session, stores, client):
    w, sha = _publishable_band_figure(session)
    publish(session, w, figures=("soil",))
    session.commit()
    pkg = client.get(f"{P}/objects/{key(session, w['soil'])}/figure").json()
    roles = {f["sha256"]: f["role"] for f in pkg["files"]}
    assert roles[BAND_SHA] == "runtime-record" and roles[sha] == "bundle"
    item = next(f for f in pkg["files"] if f["sha256"] == BAND_SHA)
    assert client.get(item["url"]).content == BAND_RAW
    row = session.scalar(select(fr.FigureRelease))
    assert {f["role"] for f in row.record["files"]} == {"bundle", "view", "runtime-record"}


def test_release_refuses_an_altered_runtime_record_and_leaves_nothing_behind(session, stores):
    w, sha = _publishable_band_figure(session)
    rec = stores["candidates"] / sha / BAND_SHA
    rec.write_bytes(rec.read_bytes().replace(b"1.8", b"1.7", 1))
    with pytest.raises(PermissionError, match="runtime bands record .* is missing or altered"):
        publish(session, w, figures=("soil",))
    assert stored(stores["release"]) == []
    assert session.scalars(select(fr.FigureRelease)).all() == []


# --- 05.0 proposed scene-3d -------------------------------------------------------------------------------------

SCENE = {
    "sceneSchema": "sc.scene-3d.v0-proposed", "coordinateSystem": {"kind": "local-cartesian", "units": "m"},
    "meshes": [{"meshId": "surface", "vertexCount": 121, "faceCount": 200, "verticesSha256": "1" * 64, "facesSha256": "2" * 64,
                "scalarsSha256": "3" * 64, "evidenceClass": "modelled"}],
    "scalar": {"field": "elevation", "unit": "m", "range": [0, 12.5], "colormap": "cividis"},
    "camera": {"position": [30, -30, 25], "focalPoint": [5, 5, 2], "viewUp": [0, 0, 1], "projection": "perspective"},
    "interpolation": {"surface": "none-flat-faces", "declaredBy": "workbench-job"},
    "verticalExaggeration": 1,
    "renderer": {"name": "pyvista-vtk", "version": "synthetic", "offscreen": True, "network": "none"},
    "budgets": {"cpuSeconds": 60, "memoryMiB": 1024, "wallSeconds": 90, "concurrency": 1},
    "pixelTolerance": {"metric": "max-abs-channel", "value": 2},
}


def scene_record(**over):
    s = {**deepcopy(SCENE), **over}
    s["sceneSha256"] = cv.canonical_sha256(s)
    return s


def as_scene(scene):
    def change(b):
        b["figureType"] = "scene-3d"
        b["scene"] = scene
        b["artifacts"].append({"format": "png", "artifactRef": "sc://workbench/scene/synthetic.png", "sha256": PNG_SHA,
                               "mediaType": "image/png", "status": "local-review"})
    return change


def test_scene_is_bound_privately_with_a_recomputed_hash(session, stores):
    sc = scene_record()
    out, _ = ingest_variant(session, as_scene(sc), [entry(PNG)])
    _, b = fr.binding_of(session, session.get(md.ResearchObject, out["objects"]["output:soil"]["id"]))
    assert b["scene"]["sceneSha256"] == sc["sceneSha256"] and b["scene"]["contractStatus"] == "proposed-owner-review"
    assert b["scene"]["meshes"][0]["verticesSha256"] == "1" * 64 and b["scene"]["meshHashMode"] == "declared-by-producer"
    assert b["scene"]["renderer"] == {"name": "pyvista-vtk", "version": "synthetic", "offscreen": True, "network": "none"}
    assert "scene" not in cv._public_figure_binding(b)  # never public while proposed


@pytest.mark.parametrize("scene, match", [
    (lambda: {**scene_record(), "sceneSha256": "0" * 64}, "sceneSha256 does not match"),
    (lambda: {**scene_record(), "verticalExaggeration": 3}, "sceneSha256 does not match"),  # edited after hashing
    (lambda: scene_record(sceneSchema="sc.scene-3d.v1"), "scene-3d figure needs"),
    (lambda: scene_record(meshes=[{"meshId": "m", "verticesSha256": "x"}]), "vertex and face SHA-256"),
])
def test_scene_binding_refuses_unverifiable_scenes(session, stores, scene, match):
    with pytest.raises(ValueError, match=match):
        ingest_variant(session, as_scene(scene()), [entry(PNG)])


def test_scene_without_a_png_view_is_refused():
    b = json.loads(raw_of(SOIL["bundleSha256"]))
    b.update(figureType="scene-3d", scene=scene_record())
    with pytest.raises(ValueError, match="static PNG view"):
        fr.scene_binding(b)


def test_scene_release_is_held_while_the_contract_is_proposed(session, stores):
    from tests.test_sc_ed01_editorial import AREA
    out, _ = ingest_variant(session, as_scene(scene_record()), [entry(PNG)])
    w = world_of(out)
    ed.create_article(session, OWNER_ACTOR, area_slug=AREA, project_key="unused", workspace_project_id="proj-1",
                      slug=ARTICLE, title="Synthetic")
    make_publishable(session, w, figures=("soil",))
    publish_area(session)
    with pytest.raises(PermissionError, match=fr.SCENE_HOLD):
        publish(session, w, figures=("soil",))
    assert stored(stores["release"]) == []


@pytest.mark.parametrize("change", [
    lambda b: b.update(figureType="scene-3d"),
    lambda b: b.update(scene={}),
    lambda b: b["artifacts"].append({"format": "glb", "sha256": "4" * 64, "mediaType": "model/gltf-binary"}),
])
def test_release_policy_holds_every_scene_marker(change):
    b = json.loads(raw_of(SOIL["bundleSha256"]))
    assert pf.release_errors(b, article=ARTICLE, release_channel="staging-synthetic") == []
    change(b)
    assert fr.SCENE_HOLD in pf.release_errors(b, article=ARTICLE, release_channel="staging-synthetic")


# --- 05.0 proposed nD axes and selected slice (FIG-TEST-025) ----------------------------------------------------

DIMS = [
    {"id": "x", "role": "spatial", "kind": "continuous", "unit": "degrees_east", "crs": "EPSG:4326", "domain": {"min": -6.6, "max": -6.4},
     "independent": True, "evidenceClass": "hypothetical", "measured": False},
    {"id": "y", "role": "spatial", "kind": "continuous", "unit": "degrees_north", "crs": "EPSG:4326", "domain": {"min": 52.3, "max": 52.4},
     "independent": True, "evidenceClass": "hypothetical", "measured": False},
    {"id": "t", "role": "time", "kind": "discrete", "samples": ["2026-09-01", "2026-10-01"], "independent": True,
     "evidenceClass": "hypothetical", "measured": False},
    {"id": "p", "role": "parameter", "kind": "discrete", "unit": "t/ha", "samples": [0, 10, 20], "independent": True,
     "evidenceClass": "hypothetical", "measured": False},
]
FIELDS = [{"name": "modeled_carbon_removal", "unit": "tCO2/ha", "evidenceClass": "hypothetical", "independent": False}]


def nd(view=None, dims=None, fields=None, rehash=True):
    dims, fields = deepcopy(dims or DIMS), deepcopy(FIELDS if fields is None else fields)
    vp = deepcopy(view or {"visibleAxes": ["x", "y"], "fixed": [{"axis": "t", "value": "2026-09-01"}, {"axis": "p", "value": 10, "unit": "t/ha"}],
                           "aggregation": {"kind": "none-exact-slice"}})
    if rehash:
        vp["sliceSha256"] = cv.canonical_sha256({"dimensions": dims, "dependentFields": fields,
                                                  "viewProjection": {k: v for k, v in vp.items() if k != "sliceSha256"}})
    return {"dimensions": dims, "dependentFields": fields, "viewProjection": vp}


def test_selected_slice_is_bound_privately_with_a_recomputed_hash(session, stores):
    parts = nd()
    out, _ = ingest_variant(session, lambda b: b.update(parts))
    _, b = fr.binding_of(session, session.get(md.ResearchObject, out["objects"]["output:soil"]["id"]))
    pr = b["projection"]
    assert pr["sliceSha256"] == parts["viewProjection"]["sliceSha256"] and pr["contractStatus"] == "proposed-owner-review"
    assert [a["id"] for a in pr["axes"]] == ["x", "y", "t", "p"] and pr["dependentFields"][0]["name"] == "modeled_carbon_removal"
    assert pr["fixed"] == [{"axis": "t", "value": "2026-09-01"}, {"axis": "p", "value": 10, "unit": "t/ha"}]
    assert "projection" not in cv._public_figure_binding(b)


Z = {"id": "z", "role": "spatial", "kind": "discrete", "unit": "m_below_surface", "verticalReference": "local soil surface; synthetic",
     "samples": [0, 0.1, 0.3], "independent": True, "evidenceClass": "hypothetical", "measured": False}
EXTRA = [
    {"id": "p2", "role": "parameter", "kind": "discrete", "unit": "um", "samples": [50, 150], "independent": True,
     "evidenceClass": "hypothetical", "measured": False},
    {"id": "p3", "role": "parameter", "kind": "continuous", "unit": "mm/yr", "domain": {"min": 600, "max": 1400}, "independent": True,
     "evidenceClass": "hypothetical", "measured": False},
    {"id": "s", "role": "scenario", "kind": "categorical", "samples": ["baseline", "reapplied"], "independent": True,
     "evidenceClass": "hypothetical", "measured": False},
]
EXACT = {"kind": "none-exact-slice"}
T0, P10 = {"axis": "t", "value": "2026-09-01"}, {"axis": "p", "value": 10, "unit": "t/ha"}
EXTRA_FIXED = [{"axis": "p2", "value": 150, "unit": "um"}, {"axis": "p3", "value": 900, "unit": "mm/yr"}, {"axis": "s", "value": "reapplied"}]


def _many(n):
    return [{"id": f"q{i}", "role": "parameter", "kind": "discrete", "unit": "1", "samples": [0, 1], "independent": True,
             "evidenceClass": "hypothetical", "measured": False} for i in range(n)]


@pytest.mark.parametrize("dims, view, axes", [
    ([DIMS[0], DIMS[1], Z], {"visibleAxes": ["x", "y", "z"], "fixed": [], "aggregation": EXACT}, 3),
    ([DIMS[0], DIMS[1], Z, DIMS[2]], {"visibleAxes": ["x", "y", "z"], "fixed": [T0], "aggregation": EXACT}, 4),
    ([DIMS[0], DIMS[1], Z] + DIMS[2:], {"visibleAxes": ["x", "y", "z"], "fixed": [T0, P10], "aggregation": EXACT}, 5),
    ([DIMS[0], DIMS[1], Z] + DIMS[2:] + EXTRA, {"visibleAxes": ["x", "y", "z"], "fixed": [T0, P10] + EXTRA_FIXED, "aggregation": EXACT}, 8),
    ([DIMS[0], DIMS[1], Z] + DIMS[2:] + _many(11), {"visibleAxes": ["x", "y", "z"],
     "fixed": [T0, P10] + [{"axis": f"q{i}", "value": 0, "unit": "1"} for i in range(11)], "aggregation": EXACT}, 16),
])
def test_3d_4d_5d_and_general_nd_slices_bind_under_the_same_rules(session, stores, dims, view, axes):
    parts = nd(view, dims=dims)
    out, _ = ingest_variant(session, lambda b: b.update(parts))
    _, b = fr.binding_of(session, session.get(md.ResearchObject, out["objects"]["output:soil"]["id"]))
    assert len(b["projection"]["axes"]) == axes and b["projection"]["sliceSha256"] == parts["viewProjection"]["sliceSha256"]
    assert fr.ND_HOLD in pf.release_errors({**json.loads(raw_of(SOIL["bundleSha256"])), **parts}, article=ARTICLE,
                                           release_channel="staging-synthetic")


def _view(**kw):
    v = {"visibleAxes": ["x", "y"], "fixed": [{"axis": "t", "value": "2026-09-01"}, {"axis": "p", "value": 10, "unit": "t/ha"}],
         "aggregation": {"kind": "none-exact-slice"}}
    v.update(kw)
    return v


@pytest.mark.parametrize("parts, match", [
    (lambda: {**nd(), "viewProjection": {**nd()["viewProjection"], "sliceSha256": "0" * 64}}, "sliceSha256 does not match"),
    (lambda: nd(_view(visibleAxes=["x", "modeled_carbon_removal"])), "at most three declared independent axes"),
    (lambda: nd(fields=FIELDS + [{"name": "p", "unit": "t/ha", "evidenceClass": "hypothetical", "independent": False}]), "dependent output is declared"),
    (lambda: nd(dims=DIMS + [DIMS[2]]), "duplicate axis"),
    (lambda: nd(_view(fixed=[{"axis": "t", "value": "2026-09-15"}, {"axis": "p", "value": 10, "unit": "t/ha"}])), "not a listed sample"),
    (lambda: nd(_view(fixed=[{"axis": "t", "value": "2026-09-01"}, {"axis": "p", "value": 15, "unit": "t/ha"}])), "not a listed sample"),
    (lambda: nd(_view(fixed=[{"axis": "t", "value": "2026-09-01"}, {"axis": "p", "value": 10, "unit": "kg/ha"}])), "unit does not match"),
    (lambda: nd(_view(fixed=[{"axis": "t", "value": "2026-09-01"}])), "neither visible nor fixed"),
    (lambda: nd(_view(fixed=[{"axis": "q", "value": 1}, {"axis": "t", "value": "2026-09-01"}, {"axis": "p", "value": 10, "unit": "t/ha"}])), "undefined"),
    (lambda: {"viewProjection": nd()["viewProjection"]}, "needs declared dimensions"),
    (lambda: nd({"visibleAxes": ["x", "y"], "fixed": [T0, P10] + [{"axis": f"q{i}", "value": 0, "unit": "1"} for i in range(13)],
                 "aggregation": EXACT}, dims=DIMS + _many(13)), "at most 16 independent axes"),
    (lambda: nd({"visibleAxes": ["x", "y"], "fixed": [T0, P10, {"axis": "p3", "value": 2000, "unit": "mm/yr"}], "aggregation": EXACT},
                dims=DIMS + [EXTRA[1]]), "outside the declared domain"),
    (lambda: nd({"visibleAxes": ["x", "y"], "fixed": [T0, P10, {"axis": "s", "value": "optimistic"}], "aggregation": EXACT},
                dims=DIMS + [EXTRA[2]]), "not a listed sample"),
    (lambda: nd(dims=[{k: v for k, v in DIMS[0].items() if k != "crs"}] + DIMS[1:]), "needs a unit and a crs"),
    (lambda: nd(dims=DIMS[:2] + [{**DIMS[2], "kind": "continuous"}] + DIMS[3:]), "must be discrete"),
    (lambda: nd(dims=DIMS[:3] + [{**DIMS[3], "measured": True}]), "marked measured"),
    (lambda: nd(_view(fixed=[P10], aggregation={"kind": "declared-method"})), "needs its method"),
])
def test_slice_binding_refuses_ambiguous_or_interpolated_selections(session, stores, parts, match):
    p = parts()
    with pytest.raises(ValueError, match=match):
        ingest_variant(session, lambda b: b.update(p))


def test_slice_binding_refuses_an_isactual5d_claim(session, stores):
    p = nd()
    with pytest.raises(ValueError, match="isActual5d"):
        ingest_variant(session, lambda b: (b.update(p), b.setdefault("temporal", {}).update(isActual5d=True)))


def test_release_policy_holds_nd_metadata():
    b = json.loads(raw_of(SOIL["bundleSha256"]))
    b.update(nd())
    assert fr.ND_HOLD in pf.release_errors(b, article=ARTICLE, release_channel="staging-synthetic")
