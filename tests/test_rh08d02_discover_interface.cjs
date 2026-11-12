"use strict";
// RH-08D-02: the Discover interface rendered from the synthetic search response, with a minimal fake DOM.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const discovery = require("../research-web/assets/research-discovery.js");

const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures/rh08d02/discovery-search-synthetic.json"), "utf8"));

class Node {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.nodeType = 1; this.children = []; this.attrs = {}; this.listeners = {};
    this.className = ""; this._text = "";
  }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(""); }
  set textContent(value) { this._text = String(value); this.children = []; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatch(type) { (this.listeners[type] || []).forEach(fn => fn({target: this})); }
}
const doc = {createElement: tag => new Node(tag)};

function all(node, predicate, out = []) {
  if (predicate(node)) out.push(node);
  node.children.forEach(c => all(c, predicate, out));
  return out;
}
const byClass = (node, cls) => all(node, n => n.className.split(" ").includes(cls));
const byTag = (node, tag) => all(node, n => n.tagName === tag.toUpperCase());

function render(response = fixture) {
  const host = new Node("div");
  const view = discovery.renderSearch(host, response, doc);
  return {host, view};
}
function clone(value) { return JSON.parse(JSON.stringify(value)); }
function cardByTitle(host, prefix) {
  return byClass(host, "discovery-candidate").find(card => byTag(card, "h3")[0].textContent.startsWith(prefix));
}

test("synthetic data is labelled and every provider outcome is reported", () => {
  const {host} = render();
  assert.match(byClass(host, "discovery-synthetic")[0].textContent, /Synthetic fixture data/);
  const outcomes = byClass(host, "discovery-outcomes")[0].children.map(li => li.textContent);
  assert.deepEqual(outcomes, ["OpenAlex: 7 records accepted, 2 not usable as candidates.",
    "Crossref: 4 records accepted, 3 not usable as candidates."]);
  assert.equal(byClass(host, "discovery-candidate").length, 9);
  assert.match(byClass(host, "discovery-count")[0].textContent, /^Showing 9 of 9 candidates\. Candidates are leads/);
});

test("every candidate is labelled unreviewed and offers source inspection", () => {
  const {host} = render();
  for (const card of byClass(host, "discovery-candidate")) {
    assert.match(card.id, /^cand-[0-9a-f]{24}$/);
    assert.equal(byClass(card, "discovery-labels")[0].children[0].textContent, "Discovered · unreviewed");
    const inspect = byClass(card, "discovery-inspect")[0];
    assert.equal(inspect.tagName, "DETAILS");
    assert.equal(byTag(inspect, "summary")[0].textContent, "Inspect source and provenance");
    assert.match(inspect.textContent, /Not evidence and not publishable/);
    assert.ok(byClass(inspect, "discovery-sources")[0].children.length >= 1);
  }
});

test("DOIs link to doi.org; provider records keep provenance hashes", () => {
  const {host} = render();
  const card = cardByTitle(host, "SYNTHETIC Basalt weathering rates");
  const doi = byTag(byClass(card, "discovery-doi")[0], "a")[0];
  assert.equal(doi.href, "https://doi.org/10.5555/sc-syn-001");
  assert.equal(doi.rel, "noopener noreferrer");
  const sources = byClass(card, "discovery-sources")[0].children;
  assert.equal(sources.length, 2);  // two OpenAlex records merged by DOI
  assert.equal(byClass(card, "discovery-sourced")[0].textContent, "From OpenAlex (2 records)");
  const record = byTag(sources[0], "a")[0];
  assert.equal(record.href, "https://openalex.org/W9000001");
  const hash = byTag(sources[0], "code")[0];
  assert.match(hash.textContent, /^[0-9a-f]{16}…$/);
  assert.match(hash.title, /^SHA-256 of the provider record: [0-9a-f]{64}$/);
  assert.match(card.textContent, /Venue — OpenAlex: Synthetic Journal of Soil Studies · OpenAlex \(record 2\): Synthetic Soil Studies/);
});

test("conflicts, retractions and missing DOIs are shown, not hidden", () => {
  const {host} = render();
  const both = cardByTitle(host, "SYNTHETIC Carbon accounting");
  assert.match(both.textContent, /Publication year — OpenAlex: 2023 · Crossref: 2022/);
  assert.match(both.textContent, /From OpenAlex and Crossref/);
  const retracted = byClass(host, "label-retracted");
  assert.equal(retracted.length, 2);
  const noDoi = byClass(host, "discovery-doi").filter(p => p.textContent === "No DOI recorded");
  assert.equal(noDoi.length, 2);
});

test("possible duplicates are cross-linked and kept as separate cards", () => {
  const {host} = render();
  const ids = new Set(byClass(host, "discovery-candidate").map(c => c.id));
  const dupLinks = byClass(host, "discovery-duplicates").flatMap(list => byTag(list, "a"));
  assert.equal(dupLinks.length, 4);  // two pairs, each linked both ways
  for (const a of dupLinks) assert.ok(ids.has(a.href.slice(1)), a.href);
  assert.ok(dupLinks.every(a => !a.target));
});

test("Library intake is visibly disabled and explains what review must resolve", () => {
  const {host} = render();
  for (const box of byClass(host, "discovery-intake")) {
    const button = byTag(box, "button")[0];
    assert.equal(button.disabled, true);
    assert.equal(button.textContent, "Propose for Library review");
    assert.equal(button.attrs["aria-describedby"], byTag(box, "p")[0].id);
    assert.match(box.textContent, /Library intake is not enabled yet\./);
  }
  assert.ok(byClass(host, "discovery-intake").some(box => /marked as retracted/.test(box.textContent)));
  assert.ok(byClass(host, "discovery-intake").some(box => /retraction status unknown/.test(box.textContent)));
});

test("filters narrow the visible candidates without another request", () => {
  const {host} = render();
  const boxes = byTag(byClass(host, "discovery-filters")[0], "input");
  assert.deepEqual(byTag(byClass(host, "discovery-filters")[0], "span").map(s => s.textContent),
    ["OpenAlex", "Crossref", "With DOI only", "Hide retracted"]);
  boxes[3].checked = true; boxes[3].dispatch("change");
  assert.equal(byClass(host, "discovery-candidate").length, 7);
  boxes[2].checked = true; boxes[2].dispatch("change");
  assert.equal(byClass(host, "discovery-candidate").length, 5);
  boxes[0].checked = false; boxes[0].dispatch("change");  // Crossref-backed candidates only
  assert.equal(byClass(host, "discovery-candidate").length, 3);
  assert.match(byClass(host, "discovery-count")[0].textContent, /^Showing 3 of 9/);
});

test("hostile provider values stay text and unsafe links are dropped", () => {
  const response = clone(fixture);
  const c = response.run.candidates[0];
  c.title = "<img src=x onerror=alert(1)>";
  c.landing_url = "javascript:alert(1)";
  c.doi = "10.5555/x\"><script>";
  c.sources[0].provider_record_id = "https://openalex.org.evil.example/W1";
  c.possible_duplicate_of = ["javascript:alert(1)"];
  const {host} = render(response);
  const card = byClass(host, "discovery-candidate")[0];
  assert.equal(byTag(card, "h3")[0].textContent, "<img src=x onerror=alert(1)>");
  const hrefs = byTag(card, "a").map(a => a.href);
  assert.ok(hrefs.every(h => /^(https:\/\/|#cand-)/.test(h)), hrefs.join(" "));
  assert.ok(!hrefs.some(h => h.includes("evil") || h.includes("javascript")));
  assert.equal(discovery.doiHref("10.5555/a b"), null);
  assert.equal(discovery.safeHttps("http://example.org/"), null);
  assert.equal(discovery.safeHttps("https://u:p@example.org/"), null);
});

test("failures, empty runs and malformed responses render truthful states", () => {
  const empty = clone(fixture);
  empty.run.candidates = [];
  empty.run.outcomes = [{provider: "openalex", status: "rate_limited", attempts: 1, records_received: 0, records_rejected: 0},
    {provider: "crossref", status: "disabled", attempts: 0, records_received: 0, records_rejected: 0}];
  const {host} = render(empty);
  assert.deepEqual(byClass(host, "discovery-outcomes")[0].children.map(li => li.textContent),
    ["OpenAlex: rate limit reached. Try again later.", "Crossref: not enabled. No request was made."]);
  assert.equal(byClass(host, "discovery-filters").length, 0);
  assert.match(byClass(host, "discovery-count")[0].textContent, /does not imply an absence of scientific evidence/);
  const bad = new Node("div");
  assert.equal(discovery.renderSearch(bad, {run: {candidates: "x"}}, doc), null);
  assert.match(bad.textContent, /could not be read\. Nothing was saved\./);
  assert.equal(discovery.outcomeText({provider: "x", status: "failed"}), "Unknown source: search failed.");
});

test("the module makes no requests and writes no HTML", () => {
  const source = fs.readFileSync(path.join(__dirname, "../research-web/assets/research-discovery.js"), "utf8");
  assert.doesNotMatch(source, /innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function|fetch\(|XMLHttpRequest|localStorage|sessionStorage/);
  const app = fs.readFileSync(path.join(__dirname, "../research-web/assets/app.js"), "utf8");
  assert.match(app, /window\.SCResearchDiscovery\.renderSearch\(get\("discovery-results"\), data, document\)/);
});
