# RH-08D-05 — Discover Interface and Governed Intake Integration

**Status:** implementation draft. **Not enabled anywhere.** It depends on RH-08D-04, including the PR #10 review fixes and migration `0014`.

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

`tests/test_rh08d05_discover_intake.cjs` (Node, offline, fake DOM and fake fetch, 12 tests) covers:
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
- the saved-status contract with `discovery_intake.SAVED`.

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

## Not authorised by this stage

- **Enabling intake** in any environment, including staging.
- **Library submission:** proposals stay recorded and are never sent.
- **Production deployment.**
- **OpenAlex activation.**
- **Evidence promotion or publication.**
- **Replacing the staging credential form** with certified Workspace sign-in, which is SC-UP-01's job.
