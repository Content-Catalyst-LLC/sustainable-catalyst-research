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
| PostgreSQL schema migrations | `research-api/alembic/` | Five versioned migrations |
| Scientific Discovery contracts | `research-api/app/discovery.py` | Providers disabled until verified |
| Public Research frontend | `research-web/` | Under development |
| Anonymous-only API bridge | `research-web/assets/research-workbench-bridge.js` | Disabled by default |
| Approved scientific figures (Plotly, Vega-Lite, verified SVG) | `research-api/app/public_figures.py`, `research-web/assets/research-figures.js` | Implemented; synthetic staging channel only ([details](docs/SC-FIG-02B-PUBLIC-FIGURES.md)) |
| Shared Lab figure renderer (vendored, SRI-locked) | `research-web/assets/vendor/sc-figure-renderer/` | Byte-identical to a pinned Lab commit |
| Public source integrity gate | `scripts/check_public_source.py` | Manifest, renderer lock, secret scan |
| Isolated deployment examples | `infrastructure/` | Generic templates; no production secrets |
| Automated verification | `tests/` | Python, JavaScript, PostgreSQL tests |

## Next milestones

0. Scientific figures: map views (MapLibre) through the shared renderer, then reviewed static views of 3D/4D scenes; no production figure channel until approved.
1. Verify the existing owner identity bridge and protect authenticated editing with authorization, CSRF safeguards, and tenant isolation.
2. Add independently authorized, typed source adapters for approved metadata and provenance only.
3. Implement reviewed public-data projection and richer accessible research graph navigation.
4. Complete site integration, backup/restore, rollback, performance and accessibility certification.
5. Maintain clean source-history provenance, public-source license/rights review, and confidential operational separation.

## Publication and access policy

A source release is **not** an operational or scientific release. Public endpoints must exclude unpublished research, private Workspace IDs, source paths, restricted evidence and credentials. Metadata connections are not proof of scientific causation. No private Research repository visibility change is authorized by this roadmap.

**Internal operations:** see the separate, access-controlled Documentation/Data/Handoffs repositories. Their details are deliberately not copied here.
