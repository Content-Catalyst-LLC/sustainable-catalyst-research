"""RH-08D-01: public discovery capability contract, providers disabled by default.

These routes make the UI/API boundary concrete without claiming that external
scholarly retrieval, evidence verification or private run persistence exists.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ConfigDict

router = APIRouter(prefix="/api/research/v1/discovery", tags=["Discovery"])

# Stable registry mirrored from Data's review-stage discovery contract.
# This is an explicitly disabled capability registry, NOT live provider config.
PROVIDERS = (
    {"id": "openalex", "category": "scholarly", "name": "OpenAlex", "phase": "RH-08D-02", "enabled": False},
    {"id": "crossref", "category": "scholarly", "name": "Crossref", "phase": "RH-08D-02", "enabled": False},
    {"id": "europe_pmc", "category": "scholarly", "name": "Europe PMC", "phase": "RH-08D-07", "enabled": False},
    {"id": "arxiv", "category": "preprint", "name": "arXiv", "phase": "RH-08D-07", "enabled": False},
    {"id": "searxng", "category": "general_web", "name": "Web discovery", "phase": "RH-08D-07", "enabled": False},
    {"id": "model_synthesis", "category": "inference_optional", "name": "Source-grounded synthesis", "phase": "RH-08D-06", "enabled": False},
)


class DiscoverySearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=3, max_length=240)
    provider: str = Field(default="all", pattern=r"^(?:all|openalex|crossref|europe_pmc|arxiv)$")
    publication_year: int | None = Field(default=None, ge=1600, le=2100)


@router.get("/providers")
def list_providers():
    # Provider capabilities are public configuration, without credentials.
    return {
        "schema": "sc-research-discovery-providers/1.0",
        "providers": [dict(provider) for provider in PROVIDERS],
        "enabled_count": 0,
        "persisted": False,
        "status": "planned",
    }


@router.get("/readiness")
def readiness():
    return {
        "schema": "sc-research-discovery-readiness/1.0",
        "stage": "RH-08D-01",
        "status": "not_activated",
        "external_retrieval_enabled": False,
        "saved_searches_enabled": False,
        "article_private_context_enabled": False,
        "research_evidence_promotion_enabled": False,
    }


@router.post("/search")
def search(request: DiscoverySearchRequest):
    # Deliberately no network call, logging of user-entered research text, or
    # private workspace lookup. No result bodies/sources are fabricated.
    raise HTTPException(
        status_code=503,
        detail={
            "code": "DISCOVERY_PROVIDERS_DISABLED",
            "message": "Scholarly discovery adapters have not been activated.",
            "stage": "RH-08D-01",
            "candidates": [],
            "saved": False,
            "external_request_performed": False,
        },
    )
