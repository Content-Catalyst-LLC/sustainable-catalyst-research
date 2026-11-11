"use strict";
// RH-03.3: Browser adapter contract and public-preview preservation tests.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const bridge = require("../research-web/assets/research-workbench-bridge.js");
const source = fs.readFileSync(
  path.join(__dirname, "../research-web/assets/research-workbench-bridge.js"), "utf8");
const css = fs.readFileSync(
  path.join(__dirname, "../research-web/assets/research-workbench-bridge.css"), "utf8");

test("validates public article slugs and bounded display metadata", () => {
  const row = bridge.normalizeArticle({
    slug: "wexford-rock-weathering", title: "Paper", summary: "Reviewed summary",
    area_slug: "climate-and-land", state: "published", version: "v1"
  });
  assert.equal(row.slug, "wexford-rock-weathering");
  assert.equal(row.title, "Paper");
  assert.equal(bridge.normalizeArticle({slug: "../../owner-manuscript", title: "Private"}), null);
  assert.equal(bridge.normalizeArticle({slug: "NOT-LOWERCASE", title: "Unknown"}), null);
  assert.equal(bridge.normalizeArticle({title: "No slug"}), null);
  assert.equal(
    bridge.normalizeArticle({slug: "record", title: "x".repeat(1000)}).title.length, 350);
});

test("filters all unreviewed, uncleared or unkeyed research objects", () => {
  const base = {
    id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    title: "Reviewed dataset", kind: "dataset",
    rights: "cleared", review_status: "approved-public",
    authority: "lab", data_classification: "observed"
  };
  assert.equal(bridge.normalizeObject(base).title, "Reviewed dataset");
  assert.equal(bridge.normalizeObject({...base, review_status: "unreviewed"}), null);
  assert.equal(bridge.normalizeObject({...base, rights: "unknown"}), null);
  assert.equal(bridge.normalizeObject({...base, id: "private-internal-ref"}), null);
  assert.equal(bridge.normalizeObject(null), null);
});

test("does not trigger any fetch without the explicit deployment flag", async () => {
  let calls = 0;
  global.fetch = async () => { calls++; throw Error("should not fetch"); };
  const root = {dataset: {scPublicApi: "disabled"}};
  const result = await bridge.connect(root);
  assert.deepEqual(result, {status: "disabled"});
  assert.equal(calls, 0);
});

test("requires same-origin URLs, no credentials, bounded timeouts and public fields", () => {
  assert.match(source, /var API_PREFIX = "\/api\/research\/v1"/);
  assert.match(source, /credentials: "omit"/);
  assert.match(source, /redirect: "error"/);
  assert.match(source, /var TIMEOUT_MS = 6000/);
  assert.match(source, /new AbortController\(\)/);
  assert.match(source, /root.dataset.scPublicApi !== "enabled"/);
  assert.match(source, /"\/objects\?limit=50"/);
  assert.match(source, /"\/graph\/metadata\?limit=50"/);
  assert.match(source, /"\/status", "\/articles", "\/objects", "\/graph"/);
  assert.match(source, /review_status !== "approved-public"/);
  assert.match(source, /row.rights !== "cleared"/);
  assert.ok(!source.includes("innerHTML"));
  assert.ok(!source.includes("credentials: \"include\""));
  assert.ok(!source.includes("localStorage"));
  assert.ok(!source.includes("sessionStorage"));
  assert.ok(!source.includes("eval("));
  assert.ok(!source.includes("fetch(\"https:"));
});

test("never replaces the original six static preview rows or global site layout", () => {
  assert.ok(!source.includes('root.querySelector("#scrh-home-articles")'));
  assert.ok(!source.includes("document.body.innerHTML"));
  assert.ok(!source.includes("location.replace("));
  assert.ok(!css.includes("body{"));
  assert.ok(!css.includes(".ast-"));
  assert.ok(!css.includes("footer{"));
  const rules = css.match(/(?:^|\n)[^@\n{}]+{/g) || [];
  assert.ok(rules.length > 10);
  assert.ok(source.includes("root.querySelector(\"#scrh-view-overview\")"));
});

test("does not promise paid discovery or private workspace editing", () => {
  assert.ok(!source.includes("POST"));
  assert.ok(!source.includes("authorization:"));
  assert.ok(!source.includes("workspace/private"));
  assert.match(source, /editorial preview remains accessible/);
});


test("RH-PROD-06 demands PostgreSQL-backed status before marking WordPress connected", () => {
  assert.match(source, /API_PREFIX === "\/api\/research\/v1"/);
  assert.match(source, /"\/public\/status", "\/articles"/);
  assert.match(source, /"\/status", "\/articles", "\/objects", "\/graph"/);
  assert.match(source, /sc-research-public-backend-status\/1\.0/);
  assert.match(source, /source !== "research-postgresql"/);
  assert.match(source, /storage !== "ready"/);
  assert.match(source, /Number\.isSafeInteger\(data\[0\]\.approved_articles\)/);
  assert.match(source, /render\(wrapper, \{articles: data\[1\], objects: data\[2\], graph: data\[3\]\}\)/);
  assert.match(source, /root\.dataset\.scBridgeStatus = "unavailable"/);
  assert.doesNotMatch(source, /"\/private\/projects"|\/internal\/v1\/workspace\/session/);
});


test("public catalog search filters reviewed records locally without network access", () => {
  const articles = [
    {title: "Wexford weathering", summary: "Basalt measurements", area: "climate"},
    {title: "SDG metrics", summary: "Indicator analysis", area: "economics"},
  ];
  const objects = [
    {title: "Sentinel dataset", summary: "Geospatial Wexford", authority: "site-intelligence"},
    {title: "Evidence collection", summary: "Bibliography", authority: "library"},
  ];
  const query = bridge.filterApprovedItems(articles, objects, "wexford", "all");
  assert.equal(query.articles.length, 1);
  assert.equal(query.objects.length, 1);
  const articleOnly = bridge.filterApprovedItems(articles, objects, "", "articles");
  assert.equal(articleOnly.articles.length, 2);
  assert.equal(articleOnly.objects.length, 0);
  const objectOnly = bridge.filterApprovedItems(articles, objects, "LIBRARY", "objects");
  assert.equal(objectOnly.articles.length, 0);
  assert.equal(objectOnly.objects.length, 1);
  assert.equal(bridge.filterApprovedItems(articles, objects, "no match", "all").objects.length, 0);
  assert.match(source, /data-sc-public-search/);
  assert.match(source, /searchInput\.maxLength = 120/);
  assert.match(source, /addEventListener\("input", showApprovedRecords\)/);
  assert.match(source, /addEventListener\("change", showApprovedRecords\)/);
  assert.match(source, /No approved records match this search/);
  assert.match(source, /already-approved, already-loaded public metadata/);
});


test("verified PostgreSQL status replaces only obsolete preview badge text", () => {
  const previousDocument = global.document;
  const previousNodeFilter = global.NodeFilter;
  const makeNode = (value) => ({nodeValue: value});
  const original = [
    makeNode("Not connected"),
    makeNode("Contabo integration pending"),
    makeNode("6 Research records"),
    makeNode("Protected project editing is not enabled in this preview")
  ];
  const nodes = original.slice();
  global.NodeFilter = {SHOW_TEXT: 4};
  global.document = {
    createTreeWalker: (_root, type) => {
      assert.equal(type, 4);
      return {nextNode: () => nodes.shift() || null};
    }
  };
  const root = {querySelector: () => null};
  try {
    bridge.reflectVerifiedPublicBackend(root, {
      source: "research-postgresql", storage: "ready", approved_articles: 0
    });
    assert.equal(original[0].nodeValue, "Connected — public API");
    assert.equal(original[1].nodeValue, "PostgreSQL ready · 0 approved articles");
    assert.equal(original[2].nodeValue, "6 Research records");
    assert.match(original[3].nodeValue, /Protected project editing/);

    const unverified = [makeNode("Not connected")];
    global.document.createTreeWalker = () => ({
      nextNode: () => unverified.shift() || null
    });
    bridge.reflectVerifiedPublicBackend(root, {
      source: "static-preview", storage: "ready", approved_articles: 100
    });
    assert.equal(unverified[0].nodeValue, "Not connected");
  } finally {
    global.document = previousDocument;
    global.NodeFilter = previousNodeFilter;
  }
});


test("verified public backend turns only its connection indicator green", () => {
  const savedDocument = global.document;
  const savedNodeFilter = global.NodeFilter;
  const track = () => {
    const values = new Set();
    return {add: (v) => values.add(v), has: (v) => values.has(v)};
  };
  const card = {
    classList: track(), parentElement: null,
    contains(node) { return node === label || node === value || node === card; }
  };
  const label = {classList: track(), parentElement: card};
  const value = {classList: track(), parentElement: card};
  const details = {classList: track(), parentElement: card};
  const nodes = [
    {nodeValue: "BACKEND", parentElement: label},
    {nodeValue: "Not connected", parentElement: value},
    {nodeValue: "Contabo integration pending", parentElement: details}
  ];
  let index = 0;
  global.NodeFilter = {SHOW_TEXT: 4};
  global.document = {
    createTreeWalker: () => ({
      nextNode: () => nodes[index++] || null
    })
  };
  try {
    bridge.reflectVerifiedPublicBackend({querySelector: () => null}, {
      source: "research-postgresql", storage: "ready", approved_articles: 0
    });
    assert.equal(nodes[1].nodeValue, "Connected — public API");
    assert.equal(nodes[2].nodeValue, "PostgreSQL ready · 0 approved articles");
    assert.equal(card.classList.has("scrh-backend-connected"), true);
    assert.equal(label.classList.has("scrh-backend-connected-label"), true);
    assert.equal(value.classList.has("scrh-backend-connected-value"), true);
    assert.match(css, /scrh-backend-connected-label::before/);
    assert.match(css, /data-sc-bridge-status="connected"/);
    assert.doesNotMatch(css, /body.*background-color: #16803c/);
  } finally {
    global.document = savedDocument;
    global.NodeFilter = savedNodeFilter;
  }
});


test("live WordPress RH-03 red status dot and border turn green only after verified API", () => {
  assert.match(css, /data-sc-bridge-status="connected"/);
  assert.match(css, /\.scrh-hero-system-status i\s*\{/);
  assert.match(css, /\.scrh-metric-health\s*\{/);
  assert.match(css, /\.scrh-hero-system,/);
  assert.match(css, /border-left-color: #16803c !important/);
  assert.match(css, /background-color: #16803c !important/);
  // These must never color every branded red dot green.
  assert.doesNotMatch(css, /^#sc-research-v2 \.scrh-dot\s*\{/m);
});
