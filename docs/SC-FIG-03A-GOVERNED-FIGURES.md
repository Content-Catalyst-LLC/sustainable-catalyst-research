# SC-FIG-03A — Governed scientific figure approval and publication

A scientific figure reaches the public Research site only through the governed path. That path
replaces the SC-FIG-02B staging seed, which set approval columns directly; the seed is removed.

```
Workspace verified run (notebook cell) ──► Workbench sc.figure-bundle.v1 + view (Workspace run output, role "figure")
   └─ signed Workspace export (output hash) + bytes ──► Research ingest: figure binding in immutable provenance
        └─ four Workspace-signed approvals of research-object:<id>#<provenance sha256> (step-up enforced)
             └─ promotion (refused without a binding) ──► article revision references the figure
                  └─ editorial publish: re-verify bytes + disclosure policy ──► release store + release record
                       └─ public API (re-checks every request) ──► research-web ──► Lab renderer (releaseContext)
```

## 1. Binding (`research-api/app/figure_releases.py`, `workspace_ingest.py`)

`POST /convergence/workspace/execution-runs` accepts an optional `figureFiles` field:
`[{sha256, contentBase64}]`, with at most 12 files of up to 8 MiB each. For every Workspace figure
output (role `figure`, media type `application/vnd.sc.figure-bundle.v1+json`), Research checks four
things:

- the bundle bytes hash to the output hash in the **signed** export;
- the run is `executed-verified`;
- every artifact the bundle declares is supplied and matches its hash (raw bytes or canonical JSON);
- every data source the bundle cites (`sourceRefs[].sha256`) is one of the run's verified input
  hashes.

Literature citations (with a DOI or citation ID and no hash) are recorded as citations. Files that
no figure references are refused.

The result is `provenance.figure` (`sc-research-figure-binding/1.0`), with these fields:

| Field | Contents |
|---|---|
| `bundleSha256`, `figureId`, `version`, `producer` | The bundle revision |
| `evidenceClass`, `measurementStatus` | Evidence classification |
| `artifacts[]` | Hash, media type, size and hash mode of each view |
| `sourceLineage[]`, `citations[]` | Data sources and literature citations |
| `calculationRefs` | The run, its runtime receipts and result hashes, the verified notebook cell, and the producer's own refs |
| `ownership` | The Workspace project, the owner scope hash, and the bundle's project reference |

Approvals name `research-object:<id>#<provenance sha256>`, so the four signed approvals cover
exactly this binding. A changed bundle is a new object revision. The older one becomes superseded,
and its approvals do not transfer.

Only the signed Workspace export path can write a binding. A generic convergence bundle that
carries `figure` in its provenance is refused. A figure without a binding can be registered, but
`promote_public_projection` refuses it.

The bytes go to `RESEARCH_FIGURE_CANDIDATE_STORE`, a private store mounted only into the writer.

## 2. Release (`editorial.publish_revision`, `rollback_article`)

After the signed article approvals and reference checks, `release_figures` handles every figure
the revision references:

1. Re-reads the bytes from the candidate store and re-checks them against the approved binding.
2. Re-applies the disclosure policy (`public_figures.release_errors` plus a scan of JSON views).
3. Requires all four signed approvals and the synthetic-only staging channel.
4. Only if every figure passes, copies the files into `RESEARCH_FIGURE_RELEASE_STORE`
   (`<bundle>/<file>`).
5. Writes an immutable `research_figure_releases` record (migration `0010`, protected by a
   Postgres trigger). The record names the article revision, provenance hash, subject and signed
   approval records.

The editorial event (`figureReleases`) puts the release on the audit chain. Any failure refuses
the publication and leaves nothing in the store.

The disclosure policy no longer reads the producer's own `review.*`, `accessibilityReview` or
artifact `status` fields. Those are producer notes; release authority is the signed approvals. The
policy still requires all of the following:

- the bundle is bound to `research-entry:<article>`;
- caption and alt text are present;
- for the staging channel, a synthetic test bundle;
- none of the following:
  - links other than doi.org;
  - file paths;
  - email addresses or IP addresses;
  - internal hosts;
  - credential-like text;
  - Workspace project, user, dataset, run, artifact or notebook identifiers.

## 3. Serving and lifecycle (`public_figures.py`)

A figure is served only while all of these hold:

- the object is public (object, entry and area approved, not revoked);
- an article revision that released it is the **live** revision, published or unlisted;
- the release names the current provenance hash;
- the stored bytes still verify.

Package schema `sc-research-public-figure/2.0` adds the release revision and time, the provenance
hash, the approval dimensions, and a `releaseContext` for the Lab renderer.

| Event | API | Release store |
|---|---|---|
| Unlist | Still served by link; leaves `/objects` listings and `/graph/metadata` | Kept |
| Relist | Listed again | Kept |
| Update (new revision without the figure) | 404; leaves listings, search and the graph | Bytes removed |
| Rollback to a revision with the figure | Re-released after re-verification | Restored from candidates |
| Withdraw (final, tombstone) / retract | 404 (article content 410 / 404) | Bytes removed |
| Workspace withdrawal of the run | Objects revoked; 404 | Bytes removed |

Discovery follows release state. `object_registry.public_query` admits a figure object only while
a live article revision releases its exact bundle. Listings, search, the knowledge graph, object
and provenance links, and article reference status all go through that query. So an approved
figure is invisible before its first release and after an update drops it.

Public provenance never names a Workspace model. `codeVersion` is published as
`workspace-model#<fingerprint>`.

Every response carries `Cache-Control: no-store`. Files are served as `application/octet-stream`
with `Content-Disposition: attachment` and `Content-Security-Policy: default-src 'none'; sandbox`.

## 4. Frontend and Lab

`research-web/assets/research-figures.js` takes its figures from the live revision's references
(`/articles/<slug>/content`, role `figure`). For a withdrawn article it shows nothing. It calls the
vendored Lab renderer, Lab 0.163.1.7, renderer 0.2.0:

`render(out, files, bundleSha256, {releaseContext})`

Lab shows Research's release line only for the exact verified bundle. Without that, it reads "Not
released here". Lab never treats bundle review fields as approval.

## Gates

- `pytest tests/test_sc_fig03a_governed_figures.py`: 41 synthetic tests covering:
  - binding;
  - forged, cross-owner, stale and superseded approvals, and step-up;
  - release recheck and disclosure policy;
  - unlist and relist, update, rollback, withdrawal, retraction and Workspace withdrawal;
  - tampered, missing and symlinked bytes;
  - channel off;
  - public provenance, with no Workspace model IDs;
  - discovery before release and after an update;
  - the knowledge graph.
- `node --test tests/test_research_figures.cjs`.
- Isolated staging acceptance with real SC Identity step-up approvals. There is no SQL and no
  seeding: every state change goes through Workspace or the editorial API.

  | Run | Result |
  |---|---|
  | Harness, publish phase | 36/36 |
  | Harness, lifecycle phase | 11/11 |
  | `scripts/sc_fig03a_public_browser_acceptance.py`, released | 17/17 |
  | `scripts/sc_fig03a_public_browser_acceptance.py`, withdrawn | 9/9 |
  | Lab Graph Studio render of the same bundle | Passed |

  Evidence: Data `evidence/research/sc-fig-03a-governed-figures-20261111/`.

Staging found two defects, both fixed here with tests:

1. Workspace model IDs appeared in public provenance.
2. A figure dropped by an update stayed discoverable.

## Public repository

The public repository has no convergence or editorial modules yet (private PRs #29, #35 and #44), and
this module depends on them. So no public export is made for SC-FIG-03A until that stack is merged
and reviewed for public release. Public #1 keeps the SC-FIG-02B surface.
