# RH-08D-03 — Controlled live discovery

**Date:** 2026-11-12 · **Status:** Implemented and tested offline. Live discovery is **disabled by default**. It can be switched on only in an isolated staging deployment, and only after separate owner authorisation. Production discovery, persistent writes, Library submission and publication stay disabled.  
**Builds on:** [RH-08D-01 adapters](RH-08D-01-DISCOVERY-ADAPTERS.md), [RH-08D-02 interface and transport](RH-08D-02-DISCOVER-INTERFACE.md) · **Register:** [RH-08D](RH-08D-IMPLEMENTATION.md)

## What changed

| Part | Location | Behaviour |
|---|---|---|
| Activation | `research-api/app/discovery_adapters/activation.py` | Decides whether live discovery may run, from the environment (see below). Any missing or invalid setting disables it and gives a reason code; it never runs half-configured. Production is refused by `PRODUCTION_DISCOVERY_AUTHORIZED = False`, a code constant. |
| Provider policy | same file, `POLICIES` and the module constants | Applies to each provider (OpenAlex, Crossref):<br>• 1 request/s, burst 1, shared by the whole process;<br>• at most 2 attempts, with no more than 3 s between them;<br>• 8 s per connect and per read, and 15 s for the whole request, including a body that arrives slowly;<br>• a 2 MB body cap and at most 25 records;<br>• 2,000 requests per UTC day;<br>• the circuit opens after 3 consecutive failures and holds requests back for 5 minutes.<br>Whole search: 25 s, with at most 2 searches at once. |
| Guarded transport | `research-api/app/discovery_adapters/live.py` | Wraps the RH-08D-02 `HttpsTransport` for one search. It refuses providers that are not enabled. It checks the run deadline and shortens each request's timeouts to the time left. It applies the daily budget and the circuit breaker. A request held back by policy is never sent, and is reported with `attempts: 0` and the code `daily-budget-exhausted`, `circuit-open` or `run-deadline`. Budgets and circuits are kept in memory only. |
| HTTPS transport | `https_transport.py` | Unchanged allowlist, address vetting, address pinning and TLS. New: the body is read in chunks against the total request deadline. Failures now have stable codes: `dns-failure`, `non-public-address`, `tls-failure`, `connection-failure`, `protocol-error`, `unexpected-content-encoding`, `response-too-large` and `timeout`. Exception text never reaches a response. |
| Search run | `search.py` | Reports policy holds and transport codes as typed provider outcomes. If a provider returns more records than were requested, the extras are dropped and counted (`beyond-requested-rows`). |
| API | `research-api/app/discovery.py` | `POST /discovery/search` responds as follows:<br>• 503 `DISCOVERY_PROVIDERS_DISABLED`, with a `reason`, unless activation is active;<br>• 503 `DISCOVERY_PROVIDER_NOT_ENABLED` for a source that is not configured;<br>• 503 `DISCOVERY_PROVIDER_NOT_AVAILABLE` for sources without an adapter;<br>• 429 `DISCOVERY_BUSY` (`Retry-After: 5`) when two searches are already running;<br>• otherwise 200 with the typed run, `Cache-Control: no-store`, `saved: false` and `publishable: false`.<br>Readiness reports the mode, the reason and the enabled providers, and whether each circuit is open. `/providers` marks enabled providers. The contact address is never returned. |
| Interface | `research-web/assets/research-discovery.js` and `.css`, `app.js` | Each card is labelled *Discovered — unreviewed*. A separation notice appears on every run. Live runs get a banner naming the sources actually searched and the retrieval time, and saying that results are not stored. Policy outcomes are explained in plain language. A refused search shows why and clears any earlier results. |
| Staging acceptance | `scripts/discovery_staging_acceptance.py` | Checks a staging API: readiness, two fixed public queries five seconds apart, and the refusal of a source without an adapter. It writes a summary report with counts, outcome codes and hashes, but no titles and no contact address. It runs only against loopback or an `https` host with a `staging` label. |

## Separation of discovered metadata, Library-reviewed sources and Research evidence

- Every candidate is typed `classification: discovered-unreviewed`, `review_status: unreviewed`, `publishable: false`. These are literal types: the models reject any other value, and unknown fields such as `evidence_status`.
- Every search response carries `separation: {tier: discovered-metadata, library_reviewed: false, research_evidence: false}`, and the interface states the same on every run.
- Discovery code imports no Library, editorial, workspace, figure or evidence module, and does not touch the database or the file system. None of those modules imports discovery. Tests check both directions.
- Library intake is still a draft only (`submit()` refuses). Saving runs or candidates is RH-08D-04 and needs the persistence, identity and owner-isolation gates.

## Tests

`tests/test_rh08d03_live_discovery.py` (66 tests). Each one goes through the real route, activation, guarded transport, `HttpsTransport`, adapters and merge. Only the resolver and the connection are fake, and sockets and DNS are blocked. The tests cover:

- **Activation:** each fail-closed reason; production refused even when everything else is set; rollback by switching the mode off.
- **Readiness:** readiness and providers without the contact address.
- **End to end:** provenance hashes against the bytes served; the request policy (hosts, `/works`, timeouts, `mailto`, no cookies or credentials); rate spacing.
- **Retractions and duplicates:** retracted works flagged; retraction notices rejected; DOI merges across providers; possible-duplicate cross-links.
- **Provider errors:** 5xx retried; short `Retry-After` honoured and long `Retry-After` not waited for; 400, 404 and 500 handled; 301 not followed.
- **Malformed responses:** HTML, invalid UTF-8, wrong shape, gzip, over 2 MB, bad records, too many records.
- **Network failures:** timeout, reset, TLS, DNS failure, private and metadata addresses (no connection made), a slowly trickled body.
- **Limits:** circuit open and recovery, the daily budget and its reset, the run deadline, timeouts capped by the time left, the concurrency limit, and no logging of the search text or contact.
- **Separation:** checked in both directions.
- **Acceptance runner:** the URL rules, a pass against a correct deployment, a fail when discovery is off, and detection of a broken separation.

`tests/test_rh08d03_discover_live.cjs` covers the live banner, the separation notice, outcome texts and refusal messages.

## Activation procedure (staging only; needs separate owner authorisation)

Preconditions, each recorded in Data before switching on:
1. **Provider terms.** The owner reviews the current OpenAlex and Crossref terms and rate guidance, and confirms or lowers `POLICIES` (a code change if lowered). Whether OpenAlex needs an API key is checked at the same time. This build sends none.
2. **Contact.** A monitored operator mailbox for `RESEARCH_DISCOVERY_CONTACT`. It is sent to the providers as `mailto` and is not shown publicly.
3. **Egress.** The staging host can reach `api.openalex.org` and `api.crossref.org` on 443 and nothing else is required. DNS resolves to public addresses.
4. **Isolation.** A staging deployment (`RESEARCH_ENV=staging`) that is not the production service, with its own logs. Searches send only the visitor's public query text to the providers, never Workspace or private context.

Switch on (staging service environment, then restart the API container):

    RESEARCH_ENV=staging
    RESEARCH_DISCOVERY_MODE=staging
    RESEARCH_DISCOVERY_PROVIDERS=openalex,crossref
    RESEARCH_DISCOVERY_CONTACT=<operator mailbox>

Verify:
1. `GET /api/research/v1/discovery/readiness` returns `status: active_staging`, `production_discovery_authorized: false`, and `false` for saving, intake and publication.
2. `python scripts/discovery_staging_acceptance.py --base-url <staging API base or SSH-tunnelled loopback> --out <report>` → `PASS`. Store the report in Data.
3. Walk through the first acceptance demonstration in the register by hand. Record outcomes, retractions, duplicates and conflicts as seen, and never fill gaps with invented results.

## Rollback

- **Immediate:** set `RESEARCH_DISCOVERY_MODE=disabled` (or remove it) and restart the API. The search returns 503 `DISCOVERY_PROVIDERS_DISABLED` (`reason: mode-disabled`) and no further provider requests are made. Readiness shows `not_activated`.
- **Single provider:** remove it from `RESEARCH_DISCOVERY_PROVIDERS`. Searches for it return 503 `DISCOVERY_PROVIDER_NOT_ENABLED`; "all" reports it as `disabled`.
- **Code:** revert this PR. RH-08D-02 behaviour returns: the search always answers 503.
- **Data:** nothing to roll back. Discovery writes no database rows, files or caches, and budgets and circuits are lost on restart.
- **Automatic:** a failing provider opens its circuit after three consecutive failures, and further requests are held back for five minutes.

## Still disabled

- **Production discovery:** needs `PRODUCTION_DISCOVERY_AUTHORIZED` changed in a reviewed PR after staging acceptance.
- **Persistent writes:** RH-08D-04.
- **Library submission and publication.**
- **Europe PMC, arXiv, web discovery and synthesis:** RH-08D-06/07.
