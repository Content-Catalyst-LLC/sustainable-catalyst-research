"""RH-PROD-06: never confuse the static preview with the live Research database."""
import sys
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research-api"))
import app.main as service

client = TestClient(service.app)
URL = "/api/research/v1/public/status"


def test_no_backend_fails_closed_even_when_a_static_preview_exists(monkeypatch):
    monkeypatch.setattr(service, "ready", lambda: {"ok": True, "storage": "ready"})
    monkeypatch.setattr(service.research_workspaces, "persisted_public_catalog", lambda: None)
    response = client.get(URL)
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    assert "research-postgresql" not in response.text


def test_storage_not_ready_fails_closed(monkeypatch):
    def unavailable():
        raise HTTPException(status_code=503, detail="Research storage not ready")
    monkeypatch.setattr(service, "ready", unavailable)
    monkeypatch.setattr(service.research_workspaces, "persisted_public_catalog", lambda: ([], []))
    response = client.get(URL)
    assert response.status_code == 503
    assert "ready" not in response.json().get("storage", "")


def test_empty_persistent_catalog_is_connected_but_not_published(monkeypatch):
    monkeypatch.setattr(service, "ready", lambda: {"ok": True, "storage": "ready"})
    monkeypatch.setattr(service.research_workspaces, "persisted_public_catalog", lambda: ([], []))
    response = client.get(URL)
    assert response.status_code == 200
    assert response.json() == {
        "schema": "sc-research-public-backend-status/1.0",
        "source": "research-postgresql",
        "storage": "ready",
        "approved_areas": 0,
        "approved_articles": 0,
    }


def test_status_exposes_only_approved_counts_not_private_metadata(monkeypatch):
    monkeypatch.setattr(service, "ready", lambda: {"ok": True, "storage": "ready"})
    monkeypatch.setattr(service.research_workspaces, "persisted_public_catalog", lambda: (
        [{"slug": "approved", "title": "Approved research area", "owner_scope": "PRIVATE_OWNER"}],
        [{"slug": "paper", "title": "Approved public paper", "private_source_ref": "PRIVATE_SOURCE"}],
    ))
    response = client.get(URL)
    assert response.status_code == 200
    assert response.json()["approved_areas"] == 1
    assert response.json()["approved_articles"] == 1
    for private in ("PRIVATE_OWNER", "PRIVATE_SOURCE", "private_source_ref", "owner_scope"):
        assert private not in response.text
