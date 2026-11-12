"""Typed records for RH-08D-01 discovery. Every model forbids unknown fields."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ADAPTER_VERSION = "rh-08d-01/1.0"
ProviderId = Literal["openalex", "crossref"]
OutcomeStatus = Literal["ok", "disabled", "rate_limited", "unavailable", "failed", "invalid_response"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderRequest(_Frozen):
    """One outbound request, built but never sent by this package. It carries no credentials."""
    provider: ProviderId
    method: Literal["GET"] = "GET"
    url: str
    params: tuple[tuple[str, str], ...]
    query_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def key(self) -> str:
        """Stable identity used by fixture transports and for request provenance."""
        return self.provider + " " + self.url + "?" + "&".join(f"{k}={v}" for k, v in self.params)


class ProviderResponse(_Frozen):
    status: int = Field(ge=100, le=599)
    headers: dict[str, str] = Field(default_factory=dict)
    body: bytes = b""


class CandidateAuthor(_Frozen):
    name: str = Field(min_length=1, max_length=300)
    orcid: str | None = Field(default=None, pattern=r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")


class CandidateSource(_Frozen):
    """Where one provider record came from. The search text is kept only as a hash."""
    provider: ProviderId
    provider_record_id: str
    retrieved_at: str
    query_sha256: str
    request_sha256: str
    response_sha256: str
    record_sha256: str
    adapter_version: str = ADAPTER_VERSION


class FieldConflict(_Frozen):
    field: str
    values: dict[str, str]


class Candidate(_Frozen):
    candidate_id: str = Field(pattern=r"^cand-[0-9a-f]{24}$")
    classification: Literal["discovered-unreviewed"] = "discovered-unreviewed"
    review_status: Literal["unreviewed"] = "unreviewed"
    publishable: Literal[False] = False
    doi: str | None = None
    title: str = Field(min_length=1, max_length=1000)
    authors: tuple[CandidateAuthor, ...] = ()
    publication_year: int | None = Field(default=None, ge=1600, le=2100)
    venue: str | None = None
    work_type: str | None = None
    landing_url: str | None = None
    retracted: bool | None = None
    sources: tuple[CandidateSource, ...] = Field(min_length=1)
    conflicts: tuple[FieldConflict, ...] = ()
    possible_duplicate_of: tuple[str, ...] = ()


class ProviderOutcome(_Frozen):
    provider: ProviderId
    status: OutcomeStatus
    attempts: int = Field(ge=0)
    code: str | None = None
    retry_after_seconds: float | None = None
    records_received: int = 0
    records_rejected: int = 0
    rejection_reasons: dict[str, int] = Field(default_factory=dict)


class DiscoveryRun(_Frozen):
    schema_: Literal["sc-research-discovery-run/1.0"] = Field(default="sc-research-discovery-run/1.0", alias="schema")
    adapter_version: str = ADAPTER_VERSION
    query_sha256: str
    started_at: str
    transport: str
    external_request_performed: bool
    persisted: Literal[False] = False
    publishable: Literal[False] = False
    outcomes: tuple[ProviderOutcome, ...]
    candidates: tuple[Candidate, ...]
