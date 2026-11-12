"""RH-08D-01 scholarly discovery adapters: OpenAlex and Crossref, offline and disabled by default.

The package builds provider requests and turns provider responses into typed, unreviewed discovery
candidates. It contains **no network transport**: a run needs a transport passed in by the caller, and the
only transports here are ``DisabledTransport`` (the default, which refuses) and ``FixtureTransport`` (for
synthetic offline tests). Enabling live provider calls, persisting candidates or publishing anything is a
separate, owner-authorised change.

A candidate is never evidence. It carries provider metadata only (no abstracts or full text), its
provenance, ``classification = "discovered-unreviewed"`` and ``publishable = False``.
"""
from .doi import normalize_doi
from .models import (ADAPTER_VERSION, Candidate, CandidateAuthor, CandidateSource, DiscoveryRun, FieldConflict,
                     ProviderOutcome, ProviderRequest, ProviderResponse)
from .ratelimit import RateLimiter, RetryPolicy
from .search import PROVIDER_ADAPTERS, discover
from .transport import DisabledTransport, FixtureTransport, ProviderDisabled, TransportError, TransportTimeout

__all__ = [
    "ADAPTER_VERSION", "Candidate", "CandidateAuthor", "CandidateSource", "DiscoveryRun", "FieldConflict",
    "ProviderOutcome", "ProviderRequest", "ProviderResponse", "RateLimiter", "RetryPolicy", "PROVIDER_ADAPTERS",
    "discover", "normalize_doi", "DisabledTransport", "FixtureTransport", "ProviderDisabled", "TransportError",
    "TransportTimeout",
]
