# RH-PUB-01 — Publication Output Workspaces and Workspace-Controlled Research Publishing

**Prepared:** 2026-11-07  
**Status:** Approved product direction / implementation specification; **not built, deployed, or production-certified**.  
**Owners:** Workspace (project coordination, user-facing control plane); Research (research content, publication and public catalog authority).  
**Dependencies:** Private Workspace-to-Research identity/owner bridge; immutable research revision and provenance controls; permissioned publication review.  
**Related architecture:** [Publications, Press, Research, Library and Workspace](https://github.com/Content-Catalyst-LLC/sustainable-catalyst-platform-core/blob/main/docs/PRODUCT-BOUNDARIES-PUBLICATIONS-PRESS-RESEARCH-2026-11-07.md).

## User outcome

An authenticated user opens a **parent Workspace project** and creates separate, linked **publication output workspaces** for every Research-facing deliverable. Each output can have independent manuscript, citations, figures, research graph, review/rights decisions, immutable revisions, public summary, public slug and release history.

From **Workspace**, that user can:
1. Create a new publication output, select its type (scientific report, policy brief, working paper, public research overview, figure/data package, research article), and create/link a corresponding Research entry workspace.
2. Open the output's Research editor with the correct owner, project, entry and source context. The user does not need a separate password, and another owner's project cannot be accessed.
3. Choose exactly which revision of that output is eligible for release; review a public preview and approve or request changes under the applicable review policy.
4. Control whether that specific output is listed on the **public Research site** without accidentally publishing private project data, source files, siblings or project-wide metadata.
5. Publish an approved revision; publish an updated revision; temporarily unlist/unpublish where supported; withdraw/correct an output, with auditable receipts and publication provenance.
6. Track publishing state, URLs, approvals, source integrity, cache status and failures in one Workspace project dashboard.

**Research publishing is not WordPress Publications posting.** WordPress Publications owns blog posts and editorial articles. Press owns media/outreach. Those may link an approved Research release, but neither is automatically published by a Research release request.

## Hierarchy: one project, many logical output workspaces

```text
Workspace Project  EXS — Enhanced Rock Weathering / Example Site
  ├── ResearchProject (linked by external_workspace_ref)
  │     ├── ResearchEntry: Example Site scientific report
  │     │     └── EntryWorkspace + immutable revisions + sources + release records
  │     ├── ResearchEntry: Example Site policy brief
  │     │     └── EntryWorkspace + independent revisions + sources + release records
  │     └── ResearchEntry: Example Site public research overview
  │           └── EntryWorkspace + independent revisions + sources + release records
  └── Workspace publication board: one row and one release control per output
```

Each output workspace is a **logical database-scoped record**, not a separate Docker container, independent account or copied Library collection. Multiple outputs can reference the same source object with scoped rights and pinned source versions.

## Reuse current Research entities

Existing `ResearchProject`, `ResearchEntry`, `EntryWorkspace`, `WorkspaceRevision`, and `WorkspaceObjectRef` already model the project/entry/workspace hierarchy, with private revisions, rights and approval predicates. Reuse them instead of rebuilding.

The current `ResearchProject.external_workspace_ref` is a candidate project-level link to the authoritative Workspace project, **not proof of an accepted, authorized mapping**. Add a durable owner/tenant-validated mapping contract before any private write or release operation.

Recommended additions, with versioned migration and compatibility review:
- **PublicationOutputBinding** (or extending ResearchEntry safely): `research_project_id`, `research_entry_id` (unique), `workspace_project_ref`, `workspace_output_ref`, `output_kind`, `owner_scope`, `created_at`. Enforce stable owner, project and entry linkage.
- **PublicationReleaseRequest**: unique idempotency key, entry and workspace refs, requested revision number and content hash, publication target `research-public`, actor reference from verified session, disclosure scope, requested_at, status and explicit rejection reasons.
- **PublicationApproval**: policy ID, distinct reviewing actor(s) if mandated, decision and immutable audit record, rights/attribution/source-evidence checks, selected exact revision hash and review timestamp. Workspace governance may coordinate review; Research must verify the release gate independently.
- **PublishedRelease**: immutable release ID, exact workspace revision and hash, public slug/metadata snapshot, rights attestation reference, approved_at, released_at, withdrawn_at, superseded_by, release receipt and current catalog pointer.
- **ReleaseAuditEvent**: actor and role, action, old/new state, request/release IDs, timestamp, outcome, reason and content digest. No passwords, sensitive source contents, or unreviewed manuscripts in events or public JSON.

Existing `ResearchEntry.visibility`, `ResearchEntry.rights_cleared`, and workspace revision flags must be updated **transactionally** by the Research publication service only after verifying the selected release and approvals. Do not expose an endpoint for clients to toggle these flags directly.

## Proposed state machine

```text
PRIVATE_DRAFT
   ↓ submit exactly one immutable revision
IN_REVIEW
   ↓ approved + rights/source/permission gate
APPROVED_UNPUBLISHED
   ↓ owner/publisher action, Research gate, immutable receipt
PUBLISHED_REVISION_N
   ├── edit → new PRIVATE_DRAFT revision (the public N is unchanged)
   ├── approved update → PUBLISHED_REVISION_N+1 (N retained as historical record)
   ├── unlist → UNLISTED / PRIVATE_FROM_CATALOG (as permitted; audit retained)
   └── withdrawal → WITHDRAWN (public current record removed or safe notice shown)
```

Rejected review returns the draft to revision with reasons. Draft edits never silently replace the published revision. A rightsholder revocation, revoked identity, unapproved source, embargo or invalid authorization **must deny promotion and trigger appropriate suppression or review of affected public material**. Public output availability and caching must reflect withdrawal or rights revocation without serving stale private content.

## Workspace UX

The parent project gains an **Outputs & Publishing** board:

| Output | Type | Research workspace | Last private revision | Public release | Review | Actions |
| --- | --- | --- | --- | --- | --- | --- |
| Example Site scientific report | Scientific report | Open Research | Draft r3 | None | Private | Edit, request review |
| Example Site policy brief | Policy brief | Open Research | Draft r4 | r2 | Update pending | Preview r2, review r4, publish approved update |
| Example Site public overview | Research overview | Open Research | r1 | r1 | Approved | View public, unlist/withdraw |

**These rows are UI examples, not asserted live records.** Each row owns an independent review and publication state. Controls should show whether the public listing is active, the exact public revision, approved rights, publish target and last release receipt.

Per-output tabs: **Overview, Manuscript, Evidence & Library, Figures & Data, Methods, Revisions, Review & Rights, Publishing, Audit/Links.** WordPress Publications links are separate optional downstream references.

## Proposed service boundary (do not assume current routes exist)

- Workspace **authenticated UI/BFF**: create output, list outputs for the currently authorized project, request review, record human decisions and request release/withdrawal.
- Research **private authenticated publication service**: resolve owner/tenant/project/entry mapping, verify review artifacts and exact revision hash, rights/embargo/approval policy; atomically publish/unlist/withdraw into the Research-owned catalog and issue a receipt.
- Research **public read-only API**: continue to serve only approved and currently public entries, revision snapshots, and rights-cleared metadata; WordPress Research Bridge remains a GET-only consumer.
- **No direct public WordPress POST**, no exposed Research private write route via `research-api.sustainablecatalyst.com`, no self-reported request owner, no user-controlled authorization headers, no blanket project-wide visibility switch.
- Prefer established Workspace internal handoff and human-governance contracts where proven; do not claim their existing synthetic/portable records certify production side effects.

Proposed private route namespace for design review only, behind Workspace-authenticated same-origin backend:
- `POST /api/research/v1/editor/outputs`
- `GET /api/research/v1/editor/projects/{project_ref}/outputs`
- `POST /api/research/v1/editor/outputs/{entry_id}/review-requests`
- `POST /api/research/v1/editor/outputs/{entry_id}/release-requests`
- `POST /api/research/v1/editor/release-requests/{request_id}/publish`
- `POST /api/research/v1/editor/releases/{release_id}/unlist`
- `POST /api/research/v1/editor/releases/{release_id}/withdraw`

These paths are **candidates**, not deployed APIs. Implement exact routes only after ingress/identity contract review.

## Creation, publication and rollback guarantees

- **Create**: authorize parent Workspace project, idempotently register Research entry/workspace, return stable Research entry ID and Workspace output link. If either service fails, retry without duplicate ownership or orphan public records.
- **Review**: pin revision number + hash + source versions; do not treat a review of an earlier revision as approval for later changes.
- **Publish**: require verified project membership, publisher role, effective review policy, distinct approver when policy requires it, rights and license checks, Research DB readiness, and durable release receipt. Default is **private and not listed**.
- **Visibility**: project/area being public does not override a private output; publishing an output does not publish its siblings. Require only safe metadata and explicitly approved attachments in public JSON.
- **Update**: keep previous immutable public release history and never switch an approved public pointer to an unreviewed private revision.
- **Withdraw/rights revoke**: fail closed, atomically remove public catalog eligibility and invalidate cached public responses; preserve governed audit/tombstone without leaking private content.
- **Cross-account**: wrong owner, cross-tenant, expired/revoked Workspace session, CSRF failure, missing source rights, concurrent publish, mismatched content hash and embargo must be denied.
- **Operations**: include backup/restore, PostgreSQL migration rollback/forward strategy, published pointer recovery, idempotency, structured logs, and explicit production change approval.

## Phased implementation build map

1. **RH-PUB-01A — Identity and project binding (BLOCKED until RH-PROD-05.1 acceptance):** Workspace session introspection + revocation, real-account owner acceptance and signed/authorized Research mapping; internal-only routing, deny tests.
2. **RH-PUB-01B — Output workspace registry:** extend the existing Research project/entry/workspace model, add output type, independent revision/release metadata, idempotent create/link and filtered Workspace project index.
3. **RH-PUB-01C — Review, approval and release service:** immutable release snapshots, rights gates, audit, publication state machine, retract/withdraw and cache invalidation.
4. **RH-PUB-01D — Workspace publishing board:** author/project creation, per-output preview, review, publish, update, unlist and withdraw buttons with user-visible receipts and errors. No fake active buttons.
5. **RH-PUB-01E — Public Research integration:** approved-output-only catalogs and stable public views; separate public WP Publications linkage; metadata graph respects per-output rights and revocation.
6. **RH-PUB-01F — End-to-end certification and guarded production-host deploy:** synthetic Example Site/Metrics fixtures, two-owner isolation, publishing one of three siblings leaves the rest private, accurate public listing/counts, restart persistence, revocation, backup/restore and real-account browser acceptance.

**Release sequencing:** Do not deploy 01B–01E to production merely because implementation exists. 01A is the blocker for authenticated writes; build registry/migrations and private-scope tests in isolated staging first.

## Definition of done

- An authorized user creates one parent project with three independent output workspaces, links Library evidence, edits and versions each.
- An approved release of **only** the Example Site public overview appears on public Research and in WordPress's read-only Research bridge; the scientific report and policy brief remain private.
- Changing private drafts does not change public content; a later approved update changes only its designated public revision.
- Withdrawing the public overview removes or safely tombstones it and clears caches; historical audit persists.
- Another account cannot list, edit, approve or publish any of the three outputs.
- The Workspace publishing board shows Research-confirmed state and receipts, not its own optimistic local flag.
- All CI, schema migration, security and live-owner acceptance gates pass; no Press/email or WordPress Publications post is generated as a side effect.

## Non-goals

No separate container or database per output, no duplicate Research/Workspace accounts, no automatic publication of existing preview rows, no open public Research edits, no bypass of scientific or rights review, and no forced migration from WordPress Publications or Press.
