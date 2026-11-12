"use strict";
// SC-FIG-02B / SC-FIG-03A: released scientific figures on public Research article pages.
// Research does not draw figures itself. The figures shown are the ones the article's live revision
// released (its "figure" references). For each, this loads the Lab sc.figure-bundle.v1 renderer
// (vendored byte-for-byte; the browser enforces Subresource Integrity), fetches the exact released
// bundle and view bytes from the public API, and passes them to the renderer together with
// Research's release record; the renderer re-verifies every hash before drawing and shows the
// release only for that exact bundle. API values enter the DOM as text only.
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SCResearchFigures = api;
}(typeof self !== "undefined" ? self : this, function (root) {
// BEGIN RENDERER LOCK (scripts/sync_figure_renderer.py)
const RENDERER_LOCK = {
  "labCommit": "302015de7f1552779e01aa1f0f5be41ece4d6910",
  "labRelease": "0.163.1.9",
  "rendererVersion": "0.3.0",
  "files": [
    {
      "role": "plotly",
      "kind": "script",
      "path": "lib/plotly-3.3.1/plotly.min.js",
      "integrity": "sha384-SsOMajmLeeY81sOzGCn88NjTdDwa+nz3Lb1ZNouSdXAz5TBsvD+Pwgf1Iqtxns6c"
    },
    {
      "role": "vega",
      "kind": "script",
      "path": "lib/vega-6.4.0/vega.min.js",
      "integrity": "sha384-VKdcJr3ZaBIJMbVcopTAI/JEuUkSY6qnwVu9iLuw0DnQ9gQ1JjsfZJhXFQAgNi43"
    },
    {
      "role": "vega-lite",
      "kind": "script",
      "path": "lib/vega-lite-6.5.0/vega-lite.min.js",
      "integrity": "sha384-pV2l7nzmyxZh02knvUH7N8zvtyCYsYiGt+SsE/emG/dFiBI6G0YJZFZ2FzEX72P3"
    },
    {
      "role": "vega-interpreter",
      "kind": "script",
      "path": "lib/vega-interpreter-2.3.2/vega-interpreter.classic.js",
      "integrity": "sha384-6ubzJVl9QzQeCiCO0ipsYQO8/ECOldDIOuEuvZTo7sQLeoCtTI9wYKIn/uKl420O"
    },
    {
      "role": "maplibre",
      "kind": "script",
      "path": "lib/maplibre-gl-5.24.0/maplibre-gl-csp.js",
      "integrity": "sha384-+lDFpEYaS5ofD0qW/5dA+duOMWMSGs+utCY+wx9H/lS1i0WZ0bQH31giSmqYsxqR"
    },
    {
      "role": "fig02a-policy",
      "kind": "script",
      "path": "js/modules/graph-studio-plotly-preview-fig02a.js",
      "integrity": "sha384-gCjSePplIKFDu2M2/rMUS9CCuNXLdzSV0GidfvDRXK8Ap92HzFDoCkM8caVxvkhW"
    },
    {
      "role": "fig02b-vega-lite",
      "kind": "script",
      "path": "js/modules/graph-studio-vega-lite-fig02b.js",
      "integrity": "sha384-+0s/pYr5IoBilCQSP7uBEbnHBYiPW7qdzHzCYrETUXZE5Ud6E0lYGaiXMzSN6PUa"
    },
    {
      "role": "fig04-maplibre-contract",
      "kind": "script",
      "path": "js/modules/graph-studio-maplibre-fig03a.js",
      "integrity": "sha384-GdASDl9BYqCpPaL3n+V0k8XcsAc1yxqRwAlSvKk14Fsy9RzFDGIuqqFfzOs9zj6l"
    },
    {
      "role": "fig04-maplibre",
      "kind": "script",
      "path": "js/modules/graph-studio-maplibre-renderer-fig04.js",
      "integrity": "sha384-BE08Y9F6EbpAJpDlwwfIAnRh3KJ79odN0uq+S+OMeoM4G17qoFoaidQi51oHgX03"
    },
    {
      "role": "renderer",
      "kind": "script",
      "path": "js/modules/graph-studio-figure-bundle-renderer.js",
      "integrity": "sha384-y6AAioOV7v+ZlH8xiofrpVcFasacif1/dHP9aB1U96F/ZEbny/9xHZI3oIdIcgUp"
    },
    {
      "role": "maplibre-style",
      "kind": "style",
      "path": "lib/maplibre-gl-5.24.0/maplibre-gl.css",
      "integrity": "sha384-uTttxo/aOKbdE5RlD/SPzSDoDmNvGlUYPjONi2MN/b7c9HPSvW07OIuyP7uL6jxK"
    },
    {
      "role": "fig02a-style",
      "kind": "style",
      "path": "css/sc-lab-graph-studio-plotly-preview-fig02a.css",
      "integrity": "sha384-0IwbQHAEX7hGcXAs17mPmMDhOSoiFbCxzMlTH9MoiNRSgGPgmaUm3rJsehLiXTpZ"
    },
    {
      "role": "renderer-style",
      "kind": "style",
      "path": "css/sc-lab-graph-studio-figure-bundle-renderer.css",
      "integrity": "sha384-qmjNMQgM74NQqKuOe7WWZsPpe+2HUzJ2NAXq3BpiNZunAZ1xXO1GKrY2lwtMzhuX"
    },
    {
      "role": "fig04-style",
      "kind": "style",
      "path": "css/sc-lab-graph-studio-maplibre-fig04.css",
      "integrity": "sha384-YhIdgxBJBZbRLwBID5wHfr3TIwMQH/rsog8MTjP+o4MevY+iYnb5CQtHWRa0JWlC"
    },
    {
      "role": "maplibre-worker",
      "kind": "worker",
      "path": "lib/maplibre-gl-5.24.0/maplibre-gl-csp-worker.js",
      "integrity": "sha384-wBzwLUk46fiEnNOOlHXeylfQrL/SzyNqSD0gYdQJskF9cbBaNcRDYn6ZmSBW4l0z"
    }
  ]
};
// END RENDERER LOCK
  // Research web defaults. The WordPress Research Hub bridge (SC-FIG-03B) calls configure() once with its
  // own same-origin REST facade and the Lab plugin's renderer files (same bytes, same integrity lock).
  let ENDPOINT = "/api/research/v1";
  let VENDOR = "/assets/vendor/sc-figure-renderer/";
  let packagePath = id => "/objects/" + id + "/figure";
  let fileBase = id => ENDPOINT + "/objects/" + id + "/figure/files/";
  const WP_ENDPOINT = /^\/(?:[A-Za-z0-9_-]+\/)*wp-json\/sc-research\/v1$/;
  const SAME_ORIGIN_DIR = /^\/(?:[A-Za-z0-9_.-]+\/)+$/;
  const PACKAGE_SCHEMA = "sc-research-public-figure/2.0";
  const SHA = /^[0-9a-f]{64}$/;
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  const MAX_FIGURES = 12, MAX_FILES = 12;
  const REQUEST = {credentials: "omit", cache: "no-store", redirect: "error"};

  function el(doc, tag, attrs, text) {
    const node = doc.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  // ---- renderer loading (once, in lock order, integrity enforced) ----
  let loading = null;
  function inject(doc, file) {
    // The MapLibre worker is not a page script: the renderer fetches it, checks its SHA-384 and runs it from blob:.
    if (file.kind === "worker") return Promise.resolve();
    return new Promise((resolve, reject) => {
      const node = file.kind === "script" ? doc.createElement("script") : doc.createElement("link");
      if (file.kind === "script") { node.src = VENDOR + file.path; node.async = false; }
      else { node.rel = "stylesheet"; node.href = VENDOR + file.path; }
      node.integrity = file.integrity;
      node.setAttribute("data-sc-figure-renderer", file.role);
      node.onload = () => resolve();
      node.onerror = () => reject(new Error("Renderer file failed its integrity check or did not load: " + file.path));
      doc.head.append(node);
    });
  }
  function rendererErrors() {
    const R = root.SCLabFigureBundleRenderer, V = root.SCLabVegaLiteFIG02B, G = root.SCLabMapLibreFIG04;
    const errors = [];
    if (!R || R.version !== RENDERER_LOCK.rendererVersion || typeof R.render !== "function") errors.push("Lab figure renderer " + RENDERER_LOCK.rendererVersion + " is not loaded");
    if (!V || typeof V.runtimeErrors !== "function" || V.runtimeErrors().length) errors.push("Vega-Lite runtime is not verified");
    if (!G || typeof G.libraryErrors !== "function" || G.libraryErrors().length) errors.push("MapLibre runtime is not verified");
    return errors;
  }
  function loadRenderer(doc) {
    if (!loading) {
      loading = RENDERER_LOCK.files.reduce((p, f) => p.then(() => inject(doc, f)), Promise.resolve()).then(() => {
        const errors = rendererErrors();
        if (errors.length) throw new Error(errors.join("; "));
        return root.SCLabFigureBundleRenderer;
      });
      loading.catch(() => { loading = null; });
    }
    return loading;
  }

  // ---- public API contract ----
  function packageErrors(pkg, id) {
    const errors = [];
    if (!pkg || pkg.schema !== PACKAGE_SCHEMA) return ["not an " + PACKAGE_SCHEMA + " response"];
    if (pkg.id !== id) errors.push("figure id does not match the request");
    const release = pkg.release || {};
    if (!SHA.test(release.bundleSha256 || "")) errors.push("release has no bundle hash");
    const files = Array.isArray(pkg.files) ? pkg.files : [];
    if (!files.length || files.length > MAX_FILES) errors.push("figure package must list 1-" + MAX_FILES + " files");
    const base = fileBase(id);
    for (const f of files) {
      // Only this figure's own content-addressed files are ever fetched.
      if (!f || !SHA.test(f.sha256 || "") || f.url !== base + f.sha256) errors.push("unexpected file reference");
    }
    const bundles = files.filter(f => f && f.role === "bundle");
    if (bundles.length !== 1 || bundles[0].sha256 !== release.bundleSha256) errors.push("bundle file does not match the release hash");
    const ctx = pkg.releaseContext || {};
    if (ctx.authority !== "sc-research" || ctx.bundleSha256 !== release.bundleSha256) errors.push("release record does not name this bundle");
    return errors;
  }
  async function fetchJSON(fetchImpl, path) {
    const response = await fetchImpl(ENDPOINT + path, REQUEST);
    if (!response.ok) throw new Error("unavailable");
    return response.json();
  }
  async function fetchFiles(fetchImpl, pkg) {
    return Promise.all(pkg.files.map(async f => {
      const response = await fetchImpl(f.url, REQUEST);
      if (!response.ok) throw new Error("figure file unavailable");
      return {name: f.role + "-" + f.sha256.slice(0, 12), bytes: new Uint8Array(await response.arrayBuffer())};
    }));
  }

  // ---- Research release context (shown beside the Lab renderer's own provenance) ----
  function releasePanel(doc, pkg) {
    const box = el(doc, "div", {"class": "research-figure-release"});
    box.append(el(doc, "p", {"class": "research-figure-channel", "role": "note"}, pkg.release.label));
    if (pkg.classificationNotice) box.append(el(doc, "p", {"class": "research-figure-notice"}, pkg.classificationNotice));
    const dl = box.appendChild(el(doc, "dl", {"class": "research-figure-facts"}));
    const facts = [
      ["Release", pkg.release.approval],
      ["Released", "Article revision " + pkg.release.articleRevision + (pkg.release.releasedAt ? " · " + pkg.release.releasedAt : "")],
      ["Provenance SHA-256", pkg.release.provenanceSha256 || "not reported"],
      ["Evidence class", pkg.evidenceClass + " (" + pkg.measurementStatus + ")"],
      ["Data classification", pkg.dataClassification],
      ["Figure revision", pkg.figureId + " v" + pkg.version + " · " + pkg.release.registryVersion],
      ["Bundle SHA-256", pkg.release.bundleSha256],
      ["Reproduction status", pkg.release.reproducibility],
      ["Units", pkg.units || "not reported"],
      ["Uncertainty", pkg.uncertainty || "not reported"],
    ];
    for (const [k, v] of facts) { dl.append(el(doc, "dt", null, k), el(doc, "dd", null, v)); }
    if (Array.isArray(pkg.citations) && pkg.citations.length) {
      box.append(el(doc, "h4", null, "Sources and citations"));
      const list = box.appendChild(el(doc, "ul", {"class": "research-figure-citations"}));
      for (const c of pkg.citations) {
        const parts = [c.citationId || c.sourceId, c.provider, c.doi ? "doi:" + c.doi : "",
          "identity " + c.identityStatus, "quality " + c.qualityStatus, "licence " + c.licenceStatus];
        list.append(el(doc, "li", null, parts.filter(Boolean).join(" · ")));
      }
    }
    if (Array.isArray(pkg.missingViews) && pkg.missingViews.length) {
      box.append(el(doc, "p", {"class": "research-figure-missing"}, pkg.missingViews.length + " view(s) of this figure are not in the public release and are not shown."));
    }
    return box;
  }

  // What the Lab renderer receives besides the bytes: Research's release record, and where the locked
  // MapLibre worker is (same origin, with the SHA-384 the renderer checks before starting it).
  function renderOptions(pkg) {
    const worker = RENDERER_LOCK.files.find(f => f.kind === "worker" && f.role === "maplibre-worker");
    return {releaseContext: pkg.releaseContext,
      maplibre: worker ? {workerUrl: VENDOR + worker.path, workerIntegrity: worker.integrity} : undefined};
  }

  async function renderOne(doc, fetchImpl, list, item) {
    const section = list.appendChild(el(doc, "section", {"class": "research-figure", "data-research-figure-state": "loading",
      "aria-busy": "true", "data-figure-key": UUID.test(item.id || "") ? item.id : ""}));
    section.append(el(doc, "h3", null, item.title));
    const status = section.appendChild(el(doc, "p", {"class": "research-figure-status", "role": "status"}, "Verifying approved figure…"));
    try {
      if (!UUID.test(item.id || "")) throw new Error("invalid figure id");
      const pkg = await fetchJSON(fetchImpl, packagePath(item.id));
      const errors = packageErrors(pkg, item.id);
      if (errors.length) throw new Error(errors.join("; "));
      section.append(releasePanel(doc, pkg));
      const [renderer, files] = await Promise.all([loadRenderer(doc), fetchFiles(fetchImpl, pkg)]);
      const out = section.appendChild(el(doc, "div", {"class": "research-figure-render"}));
      const result = await renderer.render(out, files, pkg.release.bundleSha256, renderOptions(pkg));
      section.setAttribute("data-research-figure-state", result.state);
      section.setAttribute("data-bundle-sha256", pkg.release.bundleSha256);
      status.textContent = result.state === "rendered" ? "Figure verified against its approved release."
        : result.state === "refused" ? "The figure did not pass verification and is not shown." : "Figure verified; no view could be drawn.";
    } catch (_) {
      section.setAttribute("data-research-figure-state", "unavailable");
      status.textContent = "This figure is not available for public viewing.";
    } finally {
      section.removeAttribute("aria-busy");
    }
    return section;
  }

  async function renderFigures(host, slug, options) {
    const doc = (options && options.document) || root.document;
    const fetchImpl = (options && options.fetch) || root.fetch.bind(root);
    if (!host || !SLUG.test(slug || "")) return {state: "skipped"};
    let status, article;
    try {
      status = await fetchJSON(fetchImpl, "/public/figures/status");
      if (!status || status.enabled !== true) return {state: "disabled"};
      // The live revision's figure references: exactly the figures this revision released.
      article = await fetchJSON(fetchImpl, "/articles/" + encodeURIComponent(slug) + "/content");
    } catch (_) {
      return {state: "unavailable"};
    }
    const refs = article && article.slug === slug && Array.isArray(article.references) ? article.references : [];
    const items = refs.filter(r => r && r.role === "figure" && r.kind === "figure" && r.status === "available")
      .slice(0, MAX_FIGURES).map(r => ({id: r.publicKey, title: r.title}));
    if (!items.length) return {state: "none"};
    // The panel's "will be connected" placeholder no longer applies once approved figures exist.
    for (const p of (host.querySelectorAll ? host.querySelectorAll(".empty-data") : [])) p.hidden = true;
    return renderItems(host, items, options);
  }

  // Render a list of released figures ({id, title}) into ``host``. Callers pass only figures the public API
  // listed; every package and file is still fetched and re-verified here and by the renderer.
  async function renderItems(host, items, options) {
    const doc = (options && options.document) || root.document;
    const fetchImpl = (options && options.fetch) || root.fetch.bind(root);
    const list = el(doc, "div", {"class": "research-figures", "data-research-figures": String(items.length)});
    host.append(list);
    const sections = [];
    for (const item of items.slice(0, MAX_FIGURES)) sections.push(await renderOne(doc, fetchImpl, list, item));
    return {state: "done", figures: sections.map(s => s.getAttribute("data-research-figure-state"))};
  }

  // WordPress Research Hub: same-origin REST facade and the installed Lab plugin's renderer directory.
  function configure(options) {
    const endpoint = options && options.endpoint, vendor = options && options.vendor;
    if (typeof endpoint !== "string" || !WP_ENDPOINT.test(endpoint)) throw new Error("figure endpoint must be the WordPress Research facade");
    if (typeof vendor !== "string" || !SAME_ORIGIN_DIR.test(vendor) || vendor.includes("..")) throw new Error("renderer directory must be a same-origin path");
    if (loading) throw new Error("configure before the renderer loads");
    ENDPOINT = endpoint;
    VENDOR = vendor;
    packagePath = id => "/figures/" + id;
    fileBase = id => ENDPOINT + "/figures/" + id + "/files/";
    return {endpoint: ENDPOINT, vendor: VENDOR};
  }

  return {RENDERER_LOCK, packageErrors, releasePanel, renderOptions, loadRenderer, rendererErrors, renderFigures,
    renderItems, configure};
}));
