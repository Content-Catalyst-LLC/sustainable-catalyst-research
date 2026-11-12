/* SC-FIG-03B: released scientific figures on the WordPress Research Hub.
   Shows only what the public Research API lists as released (a figure is listed only while a live
   article revision releases its exact bundle), grouped by article. Rendering is the same Lab
   sc.figure-bundle.v1 renderer (research-figures.js), loaded from the installed Lab plugin under the
   Research integrity lock. Nothing here is cached: every visit asks the API again, so an update or
   withdrawal takes effect on the next page view. API values enter the DOM as text only.
*/
(function (host) {
  "use strict";
  var UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  var SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  var MAX_FIGURES = 12;
  var REQUEST = {credentials: "omit", cache: "no-store", redirect: "error", headers: {Accept: "application/json"}};

  function make(doc, tag, text, className) {
    var node = doc.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }

  // Valid configuration from the bridge plugin, or null (then nothing is requested at all).
  function figureConfig(config) {
    var f = config && config.figures;
    if (!config || config.enabled !== true || !f || f.enabled !== true ||
        f.channel !== "staging-synthetic" || typeof f.labRelease !== "string" ||
        typeof config.apiPrefix !== "string" || !/^\/(?:[A-Za-z0-9_-]+\/)*wp-json\/sc-research\/v1$/.test(config.apiPrefix) ||
        typeof f.rendererBase !== "string" || !/^\/(?:[A-Za-z0-9_.-]+\/)+$/.test(f.rendererBase) ||
        f.rendererBase.indexOf("..") !== -1) return null;
    return {apiPrefix: config.apiPrefix, rendererBase: f.rendererBase, channel: f.channel, labRelease: f.labRelease};
  }

  // Released figures from the public object listing, grouped by article, in listing order.
  function groupFigures(objects) {
    var groups = [], byArticle = {};
    (Array.isArray(objects) ? objects : []).forEach(function (row) {
      if (!row || row.kind !== "figure" || row.review_status !== "approved-public" || row.rights !== "cleared" ||
          typeof row.id !== "string" || !UUID.test(row.id) || typeof row.article !== "string" || !SLUG.test(row.article)) return;
      if (!byArticle[row.article]) {
        byArticle[row.article] = {article: row.article, area: typeof row.area === "string" ? row.area.slice(0, 130) : "", items: []};
        groups.push(byArticle[row.article]);
      }
      if (groups.reduce(function (n, g) { return n + g.items.length; }, 0) < MAX_FIGURES) {
        byArticle[row.article].items.push({id: row.id, title: typeof row.title === "string" ? row.title.slice(0, 350) : ""});
      }
    });
    return groups.filter(function (g) { return g.items.length; });
  }

  async function getJSON(fetchImpl, url) {
    var response = await fetchImpl(url, REQUEST);
    if (!response.ok || !/application\/json/i.test(response.headers.get("content-type") || "")) throw new Error("unavailable");
    return response.json();
  }

  async function mount(root, config, options) {
    var doc = (options && options.document) || host.document;
    var fetchImpl = (options && options.fetch) || host.fetch.bind(host);
    var renderer = (options && options.renderer) || host.SCResearchFigures;
    var cfg = figureConfig(config);
    if (!root || !cfg || !renderer) return {state: "disabled"};
    if (root.dataset.scFiguresInitialized === "yes") return {state: "already_initialized"};
    root.dataset.scFiguresInitialized = "yes";
    var panel = make(doc, "section", null, "scrh-figures-panel");
    panel.id = "sc-research-figures";
    panel.setAttribute("aria-labelledby", "sc-research-figures-title");
    var title = make(doc, "h2", "Released scientific figures");
    title.id = "sc-research-figures-title";
    var status = make(doc, "p", "Checking released figures…", "scrh-figures-status");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    panel.append(title, status);
    (root.querySelector("#scrh-view-overview") || root).append(panel);
    var groups;
    try {
      var state = await getJSON(fetchImpl, cfg.apiPrefix + "/figures/status");
      if (!state || state.schema !== "sc-research-public-figure-status/1.0" || state.enabled !== true || state.channel !== cfg.channel) {
        panel.hidden = true;
        root.dataset.scFigures = "disabled";
        return {state: "disabled"};
      }
      var listing = await getJSON(fetchImpl, cfg.apiPrefix + "/objects");
      if (!listing || listing.schema !== "sc-research-public-objects/1.0") throw new Error("unverified listing");
      groups = groupFigures(listing.items);
      panel.append(make(doc, "p", state.label || "Synthetic staging release. Not published research.", "scrh-figures-channel"));
    } catch (_) {
      status.textContent = "Released figures are unavailable right now. No private records were requested.";
      root.dataset.scFigures = "unavailable";
      return {state: "unavailable"};
    }
    if (!groups.length) {
      status.textContent = "No scientific figures are currently released.";
      root.dataset.scFigures = "none";
      return {state: "none"};
    }
    renderer.configure({endpoint: cfg.apiPrefix, vendor: cfg.rendererBase});
    var states = [];
    for (var i = 0; i < groups.length; i++) {
      var group = make(doc, "section", null, "scrh-figures-article");
      group.dataset.article = groups[i].article;
      group.append(make(doc, "h3", "Article: " + groups[i].article),
        make(doc, "p", groups[i].area ? "Research area: " + groups[i].area : "", "scrh-figures-area"));
      panel.append(group);
      var result = await renderer.renderItems(group, groups[i].items, {document: doc, fetch: fetchImpl});
      states = states.concat(result.figures || []);
    }
    // Discovery anchors: each rendered figure section can be linked from the catalog list.
    Array.prototype.forEach.call(panel.querySelectorAll(".research-figure[data-figure-key]"), function (section) {
      var key = section.getAttribute("data-figure-key");
      if (UUID.test(key)) { section.id = "sc-research-figure-" + key; section.tabIndex = -1; }
    });
    status.textContent = states.filter(function (s) { return s === "rendered"; }).length + " of " + states.length +
      " released figures verified and shown.";
    root.dataset.scFigures = "rendered";
    // A catalog link (or a kept URL) naming a figure moves keyboard and screen-reader focus to it.
    function focusTarget() {
      var hash = host && host.location ? host.location.hash : "";
      var m = /^#sc-research-figure-([0-9a-f-]{36})$/.exec(hash || "");
      var target = m && UUID.test(m[1]) ? doc.getElementById("sc-research-figure-" + m[1]) : null;
      if (target && typeof target.focus === "function") target.focus();
    }
    if (host && typeof host.addEventListener === "function") host.addEventListener("hashchange", focusTarget);
    focusTarget();
    if (host && typeof doc.dispatchEvent === "function" && typeof host.CustomEvent === "function") {
      doc.dispatchEvent(new host.CustomEvent("sc-research-figures-rendered"));
    }
    return {state: "rendered", figures: states};
  }

  var api = Object.freeze({figureConfig: figureConfig, groupFigures: groupFigures, mount: mount});
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (host) host.SustainableCatalystResearchHubFigures = api;
  if (host && typeof document !== "undefined" && typeof module === "undefined") {
    var boot = function () {
      var root = document.getElementById("sc-research-v2");
      if (root) mount(root, host.SustainableCatalystResearchConfig);
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
    else boot();
  }
})(typeof window !== "undefined" ? window : null);
