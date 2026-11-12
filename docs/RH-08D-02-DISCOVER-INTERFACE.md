# RH-08D-02 — Discover interface and transport preparation

**Date:** 2026-11-12 · **Status:** Source and offline tests. Live providers, persistent writes and publication are **disabled**.  
**Builds on:** [RH-08D-01 discovery adapters](RH-08D-01-DISCOVERY-ADAPTERS.md) · **Register:** [RH-08D](RH-08D-IMPLEMENTATION.md)

## What is ready

| Part | Location | Behaviour |
|---|---|---|
| Discover results interface | `research-web/assets/research-discovery.js`, `research-discovery.css` | Renders a `sc-research-discovery-search/1.0` response. Each candidate is labelled *Discovered · unreviewed* and shows retraction, duplicate and metadata-disagreement labels, authors, year, venue and DOI (linked only through `doi.org` after validation). Provider outcomes appear per source. Filters by provider, DOI and retraction apply without a new request. A synthetic-data banner appears whenever the run came from fixtures. Works at 390 px. |
| Source inspection and provenance | same module | A disclosure per candidate lists each contributing provider record: source, record link (OpenAlex work URLs only), retrieval time, adapter version and SHA-256 record hash. It also shows metadata disagreements per provider, cross-links to possible duplicates (kept as separate cards), and the publisher landing page (https only, `noopener noreferrer`, no referrer). |
| Duplicate handling | RH-08D-01 merge, shown here | Same DOI merges, and the card says "From OpenAlex (2 records)". Different DOIs for the same work stay separate and link to each other. |
| Library intake (future) | `research-api/app/discovery_adapters/library_intake.py` | `draft_proposal()` builds a typed `sc-research-library-intake-proposal/1.0`. It copies metadata and provenance unchanged, is fixed to `draft-not-submitted` / `library_review_required` / `not-evidence`, and lists `blocking_issues`: retracted, retraction status unknown, no DOI, metadata conflict, possible duplicate. `submit()` always refuses. The interface shows a disabled "Propose for Library review" button with the issues a reviewer would need to resolve. |
| HTTPS transport (prepared) | `research-api/app/discovery_adapters/https_transport.py` | Described below. |
| Search API | `research-api/app/discovery.py` | `POST /discovery/search` returns 503 `DISCOVERY_PROVIDERS_DISABLED` while `LIVE_PROVIDERS_AUTHORIZED = False`. That is a code constant, so turning it on is a reviewed change and cannot be done by configuration. The gated path, exercised only in tests, returns the typed run, intake blocking issues, `saved: false`, `publishable: false` and `Cache-Control: no-store`. It uses one per-process rate limiter, two attempts and a wait cap of 5 seconds. Readiness reports `stage: RH-08D-02` and `transport_prepared: true`, with every other capability `false`. |

### HTTPS transport checks

`HttpsTransport` checks every request before any network activity. The tests exercise it only with a fake resolver and a fake connection, and they fail if anything attempts DNS or a socket connection.

**Before DNS:**
- the request must use that provider's fixed host (`api.openalex.org` or `api.crossref.org`), the `/works` path, `https` and port 443;
- the URL may not contain user information, a query string or a fragment; the transport encodes parameters itself;
- anything else, including look-alike hosts and metadata-service addresses, is refused.

**After DNS:**
- the request is refused unless **every** resolved address is public, so loopback, private, carrier-grade NAT, link-local, reserved, multicast and IPv4-mapped loopback addresses all fail;
- the connection is pinned to the vetted address, and TLS is verified against the host name (TLS 1.2 or later, certificate required), so a changed DNS answer cannot redirect it.

**During the request:**
- no proxy, cookies or credentials are sent, and `Accept-Encoding: identity` is requested;
- compressed bodies are refused, and bodies are capped at 2 MB (`response-too-large`);
- redirects are returned as statuses and never followed;
- only content-type and rate-limit headers are kept.

**Failures:**
- timeouts raise `TransportTimeout`;
- TLS, connection and protocol errors raise `TransportError`;
- the connection is always closed.

The adapters turn these into typed provider outcomes.

## Still disabled (each needs separate owner authorisation)

1. **Live provider calls.** Setting `LIVE_PROVIDERS_AUTHORIZED` to true and turning on the provider registry needs provider terms and policy review, a contact address (`RESEARCH_DISCOVERY_CONTACT`) and an egress review on the deployment host.
2. **Persistent writes.** Saved runs and candidates belong to RH-08D-04, after the persistence, identity and owner-isolation gates.
3. **Library intake submission.** This needs a typed Library endpoint and Library-side review.
4. **Publication of any discovered record.**

## Synthetic fixtures

`tests/fixtures/rh08d02/discovery-search-synthetic.json` is the RH-08D-01 synthetic provider responses run through the RH-08D-02 search response. Regenerate it with `python tests/fixtures/rh08d02/make_synthetic_search.py`; a test fails if the committed file is stale. Every record in it is fictitious (`10.5555` DOIs, `SYNTHETIC` titles, `example.org` links). The public site never serves it. The register's rule against fabricating results to fill the interface still applies.

## Verification

    python -m pytest -q tests/test_rh08d02_discover.py tests/test_rh08d01_discovery_adapters.py tests/test_discovery_contract.py
    node --test tests/test_rh08d02_discover_interface.cjs
