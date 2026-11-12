/* RH-08D-02 — Discover results: candidate cards, source inspection, provenance, duplicates and the
   (disabled) Library intake action. Renders a typed sc-research-discovery-search/1.0 response.
   Every provider value is untrusted: it enters the DOM through textContent, links are built only for
   https URLs and validated DOIs, and this file makes no network request of its own.
*/
(function (host) {
  "use strict";
  var PROVIDER_NAMES = {openalex: "OpenAlex", crossref: "Crossref"};
  var DOI = /^10\.\d{4,9}(?:\.\d+)*\/\S+$/;
  var OPENALEX_WORK = /^https:\/\/openalex\.org\/W\d+$/;
  var CANDIDATE_ID = /^cand-[0-9a-f]{24}$/;
  var ISSUE_TEXT = {
    "retracted": "marked as retracted",
    "retraction-status-unknown": "retraction status unknown",
    "no-doi": "no DOI",
    "metadata-conflict": "providers disagree on metadata",
    "possible-duplicate": "possible duplicate of another candidate"
  };
  var FIELD_NAMES = {title: "Title", publication_year: "Publication year", venue: "Venue"};

  function providerName(id) { return PROVIDER_NAMES[id] || "Unknown source"; }

  function doiHref(doi) {
    if (typeof doi !== "string" || !DOI.test(doi)) return null;
    return "https://doi.org/" + doi.split("/").map(encodeURIComponent).join("/");
  }

  function safeHttps(value) {
    try {
      var url = new URL(value);
      return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
    } catch (_) { return null; }
  }

  function outcomeText(outcome) {
    var name = providerName(outcome.provider);
    switch (outcome.status) {
      case "ok":
        return name + ": " + (outcome.records_received - outcome.records_rejected) + " records accepted" +
          (outcome.records_rejected ? ", " + outcome.records_rejected + " not usable as candidates" : "") + ".";
      case "disabled": return name + ": not enabled. No request was made.";
      case "rate_limited": return name + ": rate limit reached. Try again later.";
      case "unavailable": return name + ": temporarily unavailable.";
      case "invalid_response": return name + ": returned a response that could not be read.";
      default: return name + ": search failed.";
    }
  }

  function filterCandidates(candidates, filters) {
    var f = filters || {};
    return (Array.isArray(candidates) ? candidates : []).filter(function (c) {
      if (f.providers && !c.sources.some(function (s) { return f.providers.indexOf(s.provider) >= 0; })) return false;
      if (f.doiOnly && !c.doi) return false;
      if (f.hideRetracted && c.retracted === true) return false;
      return true;
    });
  }

  function el(doc, tag, className, text) {
    var node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function link(doc, href, text, external) {
    var a = el(doc, "a", "", text);
    a.href = href;
    if (external) { a.target = "_blank"; a.rel = "noopener noreferrer"; a.referrerPolicy = "no-referrer"; }
    return a;
  }

  function fact(doc, list, term, value) {
    list.append(el(doc, "dt", "", term));
    var dd = el(doc, "dd");
    if (value && typeof value === "object" && value.nodeType) dd.append(value); else dd.textContent = String(value);
    list.append(dd);
  }

  function authorsText(authors) {
    var names = (authors || []).map(function (a) { return a.name; });
    return names.length > 3 ? names.slice(0, 3).join(", ") + " et al." : names.join(", ");
  }

  function sourcedText(sources) {
    var counts = {}, order = [];
    sources.forEach(function (s) { if (!counts[s.provider]) order.push(s.provider); counts[s.provider] = (counts[s.provider] || 0) + 1; });
    return "From " + order.map(function (p) {
      return providerName(p) + (counts[p] > 1 ? " (" + counts[p] + " records)" : ""); }).join(" and ");
  }

  function renderInspector(doc, candidate, context) {
    var details = el(doc, "details", "discovery-inspect");
    details.append(el(doc, "summary", "", "Inspect source and provenance"));
    var facts = el(doc, "dl", "discovery-facts");
    fact(doc, facts, "Candidate", candidate.candidate_id);
    fact(doc, facts, "Status", "Discovered, unreviewed. Not evidence and not publishable.");
    fact(doc, facts, "Work type", candidate.work_type || "Not stated");
    details.append(facts);

    details.append(el(doc, "h4", "", "Provider records (" + candidate.sources.length + ")"));
    var sources = el(doc, "ol", "discovery-sources");
    candidate.sources.forEach(function (source) {
      var item = el(doc, "li");
      var record = OPENALEX_WORK.test(source.provider_record_id)
        ? link(doc, source.provider_record_id, source.provider_record_id, true)
        : el(doc, "span", "", source.provider_record_id);
      var list = el(doc, "dl", "discovery-facts");
      fact(doc, list, "Source", providerName(source.provider));
      fact(doc, list, "Record", record);
      fact(doc, list, "Retrieved", source.retrieved_at);
      fact(doc, list, "Adapter", source.adapter_version);
      var hash = el(doc, "code", "", String(source.record_sha256).slice(0, 16) + "…");
      hash.title = "SHA-256 of the provider record: " + source.record_sha256;
      fact(doc, list, "Record hash", hash);
      item.append(list);
      sources.append(item);
    });
    details.append(sources);

    if (candidate.conflicts.length) {
      details.append(el(doc, "h4", "", "Metadata disagreements"));
      var conflicts = el(doc, "ul", "discovery-conflicts");
      candidate.conflicts.forEach(function (conflict) {
        var values = Object.keys(conflict.values).map(function (label) {
          var base = label.split("#");
          return providerName(base[0]) + (base[1] ? " (record " + base[1] + ")" : "") + ": " + conflict.values[label];
        });
        conflicts.append(el(doc, "li", "", (FIELD_NAMES[conflict.field] || conflict.field) + " — " + values.join(" · ")));
      });
      details.append(conflicts);
    }

    if (candidate.possible_duplicate_of.length) {
      details.append(el(doc, "h4", "", "Possible duplicates (kept separate for review)"));
      var dups = el(doc, "ul", "discovery-duplicates");
      candidate.possible_duplicate_of.forEach(function (id) {
        var item = el(doc, "li");
        if (CANDIDATE_ID.test(id)) item.append(link(doc, "#" + id, context.titles[id] || id, false));
        dups.append(item);
      });
      details.append(dups);
    }

    var landing = safeHttps(candidate.landing_url);
    if (landing) details.append(link(doc, landing, "Publisher landing page →", true));
    return details;
  }

  function renderIntake(doc, candidate, context) {
    var box = el(doc, "div", "discovery-intake");
    var button = el(doc, "button", "", "Propose for Library review");
    button.type = "button";
    button.disabled = true;
    button.setAttribute("aria-describedby", candidate.candidate_id + "-intake");
    var issues = (context.issues[candidate.candidate_id] || []).map(function (i) { return ISSUE_TEXT[i] || i; });
    var note = el(doc, "p", "", "Library intake is not enabled yet." +
      (issues.length ? " A reviewer would first need to resolve: " + issues.join("; ") + "." : ""));
    note.id = candidate.candidate_id + "-intake";
    box.append(button, note);
    return box;
  }

  function renderCandidate(doc, candidate, context) {
    var card = el(doc, "article", "discovery-candidate");
    if (CANDIDATE_ID.test(candidate.candidate_id)) card.id = candidate.candidate_id;
    var labels = el(doc, "p", "discovery-labels");
    labels.append(el(doc, "span", "label", "Discovered · unreviewed"));
    if (candidate.retracted === true) labels.append(el(doc, "span", "label label-retracted", "Retracted"));
    if (candidate.possible_duplicate_of.length) labels.append(el(doc, "span", "label", "Possible duplicate"));
    if (candidate.conflicts.length) labels.append(el(doc, "span", "label", "Metadata disagreement"));
    card.append(labels);
    card.append(el(doc, "h3", "", candidate.title || "Untitled record"));
    var meta = [authorsText(candidate.authors), candidate.publication_year, candidate.venue]
      .filter(function (x) { return x !== null && x !== undefined && String(x).trim(); });
    card.append(el(doc, "p", "discovery-meta", meta.join(" · ")));
    var doi = doiHref(candidate.doi);
    var line = el(doc, "p", "discovery-doi");
    if (doi) { line.append(el(doc, "span", "", "DOI ")); line.append(link(doc, doi, candidate.doi, true)); }
    else line.textContent = "No DOI recorded";
    card.append(line);
    card.append(el(doc, "p", "discovery-sourced", sourcedText(candidate.sources)));
    card.append(renderInspector(doc, candidate, context));
    card.append(renderIntake(doc, candidate, context));
    return card;
  }

  function renderSearch(target, response, doc) {
    doc = doc || target.ownerDocument;
    var run = response && response.run;
    target.replaceChildren();
    if (!run || !Array.isArray(run.candidates) || !Array.isArray(run.outcomes)) {
      target.append(el(doc, "p", "empty", "The discovery response could not be read. Nothing was saved."));
      return null;
    }
    var context = {titles: {}, issues: (response.library_intake && response.library_intake.blocking_issues) || {}};
    run.candidates.forEach(function (c) { context.titles[c.candidate_id] = c.title; });

    if (run.transport === "fixture") {
      target.append(el(doc, "p", "discovery-synthetic",
        "Synthetic fixture data for interface testing. These are not real scholarly records."));
    }
    var outcomes = el(doc, "ul", "discovery-outcomes");
    run.outcomes.forEach(function (o) { outcomes.append(el(doc, "li", "outcome-" + o.status, outcomeText(o))); });
    target.append(outcomes);

    var providers = [];
    run.candidates.forEach(function (c) { c.sources.forEach(function (s) {
      if (providers.indexOf(s.provider) < 0) providers.push(s.provider); }); });
    var filters = {providers: providers.slice(), doiOnly: false, hideRetracted: false};
    var form = el(doc, "fieldset", "discovery-filters");
    form.append(el(doc, "legend", "", "Refine candidates"));
    function toggle(label, checked, onChange) {
      var wrap = el(doc, "label");
      var box = el(doc, "input");
      box.type = "checkbox"; box.checked = checked;
      box.addEventListener("change", function () { onChange(box.checked); draw(); });
      wrap.append(box, el(doc, "span", "", label));
      form.append(wrap);
    }
    providers.forEach(function (p) {
      toggle(providerName(p), true, function (on) {
        filters.providers = on ? filters.providers.concat([p]) : filters.providers.filter(function (x) { return x !== p; });
      });
    });
    toggle("With DOI only", false, function (on) { filters.doiOnly = on; });
    toggle("Hide retracted", false, function (on) { filters.hideRetracted = on; });
    var count = el(doc, "p", "discovery-count");
    count.setAttribute("role", "status");
    var list = el(doc, "div", "discovery-list");
    if (run.candidates.length) target.append(form);
    target.append(count, list);

    function draw() {
      var shown = filterCandidates(run.candidates, filters);
      list.replaceChildren();
      shown.forEach(function (c) { list.append(renderCandidate(doc, c, context)); });
      count.textContent = run.candidates.length
        ? "Showing " + shown.length + " of " + run.candidates.length + " candidates. Candidates are leads for review, not verified evidence."
        : "No bibliographic candidates were returned. This does not imply an absence of scientific evidence.";
    }
    draw();
    return {filters: filters, draw: draw};
  }

  var api = {renderSearch: renderSearch, filterCandidates: filterCandidates, outcomeText: outcomeText,
    doiHref: doiHref, safeHttps: safeHttps};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (host) host.SCResearchDiscovery = api;
})(typeof window !== "undefined" ? window : null);
