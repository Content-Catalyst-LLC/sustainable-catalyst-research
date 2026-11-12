/*! Sustainable Catalyst Lab — SC-FIG-01 generic sc.figure-bundle.v1 renderer.
 *
 * Renders any governed figure bundle (Workbench, Workspace, Research or Lab producer) together with
 * the artifact files it references, inside the existing Graph Studio stage. Every supplied file must
 * match an artifact hash in the bundle (canonical JSON or raw bytes); a file the bundle does not
 * reference refuses the whole package. Plotly views use the FIG-02A content policy and the vendored
 * plotly.js 3.3.1; PNG artifacts are shown as a verified static fallback. Any other media type, and
 * any referenced artifact that was not supplied, is reported as unsupported or missing — never drawn.
 * Lab renders only: nothing is uploaded, persisted, reviewed or published here. A host that serves
 * released figures (Sustainable Catalyst Research) may pass its release state as
 * options.releaseContext; Lab shows it only when it names the exact verified bundle hash. Release
 * state always comes from Research, never from fields inside the bundle.
 */
(function (root, factory) {
  'use strict';
  var api = factory(root);
  if (typeof module === 'object' && module.exports) { module.exports = api; }
  else { root.SCLabFigureBundleRenderer = api; api.boot(); }
}(typeof self !== 'undefined' ? self : this, function (root) {
  'use strict';

  var VERSION = '0.3.0';
  var BUNDLE_SCHEMA = 'sc.figure-bundle.v1';
  var PLOTLY_MEDIA = 'application/vnd.plotly.v1+json';
  var PNG_MEDIA = 'image/png';
  var MAX_FILE_BYTES = 8 * 1024 * 1024;
  var MAX_FILES = 12;
  var HASH = /^[0-9a-f]{64}$/;
  var FIGURE_ID = /^[A-Za-z][A-Za-z0-9_.-]{2,100}$/;
  var PNG_MAGIC = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  var ENUMS = {
    evidenceClass: ['observed', 'literature-derived', 'modelled', 'hypothetical', 'conceptual', 'status-only'],
    figureType: ['evidence-chart', 'spatial-map', 'screening-map', 'terrain-time-scene', 'sensitivity-heatmap',
      'readiness-heatmap', 'process-schematic', 'other'],
    measurementStatus: ['observed', 'published-literature', 'computed-from-specified-inputs', 'hypothetical-only',
      'conceptual-only', 'not-applicable'],
    accessibilityReview: ['pending', 'passed', 'failed'],
    authorityOwner: ['core', 'site-intelligence', 'workbench', 'lab', 'library', 'workspace', 'local-research-review'],
    reproducibility: ['not-tested', 'synthetic-fixture-tested', 'source-replay-tested', 'source-replay-certified']
  };

  function lib() {
    var f = root.SCLabPlotlyPreviewFIG02A;
    if (!f && typeof require === 'function') { f = require('./graph-studio-plotly-preview-fig02a.js'); }
    if (!f) { throw ['FIG-02A primitives (hashing and content policy) are not loaded']; }
    return f;
  }

  // FIG-02B (Vega-Lite views, verified SVG) takes part only when its module is loaded on the page (its own
  // switch); otherwise those media types stay 'unsupported', exactly as before.
  function fig02b() {
    return root.SCLabVegaLiteFIG02B || (typeof globalThis !== 'undefined' && globalThis.SCLabVegaLiteFIG02B) || null;
  }

  // FIG-04 (MapLibre geospatial views) likewise takes part only when its modules are loaded (its own switch).
  function fig04() {
    var m = root.SCLabMapLibreFIG04 || (typeof globalThis !== 'undefined' && globalThis.SCLabMapLibreFIG04) || null;
    return m && (root.SCLabMapLibreFIG03A || (typeof globalThis !== 'undefined' && globalThis.SCLabMapLibreFIG03A)) ? m : null;
  }

  function isObj(v) { return !!v && typeof v === 'object' && !Array.isArray(v); }
  function nonEmpty(v, min) { return typeof v === 'string' && v.length >= (min || 1); }
  function oneOf(v, key) { return ENUMS[key].indexOf(v) >= 0; }

  // ------------------------------------------------------------------
  // Bundle validation: the Data schema minimum plus cross-field rules
  // (no promotion of modelled, literature or hypothetical values to observed).
  // ------------------------------------------------------------------
  function bundleErrors(b) {
    var e = [];
    if (!isObj(b)) { return ['figure bundle is not a JSON object']; }
    if (b.schema !== BUNDLE_SCHEMA) { e.push('not an ' + BUNDLE_SCHEMA + ' document (schema ' + String(b.schema) + ')'); return e; }
    if (!FIGURE_ID.test(String(b.figureId || ''))) { e.push('figureId is missing or malformed'); }
    if (b.version !== undefined && !(Number.isInteger(b.version) && b.version >= 1)) { e.push('version must be a positive integer'); }
    if (!nonEmpty(b.title, 5)) { e.push('title is missing'); }
    if (!isObj(b.project)) { e.push('project is missing'); }
    if (!oneOf(b.evidenceClass, 'evidenceClass')) { e.push('figure bundle has no recognised evidenceClass'); }
    if (!oneOf(b.figureType, 'figureType')) { e.push('figureType is not recognised'); }
    if (!Array.isArray(b.sourceRefs) || !b.sourceRefs.length) { e.push('sourceRefs must list at least one source'); }
    var sc = b.scientificContext;
    if (!isObj(sc) || !nonEmpty(sc.interpretation, 8) || !Array.isArray(sc.limitations) || !sc.limitations.length) {
      e.push('scientificContext needs an interpretation and at least one limitation');
    } else if (!oneOf(sc.measurementStatus, 'measurementStatus')) {
      e.push('scientificContext.measurementStatus is not recognised');
    } else if ((b.evidenceClass === 'observed') !== (sc.measurementStatus === 'observed')) {
      e.push('evidenceClass ' + b.evidenceClass + ' contradicts measurementStatus ' + sc.measurementStatus + ' (no promotion to observed)');
    }
    var r = b.rendering;
    if (!isObj(r) || r.profileId !== 'sc-institutional-research-v1' || !nonEmpty(r.renderer) || !nonEmpty(r.caption, 15) ||
        !nonEmpty(r.altText, 15) || !oneOf(r.accessibilityReview, 'accessibilityReview')) {
      e.push('rendering needs profile sc-institutional-research-v1, renderer, caption, altText and accessibilityReview');
    } else if (r.visualizationSpecSha256 !== undefined && !HASH.test(r.visualizationSpecSha256)) {
      e.push('rendering.visualizationSpecSha256 is not a SHA-256');
    }
    if (!Array.isArray(b.artifacts) || !b.artifacts.length) { e.push('artifacts must reference at least one view'); }
    else {
      b.artifacts.forEach(function (a, k) {
        if (!isObj(a) || !HASH.test(String(a.sha256 || '')) || !nonEmpty(a.mediaType)) { e.push('artifacts[' + k + '] needs sha256 and mediaType'); }
      });
    }
    var p = b.provenance;
    if (!isObj(p) || !oneOf(p.authorityOwner, 'authorityOwner') || !Array.isArray(p.sourceSnapshotIds) ||
        !nonEmpty(p.generationRunId) || !nonEmpty(p.generatedAt) || !oneOf(p.reproducibility, 'reproducibility')) {
      e.push('provenance needs authorityOwner, sourceSnapshotIds, generationRunId, generatedAt and reproducibility');
    }
    if (!isObj(b.review)) { e.push('review is missing'); }
    return e;
  }

  // ------------------------------------------------------------------
  // Hashing helpers (bytes and canonical JSON via the FIG-02A primitives).
  // ------------------------------------------------------------------
  function bytesSha256(bytes) {
    var subtle = root && root.crypto && root.crypto.subtle;
    if (!subtle && typeof require === 'function') { subtle = require('crypto').webcrypto.subtle; }
    if (!subtle) { return Promise.reject(new Error('integrity-unverifiable: Web Crypto is unavailable (insecure context)')); }
    return subtle.digest('SHA-256', bytes).then(function (buf) {
      return Array.prototype.map.call(new Uint8Array(buf), function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
    });
  }

  function decode(bytes) {
    try { return new TextDecoder('utf-8', { fatal: true }).decode(bytes); } catch (e) { return null; }
  }

  // Resolves {raw, canonical|null, text|null, json|null}.
  function fingerprint(file) {
    var text = decode(file.bytes);
    var tree = null, json = null;
    if (text !== null && /^\s*[{[]/.test(text)) {
      try { tree = lib().parseLexical(text); json = JSON.parse(text); } catch (e) { tree = null; json = null; }
    }
    return Promise.all([bytesSha256(file.bytes), tree ? lib().sha256Hex(lib().canonical(tree)) : null]).then(function (h) {
      return { name: file.name, bytes: file.bytes, raw: h[0], canonical: h[1], text: text, json: json };
    });
  }

  function runtimeRecordHashes(b) {
    return (b.calculationRefs || []).reduce(function (m, r) {
      if (r && r.bandsSha256) { m.push(r.bandsSha256); }
      if (r && r.quantileBandsSha256) { m.push(r.quantileBandsSha256); }
      if (r && r.uncertaintySha256) { m.push(r.uncertaintySha256); }
      return m;
    }, []);
  }

  // SC-FIG-05.3: a map uncertainty layer is drawn only when the runtime's own per-object record was released with
  // the view and every value the view carries equals it exactly (same semantics; nothing imputed or left over).
  function mapUncertaintyErrors(view, b, recs) {
    var e = [], refs = b.calculationRefs || [], fields = isObj(view.fields) ? view.fields : {};
    (Array.isArray(view.layers) ? view.layers : []).forEach(function (l) {
      if (!isObj(l) || l.encode !== 'uncertainty') { return; }
      var ref = refs[l.uncertaintyRef], at = 'layer ' + String(l.id);
      var rec = ref && recs.filter(function (r) { return r.sha256 === ref.uncertaintySha256; })[0];
      if (!rec) { e.push(at + ': the runtime uncertainty record was not supplied, so nothing is drawn'); return; }
      var f = fields[(l.classify || {}).field] || {}, prop = f.uncertainty, vals = isObj(rec.value) ? rec.value.values : null;
      if (!prop || !isObj(vals) || rec.value.semantics !== f.uncertaintyKind) {
        e.push(at + ': the runtime record does not state ' + String(f.uncertaintyKind) + ' values for ' + String(prop)); return;
      }
      var seen = {};
      (Array.isArray(view.objects) ? view.objects : []).forEach(function (o) {
        var p = (o && o.properties) || {};
        if (typeof p[prop] !== 'number') { return; }
        var key = Object.keys(p).filter(function (k) { return typeof p[k] === 'string' && Object.prototype.hasOwnProperty.call(vals, p[k]) && vals[p[k]] === p[prop]; })[0];
        if (key === undefined) { e.push(at + ': ' + String(p.title || 'an object') + ' carries ' + prop + ' that does not equal the runtime record'); return; }
        seen[p[key]] = true;
      });
      Object.keys(vals).forEach(function (k) { if (!seen[k]) { e.push(at + ': the runtime record value for ' + k + ' is not drawn by any object'); } });
    });
    return e;
  }

  function isPng(bytes) {
    if (!bytes || bytes.length < PNG_MAGIC.length) { return false; }
    for (var k = 0; k < PNG_MAGIC.length; k++) { if (bytes[k] !== PNG_MAGIC[k]) { return false; } }
    return true;
  }

  // ------------------------------------------------------------------
  // Package verification.
  //   files: [{name, bytes: Uint8Array}] — exactly one must be the bundle.
  //   expectedBundleSha256 (optional): the hash Workspace or Research registered for the bundle bytes.
  // Resolves {bundle, bundleSha256, views:[...]} or rejects with reasons.
  // Each view: {artifact, state: rendered-plotly|static-png|unsupported|missing, hashMode, spec?, bytes?, reason?}
  // ------------------------------------------------------------------
  function verifyPackage(files, plotlyVersion, expectedBundleSha256) {
    return Promise.resolve().then(function () {
      if (!Array.isArray(files) || !files.length) { throw ['no files supplied']; }
      if (files.length > MAX_FILES) { throw ['at most ' + MAX_FILES + ' files per figure package']; }
      files.forEach(function (f) {
        if (!f || !(f.bytes instanceof Uint8Array) || !f.bytes.length || f.bytes.length > MAX_FILE_BYTES) {
          throw ['each file must be non-empty and at most 8 MiB (' + String(f && f.name) + ')'];
        }
      });
      return Promise.all(files.map(fingerprint)).catch(function (e) { throw [String(e && e.message || e)]; });
    }).then(function (prints) {
      var bundles = prints.filter(function (p) { return isObj(p.json) && p.json.schema === BUNDLE_SCHEMA; });
      if (bundles.length !== 1) { throw ['supply exactly one ' + BUNDLE_SCHEMA + ' file (found ' + bundles.length + ')']; }
      var bp = bundles[0], b = bp.json;
      if (expectedBundleSha256 && bp.raw !== expectedBundleSha256) {
        throw ['figure bundle bytes do not match the registered hash ' + String(expectedBundleSha256).slice(0, 12)];
      }
      var errors = bundleErrors(b);
      if (errors.length) { throw errors; }
      lib().walkStrings(b, function (s, path) { if (lib().forbiddenText.test(s)) { errors.push('forbidden markup or link in bundle' + path); } }, '');
      if (errors.length) { throw errors; }
      var others = prints.filter(function (p) { return p !== bp; });
      // Runtime records the bundle binds by hash are referenced files even where no renderer draws them.
      var used = others.filter(function (p) { return p.canonical && runtimeRecordHashes(b).indexOf(p.canonical) >= 0; });
      var V = fig02b(), G = fig04();
      var pending = b.artifacts.map(function (a) {
        var match = null, mode = null;
        others.forEach(function (p) {
          if (match) { return; }
          if (p.raw === a.sha256) { match = p; mode = 'raw-bytes'; }
          else if (p.canonical && p.canonical === a.sha256) { match = p; mode = 'canonical-json'; }
        });
        if (!match) { return { artifact: a, state: 'missing', reason: 'artifact not supplied; nothing drawn for it' }; }
        used.push(match);
        if (a.mediaType === PLOTLY_MEDIA) {
          var spec = match.json;
          var fig = spec && spec.data ? spec : (spec && spec.figure) || null;
          var policy = lib().figurePolicyErrors(fig, spec && spec.config, null);
          // SC-FIG-05.3: runtime band/fan records travel as package files bound by the bundle's calculationRefs.
          var recs = others.filter(function (p) { return p.canonical && runtimeRecordHashes(b).indexOf(p.canonical) >= 0; });
          recs.forEach(function (p) { if (used.indexOf(p) < 0) { used.push(p); } });
          policy = policy.concat(lib().uncertaintyErrors(fig, b, recs.map(function (p) { return { sha256: p.canonical, value: p.json }; })));
          if (plotlyVersion !== undefined && plotlyVersion !== lib().expectedPlotlyJs) {
            policy.push('loaded plotly.js is ' + String(plotlyVersion) + ', expected ' + lib().expectedPlotlyJs);
          }
          if (policy.length) { throw policy.map(function (m) { return a.sha256.slice(0, 12) + ': ' + m; }); }
          return { artifact: a, state: 'rendered-plotly', hashMode: mode, spec: fig, config: spec.config || {} };
        }
        if (a.mediaType === PNG_MEDIA) {
          if (mode !== 'raw-bytes' || !isPng(match.bytes)) { throw [a.sha256.slice(0, 12) + ': declared image/png but the bytes are not a PNG']; }
          return { artifact: a, state: 'static-png', hashMode: mode, bytes: match.bytes };
        }
        if (V && V.isVegaLiteMedia(a.mediaType)) {
          if (!match.json || match.text === null) { throw [a.sha256.slice(0, 12) + ': declared Vega-Lite but the file is not JSON']; }
          return V.viewErrors(match.text, match.json, b).then(function (errs) {
            if (errs.length) { throw errs.map(function (m) { return a.sha256.slice(0, 12) + ': ' + m; }); }
            return { artifact: a, state: 'rendered-vega-lite', hashMode: mode, spec: match.json };
          });
        }
        if (G && a.mediaType === G.media) {
          if (!match.json) { throw [a.sha256.slice(0, 12) + ': MapLibre view is not JSON']; }
          var mrecs = others.filter(function (p) { return p.canonical && runtimeRecordHashes(b).indexOf(p.canonical) >= 0; });
          mrecs.forEach(function (p) { if (used.indexOf(p) < 0) { used.push(p); } });
          var merrs = mapUncertaintyErrors(match.json, b, mrecs.map(function (p) { return { sha256: p.canonical, value: p.json }; }));
          return G.viewErrors(match.text, match.json, b).then(function (errs) {
            errs = errs.concat(merrs);
            if (errs.length) { throw errs.map(function (m) { return a.sha256.slice(0, 12) + ': ' + m; }); }
            return { artifact: a, state: 'rendered-maplibre', hashMode: mode, spec: match.json };
          });
        }
        if (V && a.mediaType === V.media.svg) {
          if (mode !== 'raw-bytes') { throw [a.sha256.slice(0, 12) + ': an SVG view must match by raw bytes']; }
          if (typeof DOMParser === 'undefined') {
            return { artifact: a, state: 'unsupported', hashMode: mode, reason: 'SVG verification needs a browser; integrity verified, nothing drawn' };
          }
          return V.verifySvg(match.bytes, b).then(function (v) {
            return { artifact: a, state: 'static-svg', hashMode: mode, bytes: match.bytes, svgProvenance: v.provenance };
          }, function (errs) { throw errs.map(function (m) { return a.sha256.slice(0, 12) + ': ' + m; }); });
        }
        return { artifact: a, state: 'unsupported', hashMode: mode,
          reason: 'media type ' + a.mediaType + ' has no Lab renderer; integrity verified, nothing drawn' };
      });
      return Promise.all(pending).then(function (views) { return finish(views); });
      function finish(views) {
      var stray = others.filter(function (p) { return used.indexOf(p) < 0; });
      if (stray.length) {
        throw stray.map(function (p) { return (p.name || 'file') + ' is not referenced by the figure bundle (hash ' + p.raw.slice(0, 12) + ')'; });
      }
      var specHash = b.rendering.visualizationSpecSha256;
      if (specHash && !b.artifacts.some(function (a) { return a.sha256 === specHash; })) {
        throw ['rendering.visualizationSpecSha256 is not one of the bundle artifacts'];
      }
      return { bundle: b, bundleSha256: bp.raw, views: views };
      }
    });
  }

  // ------------------------------------------------------------------
  // DOM (text nodes only).
  // ------------------------------------------------------------------
  function el(tag, attrs, text) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (text !== undefined && text !== null) { n.textContent = String(text); }
    return n;
  }

  function cellText(v) { return v === null || v === undefined || (typeof v === 'number' && !isFinite(v)) ? 'not reported' : String(v); }

  function axisTitle(layout, key) {
    var ax = layout && layout[key];
    var t = ax && ax.title;
    return typeof t === 'string' ? t : (t && t.text) || '';
  }

  // Accessible table derived from the verified traces (x and y, with symmetric error arrays when present).
  function traceTable(fig, caption) {
    var wrap = el('div', { 'class': 'sc-lab-fig02a-tables' });
    var xLabel = axisTitle(fig.layout, 'xaxis') || 'x', yLabel = axisTitle(fig.layout, 'yaxis') || 'y';
    fig.data.forEach(function (t, k) {
      var xs = Array.isArray(t.x) ? t.x : [], ys = Array.isArray(t.y) ? t.y : [];
      var n = Math.max(xs.length, ys.length);
      if (!n) { return; }
      var errs = t.error_y && Array.isArray(t.error_y.array) ? t.error_y.array : null;
      var table = wrap.appendChild(el('table', { 'data-trace': String(k) }));
      table.appendChild(el('caption', null, caption + ' — ' + (t.name || 'series ' + (k + 1))));
      var head = table.appendChild(el('thead')).appendChild(el('tr'));
      [xLabel, yLabel].concat(errs ? ['± (reported)'] : []).forEach(function (h) { head.appendChild(el('th', { scope: 'col' }, h)); });
      var body = table.appendChild(el('tbody'));
      for (var i = 0; i < n; i++) {
        var r = body.appendChild(el('tr'));
        r.appendChild(el('th', { scope: 'row' }, cellText(xs[i])));
        r.appendChild(el('td', null, cellText(ys[i])));
        if (errs) { r.appendChild(el('td', null, cellText(errs[i]))); }
      }
    });
    return wrap;
  }

  // Accessible table from a FIG-02B table model (labels with units, uncertainty, dataset identity).
  function modelTable(model) {
    var wrap = el('div', { 'class': 'sc-lab-fig02a-tables' });
    var table = wrap.appendChild(el('table', { 'data-view-table': 'vega-lite' }));
    table.appendChild(el('caption', null, model.caption));
    var head = table.appendChild(el('thead')).appendChild(el('tr'));
    model.columns.forEach(function (h) { head.appendChild(el('th', { scope: 'col' }, h)); });
    var body = table.appendChild(el('tbody'));
    model.rows.forEach(function (r) {
      var tr = body.appendChild(el('tr'));
      r.forEach(function (v, k) { tr.appendChild(el(k === 0 ? 'th' : 'td', k === 0 ? { scope: 'row' } : null, v)); });
    });
    if (model.truncated) { wrap.appendChild(el('p', { 'class': 'sc-lab-fig02a-help' }, model.truncated + ' further rows are in the dataset and not listed here.')); }
    wrap.appendChild(el('p', { 'class': 'sc-lab-fig02b-uncertainty' }, model.uncertainty));
    wrap.appendChild(el('p', { 'class': 'sc-lab-fig02b-dataset' }, 'Dataset: ' + model.dataset));
    return wrap;
  }

  function plotlySvgText(plot) {
    return root.Plotly.toImage(plot, { format: 'svg' }).then(function (url) {
      var comma = url.indexOf(',');
      var head = url.slice(0, comma), body = url.slice(comma + 1);
      return /;base64$/.test(head) ? new TextDecoder().decode(Uint8Array.from(atob(body), function (c) { return c.charCodeAt(0); })) : decodeURIComponent(body);
    });
  }

  // Export control: validated, provenance-bearing SVG plus its receipt, both offered as local downloads.
  function exportControl(slot, b, bundleSha256, artifact, spec, svgText) {
    var V = fig02b();
    var box = slot.appendChild(el('div', { 'class': 'sc-lab-fig02b-export' }));
    var button = box.appendChild(el('button', { type: 'button', 'class': 'sc-lab-fig02b-export-button' }, 'Export verified SVG'));
    var status = box.appendChild(el('p', { 'class': 'sc-lab-fig02a-help', 'aria-live': 'polite' }));
    function run() {
      status.textContent = 'Exporting…';
      return svgText().then(function (text) { return V.exportSvg(text, b, bundleSha256, artifact, spec); }).then(function (x) {
        slot.setAttribute('data-export-state', 'verified');
        slot.setAttribute('data-export-sha256', x.receipt.sha256);
        Array.prototype.slice.call(box.querySelectorAll('a')).forEach(function (a) { URL.revokeObjectURL(a.href); a.remove(); });
        var stem = x.receipt.artifactEntry.artifactRef.replace(/\.svg$/, '');
        box.appendChild(el('a', { href: URL.createObjectURL(new Blob([x.bytes], { type: 'image/svg+xml' })), download: stem + '.svg', 'class': 'sc-lab-fig02b-download' }, 'Download SVG'));
        box.appendChild(el('a', { href: URL.createObjectURL(new Blob([JSON.stringify(x.receipt, null, 1)], { type: 'application/json' })), download: stem + '.receipt.json', 'class': 'sc-lab-fig02b-download' }, 'Download export receipt'));
        status.textContent = 'Verified SVG ' + x.receipt.bytes + ' bytes, SHA-256 ' + x.receipt.sha256 + '. Provenance embedded; nothing uploaded or saved.';
        return x;
      }, function (reasons) {
        slot.setAttribute('data-export-state', 'refused');
        status.textContent = 'Export refused: ' + (Array.isArray(reasons) ? reasons.join('; ') : String(reasons));
        return { refused: reasons };
      });
    }
    button.addEventListener('click', run);
    return run;
  }

  function showRefusal(out, reasons) {
    out.textContent = '';
    var box = out.appendChild(el('div', { 'class': 'sc-lab-fig02a-refusal', role: 'alert', 'data-figure-bundle-state': 'refused' }));
    box.appendChild(el('strong', null, 'Figure package refused. Nothing was rendered.'));
    var ul = box.appendChild(el('ul'));
    reasons.forEach(function (r) { ul.appendChild(el('li', null, r)); });
  }

  // Bundles come from several producers: a modelled figure names its own computing authority.
  var AUTHORITY = { core: 'Platform Core', 'site-intelligence': 'Site Intelligence', workbench: 'Workbench', lab: 'Lab',
    library: 'Library', workspace: 'Workspace', 'local-research-review': 'local research review' };

  function evidenceLabel(b) {
    var who = AUTHORITY[(b.provenance || {}).authorityOwner];
    if (b.evidenceClass === 'modelled' && who) { return 'Modelled — ' + who + ' computation, not a measurement'; }
    return lib().evidenceText[b.evidenceClass];
  }

  // Release state supplied by the host. Only Sustainable Catalyst Research releases figures; Lab never
  // infers release or approval from the bundle's own review fields.
  var RELEASE_STATES = ['released'];
  var RELEASE_KEYS = ['authority', 'state', 'label', 'bundleSha256', 'articleRevision', 'releasedAt'];

  function releaseState(ctx, bundleSha256) {
    if (ctx === undefined || ctx === null) { return { state: 'none' }; }
    var shaped = isObj(ctx) && Object.keys(ctx).every(function (k) { return RELEASE_KEYS.indexOf(k) >= 0; }) &&
      ctx.authority === 'sc-research' && RELEASE_STATES.indexOf(ctx.state) >= 0 && HASH.test(String(ctx.bundleSha256 || '')) &&
      typeof ctx.label === 'string' && ctx.label.length > 0 && ctx.label.length <= 200 &&
      (ctx.articleRevision === undefined || (typeof ctx.articleRevision === 'number' && Math.floor(ctx.articleRevision) === ctx.articleRevision && ctx.articleRevision > 0)) &&
      (ctx.releasedAt === undefined || ctx.releasedAt === null || (typeof ctx.releasedAt === 'string' && ctx.releasedAt.length <= 40));
    if (!shaped) { return { state: 'invalid' }; }
    if (ctx.bundleSha256 !== bundleSha256) { return { state: 'mismatch' }; }
    return { state: ctx.state, ctx: ctx };
  }

  function header(card, b, bundleSha256, releaseContext) {
    card.appendChild(el('p', { 'class': 'sc-lab-fig02a-evidence', 'data-evidence-class': b.evidenceClass }, evidenceLabel(b)));
    card.appendChild(el('p', { 'class': 'sc-lab-figure-bundle-title' }, b.title));
    var rel = releaseState(releaseContext, bundleSha256);
    var text;
    if (rel.ctx) {
      var c = rel.ctx;
      text = c.label + ' Released by Sustainable Catalyst Research' +
        (c.articleRevision ? ' with article revision ' + c.articleRevision : '') + (c.releasedAt ? ' on ' + c.releasedAt : '') +
        ', after signed Workspace approval of this exact bundle. Lab only renders it.';
    } else if (rel.state === 'none') {
      text = 'Not released here. Lab renders figures only; publication is approved in Workspace and release state is held by Sustainable Catalyst Research.';
    } else {
      text = 'Release information was ignored because it ' + (rel.state === 'mismatch' ? 'names a different bundle' : 'is not a valid Research release record') +
        '. Lab renders figures only; release state is held by Sustainable Catalyst Research.';
    }
    card.appendChild(el('p', { 'class': 'sc-lab-figure-bundle-release', 'data-release-state': rel.state }, text));
    var rv = b.review || {};
    card.appendChild(el('p', { 'class': 'sc-lab-fig02a-review' },
      'Producer review notes (not release authority): scientific ' + cellText(rv.scientific) + ', rights ' + cellText(rv.rights) +
      ', publication ' + cellText(rv.publication) + '. Measurement status: ' + b.scientificContext.measurementStatus + '.'));
  }

  function context(card, b, bundleSha256, views) {
    var sc = b.scientificContext;
    card.appendChild(el('p', { 'class': 'sc-lab-figure-bundle-interpretation' }, sc.interpretation));
    var lim = card.appendChild(el('ul', { 'class': 'sc-lab-figure-bundle-limitations', 'aria-label': 'Limitations' }));
    sc.limitations.forEach(function (l) { lim.appendChild(el('li', null, l)); });
    // SC-FIG-05.3: say plainly whether uncertainty is shown and who computed it.
    var US = { 'runtime-computed': 'Uncertainty: computed by the Workspace runtime and shown as given (' + (sc.uncertaintyNotation || 'see legend') + ').',
      'unavailable': 'Uncertainty unavailable: the run reported none, so none is drawn or derived.',
      'as-published': 'Uncertainty: as published by the cited source.', 'not-applicable': 'Uncertainty: not applicable to this figure.' };
    if (US[sc.uncertaintyStatus]) {
      card.appendChild(el('p', { 'class': 'sc-lab-figure-bundle-uncertainty', 'data-uncertainty-status': sc.uncertaintyStatus }, US[sc.uncertaintyStatus]));
    }
    if (Array.isArray(sc.doNotInfer) && sc.doNotInfer.length) {
      card.appendChild(el('p', { 'class': 'sc-lab-figure-bundle-donotinfer' }, 'Do not infer: ' + sc.doNotInfer.join('; ')));
    }
    var prov = card.appendChild(el('dl', { 'class': 'sc-lab-fig02a-provenance' }));
    var p = b.provenance;
    [['Figure', b.figureId + ' v' + (b.version || 1)], ['Bundle SHA-256 (bytes)', bundleSha256],
      ['Authority', p.authorityOwner], ['Generation run', p.generationRunId], ['Generated', p.generatedAt],
      ['Reproducibility', p.reproducibility], ['Input manifest SHA-256', p.inputManifestSha256 || 'not reported'],
      ['Sources', b.sourceRefs.map(function (s) { return s.sourceId + (s.sha256 ? ' (' + s.sha256.slice(0, 12) + ')' : ''); }).join('; ')]
    ].concat(views.map(function (v) {
      return ['View ' + v.artifact.mediaType, v.artifact.sha256 + ' — ' + v.state + (v.hashMode ? ' (verified, ' + v.hashMode + ')' : '')];
    })).forEach(function (kv) { prov.appendChild(el('dt', null, kv[0])); prov.appendChild(el('dd', null, kv[1])); });
  }

  function render(out, files, expectedBundleSha256, options) {
    var releaseContext = isObj(options) ? options.releaseContext : undefined;
    var Plotly = root.Plotly;
    var loaded = Plotly && typeof Plotly.newPlot === 'function' ? Plotly.version : undefined;
    return verifyPackage(files, loaded, expectedBundleSha256).then(function (ok) {
      var b = ok.bundle, draws = [], exports = [], V = fig02b(), G = fig04();
      out.textContent = '';
      var card = out.appendChild(el('figure', { 'class': 'sc-lab-fig02a-card sc-lab-figure-bundle-card', 'data-figure-bundle-state': 'verified',
        'data-figure-id': b.figureId, 'data-bundle-sha256': ok.bundleSha256 }));
      header(card, b, ok.bundleSha256, releaseContext);
      card.setAttribute('data-release-state', releaseState(releaseContext, ok.bundleSha256).state);
      ok.views.forEach(function (v) {
        var slot = card.appendChild(el('div', { 'class': 'sc-lab-figure-bundle-view', 'data-view-state': v.state, 'data-artifact-sha256': v.artifact.sha256 }));
        if (v.state === 'rendered-plotly') {
          if (!loaded) { slot.setAttribute('data-view-state', 'unsupported'); slot.appendChild(el('p', null, 'plotly.js ' + lib().expectedPlotlyJs + ' is not loaded; nothing drawn.')); return; }
          var plot = slot.appendChild(el('div', { 'class': 'sc-lab-fig02a-plot', role: 'img', 'aria-label': b.rendering.altText }));
          var details = slot.appendChild(el('details', { 'class': 'sc-lab-fig02a-data' }));
          details.appendChild(el('summary', null, 'Data table (accessible alternative)'));
          details.appendChild(traceTable(v.spec, b.rendering.caption));
          var fig = JSON.parse(JSON.stringify(v.spec));
          var config = JSON.parse(JSON.stringify(v.config || {}));
          config.displaylogo = false; config.showLink = false; config.showSendToCloud = false; config.showEditInChartStudio = false;
          fig.layout.width = undefined; fig.layout.autosize = true;
          draws.push(Plotly.newPlot(plot, fig.data, fig.layout, config));
          if (V) { exports.push(exportControl(slot, b, ok.bundleSha256, v.artifact, null, function () { return plotlySvgText(plot); })); }
        } else if (v.state === 'rendered-vega-lite') {
          var holder = slot.appendChild(el('div', { 'class': 'sc-lab-fig02b-plot' }));
          var vtable = slot.appendChild(el('details', { 'class': 'sc-lab-fig02a-data' }));
          vtable.appendChild(el('summary', null, 'Data table (accessible alternative)'));
          vtable.appendChild(modelTable(V.tableModel(v.spec, b.rendering.caption)));
          draws.push(V.createView(holder, v.spec, b).then(function (view) {
            slot.setAttribute('data-vega-view', 'ready');
            exports.push(exportControl(slot, b, ok.bundleSha256, v.artifact, v.spec, function () { return view.toSVG(); }));
          }, function (reasons) {
            slot.setAttribute('data-view-state', 'refused');
            holder.appendChild(el('p', { 'class': 'sc-lab-fig02a-refusal', role: 'alert' }, 'Vega-Lite view not drawn: ' + (Array.isArray(reasons) ? reasons.join('; ') : String(reasons))));
          }));
        } else if (v.state === 'rendered-maplibre') {
          var mopts = Object.assign({}, options && options.maplibre, { bundleSha256: ok.bundleSha256 });
          draws.push(G.createMap(slot, v.spec, b, mopts).then(function (h) {
            if (h && h.state !== 'ready') { slot.setAttribute('data-view-state', 'rendered-maplibre-static'); return; }
            // SC-FIG-04.1: the static PNG export runs like the SVG export (resolves the export or {refused}).
            if (h && h.runExport) { exports.push(h.runExport); }
          }));
        } else if (v.state === 'static-svg') {
          // Verified, policy-clean SVG bytes shown as an image (no script context); nothing is fetched.
          var surl = URL.createObjectURL(new Blob([v.bytes], { type: 'image/svg+xml' }));
          slot.appendChild(el('img', { src: surl, alt: b.rendering.altText, 'class': 'sc-lab-figure-bundle-static' }));
          slot.appendChild(el('p', { 'class': 'sc-lab-fig02a-help' }, 'Static SVG view (verified bytes' +
            (v.svgProvenance ? '; embedded provenance matches this figure, view ' + v.svgProvenance.viewArtifactSha256.slice(0, 12) : '; no embedded provenance') + ').'));
        } else if (v.state === 'static-png') {
          // Verified bytes only, shown through a local object URL; nothing is fetched.
          var url = URL.createObjectURL(new Blob([v.bytes], { type: PNG_MEDIA }));
          slot.appendChild(el('img', { src: url, alt: b.rendering.altText, 'class': 'sc-lab-figure-bundle-static' }));
          slot.appendChild(el('p', { 'class': 'sc-lab-fig02a-help' }, 'Static view (verified PNG bytes).'));
        } else {
          slot.appendChild(el('p', { 'class': 'sc-lab-figure-bundle-unavailable' },
            (v.state === 'missing' ? 'Missing view: ' : 'Unsupported view: ') + v.artifact.mediaType + ' — ' + v.reason + '.'));
        }
      });
      card.appendChild(el('figcaption', null, b.rendering.caption));
      context(card, b, ok.bundleSha256, ok.views);
      var drawn = ok.views.filter(function (v) { return ['rendered-plotly', 'static-png', 'rendered-vega-lite', 'static-svg', 'rendered-maplibre'].indexOf(v.state) >= 0; }).length;
      card.setAttribute('data-figure-bundle-state', drawn ? 'rendered' : 'metadata-only');
      return Promise.all(draws).then(function () {
        return { state: card.getAttribute('data-figure-bundle-state'), views: ok.views, bundleSha256: ok.bundleSha256, exports: exports };
      });
    }, function (reasons) {
      showRefusal(out, Array.isArray(reasons) ? reasons : [String(reasons)]);
      return { state: 'refused', reasons: reasons };
    });
  }

  // ------------------------------------------------------------------
  // Graph Studio mount (default off; PHP only enqueues this when enabled).
  // ------------------------------------------------------------------
  function findHost() {
    return document.querySelector('#sc-lab-graph-studio-renderer-root [data-gs13583-stage]') ||
      document.querySelector('[data-lab-module="graph-studio"]');
  }

  function readFiles(list) {
    return Promise.all(Array.prototype.map.call(list, function (f) {
      if (f.size > MAX_FILE_BYTES) { return Promise.resolve({ name: f.name, bytes: new Uint8Array(0) }); }
      return f.arrayBuffer().then(function (buf) { return { name: f.name, bytes: new Uint8Array(buf) }; });
    }));
  }

  function mount(host) {
    if (!host || host.querySelector('[data-figure-bundle-renderer]')) { return null; }
    var section = el('section', { 'class': 'sc-lab-fig02a-preview', 'data-figure-bundle-renderer': VERSION, 'aria-label': 'Figure bundle viewer (sc.figure-bundle.v1)' });
    section.appendChild(el('h3', null, 'Figure bundle viewer'));
    section.appendChild(el('p', { 'class': 'sc-lab-fig02a-help' },
      'Select one sc.figure-bundle.v1 file and the view files it references (Plotly, Vega-Lite or MapLibre map-view JSON, SVG, PNG). Files are verified and read in this browser only; nothing is uploaded or saved.'));
    var input = section.appendChild(el('input', { type: 'file', multiple: '', accept: 'application/json,.json,image/png,.png,image/svg+xml,.svg', 'aria-label': 'Figure bundle and referenced view files' }));
    var out = section.appendChild(el('div', { 'class': 'sc-lab-fig02a-output', 'aria-live': 'polite' }));
    input.addEventListener('change', function () {
      if (!input.files || !input.files.length) { return; }
      readFiles(input.files).then(function (files) { return render(out, files); });
    });
    host.appendChild(section);
    return { section: section, output: out };
  }

  var mounted = null;
  function boot() {
    var cfg = root.SCLabFigureBundleRendererConfig || {};
    if (cfg.enabled !== true || typeof document === 'undefined') { return; }
    function attempt() { if (!mounted) { mounted = mount(findHost()); } return !!mounted; }
    function start() {
      if (attempt()) { return; }
      var obs = new MutationObserver(function () { if (attempt()) { obs.disconnect(); } });
      obs.observe(document.body, { childList: true, subtree: true });
      setTimeout(function () { obs.disconnect(); }, 120000);
    }
    if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', start); } else { start(); }
    // Programmatic entry for other Lab modules: detail.files = [{name, bytes: Uint8Array}],
    // optional detail.expectedBundleSha256.
    document.addEventListener('sc-lab:figure-bundle:render', function (ev) {
      attempt();
      if (mounted && ev.detail && Array.isArray(ev.detail.files)) { render(mounted.output, ev.detail.files, ev.detail.expectedBundleSha256); }
    });
  }

  return {
    version: VERSION, bundleSchema: BUNDLE_SCHEMA, plotlyMediaType: PLOTLY_MEDIA,
    bundleErrors: bundleErrors, verifyPackage: verifyPackage, releaseState: releaseState, render: render, mount: mount, boot: boot
  };
}));
