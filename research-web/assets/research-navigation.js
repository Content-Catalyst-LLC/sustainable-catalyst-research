/* RH-PROD-06.4 — Accessible, read-only Research preview navigation.
   WordPress may autoformat the original page-embedded script. This external
   script restores its navigation without assuming any private API access.
   All discovery/areas/graph records are derived from existing public DOM rows.
*/
(function (host) {
  "use strict";
  var VIEWS = ["overview", "discover", "areas", "graph", "updates"];

  function readPreviewRows(root) {
    return Array.from(root.querySelectorAll("#scrh-home-articles tr")).map(function (tr, index) {
      var cells = tr.querySelectorAll("td");
      if (cells.length < 4) return null;
      var titleNode = cells[0].querySelector(".scrh-record-title");
      var summaryNode = cells[0].querySelector(".scrh-record-desc");
      var title = titleNode ? titleNode.textContent.trim() : "";
      if (!title) return null;
      return {
        index: index, title: title,
        summary: summaryNode ? summaryNode.textContent.trim() : "",
        area: cells[1].textContent.trim(),
        state: cells[2].textContent.trim(),
        type: cells[3].textContent.trim(), row: tr
      };
    }).filter(Boolean);
  }

  function elem(tag, text, className) {
    var result = document.createElement(tag);
    if (text !== null && text !== undefined) result.textContent = String(text);
    if (className) result.className = className;
    return result;
  }
  function btn(label, action, className) {
    var button = elem("button", label, className || "scrh-nav-preview-button");
    button.type = "button";
    button.addEventListener("click", action);
    return button;
  }

  function initialize(root) {
    if (!root || root.dataset.scNavigationReady === "yes") return false;
    var tabs = Array.from(root.querySelectorAll("[data-tab]"));
    var panels = Array.from(root.querySelectorAll(".scrh-view[data-view]"));
    if (!tabs.length || !panels.length || !root.querySelector("#scrh-home-articles")) return false;
    root.dataset.scNavigationReady = "yes";
    var rows = readPreviewRows(root);
    var areaNames = Array.from(new Set(rows.map(function (r) { return r.area; })));
    var pinned = [];
    var $ = function (id) { return root.querySelector("#" + id); };

    function navigate(name, focus) {
      if (VIEWS.indexOf(name) < 0 || !$('scrh-view-' + name)) return false;
      panels.forEach(function (panel) {
        panel.hidden = panel.dataset.view !== name;
      });
      tabs.forEach(function (tab) {
        var active = tab.dataset.tab === name;
        tab.setAttribute("aria-selected", String(active));
        tab.tabIndex = active ? 0 : -1;
      });
      root.querySelectorAll("[data-tab-target]").forEach(function (control) {
        control.classList.toggle("scrh-rail-current", control.dataset.tabTarget === name);
      });
      root.dataset.scActiveView = name;
      if (focus) {
        var heading = $('scrh-view-' + name).querySelector("h2");
        if (heading) {
          heading.tabIndex = -1;
          heading.focus({preventScroll: true});
        }
        var bar = root.querySelector(".scrh-toolbar");
        if (bar && typeof bar.scrollIntoView === "function") bar.scrollIntoView({block:"start",behavior:"smooth"});
      }
      return true;
    }

    function selectRecord(record) {
      if (!record) return;
      var fields = [
        ["scrh-inspect-title", record.title],
        ["scrh-inspect-summary", record.summary],
        ["scrh-inspect-area", record.area],
        ["scrh-inspect-state", record.state]
      ];
      fields.forEach(function (f) { if ($(f[0])) $(f[0]).textContent = f[1]; });
      rows.forEach(function (r) { r.row.classList.toggle("scrh-nav-selected", r.index === record.index); });
      navigate("overview", true);
    }

    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () { navigate(tab.dataset.tab, true); });
      tab.addEventListener("keydown", function (event) {
        if (["ArrowRight", "ArrowLeft", "Home", "End"].indexOf(event.key) === -1) return;
        event.preventDefault();
        var idx = VIEWS.indexOf(tab.dataset.tab);
        var next = event.key === "Home" ? 0 : event.key === "End" ? VIEWS.length - 1 :
          (idx + (event.key === "ArrowRight" ? 1 : -1) + VIEWS.length) % VIEWS.length;
        var nextTab = root.querySelector('[data-tab="' + VIEWS[next] + '"]');
        if (nextTab) { navigate(VIEWS[next], false); nextTab.focus(); }
      });
    });
    root.querySelectorAll("[data-tab-target], [data-section]").forEach(function (control) {
      control.addEventListener("click", function () {
        navigate(control.dataset.tabTarget || control.dataset.section, true);
      });
    });

    // The index is intentionally a local editor-approved preview, NOT a live
    // PostgreSQL result set. Its independent 6-record count must remain labeled.
    rows.forEach(function (r) {
      r.row.tabIndex = 0;
      r.row.setAttribute("role", "button");
      r.row.setAttribute("aria-label", "Inspect research preview: " + r.title);
      r.row.addEventListener("click", function () { selectRecord(r); });
      r.row.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectRecord(r); }
      });
    });
    function filterIndex() {
      var query = $('scrh-index-search') ? $('scrh-index-search').value.trim().toLocaleLowerCase() : "";
      var areaControl = $('scrh-index-area');
      var stateControl = $('scrh-index-status');
      var area = areaControl && areaControl.selectedIndex > 0 ? areaControl.options[areaControl.selectedIndex].textContent.trim() : "";
      var state = stateControl ? stateControl.value : "all";
      var count = 0;
      rows.forEach(function (r) {
        var matches = (!query || [r.title,r.summary,r.area,r.type].join(" ").toLocaleLowerCase().includes(query)) &&
          (!area || area === r.area) &&
          (state === "all" || (state === "public" ? /public/i.test(r.state) : !/public/i.test(r.state)));
        r.row.hidden = !matches;
        if (matches) count++;
      });
      if ($('scrh-index-count')) $('scrh-index-count').textContent = count + " preview record" + (count === 1 ? "" : "s");
    }
    ["scrh-index-search", "scrh-index-area", "scrh-index-status"].forEach(function (id) {
      var input = $(id);
      if (input) input.addEventListener(id === "scrh-index-search" ? "input" : "change", filterIndex);
    });

    function buildAreas() {
      var pills = $('scrh-area-pills');
      var list = $('scrh-area-list');
      if (!pills || !list) return;
      var selected = "All areas";
      function render() {
        pills.replaceChildren(); list.replaceChildren();
        ["All areas"].concat(areaNames).forEach(function (area) {
          var button = btn(area, function () { selected = area; render(); });
          button.setAttribute("aria-pressed", String(selected === area));
          pills.append(button);
        });
        areaNames.filter(function (area) { return selected === "All areas" || area === selected; })
          .forEach(function (area) {
            var matches = rows.filter(function (r) { return r.area === area; });
            var article = elem("article");
            article.append(elem("span", matches.length + " public preview record" + (matches.length === 1 ? "" : "s"), "scrh-small-label"), elem("h3", area));
            matches.forEach(function (record) {
              article.append(btn(record.title + " →", function () { selectRecord(record); }));
            });
            list.append(article);
          });
      }
      render();
    }

    function buildGraph() {
      var graph = $('scrh-graph-svg-wrap'), list = $('scrh-graph-list'), detail = $('scrh-graph-detail');
      if (!graph || !list || !detail) return;
      graph.replaceChildren(); list.replaceChildren();
      var ns = "http://www.w3.org/2000/svg";
      var height = Math.max(480, 95 * rows.length);
      var svg = document.createElementNS(ns, "svg");
      svg.setAttribute("viewBox", "0 0 900 " + height);
      svg.setAttribute("role", "img");
      svg.setAttribute("aria-label", "Preview graph linking four research areas to six public preview records");
      var areaY = new Map();
      areaNames.forEach(function (area, index) { areaY.set(area, 75 + index * 140); });
      function pick(label, summary, kind) {
        detail.replaceChildren(elem("span", kind, "scrh-small-label"), elem("h3", label), elem("p", summary));
      }
      function node(text, x, y, color, action) {
        var group = document.createElementNS(ns,"g");
        group.setAttribute("role","button");
        group.setAttribute("tabindex","0");
        group.setAttribute("aria-label",text);
        var dot = document.createElementNS(ns,"circle");
        dot.setAttribute("cx", x); dot.setAttribute("cy", y);
        dot.setAttribute("r", 9); dot.setAttribute("fill", color);
        var label = document.createElementNS(ns,"text");
        label.setAttribute("x", x+17); label.setAttribute("y", y+4);
        label.setAttribute("font-size","13"); label.setAttribute("fill","#234149");
        label.textContent = text.length > 36 ? text.slice(0, 35) + "…" : text;
        group.append(dot,label);
        group.addEventListener("click",action);
        group.addEventListener("keydown",function (event) { if (event.key==="Enter" || event.key===" ") { event.preventDefault(); action(); }});
        svg.append(group);
        list.append(btn(text,action));
      }
      rows.forEach(function (r,index) {
        var start = areaY.get(r.area), end = 50 + index*86;
        var path = document.createElementNS(ns,"path");
        path.setAttribute("d","M 180 " + start + " C 345 " + start + ", 410 " + end + ", 550 " + end);
        path.setAttribute("fill","none"); path.setAttribute("stroke","#aebeb9");
        path.setAttribute("stroke-width","2");
        svg.append(path);
      });
      areaNames.forEach(function (area) {
        node(area,150,areaY.get(area),"#b44050",function () {
          pick(area,rows.filter(function(r){return r.area===area;}).length+" connected public preview records. Structural relationship only.","RESEARCH AREA");
        });
      });
      rows.forEach(function (r,index) {
        node(r.title,550,50 + index*86,"#287b79",function () {pick(r.title,r.summary,"RESEARCH PREVIEW RECORD");});
      });
      graph.append(svg);
      pick(areaNames[0] || "Research graph","Area and record links are structural preview relationships, not scientific evidence.","PUBLIC PREVIEW");
      if ($('scrh-graph-reset')) $('scrh-graph-reset').addEventListener("click",function () {
        pick(areaNames[0] || "Research graph","Select an area or record to inspect its public preview relationship.","PUBLIC PREVIEW");
      });
    }

    function buildDiscovery() {
      var form = $('scrh-discover-form'), results = $('scrh-discover-results');
      if (!form || !results) return;
      var pins = $('scrh-pinned'), count = $('scrh-discover-count');
      function renderPins() {
        if (!pins) return;
        pins.replaceChildren();
        if (!pinned.length) { pins.append(elem("p","No candidates selected yet.")); return; }
        pinned.forEach(function (r) {
          var article = elem("article");
          article.append(elem("h3",r.title),btn("Remove",function () {
            pinned.splice(pinned.indexOf(r),1); renderPins();
          }));
          pins.append(article);
        });
      }
      form.addEventListener("submit",function(event) {
        event.preventDefault();
        var q = $('scrh-discover-query') ? $('scrh-discover-query').value.toLocaleLowerCase().trim() : "";
        var provider = $('scrh-discover-provider') ? $('scrh-discover-provider').value : "local";
        results.replaceChildren();
        if (provider !== "local") {
          if (count) count.textContent = "External provider not connected";
          results.append(elem("p","External scholarly discovery is not enabled. No search was sent to " + provider + ". Choose Sustainable Catalyst research to search the six public preview records.","scrh-empty"));
          return;
        }
        var words = q.split(/\s+/).filter(Boolean);
        var matches = words.length ? rows.filter(function (r) {
          var haystack = (r.title + " " + r.summary + " " + r.area + " " + r.type).toLocaleLowerCase();
          return words.every(function(w) {return haystack.includes(w);});
        }) : [];
        if (count) count.textContent = matches.length + " local preview result" + (matches.length === 1 ? "" : "s");
        if (!matches.length) results.append(elem("p", q ? "No public preview records match this query." : "Enter a topic to search local public preview records.","scrh-empty"));
        matches.forEach(function (r) {
          var article = elem("article");
          article.append(elem("span","LOCAL PUBLIC PREVIEW","scrh-small-label"),elem("h3",r.title),elem("p",r.summary));
          article.append(btn("View public overview →",function () { selectRecord(r); }));
          article.append(btn("Add to working set",function () {
            if (!pinned.includes(r)) pinned.push(r);
            renderPins();
          }));
          results.append(article);
        });
      });
      if ($('scrh-clear-pins')) $('scrh-clear-pins').addEventListener("click",function() {pinned.length=0;renderPins();});
      renderPins();
    }

    // Static activity cards may contain navigation controls from the preview.
    // Opening them inspects the matching public preview record, never a private ID.
    root.querySelectorAll("#scrh-view-updates [data-open-article]").forEach(function(control,index) {
      control.addEventListener("click",function () {
        var match = rows.find(function(r) {return r.title.toLocaleLowerCase().includes(index===0 ? "weathering" : index===1 ? "sustainable development" : "security");});
        if (match) selectRecord(match);
        else navigate("overview",true);
      });
    });
    if (root.querySelector("[data-tab-target='graph']")) {
      // Catches the existing 'View research relationships' CTA too.
      root.querySelectorAll("[data-tab-target='graph']").forEach(function(control) {
        control.setAttribute("aria-label", "Open knowledge graph");
      });
    }
    buildAreas(); buildGraph(); buildDiscovery(); filterIndex();
    navigate("overview",false);
    var notice = $('scrh-runtime-notice');
    if (notice) notice.textContent = "Public preview navigation, local source discovery, filters, research areas and structural graph are active. Private Workspace editing and external scholarly providers remain separate and are not enabled here.";
    return true;
  }

  if (typeof module !== "undefined" && module.exports) module.exports = { initialize: initialize, readPreviewRows: readPreviewRows };
  if (host) host.SustainableCatalystResearchNavigation = { initialize: initialize };
  if (typeof document !== "undefined") {
    function boot() {initialize(document.getElementById("sc-research-v2"));}
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded",boot);
    else boot();
  }
})(typeof window !== "undefined" ? window : null);