"""RH-08D scholarly discovery adapters: OpenAlex and Crossref, disabled by default.

The package builds provider requests and turns provider responses into typed, unreviewed discovery
candidates. A run needs a transport passed in by the caller: ``DisabledTransport`` (the default, which
refuses), ``FixtureTransport`` (synthetic offline tests) or ``live.GuardedTransport`` (RH-08D-03), which wraps
the vetted ``https_transport`` with provider budgets, circuit breakers and a run deadline. ``app.discovery``
builds it only when ``activation.resolve()`` reports an active staging configuration. Persisting candidates,
submitting them to the Library or publishing anything is a separate, owner-authorised change.
``library_intake`` drafts Library review proposals; submission is disabled.

A candidate is never evidence. It carries provider metadata only (no abstracts or full text), its
provenance, ``classification = "discovered-unreviewed"`` and ``publishable = False``.
"""
from .doi import normalize_doi
from .models import (ADAPTER_VERSION, Candidate, CandidateAuthor, CandidateSource, DiscoveryRun, FieldConflict,
                     ProviderOutcome, ProviderRequest, ProviderResponse)
from .ratelimit import RateLimiter, RetryPolicy
from .search import PROVIDER_ADAPTERS, discover
from .transport import (DisabledTransport, FixtureTransport, ProviderDisabled, ProviderHeld, TransportError,
                        TransportTimeout)

__all__ = [
    "ADAPTER_VERSION", "Candidate", "CandidateAuthor", "CandidateSource", "DiscoveryRun", "FieldConflict",
    "ProviderOutcome", "ProviderRequest", "ProviderResponse", "RateLimiter", "RetryPolicy", "PROVIDER_ADAPTERS",
    "discover", "normalize_doi", "DisabledTransport", "FixtureTransport", "ProviderDisabled", "ProviderHeld",
    "TransportError",
    "TransportTimeout",
]
