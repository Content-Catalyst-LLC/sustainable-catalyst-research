"""RH-05.2 Research-owned metadata API tests: privacy, persistence, analysis."""
import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research-api"))

from app.main import app
from app import workspaces as ws
from app import object_registry as md


@pytest.fixture
def engine(tmp_path):
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "objects.sqlite"))
    ws.Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def make_workspace(session, *, owner="owner-a", area="climate", project="carbon", slug="carbon-work"):
    item = ws.create_entry_workspace(
        session, area_slug=area, project_key=project, owner_scope=owner,
        entry_slug=slug, title="Carbon research")
    return item


def approve_article(session, workspace):
    entry = session.get(ws.ResearchEntry, workspace.entry_id)
    project = session.get(ws.ResearchProject, entry.project_id)
    area = session.get(ws.ResearchArea, project.area_id)
    entry.visibility = ws.APPROVED
    entry.rights_cleared = True
    area.visibility = ws.APPROVED
    session.flush()


def register(session, workspace, *, authority="library", source_id="src-1",
             version="v1", kind="source", owner="owner-a", title="Verified paper"):
    return md.register_metadata_object(
        session, workspace=workspace, owner_scope=owner, authority=authority,
        source_id=source_id, source_version=version, kind=kind, title=title,
        public_summary="Public summary", doi="10.1000/example",
        concepts=["carbon removal", "agriculture"],
        source_sha256="a" * 64, data_classification="unknown",
    )


def approve_object(item):
    item.visibility = ws.APPROVED
    item.review_state = ws.APPROVED
    item.rights_cleared = True


def test_registration_persists_and_is_idempotent(engine):
    with Session(engine) as session:
        workspace = make_workspace(session)
        one = register(session, workspace)
        two = register(session, workspace, title="Modified data ignored by idempotent reimport")
        assert one.id == two.id
        assert one.title == "Verified paper"
        assert one.visibility == "private"
        assert one.rights_cleared is False
        assert one.review_state == "unreviewed"
        session.commit()
        obj_id = one.id
    with Session(engine) as session:
        loaded = session.get(md.ResearchObject, obj_id)
        assert loaded.source_version == "v1"
        assert loaded.workspace_id is not None


def test_cannot_register_in_another_owner_workspace(engine):
    with Session(engine) as session:
        workspace = make_workspace(session)
        with pytest.raises(PermissionError):
            register(session, workspace, owner="another-owner")


def test_rejects_unsupported_identity_hash_types_and_lengths(engine):
    with Session(engine) as session:
        workspace = make_workspace(session)
        base = dict(
            session=session, workspace=workspace, owner_scope="owner-a",
            authority="library", source_id="src", source_version="1",
            kind="source", title="Title",
        )
        for override in (
            {"authority":"not-trusted"},
            {"kind":"arbitrary-executable"},
            {"source_sha256":"bad"},
            {"source_version":""},
            {"title":" "},
            {"data_classification":"scientifically_proven"},
            {"concepts":["x"] * 33},
            {"doi":"suspiciously invented"},
        ):
            with pytest.raises(ValueError):
                md.register_metadata_object(**(base | override))


def test_public_api_excludes_private_and_rights_unknown(engine):
    with Session(engine) as session:
        workspace = make_workspace(session)
        approve_article(session, workspace)
        approved = register(session, workspace, source_id="approved")
        approve_object(approved)
        private = register(session, workspace, source_id="secret-internal-object")
        rights_unknown = register(session, workspace, source_id="uncleared")
        rights_unknown.visibility = ws.APPROVED
        rights_unknown.review_state = ws.APPROVED
        session.commit()
        public_key = approved.public_key
        private_key = private.public_key
        uncleared_key = rights_unknown.public_key

    def session_override():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[ws.get_session] = session_override
    try:
        client = TestClient(app)
        res = client.get("/api/research/v1/objects")
        assert res.status_code == 200
        data = res.json()
        assert data["schema"] == "sc-research-public-objects/1.0"
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == public_key
        assert data["items"][0]["title"] == "Verified paper"
        for sensitive in ("secret-internal-object", "uncleared", "owner-a", "src-1"):
            assert sensitive not in res.text
        assert client.get("/api/research/v1/objects/" + public_key).status_code == 200
        assert client.get("/api/research/v1/objects/" + private_key).status_code == 404
        assert client.get("/api/research/v1/objects/" + uncleared_key).status_code == 404
        assert client.get("/api/research/v1/objects?kind=dataset").json()["items"] == []
        assert client.get("/api/research/v1/objects?q=Verified").json()["items"][0]["id"] == public_key
        assert client.get("/api/research/v1/objects?q=Unknown").json()["items"] == []
        assert client.get("/api/research/v1/objects?limit=100").status_code == 422
        assert client.get("/api/research/v1/objects?offset=-1").status_code == 422
        stat = client.get("/api/research/v1/objects/stats/summary")
        assert stat.status_code == 200
        assert stat.json()["by_kind"] == {"source": 1}
    finally:
        app.dependency_overrides.clear()


def test_public_api_requires_approved_area_and_entry(engine):
    with Session(engine) as session:
        workspace = make_workspace(session)
        obj = register(session, workspace)
        approve_object(obj)
        assert session.execute(md.public_query()).all() == []
        entry = session.get(ws.ResearchEntry, workspace.entry_id)
        entry.visibility = ws.APPROVED
        entry.rights_cleared = True
        assert session.execute(md.public_query()).all() == []
        session.get(ws.ResearchArea,
            session.get(ws.ResearchProject, entry.project_id).area_id).visibility = ws.APPROVED
        assert len(session.execute(md.public_query()).all()) == 1


def test_relationships_are_private_by_default_and_restricted_to_owner(engine):
    with Session(engine) as session:
        workspace = make_workspace(session)
        approve_article(session, workspace)
        a = register(session, workspace, source_id="dataset", kind="dataset")
        # A statistical result: figures are public only once released (SC-FIG-03A, test_sc_fig03a_*).
        b = register(session, workspace, source_id="figure", kind="statistical-result")
        approve_object(a)
        approve_object(b)
        relation = md.propose_relation(
            session, workspace=workspace, owner_scope="owner-a",
            subject=b, target=a, predicate="derived-from",
            private_basis="figure generated from confidential dataset run")
        assert relation.review_state == "proposed"
        assert relation.visibility == "private"
        assert md.approved_metadata_graph(session)["edges"] == []
        relation.review_state = ws.APPROVED
        relation.visibility = ws.APPROVED
        relation.public_note = "Reviewed source lineage"
        session.flush()
        graph = md.approved_metadata_graph(session)
        assert len(graph["nodes"]) == 2
        assert len(graph["edges"]) == 1
        assert graph["edges"][0]["note"] == "Reviewed source lineage"
        assert "confidential" not in str(graph)
        relation.evidence_assertion = True
        session.flush()
        assert md.approved_metadata_graph(session)["edges"] == []


def test_cross_owner_relation_prohibited(engine):
    with Session(engine) as session:
        a_space = make_workspace(session)
        b_space = make_workspace(
            session, owner="owner-b", area="economics", project="metrics", slug="sdg-metrics")
        a = register(session, a_space, source_id="a")
        b = register(session, b_space, source_id="b", owner="owner-b")
        with pytest.raises(PermissionError):
            md.propose_relation(
                session, workspace=a_space, owner_scope="owner-a",
                subject=a, target=b, predicate="related-to")
        with pytest.raises(PermissionError):
            md.propose_relation(
                session, workspace=a_space, owner_scope="other-owner",
                subject=a, target=a, predicate="related-to")


def test_public_graph_does_not_publish_unapproved_node(engine):
    with Session(engine) as session:
        workspace = make_workspace(session)
        approve_article(session, workspace)
        a = register(session, workspace, source_id="public")
        b = register(session, workspace, source_id="private")
        approve_object(a)
        rel = md.propose_relation(
            session, workspace=workspace, owner_scope="owner-a",
            subject=a, target=b, predicate="related-to")
        rel.visibility = ws.APPROVED
        rel.review_state = ws.APPROVED
        session.flush()
        result = md.approved_metadata_graph(session)
        assert len(result["nodes"]) == 1
        assert result["edges"] == []


def test_public_search_escapes_sql_wildcards(engine):
    with Session(engine) as session:
        workspace = make_workspace(session)
        approve_article(session, workspace)
        a = register(session, workspace, source_id="a", title="Climate 100%")
        approve_object(a)
        b = register(session, workspace, source_id="b", title="Climate Study")
        approve_object(b)
        assert len(session.execute(md.public_query(q="%")).all()) == 1
        assert len(session.execute(md.public_query(q="_")).all()) == 0
        assert len(session.execute(md.public_query(q="Climate")).all()) == 2


def test_entry_rights_revocation_hides_all_public_projections(engine):
    with Session(engine) as session:
        workspace=make_workspace(session)
        approve_article(session,workspace)
        item=register(session,workspace); approve_object(item); session.flush()
        assert len(session.execute(md.public_query()).all())==1
        entry=session.get(ws.ResearchEntry,workspace.entry_id)
        entry.rights_cleared=False; session.flush()
        assert ws.approved_workspace_view(session,entry.slug) is None
        assert ws.approved_catalog_from_database(session)==([],[])
        assert session.execute(md.public_query()).all()==[]
        assert md.approved_metadata_graph(session)['nodes']==[]


def test_relation_in_private_workspace_does_not_leak_public_note(engine):
    with Session(engine) as session:
        public=make_workspace(session); approve_article(session,public)
        private=make_workspace(session,project='private-project',slug='private-entry')
        a=register(session,public,source_id='a'); b=register(session,public,source_id='b')
        approve_object(a); approve_object(b)
        edge=md.propose_relation(session,workspace=private,owner_scope='owner-a',subject=a,target=b,predicate='references')
        edge.visibility=ws.APPROVED; edge.review_state=ws.APPROVED
        edge.public_note='SENTINEL_PRIVATE_WORKSPACE'; session.flush()
        graph=md.approved_metadata_graph(session)
        assert len(graph['nodes'])==2
        assert graph['edges']==[]
        assert 'SENTINEL_PRIVATE_WORKSPACE' not in str(graph)
