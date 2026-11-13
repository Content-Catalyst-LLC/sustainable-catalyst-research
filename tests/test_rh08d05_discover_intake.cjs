"use strict";
// RH-08D-05 / SC-UP-01: Discover ↔ governed intake with Sustainable Catalyst sessions. Offline, with a minimal fake DOM
// and a recording fake fetch standing in for both Research and Workspace.
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

const WS = "http://127.0.0.1:18965"; // a loopback Workspace, as in local staging
const IDENTITY = {mode: "workspace-session", workspaceUrl: WS};
const OWNER = "workspace-user:owner-synthetic";
const OTHER_OWNER = "workspace-user:owner-b-synthetic";
const GRANT = "rag_" + "e".repeat(32);
const ID_A = "11111111-1111-4111-8111-111111111111";
const ID_B = "22222222-2222-4222-8222-222222222222";
const HANDLE = "run-AAAAAAAAAAAAAAAAAAAAAAAA";

function fakeFetch(routes) {
  const calls = [];
  const fn = (url, init = {}) => {
    calls.push({url, init});
    const key = (init.method || "GET") + " " + url.replace("/api/research/v1", "").replace(WS, "WS");
    const hit = Object.keys(routes).find(k => key === k || (k.endsWith("*") && key.startsWith(k.slice(0, -1))));
    const [status, body] = hit ? routes[hit](init) : [404, {detail: "Not found"}];
    return Promise.resolve({ok: status < 400, status, json: () => Promise.resolve(body)});
  };
  fn.calls = calls;
  return fn;
}
function session(subject = OWNER, seconds = 300) {
  const now = Date.now();
  return {schema: "sc-workspace-research-session/1.0", issuer: "sustainable-catalyst-workspace", audience: "sc-research",
    subject, authentication: "sc-identity-oidc-session", issuedAt: new Date(now).toISOString(),
    expiresAt: new Date(now + seconds * 1000).toISOString(), signature: {alg: "Ed25519", keyId: "k", value: "sig"}};
}
const decode = header => JSON.parse(Buffer.from(header.replace(/^SC-Workspace /, ""), "base64url").toString());
const settle = async () => { for (let i = 0; i < 12; i++) await tick(); };

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

test("Research calls carry only the signed Workspace session: no service credential, cookie or chosen owner", async () => {
  const f = fakeFetch({"POST WS/v1/research-access/session": () => [200, session()],
                       "GET /discovery/intake/candidates": () => [200, {items: []}]});
  const client = intake.createClient(f, undefined, IDENTITY);
  assert.equal(client.signedIn(), false);
  assert.equal((await client.signIn()).subject, OWNER);
  await client.list();
  const [ws, research] = f.calls;
  assert.equal(ws.url, WS + "/v1/research-access/session");
  assert.equal(ws.init.credentials, "include");  // the Workspace cookie goes only to Workspace
  assert.equal(research.url, "/api/research/v1/discovery/intake/candidates");
  assert.equal(research.init.credentials, "omit");
  assert.equal(research.init.cache, "no-store");
  assert.equal(decode(research.init.headers.Authorization).subject, OWNER);
  assert.ok(!/^Bearer/.test(research.init.headers.Authorization));
  assert.equal(research.init.headers["X-SC-Owner-Scope"], undefined);
  assert.equal(research.init.headers["X-SC-Actor-Scope"], undefined);
  assert.equal(client.isOwner(), true);
  await client.list();
  assert.equal(f.calls.filter(c => c.url.startsWith(WS)).length, 1, "a fresh session is reused, not re-fetched");
});

test("a session close to expiry is renewed, and a signed-out person makes no Research call", async () => {
  let issued = 0;
  const f = fakeFetch({"POST WS/v1/research-access/session": () => [200, session(OWNER, ++issued === 1 ? 10 : 300)],
                       "GET /discovery/intake/candidates": () => [200, {items: []}]});
  const client = intake.createClient(f, undefined, IDENTITY);
  await client.signIn();
  await client.list();
  assert.equal(issued, 2);
  const out = fakeFetch({"POST WS/v1/research-access/session": () => [401, {detail: "Valid Workspace identity required."}]});
  const anonymous = intake.createClient(out, undefined, IDENTITY);
  assert.equal(await anonymous.signIn(), null);
  await assert.rejects(anonymous.list(), err => err.status === 401 && /Sign in again/.test(err.message));
  assert.equal(out.calls.every(c => c.url.startsWith(WS)), true);
});

test("an editor acts for an owner only with that owner's fresh Workspace attestation", async () => {
  const attestation = {schema: "sc-workspace-research-role-attestation/1.0", grantee: "workspace-user:ed", ownerScope: OTHER_OWNER};
  const f = fakeFetch({
    "POST WS/v1/research-access/session": () => [200, session("workspace-user:ed")],
    "GET WS/v1/research-approvals/grants": () => [200, {items: [
      {grantId: GRANT, role: "editor", grantee: "workspace-user:ed", ownerScope: OTHER_OWNER, projectId: "proj-1", revokedAt: null},
      {grantId: "rag_" + "f".repeat(32), role: "editor", grantee: "workspace-user:ed", ownerScope: OWNER, projectId: "*",
        revokedAt: "2026-11-13T00:00:00Z"},
      {grantId: "rag_" + "0".repeat(32), role: "editor", grantee: "workspace-user:someone-else", ownerScope: OWNER, projectId: "*"},
      {grantId: "../../x", role: "editor", grantee: "workspace-user:ed", ownerScope: OWNER, projectId: "*"}]}],
    ["POST WS/v1/research-approvals/grants/" + GRANT + "/attestation"]: () => [200, attestation],
    "GET /discovery/intake/candidates": () => [200, {items: []}]
  });
  const client = intake.createClient(f, undefined, IDENTITY);
  await client.signIn();
  const grants = await client.editorGrants();
  assert.deepEqual(grants.map(g => g.grantId), [GRANT]);
  assert.throws(() => client.actFor({grantId: "../../x"}), /editor grant/);
  client.actFor(grants[0]);
  assert.equal(client.isOwner(), false);
  await client.list();
  await client.list();
  const research = f.calls.filter(c => c.url.startsWith("/api"));
  assert.equal(research[0].init.headers["X-SC-Owner-Scope"], OTHER_OWNER);
  assert.deepEqual(JSON.parse(Buffer.from(research[0].init.headers["X-SC-Editor-Attestation"], "base64url")), attestation);
  assert.equal(f.calls.filter(c => c.url.endsWith("/attestation")).length, 1, "reused within the write window");
  client.actFor(null);
  await client.list();
  assert.equal(f.calls.at(-1).init.headers["X-SC-Owner-Scope"], undefined);

  const revoked = fakeFetch({"POST WS/v1/research-access/session": () => [200, session("workspace-user:ed")],
    ["POST WS/v1/research-approvals/grants/" + GRANT + "/attestation"]: () => [404, {detail: "Active editor grant not found."}]});
  const late = intake.createClient(revoked, undefined, IDENTITY);
  await late.signIn();
  late.actFor(grants[0]);
  await assert.rejects(late.list(), /no longer active/);
  assert.equal(revoked.calls.some(c => c.url.startsWith("/api")), false);
});

test("saved ids and article slugs are validated before any request is made", () => {
  const f = fakeFetch({});
  const client = intake.createClient(f, undefined, IDENTITY);
  assert.throws(() => client.dismiss("../../editorial/articles"), /saved-source id/);
  assert.throws(() => client.associate(ID_A, "x/../y"), /article identifier/);
  assert.equal(f.calls.length, 0);
});

test("errors are reported in plain language, and expired search handles ask for a new search", async () => {
  assert.match(intake.errorText(401), /Sign in again/);
  assert.match(intake.errorText(403), /does not allow/);
  assert.match(intake.errorText(409), /concurrent change/);
  assert.match(intake.errorText(503), /not enabled/);
  assert.match(intake.errorText(500, "<script>x</script>"), /could not be completed/);
  const f = fakeFetch({"POST WS/v1/research-access/session": () => [200, session()],
    "POST /discovery/intake/candidates": () => [404, {detail: "This search result is no longer available; search again before saving"}]});
  const client = intake.createClient(f, undefined, IDENTITY);
  await client.signIn();
  await assert.rejects(client.save(HANDLE, "cand-" + "a".repeat(24)), /search again/);
  assert.deepEqual(JSON.parse(f.calls[1].init.body), {run_handle: HANDLE, candidate_id: "cand-" + "a".repeat(24)});
});

test("only a https Workspace origin, or http on loopback, is used for sign-in", () => {
  for (const ok of ["https://workspace.example.org", "https://workspace.example.org:8443", "http://127.0.0.1:18965",
                    "http://localhost:18965"]) {
    assert.equal(intake.workspaceOrigin({mode: "workspace-session", workspaceUrl: ok}), ok);
  }
  for (const bad of ["http://workspace.example.org", "https://workspace.example.org/path", "javascript:alert(1)",
                     "https://user@workspace.example.org", "", null]) {
    assert.equal(intake.workspaceOrigin({mode: "workspace-session", workspaceUrl: bad}), "");
  }
  assert.equal(intake.workspaceOrigin({mode: "closed", workspaceUrl: WS}), "");
});

test("the panel stays hidden where intake or Sustainable Catalyst sessions are not enabled", async () => {
  for (const status of [() => [200, {enabled: false, reason: "disabled"}], () => [503, {}],
                        () => [200, {enabled: true, identity: {mode: "closed"}}],
                        () => [200, {enabled: true}]]) {
    const panel = new Node("section");
    const f = fakeFetch({"GET /discovery/intake/status": status});
    assert.equal(await intake.mount(panel, doc, f), null);
    assert.equal(panel.hidden, true);
    assert.equal(panel.children.length, 0);
    assert.equal(f.calls.length, 1);
  }
});

test("a signed-out person sees a sign-in link and no form for any credential or identity", async () => {
  const f = fakeFetch({"GET /discovery/intake/status": () => [200, {enabled: true, identity: IDENTITY}],
                       "POST WS/v1/research-access/session": () => [401, {}]});
  const panel = new Node("section");
  await intake.mount(panel, doc, f, "https://research.example.org/discover?q=1");
  assert.equal(panel.hidden, false);
  assert.equal(byTag(panel, "input").length, 0);
  assert.equal(byTag(panel, "form").length, 0);
  const link = byClass(panel, "intake-signin-link")[0];
  assert.equal(link.textContent, "Sign in with Sustainable Catalyst");
  assert.equal(link.attrs.href, WS + "/v1/auth/login?returnTo=" + encodeURIComponent("https://research.example.org/discover?q=1"));
  assert.equal(f.calls.some(c => c.url.startsWith("/api/research/v1/discovery/intake/candidates")), false);
});

test("a signed-in person sees their own articles and sources; signing out clears them", async () => {
  const f = fakeFetch({
    "GET /discovery/intake/status": () => [200, {enabled: true, identity: IDENTITY}],
    "POST WS/v1/research-access/session": () => [200, session()],
    "GET WS/v1/research-approvals/grants": () => [200, {items: []}],
    "POST WS/v1/auth/logout": () => [200, {}],
    "GET /editorial/articles": () => [200, {items: [{slug: "soil-carbon", title: "Soil carbon"}]}],
    "GET /discovery/intake/candidates": () => [200, {items: [saved()]}]
  });
  const panel = new Node("section");
  panel.hidden = true;
  const ctl = await intake.mount(panel, doc, f);
  assert.ok(ctl && panel.hidden === false && ctl.signedIn() && ctl.isOwner());
  assert.match(panel.textContent, new RegExp("Signed in as " + OWNER));
  assert.equal(byClass(panel, "intake-saved").length, 1);
  assert.equal(byTag(panel, "select").length, 1, "only the article picker; no owner picker without editor grants");
  buttonNamed(panel, "Sign out").dispatch("click");
  await settle();
  assert.equal(ctl.signedIn(), false);
  assert.equal(byClass(panel, "intake-saved").length, 0);
  assert.ok(byClass(panel, "intake-signin-link")[0]);
  assert.ok(f.calls.some(c => c.url === WS + "/v1/auth/logout" && c.init.credentials === "include"));
});

test("an ended session mid-way returns the panel to sign-in and clears the sources", async () => {
  let live = true;
  const f = fakeFetch({
    "GET /discovery/intake/status": () => [200, {enabled: true, identity: IDENTITY}],
    "POST WS/v1/research-access/session": () => [200, session()],
    "GET WS/v1/research-approvals/grants": () => [200, {items: []}],
    "GET /editorial/articles": () => [200, {items: []}],
    "GET /discovery/intake/candidates": () => [200, {items: [saved()]}],
    ["POST /discovery/intake/candidates/" + ID_A + "/dismiss"]: () => live ? [200, {}] : [401, {detail: {message: "x"}}]
  });
  const panel = new Node("section");
  const ctl = await intake.mount(panel, doc, f);
  live = false;
  await ctl.dismiss(ID_A);
  assert.equal(ctl.signedIn(), false);
  assert.equal(byClass(panel, "intake-saved").length, 0);
  assert.match(all(panel, n => n.attrs.role === "status")[0].textContent, /Sign in again/);
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
