// SC-FIG-02B / SC-FIG-03A: public figure loader contract (no browser; renderer and fetch are stubbed).
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const F = require(path.join(__dirname, "..", "research-web", "assets", "research-figures.js"));

const ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const SHA = "a".repeat(64), VIEW = "b".repeat(64);
const BASE = "/api/research/v1/objects/" + ID + "/figure/files/";
function pkg(over) {
  return Object.assign({
    schema: "sc-research-public-figure/2.0", id: ID, title: "T", figureId: "SYN-X", version: 1,
    evidenceClass: "modelled", measurementStatus: "computed-from-specified-inputs", dataClassification: "synthetic",
    classificationNotice: "Modelled output computed from specified inputs. Not a measurement.",
    release: {channel: "staging-synthetic", label: "Synthetic staging release. Not published research.", bundleSha256: SHA,
      registryVersion: "v1+" + SHA.slice(0, 16), approval: "Approved", reproducibility: "synthetic-fixture-tested",
      articleRevision: 2, releasedAt: "2026-11-11T12:00:00+00:00", provenanceSha256: "c".repeat(64)},
    releaseContext: {authority: "sc-research", state: "released", label: "Synthetic staging release. Not published research.",
      bundleSha256: SHA, articleRevision: 2, releasedAt: "2026-11-11T12:00:00+00:00"},
    units: "t C ha-1", uncertainty: "band", citations: [{sourceId: "s", provider: "p", identityStatus: "x", qualityStatus: "pending", licenceStatus: "pending"}],
    files: [{role: "bundle", sha256: SHA, url: BASE + SHA}, {role: "view", sha256: VIEW, url: BASE + VIEW}], missingViews: [],
  }, over || {});
}

test("lock block lists the renderer files in load order with integrity", () => {
  const roles = F.RENDERER_LOCK.files.map(f => f.role);
  assert.deepEqual(roles, ["plotly", "vega", "vega-lite", "vega-interpreter", "maplibre", "fig02a-policy", "fig02b-vega-lite",
    "fig04-maplibre-contract", "fig04-maplibre", "renderer", "maplibre-style", "fig02a-style", "renderer-style", "fig04-style", "maplibre-worker"]);
  assert.deepEqual(F.RENDERER_LOCK.files.filter(f => f.kind === "worker").map(f => f.role), ["maplibre-worker"]);
  for (const f of F.RENDERER_LOCK.files) assert.match(f.integrity, /^sha384-[A-Za-z0-9+/]+=*$/);
  assert.match(F.RENDERER_LOCK.labCommit, /^[0-9a-f]{40}$/);
});

test("a well-formed package passes", () => assert.deepEqual(F.packageErrors(pkg(), ID), []));

test("packages that point elsewhere or do not match the release are refused", () => {
  const cases = [
    pkg({schema: "other"}),
    pkg({id: "11111111-1111-4111-8111-111111111111"}),
    pkg({files: [{role: "bundle", sha256: SHA, url: "https://example.org/" + SHA}]}),
    pkg({files: [{role: "bundle", sha256: SHA, url: "/api/research/v1/objects/" + "1".repeat(8) + "/figure/files/" + SHA}]}),
    pkg({files: [{role: "view", sha256: VIEW, url: BASE + VIEW}]}),
    pkg({files: [{role: "bundle", sha256: VIEW, url: BASE + VIEW}]}),
    pkg({files: Array.from({length: 13}, () => ({role: "view", sha256: VIEW, url: BASE + VIEW}))}),
    pkg({release: {bundleSha256: "nope"}}),
    pkg({releaseContext: undefined}),
    pkg({releaseContext: {authority: "lab", bundleSha256: SHA}}),
    pkg({releaseContext: {authority: "sc-research", bundleSha256: VIEW}}),
  ];
  for (const c of cases) assert.ok(F.packageErrors(c, ID).length > 0, JSON.stringify(c).slice(0, 80));
});

// Minimal DOM: enough for text-only element building.
function fakeDoc() {
  const make = tag => ({tag, attrs: {}, children: [], textContent: "",
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; }, removeAttribute(k) { delete this.attrs[k]; },
    append(...n) { this.children.push(...n); }, appendChild(n) { this.children.push(n); return n; }});
  return {createElement: make, head: make("head")};
}
function text(node) { return [node.textContent].concat((node.children || []).map(text)).join(" "); }

test("release panel shows channel, classification, hash and citations as text", () => {
  const box = F.releasePanel(fakeDoc(), pkg({citations: [{sourceId: "<img src=x onerror=alert(1)>", provider: "p", identityStatus: "i", qualityStatus: "q", licenceStatus: "l"}]}));
  const t = text(box);
  for (const s of ["Synthetic staging release. Not published research.", "Not a measurement", SHA, "synthetic", "t C ha-1",
    "Article revision 2", "c".repeat(64), "<img src=x onerror=alert(1)>"]) assert.ok(t.includes(s), s);
});

test("renderFigures does nothing when the server has figures off, or for a bad slug", async () => {
  const calls = [];
  const fetch = async url => { calls.push(url); return {ok: true, json: async () => ({enabled: false})}; };
  const host = fakeDoc().createElement("div");
  assert.deepEqual(await F.renderFigures(host, "a-slug", {fetch, document: fakeDoc()}), {state: "disabled"});
  assert.deepEqual(calls, ["/api/research/v1/public/figures/status"]);
  assert.deepEqual(await F.renderFigures(host, "../x", {fetch, document: fakeDoc()}), {state: "skipped"});
  assert.equal(host.children.length, 0);
});

test("a figure whose package is malformed is reported unavailable and never fetched further", async () => {
  const calls = [];
  const fetch = async (url, init) => {
    calls.push(url);
    assert.deepEqual(init, {credentials: "omit", cache: "no-store", redirect: "error"});
    if (url.endsWith("/status")) return {ok: true, json: async () => ({enabled: true})};
    if (url === "/api/research/v1/articles/a-slug/content") return {ok: true, json: async () => ({slug: "a-slug", references: [
      {role: "figure", kind: "figure", status: "available", publicKey: ID, title: "T"},
      {role: "figure", kind: "figure", status: "unavailable"}, {role: "result", kind: "result", status: "available", publicKey: ID}]})};
    return {ok: true, json: async () => pkg({files: [{role: "bundle", sha256: SHA, url: "https://evil.example/" + SHA}]})};
  };
  const doc = fakeDoc(), host = doc.createElement("div");
  const out = await F.renderFigures(host, "a-slug", {fetch, document: doc});
  assert.deepEqual(out, {state: "done", figures: ["unavailable"]});
  assert.ok(!calls.some(u => u.includes("evil")));
  assert.equal(doc.head.children.length, 0, "renderer is not loaded for a refused package");
});

test("figures come only from the live revision's references; a withdrawn article shows none", async () => {
  const calls = [];
  const fetch = async url => {
    calls.push(url);
    if (url.endsWith("/status")) return {ok: true, json: async () => ({enabled: true})};
    return {ok: false, status: 410, json: async () => ({status: "withdrawn"})};
  };
  const doc = fakeDoc(), host = doc.createElement("div");
  assert.deepEqual(await F.renderFigures(host, "a-slug", {fetch, document: doc}), {state: "unavailable"});
  assert.deepEqual(calls, ["/api/research/v1/public/figures/status", "/api/research/v1/articles/a-slug/content"]);
  assert.equal(host.children.length, 0);
});

test("the renderer receives Research's release record for the exact bundle and the locked MapLibre worker, nothing else", () => {
  // The release record, plus the locked MapLibre worker location and SHA-384 (same origin, never a page script).
  const lock = JSON.parse(fs.readFileSync(path.join(__dirname, "../research-web/assets/vendor/sc-figure-renderer/RENDERER_LOCK.json"), "utf8"));
  const worker = lock.files.find(f => f.kind === "worker");
  assert.deepEqual(F.renderOptions(pkg()), {releaseContext: pkg().releaseContext,
    maplibre: {workerUrl: "/assets/vendor/sc-figure-renderer/" + worker.path, workerIntegrity: worker.integrity}});
  assert.match(worker.integrity, /^sha384-/);
  assert.equal(F.renderOptions(pkg()).releaseContext.bundleSha256, SHA);
});
