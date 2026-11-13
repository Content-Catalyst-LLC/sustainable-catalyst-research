/* RH-08D-05 — Discover ↔ governed intake (RH-08D-04). Saves a discovered candidate to the owner's sources, manages
   saved sources (review issues, possible duplicates, Research article associations, dismissal) and records Library
   review proposals. Nothing here sends anything to the Library: a proposal is recorded with status
   awaiting-library-endpoint and sent = false.
   The panel appears only where the server reports intake enabled (never in production: the server refuses it by
   constant). Credentials are typed per session, held in this closure only (no storage, no cookies) and sent with
   credentials: "omit". Every server and provider value enters the DOM through textContent.
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
    if (status === 401 || status === 403) return "The staging credential or editor grant was not accepted for this action.";
    if (status === 404) return typeof detail === "string" && /search again/.test(detail) ? detail
      : "Not found. It may have expired or belong to another owner.";
    if (status === 409) return "A concurrent change was made. Try again.";
    if (status === 503) return "Saving sources is not enabled here. Nothing was saved.";
    if ((status === 400 || status === 422) && typeof detail === "string") return detail;
    return "The request could not be completed. Nothing was changed.";
  }

  /* Session-only credentials: the staging service token, owner scope and an optional editor attestation. */
  function createClient(fetchImpl, base) {
    var creds = null;
    var root = base || BASE;
    function headers() {
      var h = {"Content-Type": "application/json"};
      if (!creds) return h;
      h.Authorization = "Bearer " + creds.token;
      h["X-SC-Owner-Scope"] = creds.owner;
      if (creds.actor) h["X-SC-Actor-Scope"] = creds.actor;
      if (creds.attestation) h["X-SC-Editor-Attestation"] = creds.attestation;
      return h;
    }
    function request(method, path, body) {
      return fetchImpl(root + path, {method: method, headers: headers(), body: body ? JSON.stringify(body) : undefined,
        credentials: "omit", cache: "no-store", referrerPolicy: "no-referrer"})
        .then(function (res) {
          return res.json().catch(function () { return {}; }).then(function (data) {
            if (!res.ok) {
              var detail = data && data.detail;
              var err = new Error(errorText(res.status, detail && typeof detail === "object" ? detail.message : detail));
              err.status = res.status;
              throw err;
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
      setCredentials: function (values) {
        var token = String(values.token || "").trim(), owner = String(values.owner || "").trim();
        if (!token || !owner) throw new Error("Enter the staging credential and owner scope.");
        var attestation = String(values.attestation || "").trim();
        if (attestation) attestation = b64url(JSON.stringify(JSON.parse(attestation)));
        creds = {token: token, owner: owner, actor: String(values.actor || "").trim(), attestation: attestation};
      },
      clear: function () { creds = null; },
      signedIn: function () { return creds !== null; },
      isOwner: function () { return creds !== null && (!creds.actor || creds.actor === creds.owner); },
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

  /* Wire the panel. Returns the controller, or null when intake is not enabled (the panel then stays hidden). */
  function mount(panel, doc, fetchImpl) {
    var client = createClient(fetchImpl);
    return client.status().then(function (status) {
      if (!status || status.enabled !== true) { panel.hidden = true; return null; }
      var articles = [];
      var state = el(doc, "p", "discovery-notice");
      state.setAttribute("role", "status");
      var listHost = el(doc, "div", "intake-results");
      var controller;
      function report(promise, done) {
        return promise.then(function (out) { state.textContent = done; return refresh().then(function () { return out; }); },
          function (err) { state.textContent = err.message; throw err; });
      }
      function refresh() {
        return client.list().then(function (data) {
          renderSavedList(listHost, data.items || [], articles, doc, controller);
          return true;
        }, function (err) { state.textContent = err.message; return false; });
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

      var form = el(doc, "form", "intake-signin");
      form.append(el(doc, "p", "intake-note", "Staging only. The credential is kept in this page's memory and is " +
        "forgotten when you reload or sign out. It is never stored."));
      function field(label, name, type) {
        var wrap = el(doc, "label", "", label + " ");
        var input = el(doc, "input");
        input.name = name; input.type = type || "text"; input.autocomplete = "off"; input.spellcheck = false;
        wrap.append(input);
        form.append(wrap);
        return input;
      }
      var token = field("Staging credential", "token", "password");
      var owner = field("Owner scope", "owner");
      var actor = field("Acting editor (optional)", "actor");
      var attestation = field("Editor attestation JSON (optional)", "attestation");
      var signIn = el(doc, "button", "", "Use for this session");
      signIn.type = "submit";
      var signOut = button(doc, "Forget credential", function () {
        client.clear();
        token.value = ""; attestation.value = "";
        listHost.replaceChildren();
        articles = [];
        state.textContent = "Credential forgotten.";
      });
      form.append(signIn, signOut);
      form.addEventListener("submit", function (event) {
        if (event && event.preventDefault) event.preventDefault();
        try {
          client.setCredentials({token: token.value, owner: owner.value, actor: actor.value, attestation: attestation.value});
        } catch (err) { state.textContent = err.message; return; }
        token.value = "";
        // A new credential never shows the previous session's sources, and one the server rejects is not kept.
        listHost.replaceChildren();
        articles = [];
        state.textContent = "Loading your saved sources…";
        client.articles().then(function (data) { articles = data.items || []; }, function () { articles = []; })
          .then(refresh).then(function (ok) {
            if (ok) { state.textContent = "Signed in for this session. Search to save new sources."; return; }
            client.clear();
            attestation.value = "";
            state.textContent += " The credential was not kept; nothing was loaded.";
          });
      });

      panel.replaceChildren();
      panel.append(el(doc, "h3", "", "My saved sources"), form, state, listHost);
      panel.hidden = false;
      return controller;
    });
  }

  var api = {createClient: createClient, renderSavedList: renderSavedList, saveAction: saveAction, mount: mount,
    errorText: errorText, controller: null};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (host) {
    host.SCResearchDiscoveryIntake = api;
    var start = function () {
      var panel = host.document && host.document.getElementById("discovery-intake-panel");
      if (!panel || !host.fetch) return;
      mount(panel, host.document, host.fetch.bind(host)).then(function (c) { api.controller = c; });
    };
    if (host.document && host.document.readyState === "loading") host.document.addEventListener("DOMContentLoaded", start);
    else start();
  }
})(typeof window !== "undefined" ? window : null);
