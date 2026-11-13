# RH-08D-03 — Controlled live discovery

**Date:** 2026-11-12/13 · **Status:** Implemented and tested offline, with a provider-access compliance review and a staging preflight. Live discovery is **disabled by default**. It can be switched on only in an isolated staging deployment, and only after separate owner authorisation. Production discovery, persistent writes, Library submission and publication stay disabled.  
**Builds on:** [RH-08D-01 adapters](RH-08D-01-DISCOVERY-ADAPTERS.md), [RH-08D-02 interface and transport](RH-08D-02-DISCOVER-INTERFACE.md) · **Register:** [RH-08D](RH-08D-IMPLEMENTATION.md)

## What changed

| Part | Location | Behaviour |
|---|---|---|
| Activation | `research-api/app/discovery_adapters/activation.py` | Decides whether live discovery may run, from the environment (see [Activation procedure](#activation-procedure-staging-only-needs-separate-owner-authorisation)). Any missing or invalid setting disables it and gives a reason code; it never runs half-configured. Production is refused by `PRODUCTION_DISCOVERY_AUTHORIZED = False`, a code constant. |
| OpenAlex API key | same file; `https_transport.py` | Read only from the file named by `RESEARCH_OPENALEX_API_KEY_FILE` (a container secret). It is never read from an environment value. It is required whenever OpenAlex is enabled. It is sent only to `api.openalex.org`, as `Authorization: Bearer`, so it never appears in a URL, a provenance hash, a response, an error or a log. It is held in a `Secret` wrapper that hides it from `repr`. See [Key management](#key-management). |
| Provider identification | `activation.user_agent()` | Both providers receive `mailto=<contact>` and a User-Agent of the form `SustainableCatalystResearch/0.3 (+https://sustainablecatalyst.com; mailto:<contact>) research-discovery`. Contacts on reserved example or test domains are refused. |
| Provider policy | `activation.py`, `POLICIES` and the module constants | Applies to each provider:<br>• 1 request/s, burst 1, shared by the whole process;<br>• at most 2 attempts, with no more than 3 s between them;<br>• 8 s per connect and per read, and 15 s for the whole request, including a body that arrives slowly;<br>• a 2 MB body cap and at most 25 records;<br>• a daily request budget of **800 for OpenAlex**, below the free key's allowance of about 1,000 searches, and 2,000 for Crossref;<br>• the circuit opens after 3 consecutive failures and holds requests back for 5 minutes.<br>Whole search: 25 s, with at most 2 searches at once. See [Provider-policy assessment](#provider-policy-assessment-reviewed-2026-11-12). |
| Guarded transport | `research-api/app/discovery_adapters/live.py` | Wraps `HttpsTransport` for one search. It refuses providers that are not enabled. It checks the run deadline and shortens each request's timeouts to the time left. It applies the daily budget and the circuit breaker, and honours a provider's own budget signal: `X-RateLimit-Remaining: 0` with `X-RateLimit-Reset` holds that provider until the reset, at most 24 h. A request held back by policy is never sent, and is reported with `attempts: 0` and the code `daily-budget-exhausted`, `provider-budget-exhausted`, `circuit-open` or `run-deadline`. |
| HTTPS transport | `https_transport.py` | Unchanged allowlist, address vetting, address pinning and TLS. The body is read in chunks against the total request deadline. Failures have stable codes, and exception text never reaches a response. Provider rate-limit headers (OpenAlex `X-RateLimit-*`; Crossref `x-rate-limit-*`, `x-concurrency-limit`, `x-api-pool`) are kept. |
| API | `research-api/app/discovery.py` | `POST /discovery/search` responds as follows:<br>• 503 `DISCOVERY_PROVIDERS_DISABLED`, with a `reason`, unless activation is active;<br>• 503 `DISCOVERY_PROVIDER_NOT_ENABLED` for a source that is not configured;<br>• 503 `DISCOVERY_PROVIDER_NOT_AVAILABLE` for sources without an adapter;<br>• 429 `DISCOVERY_BUSY` when two searches are already running;<br>• otherwise 200 with the typed run and `Cache-Control: no-store`.<br>Readiness reports the mode, the reason, the enabled providers, and whether a contact and an OpenAlex key are configured (true or false only, never the values). |
| Interface | `research-discovery.js` and `.css`, `app.js` | Results are labelled *Discovered — unreviewed*. Every run shows the separation notice. Live runs show a banner naming the sources searched and the retrieval time. Policy outcomes and refused searches are explained in plain language. |
| Staging acceptance | `scripts/discovery_staging_acceptance.py` | Checks a staging API: readiness (including that a contact and an OpenAlex key are configured), two fixed public queries five seconds apart, and the refusal of a source without an adapter. It writes a summary report with no titles, contact address or key. It runs only against loopback or an `https` staging host. |
| Provider preflight | `scripts/discovery_provider_preflight.py` | A read-only check of DNS, TLS and one bare `GET /` per provider API root: no search, no key, no contact. It uses only the standard library and is run on the staging host and inside the staging API container. |

## Provider-policy assessment (reviewed 2026-11-12)

Sources were read on 2026-11-12. The OpenAlex help pages show "last updated 3 Nov 2026"; the Crossref sources are dated where noted.

| Topic | OpenAlex | Crossref | This build |
|---|---|---|---|
| Authentication | A key is not required for casual use, but a free key is strongly recommended. Without a key the daily budget is $0.10, with a free key $1. The key is sent as the `api_key` query parameter or `Authorization: Bearer`; the docs say both work the same way. ([authentication](https://help.openalex.org/api/authentication/), [quick reference](https://help.openalex.org/api/llm-quick-reference/)) | None for the public and polite pools; Metadata Plus is a paid service with its own key. ([access and authentication](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/)) | The OpenAlex key is **required** when OpenAlex is enabled, sent as a Bearer header (never the query parameter) and read from a file. Crossref uses the polite pool; Plus is not used. |
| Contact identification | `mailto` is not mentioned in the current docs. | "Strongly recommend" `mailto` on every request. The best-practice list also asks for a User-Agent that "identifies you and your script". Limits are applied **per email address**, and shared placeholder addresses are throttled together. Repeated invalid addresses can lead to a block. ([access and authentication](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/); [July 2026 limits post](https://community.crossref.org/t/refining-rest-api-limits-for-improved-stability-and-reliability/16137)) | Both providers get `mailto` and a User-Agent with the project URL and contact. Reserved example and test domains are refused. Whether the mailbox is monitored is an owner action. |
| Rate limits | 30 requests/s with a free key. Daily budget $1/day with a free key; a search costs $0.001, so about 1,000 searches a day. New accounts are paced at $0.10 per 5 minutes until the first $1 is used. Over the limit: 429. Headers `X-RateLimit-Limit/Remaining/Credits-Used/Reset`; the budget resets at midnight UTC. | Polite pool, list or search queries: **3 requests/s**, concurrency 3. Single DOI lookups: 10 requests/s. Public pool: 1 request/s for lists. In force since 21 July 2026 (earlier change: 1 Dec 2025). The access page's flat 5/10 table predates these changes. The live preflight saw `x-api-pool: public-array`, `x-rate-limit-limit: 1` and `x-concurrency-limit: 1` on a request with no `mailto`. | 1 request/s per provider and at most 2 concurrent searches: below both providers' limits. The OpenAlex daily budget of 800 is below the free allowance, and the provider's own `Remaining: 0` signal is honoured. The rates were kept as they were. |
| Retries | Exponential backoff on 429. The example retries 429 and 500 with `2**attempt` s, up to 5 times. `Retry-After` is documented only for the pacing of new accounts. | On 429, "take a pause then send requests at a lower rate". No backoff timings are given. Blocked clients get 403. | At most 2 attempts. `Retry-After` is honoured up to 3 s; anything longer ends the search for that provider. The circuit breaker opens after 3 consecutive failures. 403 is reported as `failed` and is not retried. |
| Metadata use | Records are CC0 ([pricing and access](https://help.openalex.org/access/pricing/)). The terms of service are PDFs linked from openalex.org/terms; they were **not read** and remain an owner review item. | Most metadata is facts, so CC0 or public domain, and may be used for any purpose. **Abstracts** may be under publisher or author copyright. ([metadata retrieval](https://www.crossref.org/documentation/retrieve-metadata/)) | Only bibliographic metadata is kept: no abstracts and no full text. Candidates are not published. |
| Caching | Not stated in the pages read. | Clients are encouraged to cache responses and avoid repeating requests. No maximum age is given. | **No cache.** Saving or reusing provider responses counts as persistence and has not been authorised. Searches send `Cache-Control: no-store`, and the low rate and daily budget limit repeated load. An in-memory cache with a short lifetime is a possible later change. |
| Key handling | Keep the key out of shared code; rotate it at openalex.org/settings/api, immediately or with a 24-hour or 7-day overlap. A personal key is also the account's sign-in credential. | Not applicable (no key). | See [Key management](#key-management). |

## Key management

1. **Account.** Create a dedicated OpenAlex account for the service (an institutional role mailbox), not a person's account. On personal accounts the key doubles as the sign-in credential. Get the free key from the account's API-key settings.
2. **Storage.** Write the key into one file outside every Git repository, readable only by the staging operator account. Mount it into the Research API container read-only as a Compose secret, for example at `/run/secrets/openalex_api_key`, and set `RESEARCH_OPENALEX_API_KEY_FILE` to that path. Never put the key in an environment value, a Compose file, a `.env` file under version control, an image, a URL, a ticket, chat, the Documentation, Data or Handoffs repositories, or a test.
3. **Exposure.**
   - The application sends the key only to `api.openalex.org`, as a header.
   - It is never in request URLs, so it is never in `request_sha256` provenance, proxy logs or error text.
   - Readiness reports only `openalex_api_key_configured: true/false`.
   - Tests check that the key never reaches responses, URLs, errors or logs, and never appears in `repr` output.
4. **Rotation.** Rotate with OpenAlex's 24-hour overlap, replace the file, restart the API, and confirm readiness. Rotate **immediately** if the key could have been exposed.
5. **Revocation and rollback.** Disabling discovery does not revoke the key. If the key could have been exposed, expire it in OpenAlex as well.

## Staging preflight (2026-11-13, read-only)

`scripts/discovery_provider_preflight.py` was run on the staging host and inside the staging Research API container. Nothing in the application was changed or enabled, and the container has no discovery settings. The detailed record is kept in the access-controlled operations evidence.

| Check | Staging host | Staging API container |
|---|---|---|
| DNS `api.openalex.org` | 4 addresses (Cloudflare), all public | **failed** (`dns-failure`) |
| DNS `api.crossref.org` | 4 addresses (AWS), all public | **failed** (`dns-failure`) |
| TLS | OpenAlex: TLSv1.3, certificate verified for the host, Google Trust Services, valid to 20 Dec 2026. Crossref: TLSv1.3, verified, Amazon, valid to 18 May 2027. | not reached |
| HTTPS `GET /` | OpenAlex: 200 `application/json`. Crossref: 302, not followed, with `x-api-pool: public-array`, `x-rate-limit-limit: 1`, `x-concurrency-limit: 1`. | not reached |

**Finding:** the staging Research API container is on an internal-only network, so it has no outbound DNS or internet route, by design. Live discovery in staging therefore needs an **owner-approved egress path** for the Research API container only. Options:

- **(a)** Attach the API container to a second, non-internal bridge network, and add a host firewall rule in the `DOCKER-USER` chain that allows that network only TCP 443 out plus DNS. Destinations are restricted by the application's host allowlist and public-address vetting. This is the simplest option.
- **(b)** Route outbound traffic through an egress proxy that allows only the two hosts. The transport currently refuses to use proxies, so this would be a code change.

Hosts are not pinned by IP address, because both providers serve from changing CDN and cloud addresses.

The deployed staging image predates the RH-08D-01 to 03 code. Activation needs a new staging image built from the merged PR #6, plus a staging deployment that the owner authorises.

## Activation procedure (staging only; needs separate owner authorisation)

Preconditions, each recorded in Data:
1. **Provider terms.** The owner reviews the OpenAlex terms-of-service PDFs and the Crossref documentation cited above, and confirms or lowers `POLICIES` (a code change if lowered).
2. **Contact.** A monitored institutional role mailbox (not on a reserved or example domain) for `RESEARCH_DISCOVERY_CONTACT`. It goes to the providers in `mailto` and the User-Agent and is not shown publicly. Crossref rate-limits per address, so do not share it with other tools.
3. **OpenAlex key.** Provisioned as described in [Key management](#key-management). Alternatively, enable Crossref only (`RESEARCH_DISCOVERY_PROVIDERS=crossref`), which needs no key.
4. **Egress.** The approved egress path for the staging Research API container, after which `discovery_provider_preflight.py` passes *inside the container*.
5. **Image.** A staging deployment of the merged RH-08D-03 code, isolated from production.

Switch on (staging Research API service only), then restart it:

    RESEARCH_ENV=staging
    RESEARCH_DISCOVERY_MODE=staging
    RESEARCH_DISCOVERY_PROVIDERS=openalex,crossref
    RESEARCH_DISCOVERY_CONTACT=<monitored institutional mailbox>
    RESEARCH_OPENALEX_API_KEY_FILE=/run/secrets/openalex_api_key

Verify:
1. Readiness returns `status: active_staging`, `contact_configured: true`, `openalex_api_key_configured: true`, `production_discovery_authorized: false`, and `false` for saving, intake and publication.
2. `python scripts/discovery_staging_acceptance.py --base-url <staging API base or SSH-tunnelled loopback> --out <report>` reports `PASS`. Store the report in Data.
3. Walk through the register's first acceptance demonstration by hand. Record outcomes, retractions, duplicates and conflicts as observed, and never fill gaps with invented results.

## Rollback

- **Immediate:** set `RESEARCH_DISCOVERY_MODE=disabled` (or remove it) and restart the API. Searches return 503 `DISCOVERY_PROVIDERS_DISABLED` (`reason: mode-disabled`), no further provider requests are made, and readiness shows `not_activated`.
- **Single provider:** remove it from `RESEARCH_DISCOVERY_PROVIDERS`.
- **Credentials:** remove the secret mount (discovery then refuses OpenAlex with `openalex-key-missing`). If the key could have been exposed, also expire it in OpenAlex.
- **Egress:** detach the extra network and remove the firewall rule; the container returns to having no egress.
- **Code:** revert PR #6; the search returns to always answering 503.
- **Data:** nothing to roll back. Discovery writes no rows, files or caches, and budgets and circuits are lost on restart.
- **Automatic:** a failing provider opens its circuit after three consecutive failures; a provider's own budget signal holds it until the reset.

## Tests

- **`tests/test_rh08d03_live_discovery.py` (88 tests).** Each search goes through the real route, activation, guarded transport, `HttpsTransport`, adapters and merge. Only the resolver and connection are fake, and sockets and DNS are blocked. Covered:
  - activation: each reason it stays off, including the key, reserved-domain and production cases;
  - the key: Bearer only to OpenAlex, never in URLs, provenance, responses, errors, logs or `repr`; file-only loading; bound to one provider;
  - the User-Agent and `mailto`;
  - provider budget signals;
  - retractions and duplicates;
  - provider errors and malformed responses;
  - network failures;
  - circuit, budget, deadline and concurrency limits;
  - separation of discovery from Library and evidence;
  - the acceptance runner and the preflight.
- **`tests/test_rh08d03_discover_live.cjs`:** the interface.

## Still disabled

- **Staging activation:** this PR does not switch it on.
- **Production discovery:** needs `PRODUCTION_DISCOVERY_AUTHORIZED` changed in a reviewed PR after staging acceptance.
- **Persistent writes and caching:** RH-08D-04.
- **Library submission and publication.**
- **Europe PMC, arXiv, web discovery and synthesis.**
