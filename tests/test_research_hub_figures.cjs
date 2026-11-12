// SC-FIG-03B: WordPress Research Hub figures (no browser; fetch, DOM and renderer are stubbed).
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const HUB = require(path.join(__dirname, "..", "research-web", "assets", "research-hub-figures.js"));
const bridge = require(path.join(__dirname, "..", "research-web", "assets", "research-workbench-bridge.js"));
const FIGURES = path.join(__dirname, "..", "research-web", "assets", "research-figures.js");

const ID = "0f8fad5b-d9cb-469f-a165-70867728950e", ID2 = "1f8fad5b-d9cb-469f-a165-70867728950e";
const SHA = "a".repeat(64), VIEW = "b".repeat(64);
const PREFIX = "/wp-json/sc-research/v1", VENDOR = "/wp-content/plugins/sustainable-catalyst-lab/assets/";
const CONFIG = {enabled: true, apiPrefix: PREFIX,
  figures: {enabled: true, channel: "staging-synthetic", labRelease: "0.163.1.9", rendererBase: VENDOR}};

function freshFigures() {
  delete require.cache[require.resolve(FIGURES)];
  return require(FIGURES);
}
function fakeDoc() {
  const head = [];
  const make = tag => ({tag, attrs: {}, children: [], dataset: {}, textContent: "", hidden: false,
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; }, removeAttribute(k) { delete this.attrs[k]; },
    append(...n) { this.children.push(...n); for (const x of n) if (x && x.onerror) setTimeout(() => x.onerror(), 0); },
    appendChild(n) { this.children.push(n); return n; },
    querySelector() { return null; }, querySelectorAll() { return []; }});
  const doc = {createElement: make, head: make("head"), events: [], dispatchEvent(e) { this.events.push(e); }};
  doc.headNodes = head;
  return doc;
}
function text(node) { return [node.textContent].concat((node.children || []).map(text)).join(" "); }
function json(body) { return {ok: true, headers: {get: () => "application/json; charset=UTF-8"}, json: async () => body}; }
function obj(over) {
  return Object.assign({id: ID, title: "Synthetic decay", kind: "figure", article: "syn-article", area: "syn-area",
    review_status: "approved-public", rights: "cleared"}, over || {});
}

test("configure accepts only the WordPress facade and a same-origin renderer directory", () => {
  const F = freshFigures();
  for (const bad of [{endpoint: "https://evil.example/wp-json/sc-research/v1", vendor: VENDOR},
    {endpoint: "/api/research/v1", vendor: VENDOR}, {endpoint: PREFIX + "/x", vendor: VENDOR},
    {endpoint: PREFIX, vendor: "//evil.example/assets/"}, {endpoint: PREFIX, vendor: "https://cdn.example/"},
    {endpoint: PREFIX, vendor: "/wp-content/../x/"}, {endpoint: PREFIX, vendor: "/no-trailing"}, {}]) {
    assert.throws(() => F.configure(bad), JSON.stringify(bad));
  }
  assert.deepEqual(F.configure({endpoint: PREFIX, vendor: VENDOR}), {endpoint: PREFIX, vendor: VENDOR});
});

test("after configure, packages must point at the facade's own figure file routes", () => {
  const F = freshFigures();
  F.configure({endpoint: PREFIX, vendor: VENDOR});
  const base = PREFIX + "/figures/" + ID + "/files/";
  const pkg = files => ({schema: "sc-research-public-figure/2.0", id: ID, release: {bundleSha256: SHA},
    releaseContext: {authority: "sc-research", bundleSha256: SHA}, files});
  assert.deepEqual(F.packageErrors(pkg([{role: "bundle", sha256: SHA, url: base + SHA}]), ID), []);
  assert.ok(F.packageErrors(pkg([{role: "bundle", sha256: SHA, url: "/api/research/v1/objects/" + ID + "/figure/files/" + SHA}]), ID).length);
});

test("renderItems fetches the facade package and loads the renderer only from the Lab plugin directory", async () => {
  const F = freshFigures();
  F.configure({endpoint: PREFIX, vendor: VENDOR});
  const calls = [];
  const base = PREFIX + "/figures/" + ID + "/files/";
  const fetch = async (url, init) => {
    calls.push(url);
    assert.deepEqual(init, {credentials: "omit", cache: "no-store", redirect: "error"});
    if (url === PREFIX + "/figures/" + ID) return json({schema: "sc-research-public-figure/2.0", id: ID,
      release: {bundleSha256: SHA, articleRevision: 2}, releaseContext: {authority: "sc-research", bundleSha256: SHA},
      files: [{role: "bundle", sha256: SHA, url: base + SHA}, {role: "view", sha256: VIEW, url: base + VIEW}], citations: [], missingViews: []});
    return {ok: true, arrayBuffer: async () => new ArrayBuffer(1)};
  };
  const doc = fakeDoc(), host = doc.createElement("div");
  const out = await F.renderItems(host, [{id: ID, title: "T"}], {fetch, document: doc});
  assert.equal(calls[0], PREFIX + "/figures/" + ID);
  assert.ok(calls.every(u => u.startsWith(PREFIX + "/figures/" + ID)), calls.join(","));
  const scripts = doc.head.children;
  assert.ok(scripts.length >= 1);
  for (const s of scripts) {
    assert.ok((s.src || s.href).startsWith(VENDOR), s.src || s.href);
    assert.match(s.integrity, /^sha384-/);
  }
  // The stub renderer files fail to load, so the figure is not shown.
  assert.deepEqual(out, {state: "done", figures: ["unavailable"]});
  assert.equal(host.children[0].children[0].attrs["data-figure-key"], ID);
});

test("figureConfig refuses anything but the staging-synthetic channel and same-origin paths", () => {
  assert.deepEqual(HUB.figureConfig(CONFIG), {apiPrefix: PREFIX, rendererBase: VENDOR, channel: "staging-synthetic", labRelease: "0.163.1.9"});
  const off = [null, {}, Object.assign({}, CONFIG, {enabled: false}), Object.assign({}, CONFIG, {figures: undefined}),
    Object.assign({}, CONFIG, {figures: Object.assign({}, CONFIG.figures, {channel: "public"})}),
    Object.assign({}, CONFIG, {figures: Object.assign({}, CONFIG.figures, {enabled: false})}),
    Object.assign({}, CONFIG, {apiPrefix: "https://evil.example/wp-json/sc-research/v1"}),
    Object.assign({}, CONFIG, {figures: Object.assign({}, CONFIG.figures, {rendererBase: "https://cdn.example/x/"})}),
    Object.assign({}, CONFIG, {figures: Object.assign({}, CONFIG.figures, {rendererBase: "/a/../b/"})})];
  for (const c of off) assert.equal(HUB.figureConfig(c), null, JSON.stringify(c));
});

test("groupFigures keeps only approved, cleared, released figure objects, grouped by article", () => {
  const groups = HUB.groupFigures([obj(), obj({id: ID2, article: "other-article", area: "a2"}),
    obj({kind: "dataset", id: ID2}), obj({rights: "pending"}), obj({review_status: "draft"}), obj({id: "x"}),
    obj({article: "../evil"}), null]);
  assert.deepEqual(groups, [{article: "syn-article", area: "syn-area", items: [{id: ID, title: "Synthetic decay"}]},
    {article: "other-article", area: "a2", items: [{id: ID2, title: "Synthetic decay"}]}]);
  const many = Array.from({length: 20}, (_, i) => obj({id: ID.slice(0, -2) + String(i).padStart(2, "0")}));
  assert.equal(HUB.groupFigures(many)[0].items.length, 12);
});

function root(doc) {
  const node = doc.createElement("div");
  return node;
}

test("mount requests nothing without a valid figure configuration", async () => {
  const calls = [];
  const doc = fakeDoc();
  const r = root(doc);
  const out = await HUB.mount(r, {enabled: true, apiPrefix: PREFIX}, {document: doc, fetch: async u => { calls.push(u); }, renderer: {}});
  assert.deepEqual(out, {state: "disabled"});
  assert.deepEqual(calls, []);
  assert.equal(r.children.length, 0);
});

test("mount hides the panel when the upstream channel is off or differs, and never lists objects", async () => {
  for (const status of [{schema: "sc-research-public-figure-status/1.0", enabled: false},
    {schema: "sc-research-public-figure-status/1.0", enabled: true, channel: "production"}]) {
    const calls = [], doc = fakeDoc(), r = root(doc);
    const out = await HUB.mount(r, CONFIG, {document: doc, fetch: async u => { calls.push(u); return json(status); }, renderer: {}});
    assert.deepEqual(out, {state: "disabled"});
    assert.deepEqual(calls, [PREFIX + "/figures/status"]);
    assert.equal(r.children[0].hidden, true);
  }
});

test("mount renders released figures by article through the shared renderer, then announces them", async () => {
  const calls = [], rendered = [];
  const renderer = {
    configure(o) { rendered.push(["configure", o]); },
    async renderItems(host, items) { rendered.push(["items", items.map(i => i.id)]); return {state: "done", figures: items.map(() => "rendered")}; },
  };
  const fetch = async url => {
    calls.push(url);
    if (url.endsWith("/figures/status")) return json({schema: "sc-research-public-figure-status/1.0", enabled: true, channel: "staging-synthetic", label: "Synthetic staging release. Not published research."});
    return json({schema: "sc-research-public-objects/1.0", items: [obj(), obj({id: ID2, kind: "dataset"})]});
  };
  const doc = fakeDoc(), r = root(doc);
  const out = await HUB.mount(r, CONFIG, {document: doc, fetch, renderer});
  assert.deepEqual(out, {state: "rendered", figures: ["rendered"]});
  assert.deepEqual(calls, [PREFIX + "/figures/status", PREFIX + "/objects"]);
  assert.deepEqual(rendered, [["configure", {endpoint: PREFIX, vendor: VENDOR}], ["items", [ID]]]);
  const t = text(r);
  for (const s of ["Released scientific figures", "Synthetic staging release. Not published research.", "Article: syn-article", "Research area: syn-area", "1 of 1"]) {
    assert.ok(t.includes(s), s);
  }
  assert.equal(r.dataset.scFigures, "rendered");
});

test("mount shows an empty state once every figure is withdrawn, and loads no renderer", async () => {
  let configured = false;
  const renderer = {configure() { configured = true; }, async renderItems() { throw new Error("must not render"); }};
  const fetch = async url => url.endsWith("/figures/status")
    ? json({schema: "sc-research-public-figure-status/1.0", enabled: true, channel: "staging-synthetic"})
    : json({schema: "sc-research-public-objects/1.0", items: [obj({kind: "dataset"})]});
  const doc = fakeDoc(), r = root(doc);
  assert.deepEqual(await HUB.mount(r, CONFIG, {document: doc, fetch, renderer}), {state: "none"});
  assert.equal(configured, false);
  assert.ok(text(r).includes("No scientific figures are currently released."));
});

test("mount reports unavailable on an API failure and shows no figures", async () => {
  const fetch = async () => ({ok: false, status: 502, headers: {get: () => "application/json"}});
  const doc = fakeDoc(), r = root(doc);
  assert.deepEqual(await HUB.mount(r, CONFIG, {document: doc, fetch, renderer: {}}), {state: "unavailable"});
  assert.equal(r.dataset.scFigures, "unavailable");
});

test("catalog filter 'figures' shows only released figure objects", () => {
  const objects = [bridge.normalizeObject(obj()), bridge.normalizeObject(obj({id: ID2, kind: "dataset"}))];
  const out = bridge.filterApprovedItems([{title: "A", summary: "", area: "x"}], objects, "", "figures");
  assert.deepEqual(out.articles, []);
  assert.deepEqual(out.objects.map(o => o.id), [ID]);
  assert.equal(objects[0].area, "syn-area");
});
