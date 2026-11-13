# RH-08D — Integrated Research Discovery: implementation register

**Date:** 2026-11-05 · **Status:** Implementation sequencing agreed, review-stage.  
**Product:** Sustainable Catalyst Research · **Primary source:** [RH-02 build map](../BUILD_MAP.md)  
**Canonical directive:** the RH-08D specification, kept in the access-controlled Documentation repository

## Scope

Research is a dynamic public knowledge base, composed of research areas, projects and individually persistent article workspaces. Discovery integrates in the Research main page and in every article. It does not replace Library, require Perplexity, or expose owner-private manuscripts. Research owns query/run/candidate state and article links; Library retains authority over source/citation identity, provenance, rights and reviewed evidence.

A source found by search is a **discovery candidate**, not automatically verified evidence, peer-reviewed work or a citable source passage. Public results may include only provider-supplied metadata and approved Research article context. Do not send private Research/Workspace material to third-party APIs without explicit authorization.

## Delivery map

| Build | User-facing result | Implementation gate | Status |
|---|---|---|---|
| RH-08D-00 | Reconciled specifications across Documentation, Data, Handoffs and Research | Cross-repo review and owner approval for merge | Draft plans reconciled, unmerged |
| RH-08D-01 | Discover main section + “Discover related evidence” in each article; accessible search, filters, loading/error/empty states | Existing research-web shell; public-only context; versioned API schema | **Next executable build** |
| RH-08D-02 | OpenAlex/Crossref scholarly metadata results with source identifiers, URLs, retrieval timestamps and provider labels | Provider policy, safe outbound HTTP, rate caps, timeouts; no private context | **Merged (PR #5, 2026-11-12):** Discover results interface, source inspection, vetted HTTPS transport, Library intake drafts ([RH-08D-02](RH-08D-02-DISCOVER-INTERFACE.md)). **Live integration (RH-08D-03 label, 2026-11-12):** guarded live search, disabled by default, staging-only activation ([RH-08D-03](RH-08D-03-LIVE-DISCOVERY.md)) |
| RH-08D-03 | DOI normalization, stable-ID deduplication, metadata conflict and retraction states | Actual candidate sources and negative metadata tests | Implemented on synthetic fixtures (RH-08D-01) and integration-tested through the live path offline (RH-08D-03); acceptance on real sources awaits an authorised staging activation |
| RH-08D-04 | Reopenable owner-scoped discovery sessions; per-article candidate review queue; Library source-review handoff | RH-02 PostgreSQL migration, certified authentication/CSRF/owner isolation, RH-07 Library adapter | **Implementation draft (RH-08D-04, 2026-11-13), disabled by default** ([RH-08D-04](RH-08D-04-GOVERNED-INTAKE.md)): owner-scoped saved candidates, article associations and recorded Library proposals behind the staging write identity; production stays refused until certified identity, and Library submission until the RH-07 adapter. Saved *searches* are not part of it. |
| RH-08D-05 | Discover interface for governed intake: save, saved-source management, article associations, Library review proposals | RH-08D-04 (with the PR #10 review fixes), SC-UP-01 certified sign-in to replace the staging credential form | **Merged (PR #11, 2026-11-13), not enabled** ([RH-08D-05](RH-08D-05-DISCOVER-INTAKE-INTERFACE.md)): appears only where intake is enabled; proposals recorded, never sent. |
| RH-08D-05 | Reviewed evidence attached to the article/knowledge graph; proposed new research revision | Library accepted evidence, explicit editorial review, RH-05/08 relationship model | Planned |
| RH-08D-06 | Optional source-passage-grounded synthesis | Rights-cleared retrieved passages, per-claim source anchors, model opt-in and budgets | Disabled |
| RH-08D-07 | Europe PMC, arXiv, permitted government datasets/web discovery | Provider-specific policy, rights, quotas, SSRF testing | Disabled |
| RH-08D-08 | Scheduled search monitoring, notifications and operations | Owner opt-in, rights, RH-12 staging backup/rollback/production acceptance | Disabled |

**2026-11-12 note:** the owner directive of this date assigned the label RH-08D-01 to the typed OpenAlex/Crossref adapter layer, i.e. the network-free part of RH-08D-02 and RH-08D-03 above; see [RH-08D-01 discovery adapters](RH-08D-01-DISCOVERY-ADAPTERS.md). The Discover interface follows it as RH-08D-02, and the controlled live integration as RH-08D-03 ([RH-08D-03](RH-08D-03-LIVE-DISCOVERY.md)). Since RH-08D-01, Discovery code and this register are developed in this public repository (the private export no longer writes them); the private-branch instructions below are historical.

## Workspace integration

- Domain relationship: `ResearchArea → ResearchProject → ResearchEntry → EntryWorkspace` (RH-02) and `EntryWorkspace → DiscoveryRun → DiscoveryCandidate` (future RH-08D-04).
- Every finished research project output remains an independently expandable research entry and stable logical workspace. No Docker service per paper.
- Article page Discover may prefill only public-approved research question/topics for an anonymous visitor; editor-specific private context requires a verified same-origin authenticated session.
- Persist discovery candidates **only** under an owner-authorized EntryWorkspace. Never bind by title string; never expose Workspace IDs through public JSON.
- Accept source metadata only through a typed, explicitly approved Library handoff. Do not copy or mutate Library's database.
- New evidence proposes a versioned update; previously released publications remain immutable.

## First acceptance demonstration

A visitor opens Research → Discover, searches “enhanced rock weathering agricultural soils,” sees genuine metadata from OpenAlex and Crossref with clear “discovered/unreviewed” labeling, filters by provider/year, inspects a DOI and provenance, and navigates to a related approved article. An owner can later save a candidate in the article workspace only after authorization, persistence and Library review gates pass. Repeat the journey with a sustainable development indicators topic; test anonymous privacy and source provider failures. Never fabricate results, authors, identifiers or datasets to fill the UI.

## Reconciliation and branch strategy

- Documentation, Data and Handoffs: reconciled in the access-controlled repositories (not public). Data records
  all six provider flags disabled and no retrieved candidates.
- Research: foundation PR #1 and per-entry workspace PR #2 in the private Research repository (not public)

Implement RH-08D-01 on a *new branch based on* `feature/rh-02-entry-workspaces` so that Discover is correctly aware of the per-entry workspace implementation. The older `feature/rh-08d-discovery-foundation` was cut from foundation and should not be assumed to include RH-02 work; do not blind-merge.

## Operational rules

GitHub source/CI development proceeds without routine SSH or Codex. Pi or container integration requires a controlled staging runner or owner-authorized deployment automation. No production WordPress edits, paid provider activation, article publishing, data imports or assertions of scientifically verified discoveries are authorized by this document.
