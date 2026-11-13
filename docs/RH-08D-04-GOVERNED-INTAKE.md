# RH-08D-04 — Governed Discovery Intake

**Status:** implementation draft. **Disabled by default**; not enabled in any environment.

**What it adds:** a reviewed path from a discovery result to:
- the owner's saved candidates, with provenance and deduplication;
- associations with the owner's Research articles;
- a recorded Library review proposal.

**What it does not do:**
- It does not send anything to the Library.
- It does not make any candidate Research evidence.
- It does not publish anything.

## Gate

| Variable | Effect |
|---|---|
| `RESEARCH_DISCOVERY_INTAKE` | `disabled` (default) or `staging`. Any other value is refused (`unknown-mode`). |
| `RESEARCH_ENV` | Must be `staging` or `development`. `production` is refused by the code constant `PRODUCTION_INTAKE_AUTHORIZED = False`. |
| Research write identity | The same gate as the convergence and editorial writes (`RESEARCH_CONVERGENCE_WRITES` with its service credential file). Without it the reason is `identity-gate-closed`. |

**Reporting:**
- `GET /api/research/v1/discovery/intake/status` reports the gate.
- Readiness reports `candidate_intake: {enabled, reason}`.
- While the gate is closed, every intake route returns 503 `DISCOVERY_INTAKE_DISABLED` with the reason.

**Search:** live search keeps its own RH-08D-03 activation. Intake is useful only where both are enabled.

## Process

1. **Search.** With intake enabled, a search response carries `intake.run_handle`.
   - The process holds that run's candidates in memory for 15 minutes, at most 32 runs. Nothing is written.
   - Without intake the response says `intake: {enabled: false, run_handle: null}`.
2. **Save** with `POST …/intake/candidates {run_handle, candidate_id}`.
   - The server stores **its own copy** of the candidate's metadata and provider provenance. Any metadata or sources in the request body are ignored, so clients cannot forge provenance.
   - **Deduplication is per owner:**
     - by DOI, normalised and hashed into the identity key;
     - otherwise by normalised title, year and first-author surname.
   - **Repeat saves** add only provider records not already held. Each record keeps the metadata it arrived with and that metadata's hash. The first saved metadata stays the candidate's display metadata, and a difference is reported as `metadataDiffers`.
   - **Reviewer view:** the `review` block, used for every Library proposal, combines all records:
     - a retraction reported by **any** record counts;
     - fields that differ between records (`doi`, `title`, `publication_year`, `venue`, `work_type`, `retracted`) become conflicts keyed by record;
     - so deduplication never hides a retraction or a correction from the Library reviewer.
   - **Possible duplicates:** the owner's candidates with the same title and year but a different identity are listed in `possibleDuplicateOf` **in both directions**, computed when read. They are never merged, and they make `possible-duplicate` a blocking issue.
3. **Associate** with `POST …/candidates/{id}/associations/{entry}`, and remove with `…/remove`.
   - This links a saved candidate to one of the owner's Research articles as a `related-candidate`. That is not evidence; evidence still goes through the existing editorial reference rules.
4. **Propose for Library review** with `POST …/candidates/{id}/library-proposal {acknowledged_blocking_issues}`. Owner only.
   - The owner must acknowledge exactly the issues a Library reviewer must resolve: `retracted`, `retraction-status-unknown`, `no-doi`, `metadata-conflict`, `possible-duplicate`.
   - The recorded proposal is the RH-08D-02 `LibraryIntakeProposal` (`draft-not-submitted`, `not-evidence`), with status `awaiting-library-endpoint` and `sent = false`.
   - **Nothing is transmitted:** `LIBRARY_SUBMISSION_AUTHORIZED = False`, there is no Library client, and `library_intake.submit` still refuses.
5. **Dismiss** with `POST …/candidates/{id}/dismiss`. Owner only.
   - Provenance and audit are kept. Saving the same candidate again restores it.

## Authorisation and isolation

**Identity:** the Research write identity.
- The service credential names the owner (`X-SC-Owner-Scope`).
- Anyone else acts with a Workspace-signed editor attestation (`X-SC-Actor-Scope`, `X-SC-Editor-Attestation`), verified by `editorial.resolve_actor`.

| Action | Owner | Editor (attested) |
|---|---|---|
| List, view, save | yes | yes, for that owner |
| Associate, dissociate | yes | only articles in the Workspace project the grant covers |
| Dismiss, propose to Library | yes | no |

**Isolation between owners:** owners never see each other's candidates. Another owner's candidate or article answers "not found", the same as an absent one.

## Storage (`discovery_intake_store.py`, migration `0013_discovery_intake`)

| Table | Contents |
|---|---|
| `research_discovery_saved_candidates` | Owner, identity key, title key, first saved candidate metadata (no abstracts), metadata hash, status. Unique on (owner, identity key). |
| `research_discovery_candidate_sources` | One row per provider record: provider, record id, retrieval time, query, request, response and record hashes, adapter version, the metadata it arrived with and its hash, and who saved it. **Append-only.** |
| `research_discovery_associations` | Saved candidate ↔ Research entry, `related-candidate`, active flag. |
| `research_discovery_library_proposals` | The proposal, its hash, acknowledged issues, status, `sent`. **Append-only.** |
| `research_discovery_intake_events` | Hash-chained audit: action, actor, owner, ids, hashes, counts. **Append-only.** No search text, no titles. |

**Append-only enforcement:** in PostgreSQL the append-only tables reject `UPDATE` and `DELETE` with the existing `research_deny_record_change()` trigger, checked by `scripts/accept_postgres.py`.

**Module split:** the tables live in `discovery_intake_store.py`, which has no discovery adapter imports. The migration, `alembic/env.py` and the PostgreSQL acceptance depend only on that module. The gated logic and routes are in `discovery_intake.py`. The search text itself is never stored; only its hash, inside provenance.

## Separation

A saved candidate is still `discovered-unreviewed` / `unreviewed`. These are fixed on every response:
- `library_reviewed: false`;
- `research_evidence: false`;
- `publishable: false`.

Saving, associating and proposing never change them.

## Tests

`tests/test_rh08d04_discovery_intake.py` (offline, SQLite, synthetic candidates) covers:
- gates and reasons;
- run handles: expiry, capacity, unknown handles;
- search response handles;
- saving with provenance;
- DOI and metadata deduplication;
- possible-duplicate flags and long-DOI keys;
- owner isolation and editor project scope;
- associations, dismissal and restore;
- Library proposals: exact acknowledgement, recorded but not sent, `submit` refusing;
- the audit chain, including tamper detection and no search text;
- HTTP: authentication, forged metadata ignored, cross-owner 404, editor 403, production refused.

`scripts/accept_postgres.py` adds a PostgreSQL storage acceptance step. It checks that the per-owner identity key is unique and that provenance, proposals and events reject `UPDATE` and `DELETE`, and it can be re-run on the same database. The deduplication and isolation logic is covered by the offline tests.

## Final review (2026-11-13)

**Authorisation:**
- Every intake route checks the intake gate before identity.
- Identity is the Research write identity:
  - the service credential is compared in constant time;
  - editor attestations are Workspace-signed and checked for owner, grantee, role and expiry.
- Owner scope is enforced on every lookup, and foreign records answer "not found".
- Editors associate only within their granted project. Dismissing and proposing are owner-only.
- Saving takes metadata only from the server's handle, never from the request.
- Editors can read all of the owner's saved candidates. That is intended: they act for the owner under a signed grant.

**Migration (`0013`):**
- Models and migration agree (`alembic check`, SQLite and PostgreSQL).
- Upgrade, downgrade to `0012` (all five tables removed) and re-upgrade are clean.
- The three append-only triggers are present and enforced.
- The per-owner identity is unique.
- New tables fall under the existing `research_reader` default `SELECT` privilege (migration `0003`), the same as every other private table. No public route reads them.

**Provenance integrity**, with these gaps found and fixed in this review:
1. A later record's disagreeing metadata, including a **retraction**, was kept only as a hash and did not reach the Library proposal. Each record now stores the metadata it arrived with, and proposals use the combined reviewer view.
2. Possible duplicates were recorded only on the later candidate, at save time, and did not raise a blocking issue. They are now computed in both directions and block until acknowledged.
3. A concurrent duplicate save surfaced as a 500 error. It is now a 409 (the transaction is rolled back).

**Residual (accepted):**
- Run handles live in one process's memory. With several API workers a save must reach the worker that ran the search, and otherwise asks the user to search again.
- Audit sequence numbers are allocated under a row lock in PostgreSQL. A rare conflict is the 409 above.

## Not authorised by this stage

- **Enabling intake** in any environment, including staging.
- **Library submission** (a typed Library endpoint and owner authorisation are needed).
- **Evidence promotion and publication.**
- **OpenAlex activation.**
- **Production deployment.**
- **Discovery UI:** the existing "Propose for Library review" button stays disabled. A staging UI for saving and proposing comes with the authenticated Workspace shell.

## Follow-up requirement from RH-08D-03 staging pilot 01

**RH-08D-03-F1 — capture provider rate-limit headers in acceptance evidence.**

- **Background:** pilot 01 recorded our policy and the preflight's Crossref pool headers. It did not record the rate-limit and pool headers of the search responses themselves, so polite-pool placement was not observed.

**Requirement:**
- **What is recorded:** each provider outcome records only allow-listed response headers:
  - Crossref: `x-rate-limit-limit`, `x-rate-limit-interval`, `x-concurrency-limit`, `x-api-pool`;
  - OpenAlex: `x-ratelimit-limit`, `x-ratelimit-remaining`, `x-ratelimit-reset`;
  - both: `retry-after`.
- **How values are kept:** each value is truncated and kept only if it matches a strict numeric or token pattern.
- **Where they appear:** the staging acceptance summary includes them.
- **What must never appear:** request headers, the User-Agent, the `mailto` value, the contact address or the OpenAlex key. Tests must assert that a synthetic contact and key never appear in the outcome or the report.
- **Gating:** this needs no new provider traffic to verify. Synthetic responses cover it offline, and the next authorised pilot records it live.
