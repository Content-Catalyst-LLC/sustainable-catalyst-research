# RH-08D-01 — Typed OpenAlex and Crossref discovery adapters (offline)

**Date:** 2026-11-12 · **Status:** Source and offline tests; provider calls **not enabled**.  
**Code:** `research-api/app/discovery_adapters/` · **Tests:** `tests/test_rh08d01_discovery_adapters.py` ·
**Fixtures:** `tests/fixtures/rh08d01/` (synthetic)

This milestone builds the adapter layer that the [RH-08D register](RH-08D-IMPLEMENTATION.md) assigns to
RH-08D-02 (provider metadata) and RH-08D-03 (DOI normalisation, deduplication, conflict and retraction states),
**without** the outbound HTTP part. The Discover interface is still to come and needs these adapters first.

## What it does

| Concern | Behaviour |
|---|---|
| Requests | `openalex.build_request` / `crossref.build_request` build a typed `ProviderRequest` (GET, fixed HTTPS endpoint, bounded rows ≤ 50, optional year filter). It carries no credentials, and a `mailto` contact is added only when an operator passes one. |
| Gating | `discover()` searches only the providers in `enabled`, which is empty by default. Its default transport is `DisabledTransport`, which refuses every request, so a bare call sends nothing. The package contains **no network transport**: the tests fail on any socket connection and on any HTTP-client import. |
| DOI normalisation | `normalize_doi` strips resolver/`doi:` prefixes, percent-encoding, trailing punctuation and unbalanced `)`, lowercases, and validates the `10.NNNN/…` form. Invalid DOIs become `None`; nothing is guessed. |
| Classification | Every `Candidate` is `discovered-unreviewed`, `review_status="unreviewed"` and `publishable=False`. These are literal types, so a model with any other value fails validation. A `DiscoveryRun` is likewise `persisted=False`, `publishable=False`. |
| Provenance | Each candidate lists every contributing provider record: provider, record ID, retrieval time, adapter version, and SHA-256 of the query, request, response body and canonical record. The search text itself is stored only as a hash. No abstract or full text is kept. |
| Duplicates | Records merge by normalised DOI, or by title key + year + first author's last name when there is no DOI. Different DOIs never merge (a preprint and its article stay separate) but are cross-referenced in `possible_duplicate_of`. Title, year and venue disagreements are recorded as `conflicts`, not resolved silently. Candidate IDs are stable hashes of the merge key. |
| Retractions | OpenAlex `is_retracted` and Crossref `updated-by` retraction/withdrawal/removal set `retracted=True`. A Crossref retraction *notice* is rejected as a work. When no signal is present, the value is unknown (`None`) rather than "not retracted". |
| Unsafe values | Landing URLs are kept only if they use `https` and contain no user information. Text is cleaned and length-capped, and ORCIDs are validated. |
| Failures | Every provider returns a typed `ProviderOutcome` (`ok`, `disabled`, `rate_limited`, `unavailable`, `failed`, `invalid_response`) with a code: `http-N`, `timeout`, `transport-error`, `body-not-json`, `unexpected-shape`, `provider-not-enabled` or `transport-disabled`. Rejected records are counted by reason. One provider failing never hides the other provider's results. |
| Rate limits | A per-provider token bucket (`RateLimiter`) spaces requests. `RetryPolicy` retries only `rate_limited` and `unavailable` outcomes, honours `Retry-After` (seconds or HTTP date), and otherwise backs off exponentially. It stops without waiting when the requested wait exceeds its budget (30 s by default). |

## Not authorized by this milestone

- An outbound HTTP transport or any live provider call;
- turning on a provider flag;
- persisting runs or candidates;
- exposing candidates through the public `/discovery/search` route, which still returns 503;
- publishing any discovered record.

Each needs separate owner authorization. The live transport must also pass the RH-08D-02 gates: provider policy, safe outbound HTTP, timeouts and rate caps.

## Fixtures

All fixture records are synthetic. They use the `10.5555` example DOI prefix, `SYNTHETIC` titles, placeholder authors and `example.org` links; see `tests/fixtures/rh08d01/SOURCE.md`. They imitate the response *shape* of the two providers but do not reproduce any provider response.

## Verification

    python -m pytest -q tests/test_rh08d01_discovery_adapters.py
