# RH-08D-05 — Discover Interface and Governed Intake Integration

**Status:** merged to `main` (PR #11, after PR #10). **Not enabled anywhere.** It depends on RH-08D-04, including the PR #10 review fixes and migration `0014`.

**What it adds** to the Discover page, for the RH-08D-04 governed intake API:
- **authenticated candidate saving:** "Save to my sources" on a search result;
- **saved-source management:** a "My saved sources" panel showing review issues, possible duplicates and provenance counts, with dismissal;
- **Research article associations:** link and unlink the owner's articles as related candidates, never as evidence;
- **Library review proposals:** recorded only, never sent.

**Files:**
- `research-web/assets/research-discovery-intake.js`: client, panel and save action;
- `research-web/assets/research-discovery.js`: an optional hook on result cards;
- `research-web/index.html`: the hidden panel and the script tag;
- `research-web/assets/research-discovery.css`: styles.

## When it appears

On load, the page reads `GET /api/research/v1/discovery/intake/status`, which needs no credentials.

| Server reports | Page shows |
|---|---|
| `enabled: false` (default; always in production, where the server refuses by constant) | Nothing new. The panel stays `hidden`, no further request is made, and result cards keep the disabled "Propose for Library review" button. |
| `enabled: true` (staging or development with the RH-08D-04 gate open) | The "My saved sources" panel with a session credential form. |

A result card offers "Save to my sources" only when all of these hold:
- the intake panel is active;
- a session credential is set;
- the search response has `intake.enabled: true` and a well-formed `run_handle`.

## Authentication

Until the certified Workspace identity (SC-UP-01) exists, the page uses the same staging pattern as the editorial console:
- The owner (or an editor) types the staging service credential, the owner scope and, optionally, an acting editor and a Workspace-signed attestation.
- These values are held **only in the page's memory** (a closure). They are never written to `localStorage`, `sessionStorage`, IndexedDB or cookies. The token field is cleared once it is accepted.
- Requests use `credentials: "omit"`, `cache: "no-store"` and `referrerPolicy: "no-referrer"`. Credentials travel only as headers, never in URLs or bodies.
- "Forget credential" drops them; reloading the page does too.
- **Authorization stays on the server** (RH-08D-04). The page hides owner-only controls (Dismiss, Record proposal) for an acting editor, but that is a convenience, not a control.

## Behaviour

**Save:** sends only `{run_handle, candidate_id}`. The server stores its own copy of the metadata and provenance.
- A repeat save reports how many new provider records were added.
- An expired run handle (15 minutes, or a different API worker) asks the user to search again.

**Saved sources:** each card shows:
- status (`Saved — unreviewed` or `Dismissed`);
- retraction reported, possible duplicate and metadata disagreement labels, all from the server's combined `review` view;
- the conflicting fields, possible duplicates by title, and the number of provider records with providers.

Dismissed sources stay listed with their provenance. Saving them again restores them, and they offer no link, dismiss or proposal actions.

**Associations:** a select lists the owner's Research articles from `GET /editorial/articles` that are not yet linked. Links show as "related candidate, not evidence".

**Library review proposal:**
- The form lists the server's blocking issues. "Record proposal (not sent)" stays disabled until **every** issue is ticked, and the server again requires the exact set.
- A recorded proposal shows: "Recorded for Library review — not sent". The Library intake endpoint is not enabled.

**Safety of displayed values:**
- Every server and provider value enters the DOM through `textContent`.
- Saved ids (UUID) and article slugs are validated before they are used in a path.
- The page's CSP (`connect-src 'self'`) is unchanged.

## Tests

`tests/test_rh08d05_discover_intake.cjs` (Node, offline, fake DOM and fake fetch, 13 tests) covers:
- credentials sent only as headers, with `credentials: "omit"` and no caching;
- no storage APIs;
- path validation;
- error text, including expired handles;
- the panel hidden and silent when intake is disabled;
- sign-in loading articles and sources and clearing the token field;
- the Save action only for enabled runs with a session;
- exact acknowledgement before a proposal;
- editors seeing no owner actions;
- association link and unlink;
- dismissed sources;
- text-only rendering;
- the saved-status contract with `discovery_intake.SAVED`;
- a rejected credential is not kept and clears the previous session's sources.

## Local acceptance (2026-11-13)

**Setup:** the real Research API and this page on loopback (127.0.0.1), with:
- SQLite;
- the RH-08D-02 synthetic search fixture in place of provider search, so no provider traffic;
- a synthetic, per-run credential;
- two synthetic articles.

**Exercised in Chromium:**
- signing in;
- searching;
- saving four candidates (metadata disagreement, retraction, and a possible-duplicate pair);
- linking one to an article;
- recording a proposal for the retracted candidate after acknowledging `retracted`;
- dismissing one of the duplicate pair.

**Confirmed through the API:**
- the association exists;
- the proposal is `awaiting-library-endpoint` with `sent: false`;
- every source is still `library_reviewed: false` and `research_evidence: false`;
- the dismissed source is kept;
- no console errors.

**Bug found and fixed:** the panel first expected the status `saved` while the API returns `saved-unreviewed`, so every source showed as dismissed. A contract test now pins the value.

## Post-merge acceptance (2026-11-13, `main` at `4f35b00`)

**Checks rerun on the merged code:**
- Node: 61 passed. Python: 594 passed. Public source check: 0 findings.
- Strict PostgreSQL cycle on the CI image: upgrade to `0014`, storage acceptance and its re-run, downgrade to base, upgrade. Pass.
- Populated `0013` → `0014` upgrade and downgrade: pass.

**Chromium, same loopback harness, driving the real page:**
- 4 saves, a link, a proposal (disabled until acknowledged, then "not sent") and a dismissal, in that order;
- `localStorage`, `sessionStorage` and cookies empty;
- the credential not present in the DOM;
- another owner sees 0 sources.

**Server refusals, checked directly:**
- no credential: 401;
- wrong credential: 401;
- acting editor without an attestation: 403;
- another owner listing: empty;
- another owner fetching by id: 404.

**Defect found and fixed** (PR "RH-08D-05: sign-in feedback"): when the server rejected a credential, the panel still said "Signed in for this session" and kept the previous session's list on screen. The server had refused the requests, so no data crossed owners, but the page misreported it. Now a new sign-in clears the list first. A rejected credential is dropped and reported as "not kept; nothing was loaded". A test covers this.

## Security limitations of the temporary browser-memory credential

The staging form exists only because certified per-user identity (SC-UP-01) does not exist yet. These are its limitations:

1. **It is a service credential, not a user credential.** The server accepts it together with any `X-SC-Owner-Scope`, so whoever holds it can act as **any owner**. That covers Research convergence and editorial writes as well as intake. Owner isolation protects owners from each other only for callers who do *not* hold the credential.
   - **Rule:** only the staging operator holds it. It is never given to an editor or tester.
   - Editor scenarios are run by the operator with Workspace-signed attestations.
2. **No per-person accountability.** Audit events record the asserted owner and actor scopes. For the owner path, the person typing the credential is not otherwise identified.
3. **Exposure in the page.** While the credential is in the page's memory, any script running in the page could read it: an XSS bug, a malicious browser extension, or someone with devtools on an unlocked machine.
   - Mitigations: the CSP allows only same-origin scripts with no inline script, all values are rendered as text, the credential is never written to storage or the DOM, the token field is cleared once accepted, and "Forget credential" or a reload drops it.
   - **Residual risk:** browser extensions and a compromised workstation cannot be mitigated by the page.
4. **No expiry or revocation per session.** The credential is valid until the server's credential file is replaced.
   - **Rule:** each isolated staging project generates its own credential at deployment and destroys it at teardown. It is never reused across projects or environments.
5. **Typing and clipboard exposure.** Password managers, clipboard history and screen sharing can capture it. Paste it only on a private screen, and clear the clipboard afterwards.
6. **CSRF is not a concern for this design**, because no cookie or ambient credential is used. The SC-UP-01 transition must keep that property, or add CSRF protection if it introduces cookies.

**Conclusion:** acceptable for an isolated staging acceptance run by the operator on synthetic or owner-approved data. **Not acceptable for production or for distribution to users.** Production intake stays refused in code (`PRODUCTION_INTAKE_AUTHORIZED = False`).

## Transition to certified Workspace sign-in (SC-UP-01)

**Target:** the browser never holds the Research service credential.
1. **Workspace signs in the person** (certified identity, with MFA per Workspace policy). Workspace issues a short-lived, audience-bound (`research`), per-person signed assertion: subject, owner scope, role, project grants, expiry of minutes, and a nonce or `jti`. This extends the existing editor-attestation format and its verification (`editorial.resolve_actor`) to owners as well.
2. **Research verifies the assertion** with the Workspace public key, as it already does for editor attestations:
   - The owner scope comes **from the signed assertion**, never from a client header.
   - `X-SC-Owner-Scope`, with the service credential, stays only for server-to-server calls.
3. **Session handling**, as either:
   - a same-origin, `HttpOnly`, `Secure`, `SameSite=Strict` session cookie set by a Research sign-in exchange, with a CSRF token on writes; or
   - the assertion held in memory and refreshed from Workspace.

   Neither exposes the service credential to scripts.
4. **Audit** records the assertion's subject and `jti`, giving per-person accountability.
5. **Revocation** through Workspace (short expiry plus a deny list) without replacing a shared credential.
6. **Removal:** the credential form and `createClient().setCredentials` are deleted, and the client sends the Workspace assertion instead. The panel's visibility, Save action and proposal rules stay unchanged.

**Acceptance criteria before production intake can be considered** (a separate, reviewed change to `PRODUCTION_INTAKE_AUTHORIZED`):
- no service credential in any browser path;
- owner scope only from signed assertions;
- negative tests for an expired, wrong-audience, wrong-owner or replayed assertion;
- CSRF tests if cookies are used;
- per-person audit;
- a staged rollout with owner sign-off.

## Not authorised by this stage

- **Enabling intake** in any environment, including staging.
- **Library submission:** proposals stay recorded and are never sent.
- **Production deployment.**
- **OpenAlex activation.**
- **Evidence promotion or publication.**
- **Replacing the staging credential form** with certified Workspace sign-in, which is SC-UP-01's job.
