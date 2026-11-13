/* RH-08D-05 / SC-UP-01 — Discover ↔ governed intake (RH-08D-04). Saves a discovered candidate to the signed-in
   person's sources, manages saved sources (review issues, possible duplicates, Research article associations,
   dismissal) and records Library review proposals. Nothing here sends anything to the Library: a proposal is recorded
   with status awaiting-library-endpoint and sent = false.
   The panel appears only where the server reports intake enabled with Sustainable Catalyst sessions
   (identity.mode "workspace-session"; never in production: the server refuses it by constant). The person signs in at
   Workspace (SC Identity); this page then asks Workspace, with the Workspace cookie, for a short-lived signed Research
   session and sends it to Research in the Authorization header (credentials: "omit"). There is no service credential,
   nothing is stored, and the person never chooses their own identity: Research takes it from Workspace's signature.
   Every server and provider value enters the DOM through textContent.
*/
(function (host) {
  "use strict";
  var BASE = "/api/research/v1";
  var SAVED_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  var SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  var RUN_HANDLE = /^run-[A-Za-z0-9_-]{16,64}$/;
  var CANDIDATE_ID = /^cand-[0-9a-f]{24}$/;
  var SAVED = "saved-unreviewed"; // discovery_intake.SAVED; a dismissed source has status "dismissed"
  var ISSUE_TEXT = {
    "retracted": "A source record reports this work as retracted",
    "retraction-status-unknown": "Retraction status is unknown",
    "no-doi": "No DOI is recorded",
    "metadata-conflict": "Source records disagree on metadata",
    "possible-duplicate": "It may duplicate another saved source",
    "metadata-invalid": "Its stored metadata cannot be read"
  };
  var METADATA_INVALID = "metadata-invalid"; // discovery_intake.METADATA_INVALID
  var NOT_SENT = "Recorded for Library review — not sent. The Library intake endpoint is not enabled; " +
    "this source is still unreviewed and is not Research evidence.";
  var SEPARATION = "Saved sources are discovered, unreviewed leads. Saving, linking to an article or proposing for " +
    "Library review does not make a source evidence and publishes nothing.";

  function el(doc, tag, className, text) {
    var node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function button(doc, text, onClick, className) {
    var b = el(doc, "button", className || "", text);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  function b64url(text) {
    return btoa(unescape(encodeURIComponent(text))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function errorText(status, detail) {
    if (status === 401) return "Your Sustainable Catalyst sign-in has ended. Sign in again; nothing was changed.";
    if (status === 403) return "Your account or editor grant does not allow this action.";
    if (status === 404) return typeof detail === "string" && /search again/.test(detail) ? detail
      : "Not found. It may have expired or belong to another owner.";
    if (status === 409) return "A concurrent change was made. Try again.";
    if (status === 503) return "Saving sources is not enabled here. Nothing was saved.";
    if ((status === 400 || status === 422) && typeof detail === "string") return detail;
    return "The request could not be completed. Nothing was changed.";
  }

  var SESSION_SCHEMA = "sc-workspace-research-session/1.0"; // user_sessions.SESSION_SCHEMA
  var SESSION_PATH = "/v1/research-access/session";
  var GRANTS_PATH = "/v1/research-approvals/grants";
  var GRANT_ID = /^rag_[0-9a-f]{32}$/;
  var SESSION_MARGIN_MS = 30000; // fetch a new signed session this long before the current one expires
  var ATTESTATION_REUSE_MS = 240000; // below editorial.EDITOR_WRITE_WINDOW_SECONDS (300 s)
  var WORKSPACE = /^(https:\/\/[A-Za-z0-9.-]+(:[0-9]{1,5})?|http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:[0-9]{1,5})?)$/;

  /* The Workspace origin the server names for sign-in (user_sessions.workspace_url), or "" when unusable. */
  function workspaceOrigin(identity) {
    var url = identity && identity.mode === "workspace-session" ? String(identity.workspaceUrl || "") : "";
    return WORKSPACE.test(url) ? url : "";
  }

  function failure(status, message) {
    var err = new Error(message || errorText(status));
    err.status = status;
    return err;
  }

  /* The signed-in person's Research client. Workspace calls carry the Workspace cookie; Research calls carry only the
     signed session (and, when acting as an editor, that owner's Workspace attestation). */
  function createClient(fetchImpl, base, identity) {
    var root = base || BASE;
    var workspace = workspaceOrigin(identity);
    var session = null, expires = 0, editor = null;
    function workspaceCall(method, path, body) {
      if (!workspace) return Promise.reject(failure(503, "Sustainable Catalyst sign-in is not configured here."));
      var init = {method: method, credentials: "include", cache: "no-store", referrerPolicy: "no-referrer"};
      if (body !== undefined) { init.headers = {"Content-Type": "application/json"}; init.body = JSON.stringify(body); }
      return fetchImpl(workspace + path, init);
    }
    function forget() { session = null; expires = 0; editor = null; }
    function refresh() {
      return workspaceCall("POST", SESSION_PATH)
        .then(function (res) { return res.ok ? res.json() : null; })
        .catch(function () { return null; })
        .then(function (doc) {
          var until = doc && doc.schema === SESSION_SCHEMA && typeof doc.subject === "string" ? Date.parse(doc.expiresAt) : NaN;
          if (isNaN(until)) { forget(); return null; }
          session = doc; expires = until;
          return doc;
        });
    }
    function current() {
      return session && expires - Date.now() > SESSION_MARGIN_MS ? Promise.resolve(session) : refresh();
    }
    function attestation() {
      if (!editor) return Promise.resolve(null);
      if (editor.value && Date.now() - editor.at < ATTESTATION_REUSE_MS) return Promise.resolve(editor.value);
      var held = editor;
      return workspaceCall("POST", GRANTS_PATH + "/" + held.grantId + "/attestation").then(function (res) {
        if (!res.ok) throw failure(403, "Your editor grant for this owner is no longer active.");
        return res.json();
      }).then(function (doc) {
        held.value = b64url(JSON.stringify(doc)); held.at = Date.now();
        return held.value;
      });
    }
    function headers() {
      return current().then(function (doc) {
        if (!doc) throw failure(401);
        var h = {"Content-Type": "application/json", Authorization: "SC-Workspace " + b64url(JSON.stringify(doc))};
        return attestation().then(function (value) {
          if (value) { h["X-SC-Owner-Scope"] = editor.owner; h["X-SC-Editor-Attestation"] = value; }
          return h;
        });
      });
    }
    function request(method, path, body) {
      return headers().then(function (h) {
        return fetchImpl(root + path, {method: method, headers: h, body: body ? JSON.stringify(body) : undefined,
          credentials: "omit", cache: "no-store", referrerPolicy: "no-referrer"});
      }).then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (!res.ok) {
            if (res.status === 401) forget();
            var detail = data && data.detail;
            throw failure(res.status, errorText(res.status, detail && typeof detail === "object" ? detail.message : detail));
          }
          return data;
        });
      });
    }
    function savedPath(id, rest) {
      if (!SAVED_ID.test(id)) throw new Error("Unexpected saved-source id");
      return "/discovery/intake/candidates/" + id + (rest || "");
    }
    function slugPart(slug) {
      if (!SLUG.test(slug)) throw new Error("Unexpected article identifier");
      return encodeURIComponent(slug);
    }
    return {
      // SC-SCI-02: the Research Console uses the same signed-session client for its own routes.
      workspace: workspace,
      workspaceCall: workspaceCall,
      request: request,
      signIn: function () { return refresh(); },
      signInUrl: function (returnTo) {
        return workspace + "/v1/auth/login?returnTo=" + encodeURIComponent(String(returnTo || "/"));
      },
      signOut: function () {
        var done = workspace ? workspaceCall("POST", "/v1/auth/logout").catch(function () {}) : Promise.resolve();
        forget();
        return done;
      },
      person: function () { return session ? session.subject : ""; },
      signedIn: function () { return session !== null; },
      isOwner: function () { return session !== null && editor === null; },
      actingFor: function () { return editor ? editor.owner : ""; },
      editorGrants: function () {
        var me = session ? session.subject : "";
        return workspaceCall("GET", GRANTS_PATH).then(function (res) { return res.ok ? res.json() : {items: []}; })
          .catch(function () { return {items: []}; })
          .then(function (data) {
            return (data.items || []).filter(function (g) {
              return g && g.role === "editor" && !g.revokedAt && g.grantee === me && GRANT_ID.test(String(g.grantId));
            });
          });
      },
      actFor: function (grant) {
        if (grant && !GRANT_ID.test(String(grant.grantId))) throw new Error("Unexpected editor grant");
        editor = grant ? {grantId: grant.grantId, owner: String(grant.ownerScope), value: "", at: 0} : null;
      },
      status: function () {
        return fetchImpl(root + "/discovery/intake/status", {credentials: "omit", cache: "no-store"})
          .then(function (res) { return res.ok ? res.json() : {enabled: false}; })
          .catch(function () { return {enabled: false}; });
      },
      list: function () { return request("GET", "/discovery/intake/candidates"); },
      articles: function () { return request("GET", "/editorial/articles"); },
      save: function (handle, candidateId) {
        return request("POST", "/discovery/intake/candidates", {run_handle: handle, candidate_id: candidateId});
      },
      dismiss: function (id) { return request("POST", savedPath(id, "/dismiss")); },
      associate: function (id, slug) { return request("POST", savedPath(id, "/associations/" + slugPart(slug))); },
      dissociate: function (id, slug) {
        return request("POST", savedPath(id, "/associations/" + slugPart(slug) + "/remove"));
      },
      propose: function (id, issues) {
        return request("POST", savedPath(id, "/library-proposal"), {acknowledged_blocking_issues: issues});
      }
    };
  }

  /* Proposal form: the owner must tick exactly the blocking issues before the proposal can be recorded. */
  function renderProposal(doc, item, controller) {
    var box = el(doc, "div", "intake-proposal");
    if (item.libraryProposal) {
      box.append(el(doc, "p", "intake-proposal-recorded", NOT_SENT));
      return box;
    }
    if (item.status !== SAVED) return box;
    var issues = (item.review && item.review.blockingIssues) || [];
    if (issues.indexOf(METADATA_INVALID) !== -1) {
      box.append(el(doc, "p", "intake-note", "This source's stored metadata cannot be read, so it cannot be proposed " +
        "for Library review. Your other saved sources are unaffected."));
      return box;
    }
    var form = el(doc, "fieldset", "intake-proposal-form");
    form.append(el(doc, "legend", "", "Propose for Library review"));
    var boxes = [];
    if (issues.length) {
      form.append(el(doc, "p", "", "Confirm each issue a Library reviewer must resolve:"));
      issues.forEach(function (issue) {
        var label = el(doc, "label");
        var check = el(doc, "input");
        check.type = "checkbox"; check.value = issue; check.checked = false;
        check.addEventListener("change", sync);
        boxes.push(check);
        label.append(check, el(doc, "span", "", ISSUE_TEXT[issue] || issue));
        form.append(label);
      });
    } else {
      form.append(el(doc, "p", "", "No blocking issues were found. The Library still reviews every source."));
    }
    var submit = button(doc, "Record proposal (not sent)", function () {
      controller.propose(item.id, boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; }));
    });
    if (!controller.isOwner()) {
      submit.disabled = true;
      form.append(el(doc, "p", "intake-note", "Only the owner can propose sources for Library review."));
    }
    function sync() {
      if (controller.isOwner()) submit.disabled = !boxes.every(function (b) { return b.checked; });
    }
    sync();
    form.append(submit);
    box.append(form);
    return box;
  }

  function renderAssociations(doc, item, articles, controller) {
    var box = el(doc, "div", "intake-associations");
    box.append(el(doc, "h4", "", "Linked Research articles"));
    var titles = {};
    (articles || []).forEach(function (a) { titles[a.slug] = a.title; });
    var linked = el(doc, "ul", "intake-linked");
    item.associations.forEach(function (link) {
      var li = el(doc, "li", "", (titles[link.entry] || link.entry) + " — related candidate, not evidence ");
      li.append(button(doc, "Unlink", function () { controller.dissociate(item.id, link.entry); }));
      linked.append(li);
    });
    if (!item.associations.length) linked.append(el(doc, "li", "empty", "Not linked to an article."));
    box.append(linked);
    if (item.status !== SAVED) return box;
    var already = item.associations.map(function (a) { return a.entry; });
    var options = (articles || []).filter(function (a) { return SLUG.test(a.slug) && already.indexOf(a.slug) < 0; });
    if (!options.length) return box;
    var label = el(doc, "label", "intake-link-label", "Link to article ");
    var select = el(doc, "select");
    options.forEach(function (a) {
      var option = el(doc, "option", "", a.title || a.slug);
      option.value = a.slug;
      select.append(option);
    });
    label.append(select);
    box.append(label, button(doc, "Link", function () { if (select.value) controller.associate(item.id, select.value); }));
    return box;
  }

  function renderSaved(doc, item, context, controller) {
    var card = el(doc, "article", "intake-saved");
    if (SAVED_ID.test(item.id)) card.id = "saved-" + item.id;
    var labels = el(doc, "p", "discovery-labels");
    labels.append(el(doc, "span", "label", item.status === "dismissed" ? "Dismissed" : "Saved — unreviewed"));
    if (item.review && item.review.retractionReported) labels.append(el(doc, "span", "label label-retracted", "Retraction reported"));
    if (item.possibleDuplicateOf && item.possibleDuplicateOf.length) labels.append(el(doc, "span", "label", "Possible duplicate"));
    if (item.review && item.review.conflictingFields.length) labels.append(el(doc, "span", "label", "Metadata disagreement"));
    card.append(labels);
    card.append(el(doc, "h3", "", item.title || "Untitled record"));
    var meta = item.metadata || {};
    card.append(el(doc, "p", "discovery-meta", [meta.publication_year, meta.venue, item.doi ? "DOI " + item.doi : "No DOI"]
      .filter(function (x) { return x !== null && x !== undefined && String(x).trim(); }).join(" · ")));
    var providers = {};
    item.sources.forEach(function (s) { providers[s.provider] = true; });
    card.append(el(doc, "p", "discovery-sourced", item.sources.length + " provider record" +
      (item.sources.length === 1 ? "" : "s") + " kept with provenance (" + Object.keys(providers).join(", ") + ")"));
    if (item.review && item.review.conflictingFields.length) {
      card.append(el(doc, "p", "intake-note", "Records disagree on: " + item.review.conflictingFields.join(", ") + "."));
    }
    if (item.possibleDuplicateOf && item.possibleDuplicateOf.length) {
      var dups = el(doc, "ul", "intake-duplicates");
      item.possibleDuplicateOf.forEach(function (id) {
        dups.append(el(doc, "li", "", "Possible duplicate of: " + (context.titles[id] || "another saved source")));
      });
      card.append(dups);
    }
    card.append(renderAssociations(doc, item, context.articles, controller));
    card.append(renderProposal(doc, item, controller));
    if (item.status === SAVED && controller.isOwner()) {
      card.append(button(doc, "Dismiss", function () { controller.dismiss(item.id); }, "intake-dismiss"));
    }
    return card;
  }

  function renderSavedList(target, items, articles, doc, controller) {
    target.replaceChildren();
    var context = {titles: {}, articles: articles || []};
    items.forEach(function (i) { context.titles[i.id] = i.title; });
    target.append(el(doc, "p", "discovery-separation", SEPARATION));
    var active = items.filter(function (i) { return i.status === SAVED; });
    var dismissed = items.filter(function (i) { return i.status !== SAVED; });
    target.append(el(doc, "p", "discovery-count", active.length + " saved source" + (active.length === 1 ? "" : "s") +
      (dismissed.length ? ", " + dismissed.length + " dismissed (provenance kept; save again to restore)" : "") + "."));
    var list = el(doc, "div", "intake-list");
    active.concat(dismissed).forEach(function (i) { list.append(renderSaved(doc, i, context, controller)); });
    target.append(list);
  }

  /* The Save action shown on a search result card, only for an intake-enabled run with a signed-in session. */
  function saveAction(doc, response, candidate, controller) {
    var intake = response && response.intake;
    if (!controller || !controller.signedIn() || !intake || intake.enabled !== true) return null;
    if (!RUN_HANDLE.test(String(intake.run_handle)) || !CANDIDATE_ID.test(candidate.candidate_id)) return null;
    var box = el(doc, "div", "intake-save");
    var note = el(doc, "span", "intake-note");
    note.setAttribute("role", "status");
    var save = button(doc, "Save to my sources", function () {
      save.disabled = true;
      note.textContent = "Saving…";
      controller.save(intake.run_handle, candidate.candidate_id).then(function (out) {
        note.textContent = out.deduplicated
          ? "Already saved; " + out.sourcesAdded + " new provider record" + (out.sourcesAdded === 1 ? "" : "s") + " added."
          : "Saved as an unreviewed source.";
      }, function (err) { save.disabled = false; note.textContent = err.message; });
    });
    box.append(save, note);
    return box;
  }

  /* Wire the panel. Returns the controller, or null when intake with Sustainable Catalyst sessions is not enabled (the
     panel then stays hidden). ``returnTo`` is where Workspace sends the person back after sign-in. */
  function mount(panel, doc, fetchImpl, returnTo) {
    return createClient(fetchImpl).status().then(function (status) {
      var identity = status && status.identity;
      if (!status || status.enabled !== true || !workspaceOrigin(identity)) { panel.hidden = true; return null; }
      var client = createClient(fetchImpl, BASE, identity);
      var articles = [];
      var state = el(doc, "p", "discovery-notice");
      state.setAttribute("role", "status");
      var account = el(doc, "div", "intake-account");
      var listHost = el(doc, "div", "intake-results");
      var controller;
      function signedOut(message) {
        listHost.replaceChildren();
        articles = [];
        var link = el(doc, "a", "intake-signin-link", "Sign in with Sustainable Catalyst");
        link.setAttribute("href", client.signInUrl(returnTo));
        account.replaceChildren(link);
        state.textContent = message || "Sign in to save sources and see your saved sources.";
      }
      function report(promise, done) {
        return promise.then(function (out) { state.textContent = done; return refresh().then(function () { return out; }); },
          function (err) {
            if (err.status === 401) signedOut(err.message); else state.textContent = err.message;
            throw err;
          });
      }
      function refresh() {
        return client.list().then(function (data) {
          renderSavedList(listHost, data.items || [], articles, doc, controller);
          return true;
        }, function (err) {
          if (err.status === 401) signedOut(err.message); else state.textContent = err.message;
          return false;
        });
      }
      function load() {
        // Switching person or owner never shows the previous list.
        listHost.replaceChildren();
        articles = [];
        state.textContent = "Loading saved sources…";
        return client.articles().then(function (data) { articles = data.items || []; }, function () { articles = []; })
          .then(refresh).then(function (ok) {
            if (ok) state.textContent = client.actingFor()
              ? "Acting as an editor for " + client.actingFor() + "." : "Signed in. Search to save new sources.";
            return ok;
          });
      }
      function signedIn() {
        account.replaceChildren(el(doc, "span", "intake-person", "Signed in as " + client.person()));
        var signOut = button(doc, "Sign out", function () {
          client.signOut().then(function () { signedOut("Signed out."); });
        });
        account.append(signOut);
        return client.editorGrants().then(function (grants) {
          if (!grants.length) return;
          var label = el(doc, "label", "intake-owner-label", "Sources of ");
          var select = el(doc, "select");
          var mine = el(doc, "option", "", "My own");
          mine.value = "";
          select.append(mine);
          grants.forEach(function (g) {
            var option = el(doc, "option", "", g.ownerScope + " (editor" + (g.projectId === "*" ? "" : ", project " + g.projectId) + ")");
            option.value = g.grantId;
            select.append(option);
          });
          select.addEventListener("change", function () {
            client.actFor(grants.filter(function (g) { return g.grantId === select.value; })[0] || null);
            load();
          });
          label.append(select);
          account.append(label);
        });
      }
      controller = {
        signedIn: client.signedIn, isOwner: client.isOwner,
        save: function (handle, id) { return report(client.save(handle, id), "Source saved."); },
        dismiss: function (id) { return report(client.dismiss(id), "Source dismissed. Its provenance is kept.").catch(function () {}); },
        associate: function (id, slug) { return report(client.associate(id, slug), "Linked to the article.").catch(function () {}); },
        dissociate: function (id, slug) { return report(client.dissociate(id, slug), "Unlinked.").catch(function () {}); },
        propose: function (id, issues) {
          return report(client.propose(id, issues), "Proposal recorded. Nothing was sent to the Library.").catch(function () {});
        }
      };

      panel.replaceChildren();
      panel.append(el(doc, "h3", "", "My saved sources"), account, state, listHost);
      panel.hidden = false;
      return client.signIn().then(function (session) {
        if (!session) { signedOut(); return controller; }
        return signedIn().then(load).then(function () { return controller; });
      });
    });
  }

  var api = {createClient: createClient, renderSavedList: renderSavedList, saveAction: saveAction, mount: mount,
    errorText: errorText, workspaceOrigin: workspaceOrigin, controller: null};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (host) {
    host.SCResearchDiscoveryIntake = api;
    var start = function () {
      var panel = host.document && host.document.getElementById("discovery-intake-panel");
      if (!panel || !host.fetch) return;
      var loc = host.location;
      var here = loc && loc.origin ? loc.origin + loc.pathname + loc.search : "/";
      mount(panel, host.document, host.fetch.bind(host), here).then(function (c) { api.controller = c; });
    };
    if (host.document && host.document.readyState === "loading") host.document.addEventListener("DOMContentLoaded", start);
    else start();
  }
})(typeof window !== "undefined" ? window : null);
