# SC-FIG-02B — Approved scientific figures in public Research

> Superseded in part by SC-FIG-03A (`docs/SC-FIG-03A-GOVERNED-FIGURES.md`): release state now comes from
> Workspace-signed approvals and Research's release step, not from fields inside the bundle.

Research shows approved scientific figures on public article pages. It uses the shared
`sc.figure-bundle.v1` contract and the Lab Graph Studio renderer. Research has no figure renderer,
figure database or figure registry of its own.

## How a figure reaches a reader

1. **Producer.** Workbench, Workspace, Lab, Site Intelligence or Library produces a figure bundle
   with its view files. Producers include Workspace notebook figures and Lab's Plotly and Vega-Lite
   views. The bundle carries dataset identity, citations, calculation references, classification,
   units, uncertainty, artifact hashes and reproduction status.
2. **Registration.** `figure_objects.register_figure_bundle` registers the exact bundle bytes as one
   private, unreviewed `figure` object in the existing Research object registry. The object's
   identity includes the SHA-256 of those bytes, so a changed figure is a new revision.
3. **Approval.** Publication approval stays with Workspace. The figure becomes public only through the
   governed approval flow, which marks the object `public-approved` and rights-cleared. Its article
   and area must also be public. Possessing a bundle never implies a right to publish it.
4. **Release store.** The approved bundle and its view files sit in a read-only, content-addressed
   directory named by `RESEARCH_PUBLIC_FIGURE_STORE`. The layout is
   `<bundle sha256>/<file sha256>`.
5. **Public API (read-only).** `research-api/app/public_figures.py` serves the figure. On every
   request it re-checks:
   - the object is approved at object, article and area level;
   - the bundle bytes hash to the registered revision;
   - each view matches its declared hash;
   - the bundle is a public release:
     - reviews are recorded and accessibility has passed;
     - it is bound to its article;
     - it contains no links other than doi.org, and no filesystem paths, email addresses, IP
       addresses, internal host names, credential-like text or Workspace project IDs.

   Anything that fails is answered with 404. Nothing is redacted, because that would break the
   hash binding.
6. **Browser.** `research-web/assets/research-figures.js` runs only when the server enables figures
   and the article has one. It loads the vendored Lab renderer under Subresource Integrity, fetches
   the exact bytes, and calls the renderer. The renderer verifies the hashes again, applies the Lab
   Plotly and Vega-Lite content policies, and draws the figure without `eval`. Next to the figure,
   the page shows:
   - an accessible data table;
   - the evidence class, with a "not a measurement" notice where it applies;
   - units and uncertainty;
   - citations, with licence and quality status exactly as the producer recorded them;
   - the provenance and the release hash.

   Readers can export a verified SVG with embedded provenance and a receipt.

| Endpoint | Returns |
|---|---|
| `GET /api/research/v1/public/figures/status` | whether figures are enabled, and the release channel |
| `GET /api/research/v1/objects?article=<slug>&kind=figure` | the existing public object listing |
| `GET /api/research/v1/objects/<id>/figure` | `sc-research-public-figure/1.0`: release facts, citations, file list |
| `GET /api/research/v1/objects/<id>/figure/files/<sha256>` | exact bytes, served as `application/octet-stream` in a sandbox |

## Release channels

`RESEARCH_PUBLIC_FIGURES` selects the channel:
- Unset or `off`: nothing is served.
- `staging-synthetic`: serves only synthetic, isolated staging bundles, labelled "Synthetic staging
  release. Not published research."
- There is no production channel yet. `production`, or any other value, is treated as `off`.

## Shared renderer

`research-web/assets/vendor/sc-figure-renderer/` holds byte-identical copies of these files from one
Lab commit:
- the Lab renderer modules: the FIG-02A Plotly policy, the FIG-02B Vega-Lite module and the
  `sc.figure-bundle.v1` renderer;
- their pinned libraries: plotly.js 3.3.1, Vega 6.4.0, Vega-Lite 6.5.0 and vega-interpreter 2.3.2,
  with their licences.

`RENDERER_LOCK.json` records the Lab commit and release, each file's SHA-256 and its SRI value.
Update the files only with:

    python3 scripts/sync_figure_renderer.py --lab <Lab checkout> --rev <Lab commit>

New adapters, such as MapLibre, CesiumJS and PyVista static views, arrive the same way. Lab adds them
to the shared renderer, and Research re-syncs.

The web server's Content-Security-Policy allows same-origin scripts only. It does not allow
`unsafe-eval`, remote scripts, remote data or tiles.

## Gates

| Gate | Command |
|---|---|
| Source and unit tests | `python -m pytest tests -q`; `node --test tests/*.cjs` |
| Public source integrity | `python3 scripts/check_public_source.py` (manifest, renderer lock, secret scan) |
| Renderer lock only | `python3 scripts/sync_figure_renderer.py --check` |

Browser acceptance against a deployed stack, staging operations and release decisions run in the
private repositories.

## Not included

- Real research.
- A production release channel.
- Publication approval of any kind (Workspace keeps that authority).
- MapLibre, CesiumJS or PyVista rendering.
