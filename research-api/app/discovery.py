"""RH-08D: public discovery capability contract, providers disabled by default.

These routes make the UI/API boundary concrete without claiming that external
scholarly retrieval, evidence verification or private run persistence exists.

RH-08D-02 prepares the search path: typed OpenAlex/Crossref adapters, the vetted
HTTPS transport and Library intake drafts. ``LIVE_PROVIDERS_AUTHORIZED`` is a code
constant rather than an environment flag, so enabling live calls is a reviewed
change, and it stays False until the owner separately authorises it.
"""
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict

from .discovery_adapters import RateLimiter, RetryPolicy, discover
from .discovery_adapters.library_intake import LIBRARY_INTAKE_ENABLED, blocking_issues

router = APIRouter(prefix="/api/research/v1/discovery", tags=["Discovery"])

STAGE = "RH-08D-02"
LIVE_PROVIDERS_AUTHORIZED = False
SEARCHABLE = {"all": ("openalex", "crossref"), "openalex": ("openalex",), "crossref": ("crossref",)}
USER_AGENT = "SustainableCatalystResearch/0.2 (research discovery; metadata only)"
# One limiter per process, shared by every request, so visitors cannot multiply the provider rate.
_LIMITER = RateLimiter(rate_per_second=1.0, burst=1)
_RETRY = RetryPolicy(max_attempts=2, backoff_seconds=1.0, max_wait_seconds=5.0)

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
        "stage": STAGE,
        "status": "not_activated",
        "external_retrieval_enabled": False,
        "saved_searches_enabled": False,
        "article_private_context_enabled": False,
        "research_evidence_promotion_enabled": False,
        "transport_prepared": True,
        "library_intake_enabled": LIBRARY_INTAKE_ENABLED,
        "publication_enabled": False,
    }


def live_transport():
    """The vetted HTTPS transport. Only reached when live calls are authorised."""
    from .discovery_adapters.https_transport import HttpsTransport
    return HttpsTransport(user_agent=USER_AGENT)


def search_response(request: DiscoverySearchRequest, transport, *, limiter=None, now=None) -> dict:
    """One discovery run as the public response. Nothing is stored; the search text appears only as a hash."""
    providers = SEARCHABLE[request.provider]
    clock = {"now": now} if now else {}
    run = discover(request.query, providers=providers, enabled=frozenset(providers), transport=transport,
                   limiter=limiter or _LIMITER, retry=_RETRY, publication_year=request.publication_year, rows=25,
                   contact=os.getenv("RESEARCH_DISCOVERY_CONTACT") or None, **clock)
    return {
        "schema": "sc-research-discovery-search/1.0",
        "stage": STAGE,
        "run": run.model_dump(mode="json", by_alias=True),
        "library_intake": {
            "enabled": LIBRARY_INTAKE_ENABLED,
            "blocking_issues": {c.candidate_id: list(blocking_issues(c)) for c in run.candidates},
        },
        "saved": False,
        "publishable": False,
    }


def _unavailable(code: str, message: str):
    raise HTTPException(
        status_code=503,
        detail={
            "code": code,
            "message": message,
            "stage": STAGE,
            "candidates": [],
            "saved": False,
            "external_request_performed": False,
        },
    )


@router.post("/search")
def search(request: DiscoverySearchRequest):
    # Deliberately no network call, logging of user-entered research text, or
    # private workspace lookup. No result bodies/sources are fabricated.
    if not LIVE_PROVIDERS_AUTHORIZED:
        _unavailable("DISCOVERY_PROVIDERS_DISABLED", "Scholarly discovery adapters have not been activated.")
    if request.provider not in SEARCHABLE:
        _unavailable("DISCOVERY_PROVIDER_NOT_AVAILABLE", "This source has no discovery adapter yet.")
    return JSONResponse(search_response(request, live_transport()), headers={"Cache-Control": "no-store"})
