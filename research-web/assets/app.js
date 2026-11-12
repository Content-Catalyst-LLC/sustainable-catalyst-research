"use strict";
// The Research browser consumes only the server-approved public feed.
// API-derived values enter the DOM via textContent, never raw HTML.
const get = id => document.getElementById(id);
const endpoint = "/api/research/v1";
function message(target, value) {
  const p = document.createElement("p");
  p.className = "empty"; p.textContent = value; target.replaceChildren(p);
}
function card(target, item) {
  const el = document.createElement("article");
  el.className = "card";
  const title = document.createElement("h3");
  title.textContent = item.title;
  const description = document.createElement("p");
  description.textContent = item.summary;
  if (item.slug && item.area_slug && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(item.slug)) {
    const link = document.createElement("a");
    link.href = "/research/articles/" + encodeURIComponent(item.slug) + "/";
    link.textContent = item.title;
    title.replaceChildren(link);
  }
  el.append(title, description);
  if (item.state) {
    const status = document.createElement("small");
    status.textContent = item.state.replaceAll("-", " ");
    el.append(status);
  }
  target.append(el);
}
async function fetchJSON(url) {
  const response = await fetch(endpoint + url, {credentials: "omit", cache: "no-store"});
  if (!response.ok) throw new Error("Research API unavailable");
  return response.json();
}
async function loadArticles() {
  const target = get("article-list");
  message(target, "Loading public research records…");
  const params = new URLSearchParams();
  const query = get("query").value.trim();
  const area = get("area-filter").value;
  if (query) params.set("q", query);
  if (area) params.set("area", area);
  try {
    const result = await fetchJSON("/articles?" + params.toString());
    target.replaceChildren();
    if (!result.items.length) message(target, "No approved public research articles are available in this view.");
    for (const item of result.items) card(target, item);
  } catch (_) {
    message(target, "Public research records are temporarily unavailable.");
  }
}
async function start() {
  const target = get("areas-list");
  try {
    const result = await fetchJSON("/areas");
    target.replaceChildren();
    if (!result.items.length) message(target, "Research areas will appear when public records have been reviewed.");
    for (const item of result.items) {
      card(target, item);
      const option = document.createElement("option");
      option.value = item.slug; option.textContent = item.title;
      get("area-filter").append(option);
    }
  } catch (_) {
    message(target, "Research areas are temporarily unavailable.");
  }
  await loadArticles();
  await loadStructuralGraph();
}

async function loadStructuralGraph() {
  const visual = get("knowledge-graph-visual");
  const list = get("knowledge-graph-list");
  if (!visual || !list) return;
  const svgNS = "http://www.w3.org/2000/svg";
  try {
    const graph = await fetchJSON("/relationships");
    const nodes = Array.isArray(graph.nodes) ? graph.nodes.slice(0, 80) : [];
    const edges = Array.isArray(graph.edges) ? graph.edges.slice(0, 100) : [];
    visual.replaceChildren();
    list.replaceChildren();
    if (!nodes.length || !edges.length) {
      message(list, "No approved public research links are available yet. Source and article relationships remain private until reviewed.");
      return;
    }
    const areaNodes = nodes.filter(n => typeof n.id === "string" && n.id.startsWith("area:"));
    const articleNodes = nodes.filter(n => typeof n.id === "string" && n.id.startsWith("article:"));
    const height = Math.max(260, 58 + Math.max(areaNodes.length, articleNodes.length) * 46);
    const coords = new Map();
    areaNodes.forEach((n, i) => coords.set(n.id, [175, 65 + i * 46]));
    articleNodes.forEach((n, i) => coords.set(n.id, [650, 65 + i * 46]));
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", "0 0 840 " + height);
    svg.setAttribute("role", "presentation");
    const label = document.createElementNS(svgNS, "title");
    label.textContent = "Approved research areas linked to their public article records";
    svg.append(label);
    const all = new Map(nodes.map(n => [n.id, n]));
    let shown = 0;
    for (const edge of edges) {
      const from = coords.get(edge.source), to = coords.get(edge.target);
      if (!from || !to || edge.relation !== "contains") continue;
      shown++;
      const line = document.createElementNS(svgNS, "line");
      line.setAttribute("x1", String(from[0]));
      line.setAttribute("y1", String(from[1]));
      line.setAttribute("x2", String(to[0]));
      line.setAttribute("y2", String(to[1]));
      line.setAttribute("stroke", "#99b9b5");
      line.setAttribute("stroke-width", "1.5");
      svg.append(line);
      const area = all.get(edge.source), article = all.get(edge.target);
      if (!area || !article) continue;
      const li = document.createElement("li");
      const slug = String(article.id).replace(/^article:/, "");
      li.textContent = String(area.label) + " contains ";
      if (/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug)) {
        const link = document.createElement("a");
        link.href = "/research/articles/" + encodeURIComponent(slug) + "/";
        link.textContent = String(article.label);
        li.append(link);
      } else {
        li.append(document.createTextNode(String(article.label)));
      }
      if (!list.firstChild || list.firstChild.tagName !== "UL") {
        list.append(document.createElement("ul"));
      }
      list.querySelector("ul").append(li);
    }
    for (const node of nodes) {
      const xy = coords.get(node.id);
      if (!xy) continue;
      const circle = document.createElementNS(svgNS, "circle");
      circle.setAttribute("cx", String(xy[0]));
      circle.setAttribute("cy", String(xy[1]));
      circle.setAttribute("r", node.id.startsWith("area:") ? "11" : "8");
      circle.setAttribute("fill", node.id.startsWith("area:") ? "#b33d4f" : "#277c76");
      const name = document.createElementNS(svgNS, "text");
      name.setAttribute("x", String(xy[0] + 19));
      name.setAttribute("y", String(xy[1] + 4));
      name.setAttribute("font-size", "12");
      name.setAttribute("fill", "#254750");
      name.textContent = String(node.label).slice(0, 31);
      svg.append(circle, name);
    }
    if (shown) visual.append(svg);
    else message(list, "No approved article containment relationships are currently available.");
  } catch (_) {
    visual.replaceChildren();
    message(list, "Research graph unavailable. Previously unpublished or private connections have not been shown.");
  }
}

function addWorkspacePanel(host, id, heading, records, fallback) {
  const panel = document.createElement("section");
  panel.className = "workspace-panel"; panel.id = id;
  const title = document.createElement("h2"); title.textContent = heading; panel.append(title);
  if (!records.length) {
    const p = document.createElement("p"); p.className = "empty-data"; p.textContent = fallback;
    panel.append(p);
  } else {
    const list = document.createElement("ul");
    for (const record of records) {
      const item = document.createElement("li");
      item.textContent = record;
      list.append(item);
    }
    panel.append(list);
  }
  host.append(panel);
}
async function showWorkspace(slug) {
  for (const selector of [".hero", ".research-modes", "#discover", "#areas", "#articles", "#knowledge-graph", "#research-updates", ".methodology"]) {
    const el = document.querySelector(selector);
    if (el) el.hidden = true;
  }
  get("workspace-view").hidden = false;
  const host = get("workspace-content");
  try {
    const record = await fetchJSON("/workspaces/" + encodeURIComponent(slug));
    document.title = record.title + " | Sustainable Catalyst Research";
    get("workspace-title").textContent = record.title;
    get("workspace-summary").textContent = record.summary;
    get("workspace-state").textContent = record.area.title + " · " + record.state.replaceAll("-", " ");
    const discoverLink = get("workspace-discover-link");
    discoverLink.href = "/research/discover/?article=" + encodeURIComponent(record.slug);
    discoverLink.hidden = false;
    host.replaceChildren();
    const objects = Array.isArray(record.objects) ? record.objects : [];
    const revisions = Array.isArray(record.revisions) ? record.revisions : [];
    addWorkspacePanel(host, "workspace-overview", "Overview",
      ["Research area: " + record.area.title, "Current record: " + record.state.replaceAll("-", " ")],
      "Research overview available.");
    addWorkspacePanel(host, "workspace-evidence", "Evidence",
      objects.filter(o => o.kind === "source" || o.kind === "analysis").map(o => o.title + " (" + o.source_authority + ")"),
      "Evidence links will appear following source and public-release review.");
    addWorkspacePanel(host, "workspace-figures", "Data & figures",
      objects.filter(o => o.kind === "figure" || o.kind === "dataset").map(o => o.title + " (" + o.source_authority + ")"),
      "Reviewed graphs and data will be connected through the Research data service.");
    // SC-FIG-02B: approved figures render inside "Data & figures" when the server enables them.
    if (window.SCResearchFigures) window.SCResearchFigures.renderFigures(get("workspace-figures"), record.slug);
    addWorkspacePanel(host, "workspace-methods", "Methods",
      objects.filter(o => o.kind === "method" || o.kind === "notebook").map(o => o.title + " (" + o.source_authority + ")"),
      "Methods and reproducible notebooks are added as approved research objects.");
    addWorkspacePanel(host, "workspace-updates", "Research updates",
      revisions.map(r => "Revision " + r.version + " · " + r.summary + " · " + r.released_at),
      "This research workspace has no public revision updates yet.");
    addWorkspacePanel(host, "workspace-related", "Related work", [],
      "Related investigations and evidence-graph connections will appear as they are reviewed.");
  } catch (_) {
    get("workspace-title").textContent = "Research workspace unavailable";
    get("workspace-summary").textContent = "The research record is not approved for public viewing or is temporarily unavailable.";
    get("workspace-state").textContent = "";
    host.replaceChildren();
  }
}
// RH-08D-01: Search UI is capability-aware and does not pretend providers are live.
function discoveryMessage(text) {
  get("discovery-state").textContent = text;
}
async function initializeDiscovery() {
  try {
    const readiness = await fetchJSON("/discovery/readiness");
    const providers = await fetchJSON("/discovery/providers");
    const available = Array.isArray(providers.providers)
      ? providers.providers.filter(p => p.enabled === true).map(p => p.name)
      : [];
    if (readiness.external_retrieval_enabled !== true || !available.length) {
      discoveryMessage("Scholarly source providers are not activated yet. You can explore the interface; no external search or saving takes place.");
    } else {
      discoveryMessage("Ready to discover bibliographic source candidates via " + available.join(", ") + ".");
    }
  } catch (_) {
    discoveryMessage("The Discovery service is unavailable. No scientific results have been retrieved.");
  }
}
function renderDiscoveryCandidates(items) {
  const host = get("discovery-results");
  host.replaceChildren();
  if (!Array.isArray(items) || !items.length) {
    message(host, "No bibliographic candidates were returned. This does not imply an absence of scientific evidence.");
    return;
  }
  for (const candidate of items) {
    // Untrusted external metadata always uses textContent, not innerHTML.
    const card = document.createElement("article");
    card.className = "discovery-candidate";
    const title = document.createElement("h3");
    title.textContent = String(candidate.title || "Untitled record");
    const meta = document.createElement("p");
    meta.textContent = [
      candidate.provider, candidate.publication_date, candidate.doi,
      candidate.metadata_verification_status || "metadata unverified"
    ].filter(x => typeof x === "string" && x.trim()).join(" · ");
    const note = document.createElement("p");
    note.textContent = "Bibliographic candidate — independent evidence review required.";
    card.append(title, meta, note);
    try {
      const url = new URL(candidate.source_url);
      if (url.protocol === "https:") {
        const link = document.createElement("a");
        link.href = url.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "Inspect source record →";
        card.append(link);
      }
    } catch (_) { /* No external link for invalid source URL. */ }
    host.append(card);
  }
}
async function runDiscovery(event) {
  event.preventDefault();
  const query = get("discovery-query").value.trim();
  if (query.length < 3 || query.length > 240) {
    discoveryMessage("Enter a research question between 3 and 240 characters.");
    return;
  }
  const payload = {
    query,
    provider: get("discovery-provider").value,
    publication_year: get("discovery-year").value ? Number(get("discovery-year").value) : null
  };
  discoveryMessage("Checking available source providers…");
  try {
    const response = await fetch(endpoint + "/discovery/search", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
      credentials: "omit",
      cache: "no-store"
    });
    if (response.status === 503) {
      renderDiscoveryCandidates([]);
      discoveryMessage("Discovery adapters are not active yet. No external search was performed and nothing was saved.");
      return;
    }
    if (!response.ok) throw Error("Discovery service error");
    const data = await response.json();
    renderDiscoveryCandidates(data.candidates || []);
    discoveryMessage("Search returned bibliographic candidates only. Verify source identity, rights, and evidence before using results.");
  } catch (_) {
    renderDiscoveryCandidates([]);
    discoveryMessage("Discovery is currently unavailable. No results were generated or saved.");
  }
}
async function initializeArticleDiscoveryContext(slug) {
  try {
    // This endpoint reveals only the already-approved public article projection.
    const article = await fetchJSON("/workspaces/" + encodeURIComponent(slug));
    const context = get("discovery-context");
    context.textContent = "Public research context: " + article.title + " · " + article.area.title;
    context.hidden = false;
    // Only public article title; no manuscript, project key or owner information.
    get("discovery-query").value = article.title.slice(0, 240);
  } catch (_) {
    get("discovery-context").hidden = true;
    discoveryMessage("This article is not approved for public context. You may enter an independent research query.");
  }
}
function enableDiscovery() {
  get("discovery-form").addEventListener("submit", runDiscovery);
  initializeDiscovery();
}

document.addEventListener("DOMContentLoaded", () => {
  const match = /^\/research\/articles\/([a-z0-9]+(?:-[a-z0-9]+)*)\/?$/.exec(window.location.pathname);
  if (match) { showWorkspace(match[1]); return; }
  if (/^\/research\/discover\/?$/.test(window.location.pathname)) {
    for (const selector of [".hero", ".research-modes", "#areas", "#articles", "#knowledge-graph", "#research-updates", ".methodology"]) {
      const el = document.querySelector(selector);
      if (el) el.hidden = true;
    }
    const slug = new URLSearchParams(window.location.search).get("article");
    enableDiscovery();
    if (slug && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug)) {
      initializeArticleDiscoveryContext(slug);
    }
    return;
  }
  enableDiscovery();
  get("search-form").addEventListener("submit", event => { event.preventDefault(); loadArticles(); });
  get("area-filter").addEventListener("change", loadArticles);
  start();
});
