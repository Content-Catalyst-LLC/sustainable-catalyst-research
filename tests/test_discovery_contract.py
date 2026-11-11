"""RH-08D-01: provider-disabling, metadata contracts, and public UI boundaries."""
from pathlib import Path

from fastapi.testclient import TestClient
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research-api"))
from app.main import app

client = TestClient(app)


def test_provider_registry_is_disabled_by_default():
    response = client.get("/api/research/v1/discovery/providers")
    assert response.status_code == 200
    data = response.json()
    assert data["schema"] == "sc-research-discovery-providers/1.0"
    assert data["enabled_count"] == 0
    assert len(data["providers"]) == 6
    assert not any(provider["enabled"] for provider in data["providers"])


def test_discovery_readiness_does_not_claim_live_retrieval():
    response = client.get("/api/research/v1/discovery/readiness")
    assert response.status_code == 200
    data = response.json()
    assert data["stage"] == "RH-08D-01"
    assert data["status"] == "not_activated"
    assert data["external_retrieval_enabled"] is False
    assert data["saved_searches_enabled"] is False
    assert data["article_private_context_enabled"] is False
    assert data["research_evidence_promotion_enabled"] is False


def test_search_returns_truthful_unavailable_not_invented_results():
    response = client.post("/api/research/v1/discovery/search", json={
        "query": "enhanced rock weathering agricultural soils",
        "provider": "openalex",
    })
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "DISCOVERY_PROVIDERS_DISABLED"
    assert detail["candidates"] == []
    assert detail["saved"] is False
    assert detail["external_request_performed"] is False


def test_search_request_bounded_and_no_private_context_fields():
    for request in (
        {"query": "ab", "provider": "openalex"},
        {"query": "x" * 241, "provider": "crossref"},
        {"query": "soil", "provider": "private-untrusted-provider"},
        {"query": "soil", "publication_year": 9999},
    ):
        response = client.post("/api/research/v1/discovery/search", json=request)
        assert response.status_code == 422
    response = client.post("/api/research/v1/discovery/search", json={
        "query": "soil weathering",
        "private_manuscript": "should never be accepted",
        "workspace_id": "owner-private-id",
    })
    # An unexpected private field must never be treated as an approved input.
    assert response.status_code == 422


def test_frontend_contains_first_party_discovery_views():
    html = (ROOT / "research-web/index.html").read_text(encoding="utf-8")
    js = (ROOT / "research-web/assets/app.js").read_text(encoding="utf-8")
    assert 'id="discover"' in html
    assert 'id="discovery-form"' in html
    assert 'id="workspace-discover-link"' in html
    assert "initializeArticleDiscoveryContext" in js
    assert "Discovery adapters are not active yet" in js
    assert "candidate.source_url" in js
    assert "title.textContent" in js
    assert "candidate.title ||" in js
    assert "innerHTML" not in js.replace("// API-derived values enter the DOM via textContent, never raw HTML.", "").replace("// Untrusted external metadata always uses textContent, not innerHTML.", "")
