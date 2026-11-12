# RH-05.2 — Python Research Backend and Metadata APIs

**Record date:** 2026-11-05  
**Repository:** canonical public source `Content-Catalyst-LLC/sustainable-catalyst-research`; developed in the private Research repository  
**Review:** Research PR #5 in the private Research repository (not public)  
**Status:** Source and CI acceptance only. Not deployed on the production host or connected to real upstream services.

## Purpose

Research must operate as a first-class, extensible analytical service behind the website, not as static WordPress markup. Every `ResearchEntry` has an enduring logical `EntryWorkspace` where versioned manuscripts, public summaries, datasets, figures, bibliographies, research metadata and source relationships can grow. A finished publication is a stable release within a continuing research process.

The Research backend is written in **Python 3.12, FastAPI and SQLAlchemy 2**, with **PostgreSQL** as the planned persistent production store. The public site consumes approved read-only JSON from Research; Library, Workspace, Lab, Workbench, Site Intelligence and Publications remain authoritative for their original objects.

## Implemented on the PR #5 branch

### Persistent domain additions

- `ResearchObject`: internal UUID and **separate public UUID**; parent `EntryWorkspace`; originating product authority + source ID + source version; type, title, public summary, DOI, concepts, SHA-256, retrieval time, license/rights metadata and data classification. Uniqueness is scoped to each workspace and source version; idempotent replays do not silently replace source metadata.
- `ResearchObjectRelation`: two research object references, explicit predicate, private rationale, public note, proposed/reviewed state, visibility and evidence-assertion flag. New relations are **private and proposed** by default.
- Internal Python write helpers require caller-verified owner scope. **No anonymous or browser-facing edit/create endpoints exist.** The owner authentication and session bridge remain an explicit RH-02 acceptance gate.
- Public queries require approved area, article, object, rights and review state. Public output excludes private workspace/project IDs, original source record IDs, internal object IDs, private relationship rationale, source hashes and backend paths.

### Read-only API (development)

| Path | Purpose |
|---|---|
| `GET /api/research/v1/objects` | Paginated, bounded search of public-approved research objects; article/kind/title/summary filters |
| `GET /api/research/v1/objects/{public_key}` | Single approved object metadata record |
| `GET /api/research/v1/objects/stats/summary` | Counts by type **only among approved objects**; sample limit disclosed |
| `GET /api/research/v1/graph/metadata` | Approved reviewed object-and-relation graph; bounded |
| `GET /api/research/v1/graph/metadata/analysis` | Node/edge, type, isolated-object and connected-component structural analysis |
| `GET /api/research/v1/graph/metadata/neighborhood/{public_key}` | Bounded approved-object relationship neighborhood |
| `GET /api/research/v1/graph/metadata/path?source=…&target=…` | Bounded shortest structural path over already-approved links |

The graph analysis **does not establish causality, scientific evidence strength, accuracy, or source legitimacy**. The pre-existing `research-graphs/metadata_engine.py` produces private proposed DOI/concept/dependency relationships; proposals are not automatically published by the public graph API.

## Frontend direction — preserve edited HTML and CSS

The public Research page should retain the existing Sustainable Catalyst site header and global branding, but **the work area must feel like Library and Lab**, not an advertising landing page:

- Compact application toolbar with Research Areas / Discover / Objects / Knowledge Graph / Workspace / Updates views.
- Search and metadata filters; object detail and provenance inspectors; research-entry and version navigation.
- Accessible list/table alongside the graph; figure/data preview and linked source metadata.
- Persistent workspaces rather than disposable article pages; explicit last-reviewed/version/rights status.
- No dynamic private data in WordPress HTML. The browser calls only a reviewed, public-approved API via same-origin HTTPS reverse proxy.
- **Owner's modified HTML/CSS are the design source of truth** once received. No automated overwrite, reformat or visual rebrand before reconciliation.

## Production host deployment path

1. **RH-02 completion:** proper PostgreSQL migration tool (not production `create_all`), existing Workspace identity bridge, per-owner authorization and audit.
2. **RH-05.2 integration:** real PostgreSQL migration and restart persistence, authorization-negative cases, large metadata / indexed query performance, invalidation/retraction and rights-revocation tests.
3. **RH-07 source adapters:** verify current *live* authorized Library knowledge-graph/lineage endpoints and individual source services; use typed read-only requests with source authority/version/freshness and explicit permissions.
4. **RH-05.4 worker:** isolated, bounded metadata/graph jobs and deterministic graph exports; no arbitrary external URLs or uploaded code execution.
5. **RH-12 production host gate:** pinned images, secrets outside Git, internal-only database and worker, Caddy route review, rate limits, HTTPS, backup/restore, reversible migrations, staging browser acceptance, rollback, explicit release authorization.
6. **Page connection:** update owner-approved WordPress HTML to request the read-only Research API over reviewed same-origin routes, rather than embedding data/private credentials in WordPress.

## Verification

As of this checkpoint, GitHub Actions reports **42 passed, 1 warning** for the stacked Research source branch. The tests run with disposable SQLite databases. This is **not** a production PostgreSQL, production-host, owner-account or full-browser certification. No real research data has been migrated by this PR.

## Next engineering tasks

Implement Alembic schema migrations, a distinct authenticated Research editor BFF (server-side signed session and CSRF), typed source-service adapters, staged object upserts with provenance, data graph worker, and Library/Lab-style frontend wired only to public approved read APIs. Maintain Research metadata analysis as the first-class platform capability and use existing Library for original bibliographic/evidence authority.
