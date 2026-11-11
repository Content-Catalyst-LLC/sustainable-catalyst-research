"""SC-FIG-02B: sc.figure-bundle.v1 mapped into the existing Research object registry. Synthetic fixtures."""
import hashlib
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research-api"))

from app import figure_objects as fo  # noqa: E402
from app import object_registry as md  # noqa: E402
from app import workspaces as ws  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "sc_fig02b"
WS_BYTES = (FIX / "workspace-notebook.bundle.json").read_bytes()
LAB_BYTES = (FIX / "lab-erw.bundle.json").read_bytes()


@pytest.fixture
def session(tmp_path):
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "fig.sqlite"))
    ws.Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def workspace(session, owner="owner-a", slug="fig-work", project="carbon"):
    return ws.create_entry_workspace(session, area_slug="climate", project_key=project, owner_scope=owner,
                                     entry_slug=slug, title="Figure research")


def dataset(session, w, sha, owner="owner-a", source_id="ds-1"):
    return md.register_metadata_object(session, workspace=w, owner_scope=owner, authority="workspace",
                                       source_id=source_id, source_version="r1", kind="dataset",
                                       title="Synthetic dataset", source_sha256=sha, data_classification="synthetic")


def edited(raw, change):
    b = json.loads(raw)
    change(b)
    return json.dumps(b).encode()


def test_workspace_figure_becomes_private_figure_object_linked_to_its_inputs(session):
    w = workspace(session)
    bundle = json.loads(WS_BYTES)
    ds = dataset(session, w, bundle["sourceRefs"][0]["sha256"])
    out = fo.register_figure_bundle(session, workspace=w, owner_scope="owner-a", raw=WS_BYTES)
    obj = out["object"]
    assert obj.kind == "figure" and obj.authority == "workspace" and obj.source_id == "figure:SYN-NB-coefficients"
    assert obj.source_sha256 == hashlib.sha256(WS_BYTES).hexdigest() == out["bundleSha256"]
    assert obj.source_version == "v1+" + out["bundleSha256"][:16]
    assert (obj.visibility, obj.review_state, obj.rights_cleared, obj.public_summary) == ("private", "unreviewed", False, "")
    assert obj.data_classification == "modelled"
    assert [(r.object_id, r.predicate, r.visibility, r.review_state) for r in out["relations"]] == [
        (ds.id, "derived-from", "private", "proposed")]
    assert len(out["unmatched"]) == 2  # the run output and projection hashes have no Research objects yet: none invented
    assert session.scalar(select(func.count()).select_from(md.ResearchObject)) == 2


def test_registration_is_idempotent_and_a_changed_revision_needs_a_new_version(session):
    w = workspace(session)
    a = fo.register_figure_bundle(session, workspace=w, owner_scope="owner-a", raw=WS_BYTES)["object"]
    assert fo.register_figure_bundle(session, workspace=w, owner_scope="owner-a", raw=WS_BYTES)["object"].id == a.id
    changed = edited(WS_BYTES, lambda b: b.update(title=b["title"] + " (edited)"))
    with pytest.raises(ValueError, match="different bytes"):
        fo.register_figure_bundle(session, workspace=w, owner_scope="owner-a", raw=changed)
    v2 = fo.register_figure_bundle(session, workspace=w, owner_scope="owner-a",
                                   raw=edited(changed, lambda b: b.update(version=2)))["object"]
    assert v2.id != a.id and v2.source_version.startswith("v2+")


def test_synthetic_lab_fixture_is_classified_synthetic(session):
    out = fo.register_figure_bundle(session, workspace=workspace(session), owner_scope="owner-a", raw=LAB_BYTES)
    assert out["object"].authority == "lab" and out["object"].data_classification == "synthetic"


def test_only_the_workspace_owner_can_register_or_link(session):
    w = workspace(session)
    with pytest.raises(PermissionError):
        fo.register_figure_bundle(session, workspace=w, owner_scope="owner-b", raw=WS_BYTES)
    other = workspace(session, owner="owner-b", slug="other-work", project="other-carbon")
    dataset(session, other, json.loads(WS_BYTES)["sourceRefs"][0]["sha256"], owner="owner-b")
    out = fo.register_figure_bundle(session, workspace=w, owner_scope="owner-a", raw=WS_BYTES)
    assert out["relations"] == []  # another owner's dataset with the same hash is never linked


@pytest.mark.parametrize("change,message", [
    (lambda b: b.update(schema="sc.figure-bundle.v0"), "Not an sc.figure-bundle.v1"),
    (lambda b: b.update(evidenceClass="observed"), "no promotion to observed"),
    (lambda b: b["scientificContext"].update(measurementStatus="observed"), "no promotion to observed"),
    (lambda b: b["provenance"].update(authorityOwner="core"), "not a Research source authority"),
    (lambda b: b.update(figureId="../x"), "Invalid figureId"),
    (lambda b: b.update(version=0), "Invalid figure version"),
    (lambda b: b.update(sourceRefs=[]), "needs artifacts and source references"),
])
def test_refusals(session, change, message):
    with pytest.raises(ValueError, match=message):
        fo.register_figure_bundle(session, workspace=workspace(session), owner_scope="owner-a", raw=edited(WS_BYTES, change))


def test_not_json_or_oversized(session):
    w = workspace(session)
    for raw in (b"", b"\xff", b"[]", b"{" + b" " * fo.MAX_BUNDLE_BYTES + b"}"):
        with pytest.raises(ValueError):
            fo.register_figure_bundle(session, workspace=w, owner_scope="owner-a", raw=raw)
