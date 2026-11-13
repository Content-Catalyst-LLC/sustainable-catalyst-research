# Sustainable Catalyst Research — Public Development Roadmap

This file intentionally contains only **public-safe product capabilities** and general engineering milestones. Internal project mappings, deployment evidence, infrastructure topology, owner-specific acceptance, and release decisions belong in the existing private Documentation, Data, and Handoffs repositories.

## Implemented in source

| Capability | Code location | Status |
|---|---|---|
| Public Research API | `research-api/app/main.py` | Implemented and tested |
| Persistent article workspaces | `research-api/app/workspaces.py` | SQLAlchemy models and internal APIs |
| Versioned research-object metadata | `research-api/app/object_registry.py` | Implemented, private by default |
| Metadata analysis and provenance | `research-api/app/metadata_diagnostics.py` | Source implemented |
| Graph analysis | `research-api/app/graph_analysis.py` and `research-graphs/` | Source implemented |
| PostgreSQL schema migrations | `research-api/alembic/` | Twelve versioned migrations, tested up, down and up on PostgreSQL 16 |
| Research convergence and provenance | `research-api/app/convergence.py`, `research-api/app/workspace_ingest.py` | Signed-approval provenance; idempotent Workspace execution ingest |
| Editorial publishing | `research-api/app/editorial.py`, `research-api/app/editorial_ui/` | Append-only revisions, stewardship and audit chain |
| Governed scientific figures | `research-api/app/figure_releases.py`, `research-api/app/public_figures.py`, `research-web/assets/research-figures.js`, `research-web/assets/research-hub-figures.js` | Figures serve only from a released article revision; synthetic staging channel only |
| Shared Lab figure renderer (vendored, hash-locked) | `research-web/assets/vendor/sc-figure-renderer/` | Byte-identical to a pinned Lab release; third-party licences included |
| Source-rights notices | `research-api/app/source_rights_sync.py` | Restricting a source withdraws dependent figures |
| WordPress Research bridge | `wordpress/sc-research-bridge/` | Disabled by default; installable package built in CI |
| Public source integrity gate | `scripts/check_public_source.py` | Manifest, renderer lock, secret scan, no links into private repositories |
| Scientific Discovery contracts | `research-api/app/discovery.py` | Providers disabled until verified |
| Discovery adapters (OpenAlex, Crossref) | `research-api/app/discovery_adapters/` | Typed, offline-tested; no network transport, nothing published |
| Discover interface and prepared HTTPS transport | `research-web/assets/research-discovery.js`, `research-api/app/discovery_adapters/https_transport.py`, `library_intake.py` | Synthetic-fixture tested; live providers, saving, Library submission and publication disabled |
| Controlled live discovery (OpenAlex, Crossref) | `research-api/app/discovery_adapters/activation.py`, `live.py`, `scripts/discovery_staging_acceptance.py` | Disabled by default; staging-only activation with provider budgets, circuit breakers and deadlines; production, saving, Library submission and publication disabled |
| Governed discovery intake (RH-08D-04) | `research-api/app/discovery_intake.py`, `discovery_intake_store.py`, migration `0013_discovery_intake` | Disabled by default: owner-scoped saved candidates with server-held provenance, deduplication, article associations and recorded (never sent) Library proposals; production, Library submission and publication disabled |
| Public Research frontend | `research-web/` | Under development |
| Anonymous-only API bridge | `research-web/assets/research-workbench-bridge.js` | Disabled by default |
| Isolated deployment examples | `infrastructure/` | Generic templates; no production secrets |
| Automated verification | `tests/`, `.github/workflows/` | Python, Node, PHP and PostgreSQL tests on every pull request; `main` requires them |

## Next milestones

0. Scientific figures: reviewed map views through the shared renderer, then reviewed static views of 3D scenes; no production figure channel until approved.
1. Verify the existing owner identity bridge and protect authenticated editing with authorization, CSRF safeguards, and tenant isolation.
2. Add independently authorized, typed source adapters for approved metadata and provenance only.
3. Implement reviewed public-data projection and richer accessible research graph navigation.
4. Complete site integration, backup/restore, rollback, performance and accessibility certification.
5. Maintain clean source-history provenance, public-source license/rights review, and confidential operational separation.

## Publication and access policy

A source release is **not** an operational or scientific release. Public endpoints must exclude unpublished research, private Workspace IDs, source paths, restricted evidence and credentials. Metadata connections are not proof of scientific causation. No private Research repository visibility change is authorized by this roadmap.

**Internal operations:** see the separate, access-controlled Documentation/Data/Handoffs repositories. Their details are deliberately not copied here.
