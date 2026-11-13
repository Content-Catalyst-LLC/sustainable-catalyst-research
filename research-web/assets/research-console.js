/* SC-SCI-02 — Research Console. The signed-in researcher's own Research records: Workspace notebooks and verified
   runs to import, Research projects, the private knowledge graph, provenance and reproduction, figures drawn by the
   vendored Lab renderer, and article revisions prepared for review.
   Identity: the page asks Workspace (with the Workspace cookie) for a short-lived signed Research session and sends it
   to Research in the Authorization header (SCResearchDiscoveryIntake.createClient). No service credential, no stored
   token, no owner the page chooses. Workspace stays the source of truth: imports send Research a Workspace grant, and
   Research fetches and verifies the signed export itself. Server values enter the DOM as text only.
*/
(function (host) {
  "use strict";
  var BASE = "/api/research/v1";
  var FIGURE_MEDIA = "application/vnd.sc.figure-bundle.v1+json"; // workspace_ingest.FIGURE_MEDIA
  var SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  var UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  var SHA = /^[0-9a-f]{64}$/;
  var ROLE_BY_KIND = {figure: "figure", result: "result", "statistical-result": "result", computation: "result",
    simulation: "result", table: "result", notebook: "notebook", dataset: "dataset", model: "model",
    source: "citation", publication: "citation"}; // editorial.REFERENCE_ROLES
  var KIND_ORDER = ["source", "publication", "dataset", "model", "notebook", "computation", "simulation", "result",
    "statistical-result", "table", "figure", "research-article"];

  function slugify(text) {
    return String(text || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80) || "study";
  }
  function b64(bytes) {
    var s = "";
    for (var i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return host.btoa(s);
  }
  function unb64(text) {
    var raw = host.atob(text), out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }
  // The linear-model coefficient figure the Notebook Desk builds (Workspace notebook_figure_bundle).
  function figureRequest(notebook, cellId, title) {
    return {figureId: (slugify(notebook.title).slice(0, 40) + "-" + cellId).replace(/^-+/, "f-"), title: title,
      rowsPath: "coefficients", mark: "point",
      x: {field: "term", label: "Model term", unit: "category", type: "nominal"},
      y: {field: "estimate", label: "Coefficient estimate", unit: "1"},
      uncertainty: {kind: "ci95", spreadField: "stdError", multiplier: 1.959964,
        statement: "Normal approximation: estimate ± 1.96 standard errors."},
      evidenceClass: "modelled", caption: title, altText: "Model coefficients with 95% intervals.",
      interpretation: title, limitations: ["Figure of a single verified notebook run; not reviewed."]};
  }
  function referencesFor(nodes, entry, chosen) {
    return nodes.filter(function (n) {
      return n.entry === entry && chosen.indexOf(n.id) >= 0 && ROLE_BY_KIND[n.kind] && SHA.test(n.provenanceSha256 || "");
    }).map(function (n) { return {objectId: n.id, role: ROLE_BY_KIND[n.kind], pin: n.provenanceSha256, caption: n.title}; });
  }

  function mount(doc, fetchImpl) {
    var Intake = host.SCResearchDiscoveryIntake, Figures = host.SCResearchFigures;
    var $ = function (id) { return doc.getElementById(id); };
    var state = {client: null, notebooks: [], graph: {nodes: [], edges: []}, projects: [], article: null};

    function h(tag, attrs) {
      var node = doc.createElement(tag);
      for (var k in attrs || {}) {
        if (k === "text") node.textContent = String(attrs[k]);
        else if (k === "onclick") node.addEventListener("click", attrs[k]);
        else node.setAttribute(k, attrs[k]);
      }
      var add = function (c) {
        if (c === null || c === undefined) return;
        if (Array.isArray(c)) { c.forEach(add); return; }
        node.append(typeof c === "string" ? doc.createTextNode(c) : c);
      };
      for (var i = 2; i < arguments.length; i++) add(arguments[i]);
      return node;
    }
    function notice(text, bad) {
      var n = $("console-notice");
      n.hidden = !text; n.textContent = text || ""; n.className = "console-notice" + (bad ? " console-bad" : "");
    }
    function run(button, fn) {
      if (button) button.disabled = true;
      notice("");
      return Promise.resolve().then(fn).catch(function (err) {
        // Research explains refusals (missing reproduction, foreign object, unverified reference); show that reason.
        var why = err && err.detail && err.status !== 401 ? err.detail : "";
        notice(why || (err && err.message) || "The request could not be completed.", true);
      }).then(function () { if (button) button.disabled = false; });
    }
    function rs(method, path, body) { return state.client.request(method, path, body); }
    function ws(method, path, body) {
      return state.client.workspaceCall(method, path, body).then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (!res.ok) {
            var d = data && (data.detail || data.message);
            if (Array.isArray(d)) d = d.map(function (x) { return (x && x.msg) || String(x); }).join("; ");
            else if (d && typeof d === "object") d = d.message || d.code;
            throw new Error("Workspace refused " + path.split("?")[0] + ": " + (d || ("HTTP " + res.status)));
          }
          return data;
        });
      });
    }
    function facts(rows) {
      var dl = h("dl", {"class": "console-facts"});
      rows.forEach(function (r) { if (r[1] !== undefined && r[1] !== null && r[1] !== "") dl.append(h("dt", {text: r[0]}), h("dd", {text: r[1]})); });
      return dl;
    }

    // ---- sign-in ----------------------------------------------------------------------------------------------
    function showSignIn() {
      $("console-signin").hidden = false; $("console-app").hidden = true; $("console-signout").hidden = true;
      notice("");
      var back = host.location.origin + "/console/";
      return state.client.workspaceCall("GET", "/v1/session/profile").then(function (res) { return res.ok ? res.json() : {}; })
        .catch(function () { return {}; }).then(function (profile) {
          var login = profile && profile.globalIdentityProviderConfigured ? "/v1/auth/login" : "/v1/private-research/login";
          $("console-signin-link").href = state.client.workspace + login + "?returnTo=" + encodeURIComponent(back);
        });
    }

    // ---- Workspace notebooks ----------------------------------------------------------------------------------
    function loadWorkspace() {
      return Promise.all([ws("GET", "/v1/projects"), ws("GET", "/v1/notebooks")]).then(function (r) {
        var projects = (r[0].items || r[0].projects || []).filter(function (p) { return p && p.projectId; });
        state.notebooks = r[1].items || [];
        var sel = $("ws-project");
        sel.replaceChildren(h("option", {value: "", text: "Choose a project"}));
        projects.forEach(function (p) { sel.append(h("option", {value: p.projectId, text: p.projectTitle || p.title || p.projectId})); });
        sel.onchange = showNotebooks;
        if (projects.length === 1) { sel.value = projects[0].projectId; showNotebooks(); }
      });
    }
    function showNotebooks() {
      var pid = $("ws-project").value, sel = $("ws-notebook");
      sel.replaceChildren(h("option", {value: "", text: "Choose a notebook"}));
      state.notebooks.filter(function (n) { return n.projectId === pid; }).forEach(function (n) {
        sel.append(h("option", {value: n.notebookId, text: (n.title || n.notebookId) + " · r" + n.revision}));
      });
      sel.onchange = showCells;
      $("ws-cells").replaceChildren();
    }
    function showCells() {
      var id = $("ws-notebook").value;
      var nb = state.notebooks.filter(function (n) { return n.notebookId === id; })[0];
      if (!nb) { $("ws-cells").replaceChildren(); return; }
      $("imp-entry").value = $("imp-entry").value || slugify(nb.title);
      $("imp-title").value = $("imp-title").value || nb.title || "";
      return run(null, function () {
        return ws("GET", "/v1/notebooks/" + encodeURIComponent(id) + "/cell-status").then(function (status) {
          var rows = (status.cells || []).map(function (c) {
            var verified = c.run && c.run.cellVerified && c.run.verification && c.run.verification.status === "executed-verified";
            var actions = h("td", {});
            if (verified) {
              actions.append(h("button", {type: "button", "class": "console-small", onclick: function (ev) { importRun(ev.target, nb, c, false); }, text: "Import run"}),
                h("button", {type: "button", "class": "console-small", onclick: function (ev) { importRun(ev.target, nb, c, true); }, text: "Import with figure"}),
                h("button", {type: "button", "class": "console-small", onclick: function (ev) { reproduce(ev.target, nb, c); }, text: "Record as reproduction"}));
            }
            return h("tr", {"data-cell": c.cellId}, h("td", {text: c.cellId}), h("td", {text: c.language || ""}),
              h("td", {text: c.operation || ""}), h("td", {text: c.state || ""}),
              h("td", {text: c.run ? (c.run.verification || {}).status || c.run.runStatus : "not run"}), actions);
          });
          $("ws-cells").replaceChildren(rows.length ? h("table", {"class": "console-table"},
            h("thead", {}, h("tr", {}, h("th", {text: "Cell"}), h("th", {text: "Language"}), h("th", {text: "Operation"}),
              h("th", {text: "State"}), h("th", {text: "Verification"}), h("th", {text: "Research"}))),
            h("tbody", {}, rows)) : h("p", {"class": "console-fine", text: "This notebook has no code cells."}));
        });
      });
    }
    function grantFor(nb, cell) {
      return ws("POST", "/v1/research-exports/grants", {projectId: nb.projectId, runId: cell.run.runId}).then(function (g) { return g.grant; });
    }
    function importRun(button, nb, cell, withFigure) {
      var area = $("imp-area").value.trim(), entry = $("imp-entry").value.trim(), title = $("imp-title").value.trim();
      return run(button, function () {
        if (!SLUG.test(area) || !SLUG.test(entry) || !title) throw new Error("Give a Research area, entry slug and title.");
        var files = Promise.resolve([]);
        if (withFigure) {
          var rev = cell.plan ? cell.plan.notebookRevision : nb.revision;
          files = ws("POST", "/v1/notebooks/" + encodeURIComponent(nb.notebookId) + "/revisions/" + rev + "/cells/" +
            encodeURIComponent(cell.cellId) + "/figure-bundle", figureRequest(nb, cell.cellId, title)).then(function (pkg) {
            var enc = new TextEncoder(), bundle = pkg.files.filter(function (f) { return f.role === "bundle"; })[0];
            return ws("POST", "/v1/runs/" + encodeURIComponent(cell.run.runId) + "/outputs", {
              schema: "sc-workspace-execution-run-output/1.0", outputId: "figure-" + pkg.figureId, role: "figure",
              label: title, mediaType: FIGURE_MEDIA, sha256: pkg.bundleSha256, bytes: enc.encode(bundle.text).length
            }).then(function () {
              // The bundle names its view by the canonical-JSON hash; Research re-checks bytes against either form.
              return pkg.files.map(function (f) { return {sha256: f.canonicalSha256 || f.sha256, contentBase64: b64(enc.encode(f.text))}; });
            });
          });
        }
        return files.then(function (figureFiles) {
          return grantFor(nb, cell).then(function (grant) {
            return rs("POST", "/console/imports", {grant: grant, researchArea: area, entrySlug: entry, title: title,
              figureFiles: figureFiles});
          });
        }).then(function (out) {
          var objs = out.objects || {};
          $("ws-result").replaceChildren(h("p", {text: "Imported run " + out.runId + " (" + out.verificationStatus + ", inputs " +
            out.inputLineage + ") as " + Object.keys(objs).length + " private Research objects."}),
            h("ul", {}, Object.keys(objs).map(function (k) {
              return h("li", {}, h("button", {type: "button", "class": "console-link", onclick: function () { inspect(objs[k].id); }, text: k}),
                " " + objs[k].kind);
            })));
          return refresh();
        });
      });
    }
    function reproduce(button, nb, cell) {
      var results = state.graph.nodes.filter(function (n) { return n.kind === "result" && n.reproduction !== null; });
      return run(button, function () {
        if (!results.length) throw new Error("Import the original result first.");
        var original = $("rs-reproduce-target") && $("rs-reproduce-target").value;
        if (!UUID.test(original || "")) {
          var sel = h("select", {id: "rs-reproduce-target"}, h("option", {value: "", text: "Choose the original result"}));
          results.forEach(function (n) { sel.append(h("option", {value: n.id, text: n.title + " · " + n.source})); });
          $("ws-result").replaceChildren(h("label", {}, "Reproduce which result? ", sel),
            h("p", {"class": "console-fine", text: "Choose it, then press “Record as reproduction” on the reproducing cell again."}));
          return;
        }
        return grantFor(nb, cell).then(function (grant) {
          return rs("POST", "/console/objects/" + original + "/reproductions", {grant: grant});
        }).then(function (out) {
          $("ws-result").replaceChildren(h("p", {text: "Reproduction recorded: " + out.status + (out.independence ? " (" + out.independence + ")" : "") + "."}));
          return refresh();
        });
      });
    }

    // ---- Research projects, graph and objects -----------------------------------------------------------------
    function refresh() {
      return Promise.all([rs("GET", "/console/projects"), rs("GET", "/console/graph")]).then(function (r) {
        state.projects = r[0].items || []; state.graph = r[1];
        renderProjects(); renderGraph(); renderPublicationChoices();
      });
    }
    function renderProjects() {
      var box = $("rs-projects");
      if (!state.projects.length) { box.replaceChildren(h("p", {"class": "console-fine", text: "No Research projects yet. Import a verified run to start one."})); return; }
      box.replaceChildren.apply(box, state.projects.map(function (p) {
        return h("div", {"class": "console-project"}, h("h3", {text: p.area + " / " + p.projectKey}),
          h("p", {"class": "console-fine", text: "Workspace project " + (p.workspaceProjectId || "not bound")}),
          h("ul", {}, p.entries.map(function (e) {
            var art = e.article ? " · article r" + e.article.headRevision + " " + e.article.reviewStatus + " / " + e.article.publicationStatus : " · no article";
            return h("li", {text: e.slug + " — " + e.title + " · " + e.objects + " objects · " + e.visibility + art});
          })));
      }));
    }
    function renderGraph() {
      var nodes = state.graph.nodes || [], edges = state.graph.edges || [];
      var cols = {}, order = [];
      nodes.forEach(function (n) {
        var k = KIND_ORDER.indexOf(n.kind) >= 0 ? n.kind : "other";
        if (!cols[k]) { cols[k] = []; order.push(k); }
        cols[k].push(n);
      });
      order.sort(function (a, b) { return (KIND_ORDER.indexOf(a) + 99) % 99 - (KIND_ORDER.indexOf(b) + 99) % 99; });
      var NS = "http://www.w3.org/2000/svg", W = 170, H = 34, GAP = 46, pos = {};
      var height = Math.max(1, Math.max.apply(null, order.map(function (k) { return cols[k].length; }).concat([1]))) * (H + 14) + 40;
      var svg = doc.createElementNS(NS, "svg");
      svg.setAttribute("viewBox", "0 0 " + Math.max(1, order.length * (W + GAP)) + " " + height);
      svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Knowledge graph: " + nodes.length + " objects, " + edges.length + " relations");
      order.forEach(function (k, i) {
        var label = doc.createElementNS(NS, "text"); label.setAttribute("x", i * (W + GAP)); label.setAttribute("y", 14);
        label.setAttribute("class", "console-graph-kind"); label.textContent = k; svg.append(label);
        cols[k].forEach(function (n, j) { pos[n.id] = {x: i * (W + GAP), y: 26 + j * (H + 14)}; });
      });
      edges.forEach(function (e) {
        var a = pos[e.source], b = pos[e.target];
        if (!a || !b) return;
        var line = doc.createElementNS(NS, "line");
        line.setAttribute("x1", a.x + (a.x < b.x ? W : 0)); line.setAttribute("y1", a.y + H / 2);
        line.setAttribute("x2", b.x + (a.x < b.x ? 0 : W)); line.setAttribute("y2", b.y + H / 2);
        line.setAttribute("class", "console-graph-edge");
        var t = doc.createElementNS(NS, "title"); t.textContent = e.relation; line.append(t); svg.append(line);
      });
      nodes.forEach(function (n) {
        var p = pos[n.id], g = doc.createElementNS(NS, "g");
        g.setAttribute("class", "console-graph-node state-" + (n.verification || "none").replace(/[^a-z-]/g, ""));
        g.setAttribute("tabindex", "0"); g.setAttribute("role", "button"); g.setAttribute("data-node", n.id);
        var r = doc.createElementNS(NS, "rect"); r.setAttribute("x", p.x); r.setAttribute("y", p.y); r.setAttribute("width", W);
        r.setAttribute("height", H); r.setAttribute("rx", 4);
        var t = doc.createElementNS(NS, "text"); t.setAttribute("x", p.x + 8); t.setAttribute("y", p.y + 21);
        t.textContent = String(n.title || n.kind).slice(0, 24);
        var tip = doc.createElementNS(NS, "title"); tip.textContent = n.kind + ": " + n.title + " (" + (n.verification || "no verification") + ")";
        g.append(r, t, tip);
        g.addEventListener("click", function () { inspect(n.id); });
        g.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); inspect(n.id); } });
        svg.append(g);
      });
      $("rs-graph-svg").replaceChildren(nodes.length ? svg : h("p", {"class": "console-fine", text: "No objects yet."}));
      $("rs-graph-list").replaceChildren(h("details", {}, h("summary", {text: nodes.length + " objects, " + edges.length + " relations (list)"}),
        h("ul", {}, edges.map(function (e) {
          var name = function (id) { var n = nodes.filter(function (x) { return x.id === id; })[0]; return n ? n.kind + " “" + n.title + "”" : id; };
          return h("li", {text: name(e.source) + " — " + e.relation + " → " + name(e.target)});
        }))));
    }
    function inspect(id) {
      if (!UUID.test(id || "")) return;
      return run(null, function () {
        return rs("GET", "/console/objects/" + id).then(function (o) {
          var approvals = Object.keys(o.approvals || {}).map(function (k) { return k + ": " + (o.approvals[k] ? "signed" : "missing"); }).join(", ");
          var box = h("div", {"class": "console-object", "data-object": o.id},
            h("h3", {text: o.kind + " — " + o.title}),
            facts([["Authority", o.authority], ["Source", o.source], ["Artifact SHA-256", o.artifactSha256],
              ["Provenance SHA-256", o.provenanceSha256], ["Verification", o.verification],
              ["Reproduction", o.reproduction], ["Notebook claim", o.claim && o.claim.status],
              ["Classification", o.classification], ["Visibility", o.visibility + " · review " + o.reviewState],
              ["Approvals (Workspace-signed)", approvals || "none recorded"], ["Subject", o.subjectRef]]),
            h("h4", {text: "Relations"}),
            h("ul", {}, o.relations.map(function (r) {
              return h("li", {}, (r.direction === "out" ? "→ " : "← ") + r.relation + " ",
                h("button", {type: "button", "class": "console-link", onclick: function () { inspect(r.objectId); }, text: r.objectId.slice(0, 8)}));
            })),
            h("details", {}, h("summary", {text: "Provenance record"}), h("pre", {text: JSON.stringify(o.provenance, null, 1)})));
          if (o.figure) box.append(h("button", {type: "button", id: "rs-render", onclick: function (ev) { renderFigure(ev.target, o); }, text: "Render in Graph Studio"}));
          var nbid = o.kind === "notebook" && /^notebook:(.+)$/.exec(o.source.split("@")[0]);
          if (nbid) box.append(h("button", {type: "button", onclick: function (ev) { notebookRef(ev.target, nbid[1]); }, text: "Notebook revisions"}));
          $("rs-object").replaceChildren(box);
          $("rs-figure").replaceChildren();
          $("object").scrollIntoView({block: "start"});
        });
      });
    }
    function notebookRef(button, notebookId) {
      return run(button, function () {
        return rs("GET", "/console/notebooks/" + encodeURIComponent(notebookId)).then(function (ref) {
          $("rs-figure").replaceChildren(h("h4", {text: "Notebook " + ref.notebookId + " (content stays in Workspace)"}),
            h("ul", {}, ref.revisions.map(function (r) {
              return h("li", {text: "revision " + r.notebookRevision + " · " + r.runs.length + " registered runs · " +
                r.runs.map(function (x) { return (x.cell || {}).cellId + " " + x.verification + " " + (x.claimStatus || {}).status; }).join("; ")});
            })),
            h("p", {"class": "console-fine", text: ref.revisionComparison.map(function (c) {
              return "r" + c.from + "→r" + c.to + ": changed " + (c.changedCells.join(", ") || "none");
            }).join(" · ")}));
        });
      });
    }
    function renderFigure(button, o) {
      return run(button, function () {
        return Promise.all([rs("GET", "/console/objects/" + o.id + "/figure-files"), Figures.loadRenderer(doc)]).then(function (r) {
          var pkg = r[0], renderer = r[1];
          var files = pkg.files.map(function (f) { return {name: f.role + "-" + f.sha256.slice(0, 12), bytes: unb64(f.contentBase64)}; });
          var out = h("div", {"class": "console-figure"});
          $("rs-figure").replaceChildren(h("p", {"class": "console-fine", text: "Private, unreleased figure. The Lab renderer re-verifies every file against bundle " + pkg.bundleSha256 + "."}), out);
          return renderer.render(out, files, pkg.bundleSha256, Figures.renderOptions({releaseContext: undefined})).then(function (res) {
            $("rs-figure").setAttribute("data-state", res.state);
          });
        });
      });
    }

    // ---- publication preparation ------------------------------------------------------------------------------
    function entries() {
      var list = [];
      state.projects.forEach(function (p) { p.entries.forEach(function (e) { list.push({project: p, entry: e}); }); });
      return list;
    }
    function renderPublicationChoices() {
      var sel = $("pub-entry"), keep = sel.value;
      sel.replaceChildren(h("option", {value: "", text: "Choose an entry"}));
      entries().forEach(function (x) { sel.append(h("option", {value: x.entry.slug, text: x.entry.title + " (" + x.entry.slug + ")"})); });
      sel.value = keep; sel.onchange = renderRefs; renderRefs();
    }
    function chosen() {
      return entries().filter(function (x) { return x.entry.slug === $("pub-entry").value; })[0];
    }
    function renderRefs() {
      var x = chosen(), box = $("pub-refs");
      box.replaceChildren(h("legend", {text: "Linked outputs"}));
      $("pub-submit").disabled = true;
      if (!x) return;
      $("pub-title").value = $("pub-title").value || x.entry.title;
      state.graph.nodes.filter(function (n) { return n.entry === x.entry.slug && ROLE_BY_KIND[n.kind]; }).forEach(function (n) {
        box.append(h("label", {"class": "console-check"}, h("input", {type: "checkbox", value: n.id}),
          " " + n.kind + " — " + n.title + " (" + (n.verification || "unverified") + (n.reproduction ? ", " + n.reproduction : "") + ")"));
      });
      var art = x.entry.article;
      $("pub-submit").disabled = !(art && art.headRevision > 0 && art.reviewStatus === "draft");
    }
    function revise(button) {
      return run(button, function () {
        var x = chosen();
        if (!x) throw new Error("Choose a Research entry.");
        var picks = Array.prototype.map.call($("pub-refs").querySelectorAll("input:checked"), function (i) { return i.value; });
        var start = x.entry.article ? Promise.resolve() : rs("POST", "/editorial/articles", {areaSlug: x.project.area,
          projectKey: x.project.projectKey, workspaceProjectId: x.project.workspaceProjectId || "", slug: x.entry.slug,
          title: $("pub-title").value.trim() || x.entry.title});
        return start.then(function () { return rs("GET", "/editorial/articles/" + x.entry.slug); }).then(function (art) {
          return rs("POST", "/editorial/articles/" + x.entry.slug + "/revisions", {baseRevision: art.headRevision || 0,
            title: $("pub-title").value.trim() || x.entry.title, summary: $("pub-summary").value,
            sections: [{heading: "Findings", body: $("pub-body").value}],
            references: referencesFor(state.graph.nodes, x.entry.slug, picks), changeSummary: "Prepared in the Research Console"});
        }).then(function (rev) { return readiness(x.entry.slug, rev.revision); }).then(refresh);
      });
    }
    function readiness(slug, revision) {
      return rs("GET", "/editorial/articles/" + slug + "/revisions/" + revision).then(function (r) {
        $("pub-result").replaceChildren(h("p", {text: "Article revision " + revision + " saved (document " + r.documentSha256.slice(0, 12) + "…). Readiness for publication:"}),
          h("ul", {}, r.references.map(function (ref) {
            return h("li", {"data-status": ref.status, text: ref.role + " " + (ref.title || ref.objectId) + ": " + ref.status +
              (ref.problems.length ? " — " + ref.problems.join(", ") : "")});
          })));
      });
    }
    function submit(button) {
      return run(button, function () {
        var x = chosen();
        if (!x || !x.entry.article) throw new Error("Save an article revision first.");
        return rs("POST", "/editorial/articles/" + x.entry.slug + "/revisions/" + x.entry.article.headRevision + "/submit").then(function (out) {
          $("pub-result").append(h("p", {text: "Revision " + out.revision + " submitted for review. It stays private until Workspace-signed approvals are recorded and an editor publishes it."}));
          return refresh();
        });
      });
    }

    // ---- boot --------------------------------------------------------------------------------------------------
    return fetchImpl(BASE + "/console/status", {credentials: "omit", cache: "no-store"}).then(function (res) {
      return res.ok ? res.json() : null;
    }).catch(function () { return null; }).then(function (status) {
      if (!status || !status.identity || status.identity.mode !== "workspace-session" || !Intake || !Figures) {
        notice("The Research Console is not enabled on this deployment.", true);
        return null;
      }
      state.client = Intake.createClient(fetchImpl, BASE, status.identity);
      return state.client.signIn().then(function (session) {
        if (!session) return showSignIn();
        $("console-who").textContent = session.subject; $("console-signout").hidden = false;
        $("console-signout").onclick = function () {
          // Ends the Workspace session (Workspace account sign-in) and any SC Identity session.
          state.client.workspaceCall("DELETE", "/v1/session").catch(function () {})
            .then(function () { return state.client.signOut(); }).then(function () { host.location.reload(); });
        };
        $("console-signin").hidden = true; $("console-app").hidden = false;
        $("pub-revise").onclick = function (ev) { revise(ev.target); };
        $("pub-submit").onclick = function (ev) { submit(ev.target); };
        notice(status.importsEnabled ? "" : "Imports from Workspace are not configured on this deployment; existing records are read-only here.");
        return Promise.all([refresh(), loadWorkspace()]).catch(function (err) { notice(err.message, true); });
      });
    });
  }

  var api = {mount: mount, slugify: slugify, figureRequest: figureRequest, referencesFor: referencesFor, ROLE_BY_KIND: ROLE_BY_KIND};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (host && host.document) {
    host.SCResearchConsole = api;
    var start = function () { if (host.document.getElementById("console-main")) mount(host.document, host.fetch.bind(host)); };
    if (host.document.readyState === "loading") host.document.addEventListener("DOMContentLoaded", start);
    else start();
  }
})(typeof window !== "undefined" ? window : null);
