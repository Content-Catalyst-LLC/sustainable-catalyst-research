# RH-05.1 — Research Object Metadata and Knowledge Relationships

**Status:** Source prototype and deployment architecture; not a production graph or verified evidence corpus.  
**Owner:** Sustainable Catalyst Research. **Date:** 2026-11-05.  
**Dependency:** RH-02 entry workspaces; RH-07 typed source adapters; RH-12 production host certification.

## Product objective

Research must be a first-class knowledge-analysis capability. Each independently published research output remains a permanent logical `EntryWorkspace`. Through Research, a visitor can inspect approved article and source relationships; an authenticated owner can review candidate links, compare evidence, attach verified datasets and figures, understand provenance, and publish new article revisions without replacing previous releases.

This service **does not** reimplement the Library's evidence/knowledge graph or make scientific truth claims. Library 6.27 documents `/api/library/v1/research-knowledge-graph`; Library 6.31 documents `/api/library/v1/research-lineage-graph`. Their current production-host availability and scope must be certified before Research invokes them.

## Metadata objects and authority

Input object kinds include publication, research article/version, source, evidence, claim, dataset, statistical result, graph, model, notebook, scene, place, policy instrument, and research question.

The canonical object identity is **(source authority, originating object ID, immutable version)**. Never identify by title or merge records with similar display names. Source metadata includes title, type, DOI (if valid), keywords/concepts, contributor and date fields where authorized, license/rights and status, version, origin, checksum or trusted signature, retrieval time, and provenance. Only source-approved fields may enter a public projection.

- **Research API / dedicated database:** owns the entry-specific metadata index, editorial relationship proposals, article-to-object links, review state and public projection.
- **Library and Librarian:** authoritative for source-record identity, citations, source rights and accepted evidence provenance.
- **Workspace / Reporting Studio:** manuscript, owner-specific article content and revision state.
- **Workbench / Lab:** original numerical experiments, calculations, datasets and graph exports.
- **Site Intelligence:** imagery, geographical data and its original source/license metadata.
- **Publications:** released canonical output and citation URLs.

Research retains versioned *references*, not copies of entire foreign databases, restricted PDFs, or unapproved private manuscripts.

## Relationship model

| Predicate family | Example | Can metadata alone prove it? |
|---|---|---|
| Structural | Article belongs to area/project; contains an approved figure | Only if explicit, sourced and permitted |
| Identity | Same DOI / publication record | Candidate; version/retraction/manual review |
| Provenance | Figure derived from dataset/run | Only explicit source dependency + verified lineage |
| Topical | Shared declared concepts or keywords | Candidate; no evidence assertion |
| Scholarly citation | Article cites a publication | Source-backed citation record required |
| Evidentiary | Supports, challenges, contradicts | **No**; requires reviewed passages or datasets and explicit editorial determination |
| Causal / policy implication | A measure caused removal / a policy produced a change | **No**; requires scientific analysis beyond metadata |

All metadata-derived edges start `proposed`. A reviewer may approve an identity/structural link after verification. Evidence-strength and causality do not emerge automatically from graph distance, semantic similarity or DOI matching.

## RH-05.1 source prototype

`research-graphs/metadata_engine.py` is an offline, deterministic metadata-only proposal engine. It uses source-authority IDs and versions, canonical DOI comparisons, declared dependencies and shared concepts; outputs reviewed-status **candidate links**, never asserted evidence. `approved_public_projection` excludes nodes without both public and rights approval, excludes unreviewed relationships, and blocks evidence assertions.

`research-web` now renders **already-approved** Research area-to-article structural relationships from the existing public `GET /api/research/v1/relationships` API as SVG with an accessible list alternative. This does not yet display persisted metadata object relationships.

**Not implemented:** metadata ingestion, owner authentication/editor UI, a job queue, graph version persistence, metadata enrichment, authorized upstream API calls, scientific support/contradiction analysis, or real production-host graph-service deployment. Do not advertise these as live.

## Production host architecture target

```text
Existing website /research-hub/ (public entry)
          │
  TLS gateway / route proxy (Caddy/nginx, gated change)
          │
  research-web  (public, no credentials)
          │
  research-api  (public-approved GET + owner-authorized editor routes)
        ├── research-db (separate PostgreSQL DB, private network)
        ├── research-graphs worker (private job queue + bounded CPU/memory)
        └── signed, typed service adapters (private network)
              ├── Library graph + lineage + source/citation services
              ├── Workspace / Reporting Studio
              ├── Workbench + Lab
              └── Site Intelligence / Publications
```

No browser-direct requests to private source APIs. The gateway provides stable public Research URL routing and separate authenticated editor session bridging to existing identity. The graph worker receives only authorized, bounded metadata jobs; does not fetch arbitrary URLs or run uploaded code. A separate container can process multiple article workspaces; never one container per paper.

**Release controls:** pinned image digests, non-root containers, internal-only DB/worker, external API allowlists, credential secrets outside Git, per-owner authorization, CSRF for editor routes, timeouts/rate limits, structured logs without private content, backups and reversible migrations, restricted graph exports, staging smoke/browser tests and rollback. Do not change active Caddy routes, WordPress, PostgreSQL, or sibling-product services until an explicitly authorized production-host acceptance/cutover.

## Milestones

1. **RH-05.1 — metadata analyzer and public structural view:** deterministic DOI/concept/dependency candidate extraction; tests; real public area/article graph UI. **Source implementation in draft PR, runtime not certified.**
2. **RH-05.2 — persistent research object index:** owner-scoped Research DB tables for source references, immutable versions, extract metadata, rights/freshness, provenance and review; production migration plan.
3. **RH-05.3 — typed production-host adapters:** verify live Library graph/lineage paths, existing Workspace/Workbench/Lab/Site Intelligence read-only contracts; source ownership, signatures and version checks.
4. **RH-05.4 — graph worker service:** bounded queue, jobs, retries and deterministic graph exports; rights-aware invalidation and source update handling.
5. **RH-05.5 — analytical Research UI:** metadata inspection, neighbors/paths, compare objects, dependency/impact analysis, conflicts or missing evidence clearly marked, accessible graph/list and provenance drill-down.
6. **RH-05.6 — approved publication and integration:** signed owner checks, editorial-reviewed public relation export, immutable research updates, production-host staging gates, rollback.

## Acceptance requirements

- In an example project, link a released scientific output to verified figures/data and a related policy interpretation **without leaking unpublished source IDs or manuscripts**. Test a different research area to prove generality.
- A DOI match, shared topics, or declared dependency yields a proposed link. A published evidentiary claim requires an additional evidence-reviewed approval step and is never emitted by a metadata-only analyzer.
- Source version or license revocation invalidates the affected public relation/cache; allow public snapshot fallback only when already approved and visibly dated.
- Anonymous API hides unreviewed nodes/relations, other-owner workspaces, source paths, secrets and embargoed records.
- Query and graph batch limits, deterministic output, tests for duplicate IDs, malformed DOI, cycles, adverse inputs, provider downtime and rendering fallbacks.
- Production-host tests require authenticated owner staging, pinned images, backup/restore and ingress rollback. CI passing is necessary but insufficient.

## Next dependency order

RH-02 identity and PostgreSQL-migration acceptance → RH-05.2 persistent metadata registry + RH-07 verified adapters → RH-05.3/05.4 production-host connectivity and worker → RH-05.5 analytical interface → RH-05.6 controlled release. RH-08D discovery candidates may propose new object metadata for review, but cannot become Library-accepted evidence automatically.

No additional paid AI subscription or new codebase is required. Continue independently through GitHub PRs and CI; host administration should be automated, not delegated as repetitive SSH commands.
