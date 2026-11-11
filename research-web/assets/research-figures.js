"use strict";
// SC-FIG-02B: approved scientific figures on public Research article pages.
// Research does not draw figures itself. When an article has an approved figure, this loads the Lab
// sc.figure-bundle.v1 renderer (vendored byte-for-byte; the browser enforces Subresource Integrity),
// fetches the exact approved bundle and view bytes from the public API, and passes them to the
// renderer, which re-verifies every hash before drawing. API values enter the DOM as text only.
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SCResearchFigures = api;
}(typeof self !== "undefined" ? self : this, function (root) {
// BEGIN RENDERER LOCK (scripts/sync_figure_renderer.py)
const RENDERER_LOCK = {
  "labCommit": "396fbffcc1fc86e8b61512e924bfbf16e1234234",
  "labRelease": "0.163.1.6",
  "rendererVersion": "0.1.0",
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
      "role": "fig02a-policy",
      "kind": "script",
      "path": "js/modules/graph-studio-plotly-preview-fig02a.js",
      "integrity": "sha384-LqId1myunfmeRRmA4AlFYje7gGjAuTIijWZMwgmxRJSFxyTE1nW2xn5JwQaSoNjh"
    },
    {
      "role": "fig02b-vega-lite",
      "kind": "script",
      "path": "js/modules/graph-studio-vega-lite-fig02b.js",
      "integrity": "sha384-+0s/pYr5IoBilCQSP7uBEbnHBYiPW7qdzHzCYrETUXZE5Ud6E0lYGaiXMzSN6PUa"
    },
    {
      "role": "renderer",
      "kind": "script",
      "path": "js/modules/graph-studio-figure-bundle-renderer.js",
      "integrity": "sha384-GilFyB8flKtndcKD7WVEG+O/Y11LBJXKarwSBWB8H4bAQfnWTtX8RJGfhm4o8WxL"
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
      "integrity": "sha384-a/fOdOB3BriA4oc4F7czD6G8xwhcC6AXBLnkHhcj2lfladKzMGxdfQQRQSW4Af/d"
    }
  ]
};
// END RENDERER LOCK
  const ENDPOINT = "/api/research/v1";
  const VENDOR = "/assets/vendor/sc-figure-renderer/";
  const PACKAGE_SCHEMA = "sc-research-public-figure/1.0";
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
    const R = root.SCLabFigureBundleRenderer, V = root.SCLabVegaLiteFIG02B;
    const errors = [];
    if (!R || R.version !== RENDERER_LOCK.rendererVersion || typeof R.render !== "function") errors.push("Lab figure renderer " + RENDERER_LOCK.rendererVersion + " is not loaded");
    if (!V || typeof V.runtimeErrors !== "function" || V.runtimeErrors().length) errors.push("Vega-Lite runtime is not verified");
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
    const base = ENDPOINT + "/objects/" + id + "/figure/files/";
    for (const f of files) {
      // Only this figure's own content-addressed files are ever fetched.
      if (!f || !SHA.test(f.sha256 || "") || f.url !== base + f.sha256) errors.push("unexpected file reference");
    }
    const bundles = files.filter(f => f && f.role === "bundle");
    if (bundles.length !== 1 || bundles[0].sha256 !== release.bundleSha256) errors.push("bundle file does not match the release hash");
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

  async function renderOne(doc, fetchImpl, list, item) {
    const section = list.appendChild(el(doc, "section", {"class": "research-figure", "data-research-figure-state": "loading", "aria-busy": "true"}));
    section.append(el(doc, "h3", null, item.title));
    const status = section.appendChild(el(doc, "p", {"class": "research-figure-status", "role": "status"}, "Verifying approved figure…"));
    try {
      if (!UUID.test(item.id || "")) throw new Error("invalid figure id");
      const pkg = await fetchJSON(fetchImpl, "/objects/" + item.id + "/figure");
      const errors = packageErrors(pkg, item.id);
      if (errors.length) throw new Error(errors.join("; "));
      section.append(releasePanel(doc, pkg));
      const [renderer, files] = await Promise.all([loadRenderer(doc), fetchFiles(fetchImpl, pkg)]);
      const out = section.appendChild(el(doc, "div", {"class": "research-figure-render"}));
      const result = await renderer.render(out, files, pkg.release.bundleSha256);
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
    let status, listing;
    try {
      status = await fetchJSON(fetchImpl, "/public/figures/status");
      if (!status || status.enabled !== true) return {state: "disabled"};
      listing = await fetchJSON(fetchImpl, "/objects?kind=figure&limit=" + MAX_FIGURES + "&article=" + encodeURIComponent(slug));
    } catch (_) {
      return {state: "unavailable"};
    }
    const items = (listing && Array.isArray(listing.items) ? listing.items : []).filter(i => i && i.kind === "figure" && i.article === slug);
    if (!items.length) return {state: "none"};
    // The panel's "will be connected" placeholder no longer applies once approved figures exist.
    for (const p of (host.querySelectorAll ? host.querySelectorAll(".empty-data") : [])) p.hidden = true;
    const list = el(doc, "div", {"class": "research-figures", "data-research-figures": String(items.length)});
    host.append(list);
    const sections = [];
    for (const item of items) sections.push(await renderOne(doc, fetchImpl, list, item));
    return {state: "done", figures: sections.map(s => s.getAttribute("data-research-figure-state"))};
  }

  return {RENDERER_LOCK, packageErrors, releasePanel, loadRenderer, rendererErrors, renderFigures};
}));
