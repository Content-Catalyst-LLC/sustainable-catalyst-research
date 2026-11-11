/*! Sustainable Catalyst Lab — SC-FIG-01 / FIG-02A Graph Studio Plotly preview.
 *
 * Renders a Workbench FIG-02A compile response (sc-workbench-plotly-figure-adapter/1.0) inside
 * Graph Studio with the vendored plotly.js 3.3.1 (the same file the Workbench export worker uses).
 * Preview only: Workbench stays the numerical authority, nothing is uploaded, persisted, reviewed or
 * published here. A response is refused unless its specSha256 and resultHash recompute exactly and
 * its figure passes the content policy below.
 */
(function (root, factory) {
  'use strict';
  var api = factory(root);
  if (typeof module === 'object' && module.exports) { module.exports = api; }
  else { root.SCLabPlotlyPreviewFIG02A = api; api.boot(); }
}(typeof self !== 'undefined' ? self : this, function (root) {
  'use strict';

  var VERSION = '0.1.0';
  var RESPONSE_SCHEMA = 'sc-workbench-plotly-figure-adapter/1.0';
  var EXPECTED_PLOTLY_JS = '3.3.1';
  var MAX_RESPONSE_CHARS = 8 * 1024 * 1024;
  var ALLOWED_TRACES = { scatter: 1, bar: 1, box: 1, histogram: 1, heatmap: 1 };
  var FORBIDDEN_TEXT = /<\s*a[\s>]|href\s*=|src\s*=|javascript:|<\s*(script|iframe|img|svg|object|embed)\b/i;
  var EVIDENCE_TEXT = {
    observed: 'Observed — qualified measurement',
    'literature-derived': 'Literature-derived — published values, not measurements of this project',
    modelled: 'Modelled — Workbench computation, not a measurement',
    hypothetical: 'Hypothetical — scenario values',
    conceptual: 'Conceptual — illustrative only',
    'status-only': 'Status only — no quantitative claim'
  };

  // ------------------------------------------------------------------
  // Canonical hashing. Workbench hashes json.dumps(value, sort_keys=True,
  // separators=(",", ":"), ensure_ascii=False). JSON.parse would lose the
  // Python float spelling (100.0 vs 100), so number lexemes are kept as text.
  // ------------------------------------------------------------------
  function parseLexical(text) {
    var i = 0;
    function ws() { while (i < text.length && ' \t\n\r'.indexOf(text[i]) >= 0) { i++; } }
    function fail(msg) { throw new Error('invalid JSON: ' + msg + ' at ' + i); }
    function value() {
      ws();
      var c = text[i];
      if (c === '{') {
        i++; var obj = { t: 'o', v: [] }; ws();
        if (text[i] === '}') { i++; return obj; }
        for (;;) {
          ws(); if (text[i] !== '"') { fail('key'); }
          var k = string(); ws(); if (text[i] !== ':') { fail(':'); } i++;
          obj.v.push([k, value()]); ws();
          if (text[i] === ',') { i++; continue; }
          if (text[i] === '}') { i++; return obj; }
          fail('object');
        }
      }
      if (c === '[') {
        i++; var arr = { t: 'a', v: [] }; ws();
        if (text[i] === ']') { i++; return arr; }
        for (;;) {
          arr.v.push(value()); ws();
          if (text[i] === ',') { i++; continue; }
          if (text[i] === ']') { i++; return arr; }
          fail('array');
        }
      }
      if (c === '"') { return { t: 's', v: string() }; }
      if (text.startsWith('true', i)) { i += 4; return { t: 'l', v: 'true' }; }
      if (text.startsWith('false', i)) { i += 5; return { t: 'l', v: 'false' }; }
      if (text.startsWith('null', i)) { i += 4; return { t: 'l', v: 'null' }; }
      var m = /^-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?/.exec(text.slice(i, i + 64));
      if (!m) { fail('value'); }
      i += m[0].length;
      return { t: 'n', v: m[0] };
    }
    function string() {
      var start = i; i++;
      while (i < text.length && text[i] !== '"') { i += text[i] === '\\' ? 2 : 1; }
      if (i >= text.length) { fail('string'); }
      i++;
      return JSON.parse(text.slice(start, i));
    }
    var out = value(); ws();
    if (i !== text.length) { fail('trailing data'); }
    return out;
  }

  function codePointCompare(a, b) {
    var ia = 0, ib = 0;
    while (ia < a.length && ib < b.length) {
      var ca = a.codePointAt(ia), cb = b.codePointAt(ib);
      if (ca !== cb) { return ca < cb ? -1 : 1; }
      ia += ca > 0xffff ? 2 : 1; ib += cb > 0xffff ? 2 : 1;
    }
    if (ia >= a.length && ib >= b.length) { return 0; }
    return ia >= a.length ? -1 : 1;
  }

  function canonical(node) {
    if (node.t === 'o') {
      var pairs = node.v.slice().sort(function (x, y) { return codePointCompare(x[0], y[0]); });
      return '{' + pairs.map(function (p) { return JSON.stringify(p[0]) + ':' + canonical(p[1]); }).join(',') + '}';
    }
    if (node.t === 'a') { return '[' + node.v.map(canonical).join(',') + ']'; }
    if (node.t === 's') { return JSON.stringify(node.v); }
    return node.v;
  }

  function member(node, key) {
    if (!node || node.t !== 'o') { return null; }
    for (var j = 0; j < node.v.length; j++) { if (node.v[j][0] === key) { return node.v[j][1]; } }
    return null;
  }

  function sha256Hex(text) {
    var bytes = new TextEncoder().encode(text);
    var subtle = root && root.crypto && root.crypto.subtle;
    if (!subtle && typeof require === 'function') { subtle = require('crypto').webcrypto.subtle; }
    if (!subtle) { return Promise.reject(new Error('integrity-unverifiable: Web Crypto is unavailable (insecure context)')); }
    return subtle.digest('SHA-256', bytes).then(function (buf) {
      return Array.prototype.map.call(new Uint8Array(buf), function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
    });
  }

  // ------------------------------------------------------------------
  // Content policy (on the parsed response).
  // ------------------------------------------------------------------
  function walkStrings(value, fn, path) {
    if (typeof value === 'string') { fn(value, path); return; }
    if (Array.isArray(value)) { value.forEach(function (v, k) { walkStrings(v, fn, path + '[' + k + ']'); }); return; }
    if (value && typeof value === 'object') {
      Object.keys(value).forEach(function (k) { fn(k, path + '{key}'); walkStrings(value[k], fn, path + '.' + k); });
    }
  }

  // Figure-level policy shared with the generic sc.figure-bundle.v1 renderer.
  function figurePolicyErrors(fig, config, extra) {
    var errors = [];
    if (!fig || !Array.isArray(fig.data) || !fig.layout || typeof fig.layout !== 'object') { errors.push('figure has no data/layout'); return errors; }
    if (fig.frames) { errors.push('figure frames are not allowed'); }
    ['images', 'template', 'updatemenus', 'sliders'].forEach(function (k) { if (k in fig.layout) { errors.push('layout.' + k + ' is not allowed'); } });
    fig.data.forEach(function (t, k) { if (!ALLOWED_TRACES[t && t.type]) { errors.push('trace ' + k + ' type ' + String(t && t.type) + ' is not allowed'); } });
    walkStrings({ figure: fig, config: config, table: extra }, function (s, path) {
      if (FORBIDDEN_TEXT.test(s)) { errors.push('forbidden markup or link in ' + path); }
    }, '');
    return errors;
  }

  function policyErrors(resp, plotlyVersion) {
    var errors = [];
    if (!resp || resp.ok !== true) { errors.push('response is not a successful compile result'); return errors; }
    if (resp.schema !== RESPONSE_SCHEMA) { errors.push('unexpected response schema ' + String(resp.schema)); }
    var p = resp.plotly || {};
    var fig = p.figure || {};
    if (p.plotlyJsVersion !== EXPECTED_PLOTLY_JS) { errors.push('figure compiled for plotly.js ' + String(p.plotlyJsVersion) + ', Lab pins ' + EXPECTED_PLOTLY_JS); }
    if (plotlyVersion !== undefined && plotlyVersion !== EXPECTED_PLOTLY_JS) { errors.push('loaded plotly.js is ' + String(plotlyVersion) + ', expected ' + EXPECTED_PLOTLY_JS); }
    errors = errors.concat(figurePolicyErrors(fig, p.config, resp.accessibleAlternative));
    if (!Array.isArray(fig.data) || !fig.layout || typeof fig.layout !== 'object') { return errors; }
    var b = resp.figureBundle || {};
    if (!EVIDENCE_TEXT[b.evidenceClass]) { errors.push('figure bundle has no recognised evidenceClass'); }
    if (!resp.accessibleAlternative || !Array.isArray(resp.accessibleAlternative.panels)) { errors.push('accessible alternative is missing'); }
    return errors;
  }

  // Resolves with {response, specSha256, resultHash} or rejects with a list of reasons.
  function verify(text, plotlyVersion) {
    return Promise.resolve().then(function () {
      if (typeof text !== 'string' || text.length > MAX_RESPONSE_CHARS) { throw ['response is empty or larger than 8 MiB']; }
      var tree, resp;
      try { tree = parseLexical(text); resp = JSON.parse(text); } catch (e) { throw [String(e.message || e)]; }
      var errors = policyErrors(resp, plotlyVersion);
      if (errors.length) { throw errors; }
      var figureNode = member(member(tree, 'plotly'), 'figure');
      var withoutExport = { t: 'o', v: tree.v.filter(function (kv) { return kv[0] !== 'staticExport' && kv[0] !== 'resultHash'; }) };
      return Promise.all([sha256Hex(canonical(figureNode)), sha256Hex(canonical(withoutExport))]).then(function (h) {
        var bad = [];
        if (h[0] !== resp.plotly.specSha256) { bad.push('specSha256 mismatch: figure was altered after Workbench compiled it'); }
        if (h[1] !== resp.resultHash) { bad.push('resultHash mismatch: response was altered after Workbench compiled it'); }
        if (resp.figureBundle.rendering && resp.figureBundle.rendering.visualizationSpecSha256 !== h[0]) { bad.push('figure bundle does not reference this figure spec'); }
        if (bad.length) { throw bad; }
        return { response: resp, specSha256: h[0], resultHash: h[1] };
      }, function (e) { throw [String(e.message || e)]; });
    });
  }

  // ------------------------------------------------------------------
  // DOM rendering (text nodes only; server HTML is never injected).
  // ------------------------------------------------------------------
  function el(tag, attrs, text) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (text !== undefined && text !== null) { n.textContent = String(text); }
    return n;
  }

  function cellText(v) { return v === null || v === undefined ? 'not reported' : String(v); }

  function accessibleTables(alt) {
    var wrap = el('div', { 'class': 'sc-lab-fig02a-tables' });
    alt.panels.forEach(function (panel) {
      var table = el('table', { 'data-panel-id': panel.panelId });
      table.appendChild(el('caption', null, alt.caption + ' — ' + panel.title));
      var tr = el('tr');
      panel.columns.forEach(function (c) { tr.appendChild(el('th', { scope: 'col' }, c.label + (c.unit ? ' (' + c.unit + ')' : ''))); });
      table.appendChild(el('thead')).appendChild(tr);
      var body = table.appendChild(el('tbody'));
      panel.rows.forEach(function (row) {
        var r = body.appendChild(el('tr'));
        panel.columns.forEach(function (c, k) { r.appendChild(el(k === 0 ? 'th' : 'td', k === 0 ? { scope: 'row' } : null, cellText(row[c.key]))); });
      });
      var list = el('ul', { 'class': 'sc-lab-fig02a-summary' });
      (panel.descriptiveSummary || []).forEach(function (s) { list.appendChild(el('li', null, s)); });
      wrap.appendChild(table); wrap.appendChild(list);
    });
    return wrap;
  }

  function summaryText(resp) {
    var parts = [resp.accessibleAlternative.caption];
    resp.accessibleAlternative.panels.forEach(function (p) { parts.push(p.title + ': ' + (p.descriptiveSummary || []).join('; ')); });
    return parts.join('. ');
  }

  function showRefusal(out, reasons) {
    out.textContent = '';
    var box = out.appendChild(el('div', { 'class': 'sc-lab-fig02a-refusal', role: 'alert', 'data-fig02a-state': 'refused' }));
    box.appendChild(el('strong', null, 'Preview refused. Nothing was rendered.'));
    var ul = box.appendChild(el('ul'));
    reasons.forEach(function (r) { ul.appendChild(el('li', null, r)); });
  }

  function render(out, text) {
    var Plotly = root.Plotly;
    if (!Plotly || typeof Plotly.newPlot !== 'function') { showRefusal(out, ['vendored plotly.js ' + EXPECTED_PLOTLY_JS + ' is not loaded']); return Promise.resolve(false); }
    return verify(text, Plotly.version).then(function (ok) {
      var resp = ok.response, b = resp.figureBundle;
      out.textContent = '';
      var card = out.appendChild(el('figure', { 'class': 'sc-lab-fig02a-card', 'data-fig02a-state': 'rendered', 'data-spec-sha256': ok.specSha256 }));
      card.appendChild(el('p', { 'class': 'sc-lab-fig02a-evidence', 'data-evidence-class': b.evidenceClass }, EVIDENCE_TEXT[b.evidenceClass]));
      card.appendChild(el('p', { 'class': 'sc-lab-fig02a-review' },
        'Review: scientific ' + b.review.scientific + ', rights ' + b.review.rights + ', publication ' + b.review.publication +
        '. Not publication-approved. Preview only; Workbench is the numerical authority.'));
      var plot = card.appendChild(el('div', { 'class': 'sc-lab-fig02a-plot', role: 'img', 'aria-label': summaryText(resp) }));
      var cap = card.appendChild(el('figcaption', null, resp.accessibleAlternative.caption));
      cap.id = 'sc-lab-fig02a-cap-' + ok.specSha256.slice(0, 12);
      var details = card.appendChild(el('details', { 'class': 'sc-lab-fig02a-data', open: '' }));
      details.appendChild(el('summary', null, 'Data table (accessible alternative)'));
      details.appendChild(accessibleTables(resp.accessibleAlternative));
      var prov = card.appendChild(el('dl', { 'class': 'sc-lab-fig02a-provenance' }));
      [['Figure', b.figureId + ' v' + b.version], ['Spec SHA-256 (verified)', ok.specSha256], ['Result hash (verified)', ok.resultHash],
        ['Input manifest SHA-256', b.provenance.inputManifestSha256], ['Adapter script SHA-256', b.provenance.scriptSha256],
        ['plotly.js', resp.plotly.plotlyJsVersion], ['Generated', b.provenance.generatedAt]].forEach(function (kv) {
        prov.appendChild(el('dt', null, kv[0])); prov.appendChild(el('dd', null, kv[1]));
      });
      var fig = JSON.parse(JSON.stringify(resp.plotly.figure));
      var config = JSON.parse(JSON.stringify(resp.plotly.config || {}));
      config.displaylogo = false; config.showLink = false; config.showSendToCloud = false; config.showEditInChartStudio = false;
      fig.layout.width = undefined; fig.layout.autosize = true;
      return Plotly.newPlot(plot, fig.data, fig.layout, config).then(function () { return true; });
    }, function (reasons) {
      showRefusal(out, Array.isArray(reasons) ? reasons : [String(reasons)]);
      return false;
    });
  }

  // ------------------------------------------------------------------
  // Graph Studio mount (default off; PHP only enqueues this when enabled).
  // ------------------------------------------------------------------
  function findHost() {
    return document.querySelector('#sc-lab-graph-studio-renderer-root [data-gs13583-stage]') ||
      document.querySelector('[data-lab-module="graph-studio"]');
  }

  function mount(host) {
    if (!host || host.querySelector('[data-fig02a-preview]')) { return null; }
    var section = el('section', { 'class': 'sc-lab-fig02a-preview', 'data-fig02a-preview': VERSION, 'aria-label': 'Plotly figure preview (FIG-02A)' });
    section.appendChild(el('h3', null, 'Plotly figure preview'));
    section.appendChild(el('p', { 'class': 'sc-lab-fig02a-help' },
      'Load a Workbench FIG-02A compile response (.json). The file is read in this browser only and is not uploaded or saved.'));
    var input = section.appendChild(el('input', { type: 'file', accept: 'application/json,.json', 'aria-label': 'Workbench FIG-02A compile response' }));
    var out = section.appendChild(el('div', { 'class': 'sc-lab-fig02a-output', 'aria-live': 'polite' }));
    input.addEventListener('change', function () {
      var f = input.files && input.files[0];
      if (!f) { return; }
      if (f.size > MAX_RESPONSE_CHARS) { showRefusal(out, ['response is empty or larger than 8 MiB']); return; }
      f.text().then(function (t) { return render(out, t); });
    });
    host.appendChild(section);
    return { section: section, output: out };
  }

  var mounted = null;
  function boot() {
    var cfg = root.SCLabFig02aPlotlyPreviewConfig || {};
    if (cfg.enabled !== true || typeof document === 'undefined') { return; }
    function attempt() { if (!mounted) { mounted = mount(findHost()); } return !!mounted; }
    function start() {
      if (attempt()) { return; }
      var obs = new MutationObserver(function () { if (attempt()) { obs.disconnect(); } });
      obs.observe(document.body, { childList: true, subtree: true });
      setTimeout(function () { obs.disconnect(); }, 120000);
    }
    if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', start); } else { start(); }
    // Programmatic entry for other Lab modules: dispatch with detail.responseText (a JSON string).
    document.addEventListener('sc-lab:fig02a:preview', function (ev) {
      attempt();
      if (mounted && ev.detail && typeof ev.detail.responseText === 'string') { render(mounted.output, ev.detail.responseText); }
    });
  }

  return {
    version: VERSION, expectedPlotlyJs: EXPECTED_PLOTLY_JS, responseSchema: RESPONSE_SCHEMA,
    parseLexical: parseLexical, canonical: canonical, sha256Hex: sha256Hex,
    policyErrors: policyErrors, figurePolicyErrors: figurePolicyErrors, evidenceText: EVIDENCE_TEXT,
    walkStrings: walkStrings, forbiddenText: FORBIDDEN_TEXT, verify: verify, render: render, mount: mount, boot: boot
  };
}));
