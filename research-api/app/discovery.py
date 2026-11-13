"""RH-08D: public discovery capability contract, providers disabled by default.

These routes make the UI/API boundary concrete without claiming that external
scholarly retrieval, evidence verification or private run persistence exists.

RH-08D-03 connects live OpenAlex/Crossref search through the guarded HTTPS transport.
It is disabled by default: ``activation.resolve()`` enables it only for an explicitly
configured staging deployment, and production stays refused by a code constant
(``PRODUCTION_DISCOVERY_AUTHORIZED``). Results are discovered metadata only: nothing
is saved, submitted to the Library, treated as Research evidence or published.
"""
import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict

from .discovery_adapters import RateLimiter, RetryPolicy, discover
from .discovery_adapters import activation as discovery_activation
from .discovery_adapters.library_intake import LIBRARY_INTAKE_ENABLED, blocking_issues
from .discovery_adapters.live import GuardedTransport, ProviderGuard, https_factory

router = APIRouter(prefix="/api/research/v1/discovery", tags=["Discovery"])

STAGE = "RH-08D-03"
SEARCHABLE = {"all": ("openalex", "crossref"), "openalex": ("openalex",), "crossref": ("crossref",)}
USER_AGENT = "SustainableCatalystResearch/0.3 (research discovery; metadata only)"
# Shared by every request in the process, so visitors cannot multiply provider traffic.
_LIMITER = RateLimiter(rate_per_second=discovery_activation.RATE_PER_SECOND, burst=discovery_activation.BURST)
_RETRY = RetryPolicy(max_attempts=discovery_activation.MAX_ATTEMPTS, backoff_seconds=1.0,
                     max_wait_seconds=discovery_activation.MAX_RETRY_WAIT_SECONDS)
_GUARD = ProviderGuard()
_SLOTS = threading.BoundedSemaphore(discovery_activation.MAX_CONCURRENT_SEARCHES)
# Discovered metadata is kept apart from Library-reviewed sources and approved Research evidence.
SEPARATION = {
    "tier": "discovered-metadata",
    "library_reviewed": False,
    "research_evidence": False,
    "statement": "Discovered, unreviewed metadata. Not reviewed by the Library and not approved Research evidence.",
}

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


def current_activation() -> discovery_activation.Activation:
    return discovery_activation.resolve()


@router.get("/providers")
def list_providers():
    # Provider capabilities are public configuration, without credentials or the contact address.
    state = current_activation()
    providers = [{**provider, "enabled": state.active and provider["id"] in state.providers} for provider in PROVIDERS]
    return {
        "schema": "sc-research-discovery-providers/1.0",
        "providers": providers,
        "enabled_count": sum(p["enabled"] for p in providers),
        "persisted": False,
        "status": "staging" if state.active else "planned",
    }


@router.get("/readiness")
def readiness():
    state = current_activation()
    return {
        "schema": "sc-research-discovery-readiness/1.0",
        "stage": STAGE,
        "status": "active_staging" if state.active else "not_activated",
        "mode": state.mode,
        "reason": state.reason,
        "external_retrieval_enabled": state.active,
        "enabled_providers": list(state.providers),
        "production_discovery_authorized": discovery_activation.PRODUCTION_DISCOVERY_AUTHORIZED,
        "saved_searches_enabled": False,
        "article_private_context_enabled": False,
        "research_evidence_promotion_enabled": False,
        "transport_prepared": True,
        "library_intake_enabled": LIBRARY_INTAKE_ENABLED,
        "publication_enabled": False,
        "providers": _GUARD.status() if state.active else {},
    }


def live_transport(state: discovery_activation.Activation) -> GuardedTransport:
    """The guarded HTTPS transport for one search. Only reached when activation is active."""
    return GuardedTransport(_GUARD, state.providers, https_factory(USER_AGENT))


def search_response(request: DiscoverySearchRequest, transport, *, enabled=None, contact=None, limiter=None,
                    now=None) -> dict:
    """One discovery run as the public response. Nothing is stored; the search text appears only as a hash."""
    providers = SEARCHABLE[request.provider]
    clock = {"now": now} if now else {}
    run = discover(request.query, providers=providers,
                   enabled=frozenset(providers if enabled is None else enabled), transport=transport,
                   limiter=limiter or _LIMITER, retry=_RETRY, publication_year=request.publication_year,
                   rows=discovery_activation.ROWS, contact=contact, **clock)
    return {
        "schema": "sc-research-discovery-search/1.0",
        "stage": STAGE,
        "separation": SEPARATION,
        "run": run.model_dump(mode="json", by_alias=True),
        "library_intake": {
            "enabled": LIBRARY_INTAKE_ENABLED,
            "blocking_issues": {c.candidate_id: list(blocking_issues(c)) for c in run.candidates},
        },
        "saved": False,
        "publishable": False,
    }


def _unavailable(code: str, message: str, *, status_code: int = 503, reason: str | None = None, headers=None):
    detail = {"code": code, "message": message, "stage": STAGE, "candidates": [], "saved": False,
              "external_request_performed": False}
    if reason:
        detail["reason"] = reason
    raise HTTPException(status_code=status_code, detail=detail, headers=headers)


@router.post("/search")
def search(request: DiscoverySearchRequest):
    # No logging of user-entered research text, no private workspace lookup, nothing saved.
    # No result bodies or sources are fabricated: candidates come only from provider responses.
    state = current_activation()
    if not state.active:
        _unavailable("DISCOVERY_PROVIDERS_DISABLED", "Scholarly discovery has not been activated here.",
                     reason=state.reason)
    if request.provider not in SEARCHABLE:
        _unavailable("DISCOVERY_PROVIDER_NOT_AVAILABLE", "This source has no discovery adapter yet.")
    enabled = [p for p in SEARCHABLE[request.provider] if p in state.providers]
    if not enabled:
        _unavailable("DISCOVERY_PROVIDER_NOT_ENABLED", "This source is not enabled here.")
    if not _SLOTS.acquire(blocking=False):
        _unavailable("DISCOVERY_BUSY", "Discovery is handling other searches. Try again shortly.", status_code=429,
                     headers={"Retry-After": "5"})
    try:
        data = search_response(request, live_transport(state), enabled=enabled, contact=state.contact)
    finally:
        _SLOTS.release()
    return JSONResponse(data, headers={"Cache-Control": "no-store"})
