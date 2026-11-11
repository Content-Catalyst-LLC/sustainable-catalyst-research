"""RH-02 — Each research output gets a durable logical workspace.

These tests use a *separate disposable SQLite database*. Production will use
Research's own PostgreSQL service; no Workspace or Library DB is accessed.
"""
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research-api"))
from app.main import app
from app import workspaces as ws


@pytest.fixture()
def db(tmp_path):
    db_file = tmp_path / "research-test.sqlite3"
    engine = create_engine(f"sqlite+pysqlite:///{db_file}")
    ws.Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def provision(session, slug="wexford-scientific-research", project="wexford-erw"):
    workspace = ws.create_entry_workspace(
        session, area_slug="climate-and-land",
        project_key=project, owner_scope="owner-test",
        entry_slug=slug, title=slug.replace("-", " ").title(),
        public_summary="Verified safe summary for public visitors.",
    )
    session.flush()
    return workspace


def test_workspace_always_created_and_idempotent(db):
    with Session(db) as session:
        first = provision(session)
        second = provision(session)
        assert first.id == second.id
        assert len(session.scalars(select(ws.EntryWorkspace)).all()) == 1
        session.commit()
        first_id = first.id
    # Persistence across independent DB sessions
    with Session(db) as session:
        loaded = session.get(ws.EntryWorkspace, first_id)
        assert loaded is not None
        assert loaded.entry_id is not None


def test_each_article_has_independent_workspace_under_one_project(db):
    with Session(db) as session:
        scientific = provision(session, "wexford-scientific-research")
        policy = provision(session, "wexford-policy-research")
        assert scientific.id != policy.id
        assert len(session.scalars(select(ws.EntryWorkspace)).all()) == 2
        assert len(session.scalars(select(ws.ResearchProject)).all()) == 1
        session.commit()


def test_owner_scope_cannot_be_reassigned(db):
    with Session(db) as session:
        provision(session)
        with pytest.raises(PermissionError):
            ws.create_entry_workspace(
                session, area_slug="climate-and-land",
                project_key="wexford-erw", owner_scope="attacker",
                entry_slug="another-paper", title="Unauthorized",
            )
        assert len(session.scalars(select(ws.EntryWorkspace)).all()) == 1


def test_revisions_append_and_private_by_default(db):
    with Session(db) as session:
        workspace = provision(session)
        first = ws.add_private_revision(
            session, workspace=workspace, change_summary="First version",
            content_ref="workspace:private-source-1", content_sha256=hashlib.sha256(b"a").hexdigest(),
        )
        second = ws.add_private_revision(
            session, workspace=workspace, change_summary="Additional evidence",
            content_ref="workspace:private-source-2", content_sha256=hashlib.sha256(b"b").hexdigest(),
        )
        assert (first.revision_number, second.revision_number) == (1, 2)
        assert second.review_state == "private"
        assert second.released_at is None
        assert first.content_ref != second.content_ref
        with pytest.raises(ValueError):
            ws.add_private_revision(
                session, workspace=workspace, change_summary="Bad",
                content_ref="something", content_sha256="unverified",
            )


def test_research_objects_are_linked_not_copied(db):
    with Session(db) as session:
        workspace = provision(session)
        first = ws.attach_private_object(
            session, workspace=workspace, source_authority="workbench",
            source_ref="graph:private-1", kind="figure",
        )
        second = ws.attach_private_object(
            session, workspace=workspace, source_authority="workbench",
            source_ref="graph:private-1", kind="figure",
        )
        assert first.id == second.id
        assert first.visibility == "private"
        assert first.rights_cleared is False
        with pytest.raises(ValueError):
            ws.attach_private_object(
                session, workspace=workspace, source_authority="unknown",
                source_ref="file:///etc/passwd", kind="figure",
            )


def test_public_workspace_requires_area_and_entry_approval(db):
    with Session(db) as session:
        workspace = provision(session)
        entry = session.get(ws.ResearchEntry, workspace.entry_id)
        area = session.get(ws.ResearchArea, session.get(ws.ResearchProject, entry.project_id).area_id)
        assert ws.approved_workspace_view(session, entry.slug) is None
        entry.visibility = "public-approved"
        entry.rights_cleared = True
        session.flush()
        assert ws.approved_workspace_view(session, entry.slug) is None
        area.visibility = "public-approved"
        session.flush()
        data = ws.approved_workspace_view(session, entry.slug)
        assert data["slug"] == "wexford-scientific-research"
        assert data["summary"] == "Verified safe summary for public visitors."
        assert data["revisions"] == []
        assert data["objects"] == []


def test_private_revisions_and_private_figure_never_leak(db):
    with Session(db) as session:
        workspace = provision(session)
        entry = session.get(ws.ResearchEntry, workspace.entry_id)
        entry.visibility = "public-approved"
        entry.rights_cleared = True
        project = session.get(ws.ResearchProject, entry.project_id)
        area = session.get(ws.ResearchArea, project.area_id)
        area.visibility = "public-approved"
        first = ws.add_private_revision(
            session, workspace=workspace, change_summary="private note",
            content_ref="private-path:/secrets/manuscript", content_sha256=hashlib.sha256(b"secret").hexdigest(),
        )
        obj = ws.attach_private_object(
            session, workspace=workspace, source_authority="lab",
            source_ref="lab:private-hash", kind="dataset",
        )
        session.flush()
        public = ws.approved_workspace_view(session, entry.slug)
        assert public["revisions"] == [] and public["objects"] == []
        assert "secrets" not in str(public) and "lab:private-hash" not in str(public)
        # Publication release is an independent, explicit editorial action.
        first.review_state = "public-approved"
        first.rights_cleared = True
        first.released_at = datetime.now(timezone.utc)
        first.change_summary = "Additional evidence now reviewed."
        obj.visibility = "public-approved"
        obj.rights_cleared = True
        obj.public_title = "Approved dataset overview"
        session.flush()
        public = ws.approved_workspace_view(session, entry.slug)
        assert len(public["revisions"]) == 1
        assert public["objects"] == [{"kind":"dataset", "title":"Approved dataset overview", "source_authority":"lab"}]
        assert "lab:private-hash" not in str(public)
        assert "private-path" not in str(public)


def test_public_api_workspace_endpoint_and_404(db):
    def override_session():
        with Session(db) as session:
            yield session
    app.dependency_overrides[ws.get_session] = override_session
    try:
        with Session(db) as session:
            workspace = provision(session)
            entry = session.get(ws.ResearchEntry, workspace.entry_id)
            entry.visibility = "public-approved"
            entry.rights_cleared = True
            p = session.get(ws.ResearchProject, entry.project_id)
            session.get(ws.ResearchArea, p.area_id).visibility = "public-approved"
            session.commit()
        client = TestClient(app)
        response = client.get("/api/research/v1/workspaces/wexford-scientific-research")
        assert response.status_code == 200
        assert response.json()["sections"][0] == "overview"
        assert client.get("/api/research/v1/workspaces/private-entry").status_code == 404
        assert client.get("/api/research/v1/workspaces/NOT-A-SLUG").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_workspace_has_no_unauthenticated_editor_route(db):
    client = TestClient(app)
    for path in (
        "/api/research/v1/workspaces/wexford-scientific-research/revisions",
        "/api/research/v1/editor/workspaces",
    ):
        assert client.post(path, json={"title":"malicious"}).status_code in (404, 405)
