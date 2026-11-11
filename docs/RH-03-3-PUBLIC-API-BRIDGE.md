# RH-03.3 — WordPress Research Workbench to public Python API bridge

**Date:** 2026-11-05 · **Review status:** SOURCE IMPLEMENTED, NOT DEPLOYED  
**Base:** RH-05.2 Python Research backend / RH-03.2 owner-preferred WordPress Workbench design.

## Outcome

The existing site-matched, cream/red Library/Lab-style workbench stays intact. The new standalone `research-web/assets/research-workbench-bridge.js` adds a **separate approved-metadata panel**, so that the original six static editorial preview records never disappear if the API fails, JavaScript is stripped, or Contabo has not yet passed acceptance. `research-workbench-bridge.css` contains only `#sc-research-v2` styles and does not modify Astra or other site pages.

The module is **disabled by default**. It makes **zero backend requests** until the existing Research HTML root has `data-sc-public-api="enabled"`. Only an approved release may set that flag. The frontend uses same-origin relative URLs with `credentials: "omit"`, a 6-second request timeout and redirect refusal, and renders externally sourced metadata using `textContent`. No private login, cookie, source-download, POST, persisted search or Library mutation path is present.

## Read-only API calls on activation

| Endpoint | Required public contract |
|---|---|
| `GET /api/research/v1/articles` | `items[]` of approved article slug/title/summary/area/state |
| `GET /api/research/v1/objects?limit=50` | `sc-research-public-objects/1.0`, public UUID/title/type/approved rights |
| `GET /api/research/v1/graph/metadata?limit=50` | `sc-research-public-object-graph/1.0`, reviewed node and relation snapshot |

Both the Research API and the WordPress page must be on a **reviewed same-origin HTTPS route** before activation. Do not construct absolute requests to an unauthenticated Contabo IP address, enable cross-origin cookies, inject credentials, or expose private Library/Workspace API endpoints in the browser. The graph displays *approved structural metadata relations*, not scientific proof.

## WordPress installation contract

Preserve the existing page header, footer, consolidated site CSS and **RH-03.2 corrected page**. The WordPress workbench content block has `id="sc-research-v2"`. The versioned bridge is a separate public site asset, not pasted into Additional CSS.

**Do not activate yet.** When RH-02/RH-05/RH-07/RH-12 gates pass and an owner-authorized site release exists:

1. Enqueue `research-workbench-bridge.css` and `research-workbench-bridge.js` via WordPress's approved versioned asset mechanism, keeping the original workbench's own script and CSS unchanged.
2. Confirm the current same-origin ingress forwards `/api/research/v1/*` only to the authorized Research API. Confirm accepted public records and zero private fields.
3. In the *approved version* of the Research workbench HTML root, set `data-sc-public-api="enabled"`.
4. Verify that the additional read-only panel shows source records returned by the API, with accurate limits and provenance, while the existing static preview remains unchanged.
5. Test the API's empty state, outage/timeout/redirect, invalid payloads, CSP restrictions, WordPress optimizer, desktop/mobile CSS, owner session isolation, source rights revocations, and backup/rollback.

**Never claim the editorial preview's 4 areas, 6 articles, 2 public PDFs are synchronized Research API counts.** Real API records appear in a separately labeled panel; no automatic merging of editorial and reviewed records is performed.

## Test and runtime gates

- `node --check research-web/assets/research-workbench-bridge.js`
- `node --test tests/test_workbench_bridge.cjs`
- Full existing Python suite and Python compilation continue on GitHub Actions.
- Automated tests cover bounded public record projection, preview protection, no background requests without explicit activation, and no private session/credential usage.
- **No Contabo deployment, owner-account staging, PostgreSQL runtime, public-page installation, GitHub PR merge, or real scientific source import is authorized by RH-03.3 source commits.**

## Follow-up builds

**RH-03.3A:** reviewed same-origin proxy and container smoke-testing, plus live WordPress visual acceptance (RH-03.2).  
**RH-02 / RH-05:** authenticated persistent workspaces and PostgreSQL migrations.  
**RH-07:** typed read-only service adapters and data-provenance contracts.  
**RH-03.3B:** replace preview content *only after* the authorizing sources and final page layout are accepted; preserve owner-edited HTML for rollback.  
**RH-12:** Contabo release gate (secrets, backup/restore, ingress, owner identity, rollback).
