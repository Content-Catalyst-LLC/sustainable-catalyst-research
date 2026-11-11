import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research-api"))
from fastapi.testclient import TestClient
import app.main as service

client = TestClient(service.app)


def test_development_health():
    response = client.get("/api/research/v1/health")
    assert response.status_code == 200
    assert response.json()["service"] == "research-api"


def test_default_catalog_empty():
    assert client.get("/api/research/v1/areas").json()["items"] == []
    assert client.get("/api/research/v1/articles").json()["items"] == []
    assert client.get("/api/research/v1/relationships").json()["nodes"] == []


def test_unknown_article_returns_404():
    assert client.get("/api/research/v1/articles/not-a-public-article").status_code == 404


def test_allowlist_blocks_private_artifacts(tmp_path, monkeypatch):
    fixture = {
        "schema": "sc-research-public-catalog/1.0",
        "areas": [
            {"slug": "climate", "title": "Climate", "summary": "Public overview",
             "visibility": "public-approved", "owner_secret": "secret-owner"},
            {"slug": "economics", "title": "Economics", "summary": "Private",
             "visibility": "private"}],
        "articles": [
            {"slug": "rights-unknown", "area_slug": "climate", "title": "Rights unknown sentinel",
             "summary": "Secret", "version": "1", "state": "published", "visibility": "public-approved"},
            {"slug": "draft", "area_slug": "climate", "title": "Private manuscript",
             "summary": "Secret", "version": "1", "state": "active-research", "visibility": "private"},
            {"slug": "published", "area_slug": "climate", "title": "Public synopsis",
             "summary": "Reviewed", "version": "1", "state": "active-research",
             "visibility": "public-approved", "rights_cleared": True, "workspace_id": "secret-workspace"},
            {"slug": "blocked-area", "area_slug": "economics", "title": "Blocked area",
             "summary": "Should not appear", "version": "1", "state": "published",
             "visibility": "public-approved"}]}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    monkeypatch.setattr(service, "CATALOG_FILE", path)
    areas = client.get("/api/research/v1/areas").text
    articles = client.get("/api/research/v1/articles").text
    assert "Public synopsis" in articles
    for secret in ("secret-owner", "secret-workspace", "Private manuscript", "Blocked area", "Rights unknown sentinel"):
        assert secret not in areas + articles
    assert client.get("/api/research/v1/articles/draft").status_code == 404
