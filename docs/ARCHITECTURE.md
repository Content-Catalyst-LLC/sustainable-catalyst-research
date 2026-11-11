# RH-02 — Architecture Decision (ADR-001): Independent Research Knowledge Base

> **2026-11-07 terminology clarification (approved):** “Publications” means the WordPress **editorial blog/article site**. Scientific papers, manuscripts, technical reports and their research lineage remain owned by Research and authorized Reporting/Publication Studio source services. “Press” is the future customer-facing name for the existing Engagement & Communications application. See [cross-product decision](https://github.com/Content-Catalyst-LLC/sustainable-catalyst-platform-core/blob/main/docs/PRODUCT-BOUNDARIES-PUBLICATIONS-PRESS-RESEARCH-2026-11-07.md) and [Research handoff](handoffs/2026-11-07-RESEARCH-TO-PUBLICATIONS-PRESS.md). This clarifies naming only; no production migration.

**Date:** 2026-11-05 · **Decision:** Proposed design for implementation; not production deployed.

## Why separate Research?
A static WordPress page cannot reliably own a continuously expanding corpus of article research workspaces, interactive scientific charts, evidence lineage, searchable graph links and versioned manuscript output. Rather than expanding the already feature-rich Library or Workspace, create a first-party Research service with strict API boundaries to the others. This gives Research parity with other major products without copying their data or rewriting their architectures.

## Runtime composition
- Public site: Existing `/research-hub/` remains the canonical entry point until approved cutover. A lightweight WordPress link/proxy is non-authoritative and consumes an allowlisted public feed.
- `research-web`: first-party accessible graphical research UI using Library design conventions adapted to active research; visual graph + list fallback, article dashboard, charts/data, timeline.
- `research-api`: separate versioned backend; anonymous public GET only allowlisted approved content; protected editorial endpoints use existing session identity verified server-side (same-origin reverse proxy recommended).
- `research-worker`: data/graph transformations and trusted SVG/PNG/CSV rendering with provenance. No arbitrary uploaded code execution; no external raw URL fetches.
- `research-db`: separate PostgreSQL database/schema and migrations; immutable published revision pointers, article metadata, external object link records.
- Object storage: immutable copies only where approved and rights-cleared; by default link to existing storage with stable authority references.

## Authority matrix
| Data or action | Canonical authority | Research responsibility |
|---|---|---|
| Public research article pages | Research API | Create and serve approved versions |
| Editorial identity | Existing Workspace identity service | Verify session and enforce owner scope; never migrate passwords |
| Manuscript source / Reporting | Workspace/Reporting Studio where original resides | Reference version and expose authorized editor deep link |
| Literature, bibliographies and evidence | Library/Librarian | Reference original IDs, status, permission, source and citation |
| Numerical computation and graph outputs | Workbench/Lab | Embed verified outputs and note their run/source identity |
| Geospatial/earth observations | Site Intelligence | Show reviewed maps/scenes with data license and caveats |
| Scientific papers, reports and manuscript release history | Research / authorized original Reporting or Publication Studio source | Preserve source authority, reviewed versions and release references; do not imply WordPress owns scientific evidence | 
| Editorial blog posts, explainers and research summaries | WordPress Publications | Link approved WordPress post/permalink and source Research release; do not duplicate the CMS | 
| Press releases, newsroom and media communications | Existing Engagement & Communications (future Press interface) | Provide only approved public release metadata and source references after separate review |
| Research graph and web composition | Research API | Link permitted objects and render safe view models |

## Research article UI
Header (title, summary, scope, status, update date), visual summary/graph, tabs for Overview / Figures & Data / Evidence / Methods / Versions / Related Work, publication CTA when applicable, and protected Open in Workspace action. Show data freshness, units, model assumptions and source citations next to the plotted figure.

## Explicit non-goals
- No Docker container per article; article workspaces are database-scoped records.
- No university/coursework-facing marketing or course inventory.
- No automatic public release of drafts, underlying sources or restricted datasets.
- No direct SQL joins across separate product databases; no new migrations on Library/Workspace/Workbench.
- No live production cutover based only on source existence or unit tests.

## Interface contract plan
`GET /api/research/v1/areas`
`GET /api/research/v1/areas/{slug}`
`GET /api/research/v1/articles?area=&q=&status=`
`GET /api/research/v1/articles/{slug}`
`GET /api/research/v1/articles/{slug}/figures`
`GET /api/research/v1/articles/{slug}/relationships`
`GET /api/research/v1/articles/{slug}/versions` (public-approved only)
`GET /api/research/v1/articles/{slug}/data/{id}` (approved downloads)
`GET /api/research/v1/health`
Protected namespace: `/api/research/v1/editor/…` (owner auth, CSRF, audit and revisions; no public caching).

Exact routes and transport are candidates and must be verified against reserved WordPress/Workspace ingress paths before promotion.
