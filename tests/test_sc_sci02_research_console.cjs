"use strict";
// SC-SCI-02: Research Console page helpers and its use of the signed-session client. Offline, fake fetch.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const consoleApi = require("../research-web/assets/research-console.js");
const intake = require("../research-web/assets/research-discovery-intake.js");

const SHA = "a".repeat(64);
const nodes = [
  {id: "11111111-1111-4111-8111-111111111111", kind: "figure", entry: "s", title: "Fig", provenanceSha256: SHA},
  {id: "22222222-2222-4222-8222-222222222222", kind: "computation", entry: "s", title: "Run", provenanceSha256: SHA},
  {id: "33333333-3333-4333-8333-333333333333", kind: "result", entry: "other", title: "Elsewhere", provenanceSha256: SHA},
  {id: "44444444-4444-4444-8444-444444444444", kind: "result", entry: "s", title: "No provenance", provenanceSha256: null},
];

test("only chosen, pinned outputs of the chosen entry become article references", () => {
  const refs = consoleApi.referencesFor(nodes, "s", nodes.map(n => n.id));
  assert.deepEqual(refs.map(r => [r.role, r.objectId.slice(0, 1), r.pin]), [["figure", "1", SHA], ["result", "2", SHA]]);
  assert.deepEqual(consoleApi.referencesFor(nodes, "s", [nodes[0].id]).length, 1);
});

test("the figure request is the Notebook Desk coefficient figure and is marked synthetic and unreviewed", () => {
  const req = consoleApi.figureRequest({title: "Wexford ERW notebook"}, "fit", "OLS fit");
  assert.equal(req.figureId, "wexford-erw-notebook-fit");
  assert.equal(req.evidenceClass, "synthetic-demonstration");
  assert.equal(req.uncertainty.kind, "ci95");
  assert.equal(consoleApi.slugify("  Ünïcode / Study 1 "), "n-code-study-1");
});

test("the session client carries the signed session to Research and the Workspace cookie only to Workspace", async () => {
  const calls = [];
  const session = {schema: "sc-workspace-research-session/1.0", subject: "workspace-user:a",
    expiresAt: new Date(Date.now() + 300000).toISOString()};
  const fetchImpl = async (url, init) => {
    calls.push([url, init]);
    const body = url.endsWith("/v1/research-access/session") ? session : {ok: true};
    return {ok: true, status: 200, json: async () => body};
  };
  const client = intake.createClient(fetchImpl, "/api/research/v1",
    {mode: "workspace-session", workspaceUrl: "https://workspace.example.org"});
  assert.equal(client.workspace, "https://workspace.example.org");
  await client.workspaceCall("POST", "/v1/research-exports/grants", {projectId: "p", runId: "r"});
  await client.request("GET", "/console/projects");
  const [grantUrl, grantInit] = calls[0];
  assert.equal(grantUrl, "https://workspace.example.org/v1/research-exports/grants");
  assert.equal(grantInit.credentials, "include");
  assert.equal(grantInit.body, JSON.stringify({projectId: "p", runId: "r"}));
  const [rsUrl, rsInit] = calls[calls.length - 1];
  assert.equal(rsUrl, "/api/research/v1/console/projects");
  assert.equal(rsInit.credentials, "omit");
  assert.match(rsInit.headers.Authorization, /^SC-Workspace /);
});

test("the console page and server policy keep it same-origin plus the configured Workspace origin", () => {
  const root = path.join(__dirname, "..", "research-web");
  const page = fs.readFileSync(path.join(root, "console", "index.html"), "utf8");
  assert.doesNotMatch(page, /<script>(?!<)/);  // no inline script
  for (const src of page.match(/src="[^"]+"/g)) assert.match(src, /^src="\/assets\//);
  const nginx = fs.readFileSync(path.join(root, "nginx.conf"), "utf8");
  const block = nginx.split("location /console/ {")[1].split("}")[0];
  assert.match(block, /connect-src 'self' __WORKSPACE_ORIGIN__;/);
  assert.match(block, /script-src 'self';/);
  assert.match(nginx, /location ~ \^\/api\/research\/v1\/\(console\|editorial\)\/ \{/);
  const docker = fs.readFileSync(path.join(root, "Dockerfile"), "utf8");
  assert.match(docker, /grep -Eqx '\(https:\/\/\[A-Za-z0-9\.-\]\+\(:\[0-9\]\{1,5\}\)\?\)\?'/);
});
