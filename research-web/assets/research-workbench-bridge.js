/* RH-03.3 — Public-only Research API bridge for the approved WP workbench.
   The existing #sc-research-v2 HTML, six static editorial rows, Astra header,
   and site CSS are never replaced. Explicit data-sc-public-api="enabled" gate.
   Script is intentionally standalone and can be enqueued as a versioned asset.
*/
(function (host) {
  "use strict";
  var API_PREFIX = "/api/research/v1";
  // The installable WordPress plugin supplies a same-origin REST route.
  // Never accept an arbitrary origin, query string or private API prefix.
  var wpConfig = host && host.SustainableCatalystResearchConfig;
  if (wpConfig && typeof wpConfig.apiPrefix === "string" &&
      /^\/(?:[a-zA-Z0-9_-]+\/)*wp-json\/sc-research\/v1$/.test(wpConfig.apiPrefix)) {
    API_PREFIX = wpConfig.apiPrefix;
  }
  var MAX_ITEMS = 50;
  var TIMEOUT_MS = 6000;
  var slugPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  var uuidPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;

  function make(tag, value, className) {
    var node = document.createElement(tag);
    if (value !== undefined && value !== null) node.textContent = String(value);
    if (className) node.className = className;
    return node;
  }
  function safeText(value, size) {
    return typeof value === "string" ? value.slice(0, size || 350) : "";
  }
  function escapeSegment(value, pattern) {
    return typeof value === "string" && pattern.test(value) ? encodeURIComponent(value) : "";
  }
  async function getPublicJSON(path, signal) {
    if (!path.startsWith("/") || path.startsWith("//") || path.includes("://")) {
      throw new Error("Invalid Research API path");
    }
    // Same-origin relative path only; no arbitrary URLs or credentials.
    var response = await fetch(API_PREFIX + path, {
      credentials: "omit", cache: "no-store", redirect: "error",
      headers: {Accept: "application/json"}, signal: signal
    });
    if (!response.ok) throw new Error("Public Research API unavailable");
    var type = response.headers.get("content-type") || "";
    if (!type.toLowerCase().includes("application/json")) throw new Error("Research API content-type mismatch");
    return response.json();
  }
  function normalizeArticle(row) {
    if (!row || !escapeSegment(row.slug, slugPattern)) return null;
    return {
      slug: row.slug, title: safeText(row.title, 350),
      summary: safeText(row.summary, 1400),
      area: safeText(row.area_slug, 130),
      state: safeText(row.state, 32),
      version: safeText(row.version, 64)
    };
  }
  function normalizeObject(row) {
    if (!row || !escapeSegment(row.id, uuidPattern) ||
        row.review_status !== "approved-public" || row.rights !== "cleared") return null;
    return {
      id: row.id, title: safeText(row.title, 350),
      summary: safeText(row.summary, 1400),
      kind: safeText(row.kind, 48),
      article: safeText(row.article, 150),
      area: safeText(row.area, 130),
      authority: safeText(row.authority, 64),
      data_classification: safeText(row.data_classification, 30),
      doi: safeText(row.doi, 240)
    };
  }
  function statusText(element, text) { element.textContent = text; }

  // Search locally across already-approved, already-loaded public metadata.
  // No keystrokes, private terms or identifiers are sent to any provider.
  function filterApprovedItems(articles, objects, query, kind) {
    var needle = safeText(query, 120).trim().toLowerCase();
    var selected = kind === "articles" || kind === "objects" || kind === "figures" ? kind : "all";
    function matches(row) {
      return !needle || (row.title + " " + row.summary + " " +
        (row.area || row.article || "") + " " + (row.authority || "")).toLowerCase().includes(needle);
    }
    // "figures": released scientific figures only (the public API lists a figure only while released).
    return {
      articles: selected === "objects" || selected === "figures" ? [] : articles.filter(matches),
      objects: selected === "articles" ? [] : objects.filter(function (row) {
        return matches(row) && (selected !== "figures" || row.kind === "figure");
      })
    };
  }

  function render(root, result) {
    var list = root.querySelector('[data-sc-public-list]');
    var summary = root.querySelector('[data-sc-public-summary]');
    var graph = root.querySelector('[data-sc-public-graph]');
    var search = root.querySelector('[data-sc-public-search]');
    var kind = root.querySelector('[data-sc-public-type]');
    graph.replaceChildren();
    var articles = Array.isArray(result.articles.items) ?
      result.articles.items.slice(0, MAX_ITEMS).map(normalizeArticle).filter(Boolean) : [];
    var objects = Array.isArray(result.objects.items) ?
      result.objects.items.slice(0, MAX_ITEMS).map(normalizeObject).filter(Boolean) : [];
    var total = articles.length + objects.length;

    function showApprovedRecords() {
      var filtered = filterApprovedItems(articles, objects,
        search ? search.value : "", kind ? kind.value : "all");
      var visible = filtered.articles.length + filtered.objects.length;
      list.replaceChildren();
      statusText(summary, "Persistent Research database connected · showing " + visible +
        " of " + total + " reviewed records loaded (up to 50 articles and 50 objects). " +
        "The original editorial preview above is separate and not counted.");
      if (!total) {
        list.append(make("p",
          "The Research database is connected, but no records have been approved for public release.",
          "scrh-api-empty"));
      } else if (!visible) {
        list.append(make("p", "No approved records match this search.", "scrh-api-empty"));
      }
      for (var i = 0; i < filtered.articles.length; i++) {
        var article = filtered.articles[i];
        var item = make("article", null, "scrh-api-item");
        var title = make("h3", article.title || article.slug);
        // Never advertise a public detail URL until the WordPress route is certified.
        item.append(title, make("p", article.summary),
          make("small", "Article · " + article.area + " · " + article.state));
        list.append(item);
      }
      for (var j = 0; j < filtered.objects.length; j++) {
        var obj = filtered.objects[j];
        var card = make("article", null, "scrh-api-item");
        var heading = make("h3", obj.title || "Approved research object");
        card.append(heading, make("p", obj.summary),
          make("small", obj.kind + " · " + obj.authority + " · " +
            obj.data_classification + " · Rights cleared"));
        if (obj.kind === "figure" && root.ownerDocument &&
            root.ownerDocument.getElementById("sc-research-figure-" + obj.id)) {
          // Only link to a figure the Research Hub actually rendered on this page.
          var view = make("a", "View released figure", "scrh-api-figure-link");
          view.href = "#sc-research-figure-" + obj.id;
          card.dataset.scFigure = obj.id;
          card.append(view);
        }
        list.append(card);
      }
    }
    if (search) search.addEventListener("input", showApprovedRecords);
    if (kind) kind.addEventListener("change", showApprovedRecords);
    // The Research Hub figure panel renders asynchronously; refresh the figure links once it has.
    if (root.ownerDocument) root.ownerDocument.addEventListener("sc-research-figures-rendered", showApprovedRecords);
    showApprovedRecords();

    var metadata = result.graph && result.graph.schema ===
      "sc-research-public-object-graph/1.0" ? result.graph : null;
    if (metadata) {
      var verified = new Set(objects.map(function (x) { return x.id; }));
      var nodes = Array.isArray(metadata.nodes) ? metadata.nodes.filter(function (node) {
        return node && verified.has(node.id);
      }) : [];
      var edges = Array.isArray(metadata.edges) ? metadata.edges.filter(function (edge) {
        return edge && verified.has(edge.source) && verified.has(edge.target);
      }).slice(0, 30) : [];
      var figureNodes = objects.filter(function (x) {
        return x.kind === "figure" && nodes.some(function (n) { return n.id === x.id; });
      }).length;
      var graphSummary = make("p", nodes.length + " approved metadata nodes (" + figureNodes +
        " released figures) · " + edges.length + " reviewed structural relations among the loaded records. " +
        "These links are not scientific proof.");
      graphSummary.dataset.scGraphFigures = String(figureNodes);
      graph.append(graphSummary);
      if (edges.length) {
        var relationList = make("ul");
        edges.forEach(function (edge) {
          relationList.append(make("li", safeText(edge.relation, 80) +
            " · " + safeText(edge.note, 250)));
        });
        graph.append(relationList);
      }
    } else {
      graph.append(make("p", "Approved metadata graph unavailable."));
    }
  }
  // The WordPress-authored preview badge predates the public API bridge.
  // Update only its literal backend status after the persistent API has
  // been verified. Editorial record counts and private-workspace notices
  // remain explicitly distinct from the live public catalog.
  function reflectVerifiedPublicBackend(root, status) {
    if (!root || !status || status.source !== "research-postgresql" ||
        status.storage !== "ready" ||
        !Number.isSafeInteger(status.approved_articles) ||
        status.approved_articles < 0 ||
        typeof document === "undefined" ||
        typeof document.createTreeWalker !== "function") return;
    var overview = root.querySelector("#scrh-view-overview") || root;
    var walker = document.createTreeWalker(overview, NodeFilter.SHOW_TEXT);
    var current;
    var labelElement = null;
    var statusElement = null;
    while ((current = walker.nextNode())) {
      var content = (current.nodeValue || "").trim();
      if (content === "BACKEND") labelElement = current.parentElement;
      if (content === "Not connected") {
        statusElement = current.parentElement;
        current.nodeValue = current.nodeValue.replace(
          "Not connected", "Connected — public API");
      } else if (/ integration pending$/.test(content)) {
        // The live page names its host before "integration pending"; match the stable suffix only.
        current.nodeValue = "PostgreSQL ready · " + status.approved_articles + " approved articles";
      } else if (content === "Connected — public API") {
        statusElement = current.parentElement;
      }
    }
    // Turn only the verified BACKEND card's indicator green; do not change
    // the red Sustainable Catalyst brand accents elsewhere on the page.
    if (labelElement && statusElement) {
      var ancestor = labelElement;
      for (var depth = 0; ancestor && depth < 5; depth++, ancestor = ancestor.parentElement) {
        if (typeof ancestor.contains === "function" &&
            ancestor.contains(statusElement)) {
          if (ancestor.classList) ancestor.classList.add("scrh-backend-connected");
          if (labelElement.classList) labelElement.classList.add("scrh-backend-connected-label");
          if (statusElement.classList) statusElement.classList.add("scrh-backend-connected-value");
          break;
        }
      }
    }
  }

  async function connect(root) {
    // No API calls from a public preview unless a reviewed deployment explicitly
    // activates this marker after routing and origin checks.
    if (!root || root.dataset.scPublicApi !== "enabled") return {status: "disabled"};
    if (root.dataset.scBridgeInitialized === "yes") return {status: "already_initialized"};
    root.dataset.scBridgeInitialized = "yes";
    var wrapper = make("section", null, "scrh-api-panel");
    wrapper.setAttribute("aria-label", "Database-backed approved Research catalog");
    wrapper.append(make("h2", "Reviewed Research metadata"));
    var summary = make("p", "Checking the public Research API…", "scrh-api-status");
    summary.setAttribute("role", "status");
    summary.setAttribute("aria-live", "polite");
    summary.dataset.scPublicSummary = "true";
    var filters = make("div", null, "scrh-api-controls");
    var searchLabel = make("label", "Find approved research");
    var searchInput = make("input");
    searchInput.type = "search";
    searchInput.maxLength = 120;
    searchInput.placeholder = "Search reviewed titles and summaries";
    searchInput.dataset.scPublicSearch = "true";
    searchLabel.append(searchInput);
    var typeLabel = make("label", "Record type");
    var typeSelect = make("select");
    typeSelect.dataset.scPublicType = "true";
    [["all", "All reviewed records"], ["articles", "Articles"], ["objects", "Research objects"],
      ["figures", "Released figures"]]
      .forEach(function (entry) {
        var option = make("option", entry[1]);
        option.value = entry[0];
        typeSelect.append(option);
      });
    typeLabel.append(typeSelect);
    filters.append(searchLabel, typeLabel);
    var list = make("div", null, "scrh-api-list");
    list.dataset.scPublicList = "true";
    var graph = make("div", null, "scrh-api-graph");
    graph.dataset.scPublicGraph = "true";
    wrapper.append(summary, filters, list, graph);
    // Additive panel: NEVER clear the old #scrh-home-articles table.
    var anchor = root.querySelector("#scrh-view-overview") || root;
    anchor.append(wrapper);
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, TIMEOUT_MS);
    try {
      // WordPress exposes a deliberately narrower REST facade with different
      // route names than the upstream Research API. Never send an upstream
      // path such as /public/status to the WordPress REST namespace.
      var paths = API_PREFIX === "/api/research/v1" ?
        ["/public/status", "/articles", "/objects?limit=50", "/graph/metadata?limit=50"] :
        ["/status", "/articles", "/objects", "/graph"];
      var data = await Promise.all(paths.map(function (p) {
        return getPublicJSON(p, controller.signal);
      }));
      if (!data[0] || data[0].schema !== "sc-research-public-backend-status/1.0" ||
          data[0].source !== "research-postgresql" || data[0].storage !== "ready" ||
          !Number.isSafeInteger(data[0].approved_articles) || data[0].approved_articles < 0 ||
          !Number.isSafeInteger(data[0].approved_areas) || data[0].approved_areas < 0 ||
          !data[1] || !Array.isArray(data[1].items) ||
          !data[2] || data[2].schema !== "sc-research-public-objects/1.0" ||
          !data[3] || data[3].schema !== "sc-research-public-object-graph/1.0") {
        throw new Error("Research backend is not verified as database-connected");
      }
      // No fallback catalog is accepted as proof of a connected Research database.
      render(wrapper, {articles: data[1], objects: data[2], graph: data[3]});
      reflectVerifiedPublicBackend(root, data[0]);
      root.dataset.scBridgeStatus = "connected";
      return {status: "connected"};
    } catch (_) {
      root.dataset.scBridgeStatus = "unavailable";
      statusText(summary,
        "Research backend unavailable or not certified. The editorial preview remains accessible; no private records were requested.");
      return {status: "unavailable"};
    } finally {
      clearTimeout(timeout);
    }
  }
  var api = Object.freeze({
    connect: connect, normalizeArticle: normalizeArticle,
    normalizeObject: normalizeObject, filterApprovedItems: filterApprovedItems,
    reflectVerifiedPublicBackend: reflectVerifiedPublicBackend
  });
  // Export for browser and isolated Node contract tests. No side effects in tests.
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (host) host.SustainableCatalystResearchBridge = api;
  if (typeof document !== "undefined") {
    function boot() {
      var root = document.getElementById("sc-research-v2");
      if (!root) return;
      // Explicit admin-controlled plugin configuration supersedes old HTML flags.
      if (wpConfig) root.dataset.scPublicApi =
        (wpConfig.enabled === true && API_PREFIX !== "/api/research/v1") ? "enabled" : "disabled";
      connect(root);
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
    else boot();
  }
})(typeof window !== "undefined" ? window : null);
