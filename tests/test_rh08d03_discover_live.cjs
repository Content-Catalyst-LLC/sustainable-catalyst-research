"use strict";
// RH-08D-03: the Discover interface for live (staging) runs and refused searches, with a minimal fake DOM.
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
}
const doc = {createElement: tag => new Node(tag)};

function all(node, predicate, out = []) {
  if (predicate(node)) out.push(node);
  node.children.forEach(c => all(c, predicate, out));
  return out;
}
const byClass = (node, cls) => all(node, n => n.className.split(" ").includes(cls));

function liveResponse(edit) {
  const response = JSON.parse(JSON.stringify(fixture));
  response.run.transport = "https";
  response.run.external_request_performed = true;
  response.run.started_at = "2026-11-13T09:30:00+00:00";
  if (edit) edit(response);
  return response;
}
function render(response) {
  const host = new Node("div");
  discovery.renderSearch(host, response, doc);
  return host;
}

test("live runs say where and when the metadata came from, without the synthetic banner", () => {
  const host = render(liveResponse());
  assert.equal(byClass(host, "discovery-synthetic").length, 0);
  assert.equal(byClass(host, "discovery-live")[0].textContent,
    "Live metadata from OpenAlex and Crossref, retrieved 2026-11-13T09:30:00+00:00. Results are not stored and may change when searched again.");
});

test("live banner names only the sources that were actually asked", () => {
  const host = render(liveResponse(r => {
    r.run.outcomes[1] = {provider: "crossref", status: "disabled", attempts: 0, code: "provider-not-enabled",
      records_received: 0, records_rejected: 0};
  }));
  assert.match(byClass(host, "discovery-live")[0].textContent, /^Live metadata from OpenAlex, retrieved/);
});

test("every run separates discovered metadata from Library review and Research evidence", () => {
  for (const response of [fixture, liveResponse()]) {
    const host = render(response);
    assert.match(byClass(host, "discovery-separation")[0].textContent,
      /^Discovered — unreviewed: .*not been reviewed by the Library and are not approved Research evidence\. Nothing from this search is saved\.$/);
    for (const card of byClass(host, "discovery-candidate")) {
      assert.equal(byClass(card, "discovery-labels")[0].children[0].textContent, "Discovered — unreviewed");
    }
  }
});

test("provider policy outcomes are explained", () => {
  const text = (status, code) => discovery.outcomeText({provider: "openalex", status, code, attempts: 0});
  assert.equal(text("rate_limited", "daily-budget-exhausted"), "OpenAlex: daily request limit for this service reached. Try again tomorrow.");
  assert.equal(text("rate_limited", "http-429"), "OpenAlex: rate limit reached. Try again later.");
  assert.equal(text("unavailable", "circuit-open"), "OpenAlex: paused after repeated failures. Try again in a few minutes.");
  assert.equal(text("unavailable", "run-deadline"), "OpenAlex: not searched because the search ran out of time.");
  assert.equal(text("unavailable", "timeout"), "OpenAlex: did not respond in time.");
  assert.equal(text("unavailable", "http-503"), "OpenAlex: temporarily unavailable.");
  assert.equal(text("failed", "tls-failure"), "OpenAlex: search failed.");
});

test("refused searches explain themselves and never claim a search ran", () => {
  const m = discovery.statusMessage;
  assert.match(m(503, {code: "DISCOVERY_PROVIDERS_DISABLED"}), /^Live discovery is not enabled here\. No external search was performed/);
  assert.match(m(503, {code: "DISCOVERY_PROVIDER_NOT_ENABLED"}), /^That source is not enabled here\./);
  assert.match(m(503, {code: "DISCOVERY_PROVIDER_NOT_AVAILABLE"}), /no discovery adapter yet/);
  assert.match(m(429, {code: "DISCOVERY_BUSY"}), /Try again in a few seconds\. Nothing was saved\./);
  assert.equal(m(422, [{msg: "x"}]), "Enter a research question between 3 and 240 characters.");
  assert.equal(m(500, null), "Discovery is currently unavailable. No results were generated or saved.");
  assert.equal(m(503, {code: "<script>"}), "Discovery is currently unavailable. No results were generated or saved.");
});

test("app.js routes refused searches through the module and clears old results", () => {
  const app = fs.readFileSync(path.join(__dirname, "../research-web/assets/app.js"), "utf8");
  assert.match(app, /window\.SCResearchDiscovery\.statusMessage\(response\.status, detail\)/);
  assert.match(app, /get\("discovery-results"\)\.replaceChildren\(\);/);
  assert.doesNotMatch(app, /Discovery adapters are not active yet/);
});
