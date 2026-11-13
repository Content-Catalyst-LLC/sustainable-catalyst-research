"use strict";
// RH-08D-05: Discover ↔ governed intake. Offline, with a minimal fake DOM and a recording fake fetch.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const intake = require("../research-web/assets/research-discovery-intake.js");
const discovery = require("../research-web/assets/research-discovery.js");

const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures/rh08d02/discovery-search-synthetic.json"), "utf8"));

class Node {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.nodeType = 1; this.children = []; this.attrs = {}; this.listeners = {};
    this.className = ""; this._text = ""; this.hidden = false; this.disabled = false;
  }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(""); }
  set textContent(value) { this._text = String(value); this.children = []; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatch(type) { (this.listeners[type] || []).forEach(fn => fn({target: this, preventDefault() {}})); }
}
const doc = {createElement: tag => new Node(tag)};
function all(node, predicate, out = []) {
  if (predicate(node)) out.push(node);
  node.children.forEach(c => all(c, predicate, out));
  return out;
}
const byClass = (node, cls) => all(node, n => n.className.split(" ").includes(cls));
const byTag = (node, tag) => all(node, n => n.tagName === tag.toUpperCase());
const buttonNamed = (node, text) => byTag(node, "button").find(b => b.textContent === text);
const tick = () => new Promise(resolve => setImmediate(resolve));

const CREDENTIAL = "synthetic-staging-credential"; // a fixed test value, not a secret
const OWNER = "workspace:owner:synthetic";
const ID_A = "11111111-1111-4111-8111-111111111111";
const ID_B = "22222222-2222-4222-8222-222222222222";
const HANDLE = "run-AAAAAAAAAAAAAAAAAAAAAAAA";

function fakeFetch(routes) {
  const calls = [];
  const fn = (url, init = {}) => {
    calls.push({url, init});
    const key = (init.method || "GET") + " " + url.replace("/api/research/v1", "");
    const hit = Object.keys(routes).find(k => key === k || (k.endsWith("*") && key.startsWith(k.slice(0, -1))));
    const [status, body] = hit ? routes[hit](init) : [404, {detail: "Not found"}];
    return Promise.resolve({ok: status < 400, status, json: () => Promise.resolve(body)});
  };
  fn.calls = calls;
  return fn;
}

function saved(overrides = {}) {
  return {id: ID_A, candidateId: "cand-" + "a".repeat(24), status: "saved-unreviewed", title: "Synthetic weathering study",
    doi: "10.5555/synthetic.1", metadata: {publication_year: 2024, venue: "Synthetic Journal"},
    possibleDuplicateOf: [], savedBy: OWNER, associations: [], libraryProposal: null,
    review: {blockingIssues: [], retractionReported: false, conflictingFields: []},
    sources: [{provider: "crossref", providerRecordId: "10.5555/synthetic.1", retrievedAt: "2026-11-12T00:00:00Z",
      recordSha256: "f".repeat(64)}], ...overrides};
}
function controller(owner = true, log = []) {
  const record = name => (...args) => { log.push([name, ...args]); return Promise.resolve({}); };
  return {signedIn: () => true, isOwner: () => owner, save: record("save"), dismiss: record("dismiss"),
    associate: record("associate"), dissociate: record("dissociate"), propose: record("propose"), log};
}

test("the client sends session credentials only as headers, never with cookies or caching", async () => {
  const f = fakeFetch({"GET /discovery/intake/candidates": () => [200, {items: []}]});
  const client = intake.createClient(f);
  assert.throws(() => client.setCredentials({token: "", owner: OWNER}), /staging credential/);
  client.setCredentials({token: " " + CREDENTIAL + " ", owner: OWNER,
    actor: "workspace:editor:synthetic", attestation: '{"grantee": "workspace:editor:synthetic"}'});
  await client.list();
  const {url, init} = f.calls[0];
  assert.equal(url, "/api/research/v1/discovery/intake/candidates");
  assert.equal(init.credentials, "omit");
  assert.equal(init.cache, "no-store");
  assert.equal(init.headers.Authorization, "Bearer " + CREDENTIAL);
  assert.equal(init.headers["X-SC-Owner-Scope"], OWNER);
  assert.equal(init.headers["X-SC-Actor-Scope"], "workspace:editor:synthetic");
  assert.equal(Buffer.from(init.headers["X-SC-Editor-Attestation"], "base64url").toString(),
    '{"grantee":"workspace:editor:synthetic"}');
  assert.ok(!url.includes(CREDENTIAL) && init.body === undefined);
  assert.equal(client.isOwner(), false);
  client.clear();
  assert.equal(client.signedIn(), false);
  await client.list().catch(() => {});
  assert.equal(f.calls[1].init.headers.Authorization, undefined);
});

test("saved ids and article slugs are validated before any request is made", () => {
  const f = fakeFetch({});
  const client = intake.createClient(f);
  client.setCredentials({token: CREDENTIAL, owner: OWNER});
  assert.throws(() => client.dismiss("../../editorial/articles"), /saved-source id/);
  assert.throws(() => client.associate(ID_A, "x/../y"), /article identifier/);
  assert.equal(f.calls.length, 0);
  client.associate(ID_A, "soil-carbon").catch(() => {});
  assert.equal(f.calls[0].url, "/api/research/v1/discovery/intake/candidates/" + ID_A + "/associations/soil-carbon");
});

test("errors are reported in plain language, and expired search handles ask for a new search", async () => {
  assert.match(intake.errorText(401), /not accepted/);
  assert.match(intake.errorText(409), /concurrent change/);
  assert.match(intake.errorText(503), /not enabled/);
  assert.match(intake.errorText(500, "<script>x</script>"), /could not be completed/);
  const f = fakeFetch({"POST /discovery/intake/candidates": () =>
    [404, {detail: "This search result is no longer available; search again before saving"}]});
  const client = intake.createClient(f);
  client.setCredentials({token: CREDENTIAL, owner: OWNER});
  await assert.rejects(client.save(HANDLE, "cand-" + "a".repeat(24)), /search again/);
  assert.deepEqual(JSON.parse(f.calls[0].init.body), {run_handle: HANDLE, candidate_id: "cand-" + "a".repeat(24)});
});

test("where intake is disabled the panel stays hidden and makes no further request", async () => {
  for (const status of [() => [200, {enabled: false, reason: "disabled"}], () => [503, {}]]) {
    const panel = new Node("section");
    const f = fakeFetch({"GET /discovery/intake/status": status});
    assert.equal(await intake.mount(panel, doc, f), null);
    assert.equal(panel.hidden, true);
    assert.equal(panel.children.length, 0);
    assert.equal(f.calls.length, 1);
  }
});

test("where intake is enabled, signing in loads articles and saved sources and clears the token field", async () => {
  const f = fakeFetch({
    "GET /discovery/intake/status": () => [200, {enabled: true}],
    "GET /editorial/articles": () => [200, {items: [{slug: "soil-carbon", title: "Soil carbon"}]}],
    "GET /discovery/intake/candidates": () => [200, {items: [saved()]}]
  });
  const panel = new Node("section");
  panel.hidden = true;
  const ctl = await intake.mount(panel, doc, f);
  assert.ok(ctl && panel.hidden === false);
  const inputs = byTag(panel, "input");
  const token = inputs.find(i => i.name === "token");
  assert.equal(token.type, "password");
  assert.equal(token.autocomplete, "off");
  token.value = CREDENTIAL;
  inputs.find(i => i.name === "owner").value = OWNER;
  byTag(panel, "form")[0].dispatch("submit");
  await tick(); await tick(); await tick();
  assert.equal(token.value, "");
  assert.deepEqual(f.calls.map(c => c.url.replace("/api/research/v1", "")),
    ["/discovery/intake/status", "/editorial/articles", "/discovery/intake/candidates"]);
  assert.equal(byClass(panel, "intake-saved").length, 1);
  assert.match(panel.textContent, /Signed in for this session/);
  assert.ok(!panel.textContent.includes(CREDENTIAL));
  buttonNamed(panel, "Forget credential").dispatch("click");
  assert.equal(ctl.signedIn(), false);
  assert.equal(byClass(panel, "intake-saved").length, 0);
});

test("a search card offers Save only for an intake-enabled run with a signed-in session", async () => {
  const log = [];
  const ctl = controller(true, log);
  const enabled = {...fixture, intake: {enabled: true, run_handle: HANDLE}};
  const host = new Node("div");
  discovery.renderSearch(host, enabled, doc, {intake: {controller: ctl, saveAction: intake.saveAction}});
  const cards = byClass(host, "discovery-candidate");
  const save = buttonNamed(cards[0], "Save to my sources");
  assert.ok(save);
  assert.equal(buttonNamed(cards[0], "Propose for Library review"), undefined);
  save.dispatch("click");
  assert.deepEqual(log[0], ["save", HANDLE, cards[0].id]);
  await tick();
  assert.match(byClass(cards[0], "intake-save")[0].textContent, /Saved as an unreviewed source/);

  for (const response of [fixture, {...fixture, intake: {enabled: false, run_handle: null}},
                          {...fixture, intake: {enabled: true, run_handle: "javascript:alert(1)"}}]) {
    const other = new Node("div");
    discovery.renderSearch(other, response, doc, {intake: {controller: ctl, saveAction: intake.saveAction}});
    const first = byClass(other, "discovery-candidate")[0];
    assert.equal(buttonNamed(first, "Save to my sources"), undefined);
    assert.equal(buttonNamed(first, "Propose for Library review").disabled, true);
  }
  const signedOut = {...ctl, signedIn: () => false};
  assert.equal(intake.saveAction(doc, enabled, fixture.run.candidates[0], signedOut), null);
});

test("the proposal is recorded only after the owner confirms exactly the blocking issues", () => {
  const log = [];
  const host = new Node("div");
  const item = saved({review: {blockingIssues: ["no-doi", "retracted"], retractionReported: true, conflictingFields: ["title"]}});
  intake.renderSavedList(host, [item], [], doc, controller(true, log));
  const card = byClass(host, "intake-saved")[0];
  assert.match(byClass(card, "discovery-labels")[0].textContent, /Retraction reported/);
  assert.match(card.textContent, /Records disagree on: title/);
  const submit = buttonNamed(card, "Record proposal (not sent)");
  const boxes = byTag(byClass(card, "intake-proposal-form")[0], "input");
  assert.deepEqual(boxes.map(b => b.value), ["no-doi", "retracted"]);
  assert.equal(submit.disabled, true);
  boxes[0].checked = true; boxes[0].dispatch("change");
  assert.equal(submit.disabled, true);
  boxes[1].checked = true; boxes[1].dispatch("change");
  assert.equal(submit.disabled, false);
  submit.dispatch("click");
  assert.deepEqual(log, [["propose", ID_A, ["no-doi", "retracted"]]]);
});

test("a source with unreadable metadata is listed with the others but offers no proposal", () => {
  const host = new Node("div");
  const broken = saved({id: ID_B, review: {blockingIssues: ["metadata-invalid"], retractionReported: false,
    conflictingFields: []}});
  intake.renderSavedList(host, [saved(), broken], [], doc, controller(true));
  const cards = byClass(host, "intake-saved");
  assert.equal(cards.length, 2);
  assert.equal(byTag(cards[0], "fieldset").length, 1);
  assert.equal(byTag(cards[1], "fieldset").length, 0);
  assert.equal(buttonNamed(cards[1], "Record proposal (not sent)"), undefined);
  assert.match(cards[1].textContent, /stored metadata cannot be read/);
});

test("a recorded proposal says it was not sent, and editors cannot dismiss or propose", () => {
  const host = new Node("div");
  intake.renderSavedList(host, [saved({libraryProposal: {status: "awaiting-library-endpoint", sent: false}})], [], doc,
    controller(true));
  assert.match(byClass(host, "intake-proposal-recorded")[0].textContent, /not sent/);
  assert.equal(buttonNamed(host, "Record proposal (not sent)"), undefined);

  const editor = new Node("div");
  intake.renderSavedList(editor, [saved()], [], doc, controller(false));
  assert.equal(buttonNamed(editor, "Dismiss"), undefined);
  assert.equal(buttonNamed(editor, "Record proposal (not sent)").disabled, true);
  assert.match(editor.textContent, /Only the owner can propose/);
});

test("associations link and unlink owner articles; dismissed sources keep provenance but take no new actions", () => {
  const log = [];
  const host = new Node("div");
  const articles = [{slug: "soil-carbon", title: "Soil carbon"}, {slug: "water-quality", title: "Water quality"},
    {slug: "Bad Slug", title: "ignored"}];
  const items = [saved({associations: [{entry: "soil-carbon", relation: "related-candidate"}]}),
    saved({id: ID_B, title: "Dismissed lead", status: "dismissed", possibleDuplicateOf: [ID_A]})];
  intake.renderSavedList(host, items, articles, doc, controller(true, log));
  const [active, dismissed] = byClass(host, "intake-saved");
  assert.match(byClass(active, "intake-linked")[0].textContent, /Soil carbon — related candidate, not evidence/);
  const options = byTag(active, "option").map(o => o.value);
  assert.deepEqual(options, ["water-quality"]);
  byTag(active, "select")[0].value = "water-quality";
  buttonNamed(active, "Link").dispatch("click");
  buttonNamed(active, "Unlink").dispatch("click");
  buttonNamed(active, "Dismiss").dispatch("click");
  assert.deepEqual(log, [["associate", ID_A, "water-quality"], ["dissociate", ID_A, "soil-carbon"], ["dismiss", ID_A]]);
  assert.match(byClass(host, "discovery-count")[0].textContent, /1 saved source, 1 dismissed/);
  assert.equal(byClass(dismissed, "discovery-labels")[0].children[0].textContent, "Dismissed");
  assert.equal(byTag(dismissed, "select").length, 0);
  assert.equal(buttonNamed(dismissed, "Dismiss"), undefined);
  assert.equal(byClass(dismissed, "intake-proposal-form").length, 0);
  assert.match(byClass(dismissed, "intake-duplicates")[0].textContent, /Possible duplicate of: Synthetic weathering study/);
});

test("server and provider values are rendered as text only", () => {
  const host = new Node("div");
  const hostile = "<img src=x onerror=alert(1)>";
  intake.renderSavedList(host, [saved({title: hostile, metadata: {venue: hostile}})], [{slug: "a", title: hostile}],
    doc, controller(true));
  assert.equal(byTag(host, "img").length, 0);
  assert.equal(byTag(host, "script").length, 0);
  assert.ok(byTag(host, "h3")[0].textContent === hostile);
  assert.match(byClass(host, "discovery-separation")[0].textContent, /does not make a source evidence/);
});

test("the intake script performs no storage and the page loads it before the Discover renderer", () => {
  const source = fs.readFileSync(path.join(__dirname, "../research-web/assets/research-discovery-intake.js"), "utf8");
  for (const banned of ["localStorage", "sessionStorage", "indexedDB", "document.cookie", "innerHTML", "console."]) {
    assert.ok(!source.includes(banned), banned);
  }
  const html = fs.readFileSync(path.join(__dirname, "../research-web/index.html"), "utf8");
  assert.match(html, /<section id="discovery-intake-panel"[^>]*hidden><\/section>/);
  assert.ok(html.indexOf("research-discovery-intake.js") < html.indexOf("research-discovery.js\""));
});

test("the saved status the panel expects is the one the intake API returns", () => {
  const api = fs.readFileSync(path.join(__dirname, "../research-api/app/discovery_intake.py"), "utf8");
  assert.match(api, /^SAVED, DISMISSED = "saved-unreviewed", "dismissed"$/m);
  const source = fs.readFileSync(path.join(__dirname, "../research-web/assets/research-discovery-intake.js"), "utf8");
  assert.match(source, /var SAVED = "saved-unreviewed";/);
});

test("a credential the server rejects is not kept and the previous session's sources are cleared", async () => {
  let accept = true;
  const f = fakeFetch({
    "GET /discovery/intake/status": () => [200, {enabled: true}],
    "GET /editorial/articles": () => accept ? [200, {items: []}] : [401, {detail: "unauthorised"}],
    "GET /discovery/intake/candidates": () => accept ? [200, {items: [saved()]}] : [401, {detail: "unauthorised"}]
  });
  const panel = new Node("section");
  const ctl = await intake.mount(panel, doc, f);
  const input = name => byTag(panel, "input").find(i => i.name === name);
  const signIn = async () => { byTag(panel, "form")[0].dispatch("submit"); for (let i = 0; i < 6; i++) await tick(); };
  input("token").value = CREDENTIAL; input("owner").value = OWNER;
  await signIn();
  assert.equal(byClass(panel, "intake-saved").length, 1);
  accept = false;
  input("token").value = "rejected-value"; input("actor").value = "workspace-user:editor";
  await signIn();
  assert.equal(ctl.signedIn(), false);
  assert.equal(byClass(panel, "intake-saved").length, 0);
  const status = all(panel, n => n.attrs.role === "status")[0].textContent;
  assert.match(status, /not accepted/);
  assert.match(status, /credential was not kept/);
  assert.doesNotMatch(status, /Signed in/);
});
