/*! Sustainable Catalyst Lab — MapLibre geospatial figure renderer (SC-FIG-04).
 *
 * Draws a verified sc-lab-maplibre-view (1.0 or 1.1) with the vendored MapLibre GL JS 5.24.0 CSP build.
 * The view is validated by graph-studio-maplibre-fig03a.js (Site Intelligence digests, evidence classes,
 * constant styles, no URLs) before anything here runs. Controlled network access:
 *
 * - the compiled style has one inline GeoJSON source and no glyphs, sprite, tiles or basemap;
 * - the worker script is fetched from the same origin only, its bytes are checked against the pinned
 *   SHA-384 and it is started from a blob: URL (so CSP needs worker-src blob:, and nothing unverified runs);
 * - every resource MapLibre asks for is recorded (data-map-requests); none is expected;
 * - attribution, legends, tables and feature details are built with textContent, never HTML.
 *
 * Accessible alternatives: layer checkboxes, a scientific legend with units, a labelled time slider with
 * previous and next buttons, a side-by-side comparison with a change table, calculation results with their
 * uncertainty, and the full data table. If WebGL is unavailable the map is skipped and the rest stays.
 *
 * SC-FIG-04.1: a static PNG export of what is on screen (one map, or both comparison maps) with the title,
 * evidence class, time or comparison state, visible-layer legend with units, calculation and uncertainty
 * text, attribution and provenance drawn in. A receipt binding it to the bundle bytes, view, sources and
 * calculations is embedded as a PNG tEXt chunk (sc:static-map-receipt) and offered as JSON; verifyExport()
 * checks both offline. Calculations show a runtime confidence interval when one was reported and say that
 * trend uncertainty is unavailable when nothing was.
 */
(function (root, factory) {
  'use strict';
  var api = factory(root);
  if (typeof module === 'object' && module.exports) { module.exports = api; }
  else { root.SCLabMapLibreFIG04 = api; }
}(typeof self !== 'undefined' ? self : this, function (root) {
  'use strict';

  var VERSION = '0.2.0';
  var MAPLIBRE_VERSION = '5.24.0';
  var MEDIA = 'application/vnd.sc.maplibre-view.v1+json';

  function contract() {
    var c = root.SCLabMapLibreFIG03A || (typeof globalThis !== 'undefined' && globalThis.SCLabMapLibreFIG03A);
    if (!c && typeof require === 'function') { c = require('./graph-studio-maplibre-fig03a.js'); }
    if (!c) { throw ['MapLibre view contract is not loaded']; }
    return c;
  }
  function config() { return root.SCLabMapLibreFIG04Config || {}; }
  function gl() { return root.maplibregl; }

  // Library check: the vendored build, at the pinned version, with its CSP worker hook.
  function libraryErrors() {
    var m = gl();
    if (!m || typeof m.Map !== 'function' || typeof m.setWorkerUrl !== 'function') { return ['MapLibre GL JS ' + MAPLIBRE_VERSION + ' (CSP build) is not loaded']; }
    var v = typeof m.getVersion === 'function' ? m.getVersion() : m.version;
    if (v !== MAPLIBRE_VERSION) { return ['MapLibre GL JS ' + String(v) + ' is loaded, but ' + MAPLIBRE_VERSION + ' is pinned']; }
    return [];
  }

  function b64(buf) {
    var s = '', a = new Uint8Array(buf);
    for (var i = 0; i < a.length; i++) { s += String.fromCharCode(a[i]); }
    return root.btoa(s);
  }

  // Worker: same-origin bytes, checked against the pinned SHA-384, run from a blob: URL.
  var workerPromise = null;
  function prepareWorker(url, integrity) {
    if (workerPromise) { return workerPromise; }
    workerPromise = Promise.resolve().then(function () {
      var e = libraryErrors();
      if (e.length) { throw e; }
      if (typeof url !== 'string' || !/^sha384-[A-Za-z0-9+/]{64}$/.test(String(integrity))) { throw ['MapLibre worker URL and SHA-384 integrity are not configured']; }
      var abs = new URL(url, root.location.href);
      if (abs.origin !== root.location.origin) { throw ['MapLibre worker must be served from this site (' + abs.origin + ' refused)']; }
      return root.fetch(abs.href, { credentials: 'omit', cache: 'no-cache', redirect: 'error' });
    }).then(function (r) {
      if (!r.ok) { throw ['MapLibre worker could not be loaded (HTTP ' + r.status + ')']; }
      return r.arrayBuffer();
    }).then(function (buf) {
      return root.crypto.subtle.digest('SHA-384', buf).then(function (h) {
        if ('sha384-' + b64(h) !== integrity) { throw ['MapLibre worker bytes do not match the pinned SHA-384; not started']; }
        var blobUrl = URL.createObjectURL(new Blob([buf], { type: 'text/javascript' }));
        gl().setWorkerUrl(blobUrl);
        return { workerUrl: blobUrl, integrity: integrity };
      });
    });
    workerPromise.catch(function () { workerPromise = null; });
    return workerPromise;
  }

  function webglAvailable() {
    try {
      var c = root.document.createElement('canvas');
      return !!(c.getContext('webgl2') || c.getContext('webgl'));
    } catch (e) { return false; }
  }

  function el(tag, attrs, text) {
    var n = root.document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (text !== undefined && text !== null) { n.textContent = String(text); }
    return n;
  }
  function swatch(color) {
    var s = el('span', { 'class': 'sc-lab-fig04-swatch', 'aria-hidden': 'true' });
    s.style.backgroundColor = color; // CSSOM, not a style attribute (strict CSP)
    return s;
  }
  function table(model) {
    var t = el('table', { 'class': 'sc-lab-fig02a-table' });
    t.appendChild(el('caption', null, model.caption));
    var head = t.appendChild(el('thead')).appendChild(el('tr'));
    model.columns.forEach(function (h) { head.appendChild(el('th', { scope: 'col' }, h)); });
    var body = t.appendChild(el('tbody'));
    model.rows.forEach(function (r) {
      var tr = body.appendChild(el('tr'));
      r.forEach(function (v, k) { tr.appendChild(el(k === 0 ? 'th' : 'td', k === 0 ? { scope: 'row' } : null, v)); });
    });
    return t;
  }
  function shortTime(iso) { return String(iso).slice(0, 10); }

  function legendBox(spec) {
    var box = el('div', { 'class': 'sc-lab-fig04-legend', role: 'group', 'aria-label': 'Map legend' });
    contract().legendModel(spec).forEach(function (g) {
      var item = box.appendChild(el('div', { 'class': 'sc-lab-fig04-legend-layer', 'data-legend-layer': g.layer }));
      item.appendChild(el('p', { 'class': 'sc-lab-fig04-legend-title' }, g.label + (g.evidenceClass ? ' — ' + g.evidenceClass : '')));
      if (g.classes) {
        item.appendChild(el('p', { 'class': 'sc-lab-fig04-legend-field' }, g.field));
        var ul = item.appendChild(el('ul'));
        g.classes.forEach(function (c) { var li = ul.appendChild(el('li')); li.appendChild(swatch(c.color)); li.appendChild(el('span', null, c.label)); });
        if (g.encode === 'uncertainty') {
          item.setAttribute('data-legend-encode', 'uncertainty');
          item.appendChild(el('p', { 'class': 'sc-lab-fig02a-help' }, g.note));
        }
      } else if (g.ramp) {
        item.appendChild(el('p', { 'class': 'sc-lab-fig04-legend-field' }, g.field));
        var ramp = item.appendChild(el('p', { 'class': 'sc-lab-fig04-ramp' }));
        ramp.appendChild(el('span', null, g.rampLabels[0]));
        g.ramp.forEach(function (c) { ramp.appendChild(swatch(c)); });
        ramp.appendChild(el('span', null, g.rampLabels[1]));
        item.appendChild(el('p', { 'class': 'sc-lab-fig02a-help' }, g.note));
      } else {
        var one = item.appendChild(el('p'));
        one.appendChild(swatch(g.color)); one.appendChild(el('span', null, g.geometry));
      }
    });
    return box;
  }

  var KIND_LABELS = { 'standard-error': 'standard error', 'standard-deviation': 'standard deviation', 'ci95-half-width': '95% CI half-width' };
  function sig(x) { return String(Number(Number(x).toPrecision(4))); }

  // SC-FIG-04.1: one calculation's reported uncertainty, as lines of plain text (also drawn into the static
  // export). Only what the runtime reported is shown; with neither a standard error nor an interval the
  // result says plainly that its uncertainty is unavailable.
  function calculationLines(c, ref) {
    var lines = [c.label + ': ' + c.value + (c.uncertainty !== undefined ? ' ± ' + c.uncertainty : '') + ' ' + c.unit +
      (c.uncertainty !== undefined ? ' (' + KIND_LABELS[c.uncertaintyKind] + ')' : '')];
    var iv = c.interval;
    if (iv) {
      lines.push(Math.round(iv.level * 1000) / 10 + '% confidence interval ' + sig(iv.lower) + ' to ' + sig(iv.upper) + ' ' + c.unit +
        ' (rounded for display; ' + (iv.distribution ? iv.distribution + ', ' : '') + 'df ' + iv.degreesOfFreedom +
        '). Computed by the Workspace runtime: ' + iv.method + '.' +
        (iv.consistency === 'estimate-plus-minus-t-standard-error' ? ' Checked by Workbench to equal the estimate ± t × standard error; not an independent reproduction.' : ' Not checked against a standard error.'));
    }
    if (c.uncertainty === undefined && !iv) {
      lines.push('Trend uncertainty unavailable: the Workspace run reported no standard error or interval for this value.');
    } else if (!iv && c.uncertaintyKind === 'standard-error') {
      lines.push('No confidence interval was reported by the run.');
    }
    if (c.assumptions) { lines.push('Assumptions declared by the model, not tested: ' + c.assumptions.statements.join('; ') + '.'); }
    lines.push('Method: ' + c.method + '. Run ' + ref.runRef + ', output SHA-256 ' + ref.outputSha256.slice(0, 16) + '…');
    return lines;
  }

  function calculationsBox(spec, bundle) {
    var calcs = Array.isArray(spec.calculations) ? spec.calculations : [];
    if (!calcs.length) { return null; }
    var dl = el('dl', { 'class': 'sc-lab-fig04-calculations', 'aria-label': 'Calculation results' });
    calcs.forEach(function (c) {
      var ref = bundle.calculationRefs[c.calculationRef];
      var lines = calculationLines(c, ref);
      dl.appendChild(el('dt', null, c.label));
      var dd = dl.appendChild(el('dd', { 'data-calculation-ref': String(c.calculationRef),
        'data-uncertainty': c.interval ? 'interval' : (c.uncertainty !== undefined ? 'reported' : 'unavailable') }));
      dd.appendChild(el('span', null, lines[0].slice(c.label.length + 2)));
      lines.slice(1).forEach(function (t) {
        dd.appendChild(el('span', { 'class': 'sc-lab-fig04-uncertainty-note', 'data-calculation-note': /^Trend uncertainty unavailable/.test(t) ? 'unavailable' : 'detail' }, ' ' + t));
      });
      if (c.interval) {
        dd.setAttribute('data-interval-lower', String(c.interval.lower));
        dd.setAttribute('data-interval-upper', String(c.interval.upper));
      }
    });
    return dl;
  }

  // --- SC-FIG-04.1 static export: PNG with legend, units, state, attribution and an embedded receipt -------

  var EXPORT_SCHEMA = 'sc-lab-static-map-export/1.0';
  var EXPORT_KEYWORD = 'sc:static-map-receipt';
  var PNG_SIG = [137, 80, 78, 71, 13, 10, 26, 10];

  function canonical(v) {
    if (Array.isArray(v)) { return '[' + v.map(canonical).join(',') + ']'; }
    if (v && typeof v === 'object') {
      return '{' + Object.keys(v).sort().map(function (k) { return JSON.stringify(k) + ':' + canonical(v[k]); }).join(',') + '}';
    }
    return JSON.stringify(v);
  }
  function subtle() { return (root.crypto && root.crypto.subtle) || (typeof globalThis !== 'undefined' && globalThis.crypto && globalThis.crypto.subtle); }
  function hex(buf) { return Array.prototype.map.call(new Uint8Array(buf), function (b) { return ('0' + b.toString(16)).slice(-2); }).join(''); }
  function sha256(bytes) { return subtle().digest('SHA-256', bytes).then(hex); }
  function utf8(s) { return new TextEncoder().encode(s); }

  var CRC_TABLE = (function () {
    var t = [];
    for (var n = 0; n < 256; n++) { var c = n; for (var k = 0; k < 8; k++) { c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; } t[n] = c >>> 0; }
    return t;
  }());
  function crc32(bytes) {
    var c = 0xffffffff;
    for (var i = 0; i < bytes.length; i++) { c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8); }
    return (c ^ 0xffffffff) >>> 0;
  }
  function u32(a, i) { return ((a[i] << 24) | (a[i + 1] << 16) | (a[i + 2] << 8) | a[i + 3]) >>> 0; }

  // Strict chunk walk: signature, lengths, CRCs and a single final IEND. Returns null for anything else.
  function pngChunks(bytes) {
    var a = new Uint8Array(bytes), out = [], i = 8;
    for (var k = 0; k < 8; k++) { if (a[k] !== PNG_SIG[k]) { return null; } }
    while (i + 12 <= a.length) {
      var len = u32(a, i), type = String.fromCharCode(a[i + 4], a[i + 5], a[i + 6], a[i + 7]);
      if (i + 12 + len > a.length) { return null; }
      if (crc32(a.subarray(i + 4, i + 8 + len)) !== u32(a, i + 8 + len)) { return null; }
      out.push({ type: type, data: a.subarray(i + 8, i + 8 + len), at: i });
      i += 12 + len;
      if (type === 'IEND') { return i === a.length ? out : null; }
    }
    return null;
  }

  // Adds one tEXt chunk (Latin-1 keyword, ASCII JSON text) just before IEND.
  function withTextChunk(bytes, keyword, text) {
    var a = new Uint8Array(bytes), chunks = pngChunks(a);
    if (!chunks || !/^[\x20-\x7e]+$/.test(text)) { throw new Error('cannot embed the export receipt'); }
    var iend = chunks[chunks.length - 1].at;
    var body = utf8(keyword + '\u0000' + text), chunk = new Uint8Array(12 + body.length);
    chunk.set([body.length >>> 24 & 255, body.length >>> 16 & 255, body.length >>> 8 & 255, body.length & 255], 0);
    chunk.set(utf8('tEXt'), 4); chunk.set(body, 8);
    var crc = crc32(chunk.subarray(4, 8 + body.length));
    chunk.set([crc >>> 24 & 255, crc >>> 16 & 255, crc >>> 8 & 255, crc & 255], 8 + body.length);
    var out = new Uint8Array(a.length + chunk.length);
    out.set(a.subarray(0, iend), 0); out.set(chunk, iend); out.set(a.subarray(iend), iend + chunk.length);
    return out;
  }
  function textChunks(chunks) {
    return chunks.filter(function (c) { return c.type === 'tEXt'; }).map(function (c) {
      var z = Array.prototype.indexOf.call(c.data, 0);
      return { keyword: String.fromCharCode.apply(null, c.data.subarray(0, z)), text: new TextDecoder('latin1').decode(c.data.subarray(z + 1)) };
    });
  }
  function ascii(s) { return String(s).replace(/[^\x20-\x7e]/g, function (ch) { return '\\u' + ('000' + ch.charCodeAt(0).toString(16)).slice(-4); }); }

  // The receipt binds the picture to the exact verified view, bundle sources and calculations, and to the
  // state shown. It never includes the PNG's own hash (that is reported beside it).
  function exportReceipt(spec, bundle, bundleSha256, state, legend, attribution, calcLines, size) {
    return {
      schema: EXPORT_SCHEMA,
      figure: { figureId: bundle.figureId, version: bundle.version, title: bundle.title, evidenceClass: bundle.evidenceClass,
        bundleSha256: /^[0-9a-f]{64}$/.test(String(bundleSha256 || '')) ? bundleSha256 : null,
        measurementStatus: bundle.scientificContext && bundle.scientificContext.measurementStatus || null },
      view: { mediaType: MEDIA, sha256: bundle.artifacts[0].sha256, schema: spec.schema },
      sources: bundle.sourceRefs.map(function (s) { return { sourceId: s.sourceId, sha256: s.sha256 || null, licenceStatus: s.licenceStatus }; }),
      calculations: (bundle.calculationRefs || []).map(function (c) {
        var r = { runRef: c.runRef, outputSha256: c.outputSha256, intervalsSha256: c.intervalsSha256 || null };
        if (c.uncertaintySha256) { r.uncertaintySha256 = c.uncertaintySha256; } // SC-FIG-05.3 uncertainty layer binding
        return r;
      }),
      state: state,
      legend: legend,
      units: (bundle.scientificContext && bundle.scientificContext.units) || '',
      calculationText: calcLines,
      attribution: attribution,
      renderer: { module: 'SCLabMapLibreFIG04 ' + VERSION, maplibre: MAPLIBRE_VERSION, basemap: 'none', networkRequests: state.networkRequests },
      image: { width: size.width, height: size.height, mediaType: 'image/png' }
    };
  }

  function legendLines(spec) {
    return contract().legendModel(spec).map(function (g) {
      var head = g.label + (g.evidenceClass ? ' (' + g.evidenceClass + ')' : '');
      if (g.classes) {
        var line = { layer: g.layer, title: head, field: g.field, items: g.classes.map(function (c) { return { color: c.color, label: c.label }; }) };
        if (g.note) { line.note = g.note; } // only uncertainty layers carry one; the receipt never holds undefined
        return line;
      }
      if (g.ramp) { return { layer: g.layer, title: head, field: g.field, items: g.ramp.map(function (c, i) { return { color: c, label: i === 0 ? g.rampLabels[0] : (i === g.ramp.length - 1 ? g.rampLabels[1] : '') }; }), note: g.note }; }
      return { layer: g.layer, title: head, items: [{ color: g.color, label: g.geometry }] };
    });
  }

  // Word-wraps text onto a 2D canvas; returns the y after the last line.
  function wrapText(ctx, text, x, y, width, lh) {
    var words = String(text).split(/\s+/), line = '';
    words.forEach(function (w) {
      var t = line ? line + ' ' + w : w;
      if (ctx.measureText(t).width > width && line) { ctx.fillText(line, x, y); y += lh; line = w; } else { line = t; }
    });
    if (line) { ctx.fillText(line, x, y); y += lh; }
    return y;
  }

  // Reads the current frame of each map (drawn inside a render event, so no preserveDrawingBuffer is needed).
  function grabFrame(m) {
    return new Promise(function (resolve, reject) {
      var done = false;
      var timer = root.setTimeout(function () { if (!done) { done = true; reject(new Error('map frame not available')); } }, 5000);
      m.once('render', function () {
        if (done) { return; }
        done = true; root.clearTimeout(timer);
        var src = m.getCanvas(), c = root.document.createElement('canvas');
        c.width = src.width; c.height = src.height;
        c.getContext('2d').drawImage(src, 0, 0);
        resolve(c);
      });
      m.triggerRepaint();
    });
  }

  function composeExport(spec, bundle, frames, state, legend, attribution, calcLines) {
    var W = 1200, pad = 24, gap = 12, doc = root.document;
    var mapW = frames.length === 2 ? (W - 2 * pad - gap) / 2 : W - 2 * pad;
    var mapH = Math.round(mapW * frames[0].height / frames[0].width);
    var measure = doc.createElement('canvas').getContext('2d');
    // First pass measures; second pass draws on a canvas of exactly that height.
    function draw(ctx) {
      var y = pad + 22;
      ctx.fillStyle = '#ffffff'; if (ctx.canvas) { ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height); }
      ctx.fillStyle = '#111111'; ctx.font = '600 20px sans-serif';
      y = wrapText(ctx, bundle.title, pad, y, W - 2 * pad, 26);
      ctx.font = '14px sans-serif'; ctx.fillStyle = '#333333';
      y = wrapText(ctx, 'Evidence: ' + bundle.evidenceClass + (state.measurementStatus ? ' · ' + state.measurementStatus : '') + ' · ' + state.description, pad, y + 2, W - 2 * pad, 19);
      y += 6;
      frames.forEach(function (f, k) {
        var x = pad + k * (mapW + gap);
        if (ctx.drawImage) { ctx.drawImage(f, x, y, mapW, mapH); }
        ctx.strokeStyle = '#888888'; ctx.lineWidth = 1; ctx.strokeRect(x + 0.5, y + 0.5, mapW - 1, mapH - 1);
        if (frames.length === 2) {
          ctx.fillStyle = 'rgba(255,255,255,0.85)'; ctx.fillRect(x + 6, y + 6, 150, 24);
          ctx.fillStyle = '#111111'; ctx.font = '600 13px sans-serif';
          ctx.fillText((k === 0 ? 'Left: ' : 'Right: ') + shortTime(k === 0 ? state.comparison.from : state.comparison.to), x + 12, y + 23);
        }
      });
      y += mapH + 24;
      ctx.font = '600 15px sans-serif'; ctx.fillStyle = '#111111'; ctx.fillText('Legend', pad, y); y += 22;
      legend.forEach(function (g) {
        ctx.font = '600 13px sans-serif'; ctx.fillStyle = '#111111'; y = wrapText(ctx, g.title + (g.field ? ': ' + g.field : ''), pad, y, W - 2 * pad, 18);
        var x = pad;
        ctx.font = '13px sans-serif';
        g.items.forEach(function (it) {
          var lw = ctx.measureText(it.label).width;
          if (x + 22 + lw > W - pad) { x = pad; y += 22; }
          ctx.fillStyle = it.color; ctx.fillRect(x, y - 12, 16, 16);
          ctx.strokeStyle = '#555555'; ctx.strokeRect(x + 0.5, y - 11.5, 15, 15);
          ctx.fillStyle = '#222222'; ctx.fillText(it.label, x + 22, y + 1);
          x += 22 + lw + 18;
        });
        y += 22;
        if (g.note) { ctx.fillStyle = '#444444'; y = wrapText(ctx, g.note, pad, y, W - 2 * pad, 17); }
        y += 4;
      });
      ctx.font = '13px sans-serif'; ctx.fillStyle = '#222222';
      calcLines.forEach(function (t) { y = wrapText(ctx, t, pad, y + 2, W - 2 * pad, 18); });
      y += 6;
      ctx.fillStyle = '#333333'; ctx.font = '12px sans-serif';
      y = wrapText(ctx, attribution, pad, y, W - 2 * pad, 16);
      y = wrapText(ctx, 'Figure ' + bundle.figureId + ' v' + bundle.version + ' · view SHA-256 ' + bundle.artifacts[0].sha256 +
        ' · sources ' + bundle.sourceRefs.map(function (s) { return s.sourceId + (s.sha256 ? ' ' + s.sha256.slice(0, 16) + '…' : ''); }).join('; ') +
        ' · Static export from Sustainable Catalyst Lab; the receipt is embedded in this PNG (' + EXPORT_KEYWORD + ').', pad, y + 2, W - 2 * pad, 16);
      return y + pad;
    }
    measure.canvas.width = W; measure.canvas.height = 10;
    var H = Math.ceil(draw({ fillStyle: '', font: '', strokeStyle: '', lineWidth: 1, fillRect: function () {}, strokeRect: function () {},
      fillText: function () {}, measureText: function (t) { measure.font = this.font; return measure.measureText(t); } }));
    var out = doc.createElement('canvas');
    out.width = W; out.height = H;
    draw(out.getContext('2d'));
    return out;
  }

  function toPng(canvas) {
    return new Promise(function (resolve, reject) {
      canvas.toBlob(function (b) { if (!b) { reject(new Error('PNG encoding failed')); return; } b.arrayBuffer().then(resolve, reject); }, 'image/png');
    });
  }

  // Verifies a static export against the receipt and, when given, the verified figure it claims to show.
  // Resolves to {ok, errors, sha256}. No network.
  function verifyExport(pngBytes, receipt, expected) {
    var errors = [], bytes = new Uint8Array(pngBytes);
    return sha256(bytes).then(function (digest) {
      var chunks = pngChunks(bytes);
      if (!chunks) { errors.push('not a well-formed PNG (signature, chunk length or CRC)'); return { ok: false, errors: errors, sha256: digest }; }
      var ihdr = chunks[0], texts = textChunks(chunks).filter(function (t) { return t.keyword === EXPORT_KEYWORD; });
      if (texts.length !== 1) { errors.push('PNG must carry exactly one embedded receipt'); return { ok: false, errors: errors, sha256: digest }; }
      var embedded;
      try { embedded = JSON.parse(texts[0].text); } catch (e) { errors.push('embedded receipt is not JSON'); return { ok: false, errors: errors, sha256: digest }; }
      if (!embedded || embedded.schema !== EXPORT_SCHEMA) { errors.push('embedded receipt schema is not ' + EXPORT_SCHEMA); }
      if (ihdr.type !== 'IHDR' || embedded.image && (u32(ihdr.data, 0) !== embedded.image.width || u32(ihdr.data, 4) !== embedded.image.height)) { errors.push('image size differs from the receipt'); }
      if (receipt) {
        if (receipt.png && receipt.png.sha256 !== digest) { errors.push('PNG SHA-256 differs from the receipt'); }
        if (receipt.png && receipt.png.bytes !== bytes.length) { errors.push('PNG length differs from the receipt'); }
        if (canonical(receipt.receipt) !== canonical(embedded)) { errors.push('embedded receipt differs from the receipt file'); }
      }
      if (expected && expected.bundle) {
        var b = expected.bundle;
        if (embedded.view.sha256 !== b.artifacts[0].sha256) { errors.push('receipt names a different view'); }
        if (embedded.figure.figureId !== b.figureId || embedded.figure.version !== b.version) { errors.push('receipt names a different figure revision'); }
        if (expected.bundleSha256 && embedded.figure.bundleSha256 !== expected.bundleSha256) { errors.push('receipt names different bundle bytes'); }
        if (canonical(embedded.sources.map(function (s) { return s.sha256; })) !== canonical(b.sourceRefs.map(function (s) { return s.sha256 || null; }))) { errors.push('receipt sources differ from the bundle'); }
        if (canonical(embedded.calculations.map(function (c) { return c.outputSha256; })) !== canonical((b.calculationRefs || []).map(function (c) { return c.outputSha256; }))) { errors.push('receipt calculations differ from the bundle'); }
      }
      if (expected && expected.spec) {
        var steps = contract().timeSteps(expected.spec), st = embedded.state || {};
        var ids = expected.spec.layers.map(function (l) { return l.id; });
        if (!Array.isArray(st.visibleLayers) || !st.visibleLayers.every(function (id) { return ids.indexOf(id) >= 0; })) { errors.push('receipt lists layers the view does not have'); }
        if (st.timeStep !== null && st.timeStep !== undefined && steps.indexOf(st.timeStep) < 0) { errors.push('receipt time step is not in the view'); }
      }
      return { ok: errors.length === 0, errors: errors, sha256: digest, receipt: embedded };
    });
  }

  function fileStem(bundle, state) {
    var t = state.comparison ? shortTime(state.comparison.from) + '_vs_' + shortTime(state.comparison.to) : (state.timeStep ? shortTime(state.timeStep) : 'all');
    return (String(bundle.figureId) + '-v' + bundle.version + '-' + t).replace(/[^A-Za-z0-9._-]+/g, '-');
  }

  // Draws one verified view into slot. Resolves to a handle; on any failure the slot keeps the
  // legend and tables and records data-map-state="unavailable" with the reason.
  function createMap(slot, spec, bundle, options) {
    var C = contract(), opts = options || {}, requests = [];
    var v11 = spec.schema === 'sc-lab-maplibre-view/1.1';
    var steps = C.timeSteps(spec), step = v11 && spec.time ? spec.time.initial : null;
    slot.setAttribute('data-map-state', 'loading');
    slot.setAttribute('data-map-requests', '0');
    var wrap = slot.appendChild(el('div', { 'class': 'sc-lab-fig04-map-wrap' }));
    var controls = wrap.appendChild(el('div', { 'class': 'sc-lab-fig04-controls' }));
    var maps = wrap.appendChild(el('div', { 'class': 'sc-lab-fig04-maps' }));
    var main = maps.appendChild(el('div', { 'class': 'sc-lab-fig04-map', role: 'region', 'aria-label': 'Interactive map: ' + bundle.rendering.altText, 'data-map-pane': 'main' }));
    var second = null;
    var status = wrap.appendChild(el('p', { 'class': 'sc-lab-fig04-status', 'aria-live': 'polite' }));
    var detail = wrap.appendChild(el('output', { 'class': 'sc-lab-fig04-detail', 'aria-live': 'polite' }));
    wrap.appendChild(legendBox(spec));
    var calc = calculationsBox(spec, bundle);
    if (calc) { wrap.appendChild(calc); }
    var attribution = wrap.appendChild(el('p', { 'class': 'sc-lab-fig04-attribution' },
      'Data: ' + bundle.sourceRefs.map(function (s) { return s.sourceId + ' (' + (s.notes || s.licenceStatus) + ')'; }).join('; ') +
      '. No basemap or imagery. Rendered with MapLibre GL JS ' + MAPLIBRE_VERSION + '.'));
    attribution.setAttribute('data-map-attribution', 'sources');
    var exportBox = wrap.appendChild(el('div', { 'class': 'sc-lab-fig04-export', role: 'group', 'aria-label': 'Static map export' }));
    var exportBtn = exportBox.appendChild(el('button', { type: 'button', 'data-export-png': '', disabled: '' }, 'Export static map (PNG)'));
    var exportOut = exportBox.appendChild(el('output', { 'aria-live': 'polite', 'data-export-status': '' }));
    var tables = wrap.appendChild(el('details', { 'class': 'sc-lab-fig02a-data' }));
    tables.appendChild(el('summary', null, 'Data table (accessible alternative)'));
    // Wide tables scroll inside their own box so narrow screens never scroll the page sideways.
    tables.appendChild(el('div', { 'class': 'sc-lab-fig02a-tables' })).appendChild(table(C.tableModel(spec, bundle.rendering.caption)));
    if (v11 && spec.comparison) {
      var ct = wrap.appendChild(el('details', { 'class': 'sc-lab-fig02a-data', 'data-comparison-table': '' }));
      ct.appendChild(el('summary', null, 'Comparison table: ' + spec.comparison.label));
      ct.appendChild(el('div', { 'class': 'sc-lab-fig02a-tables' })).appendChild(table(C.comparisonModel(spec)));
    }

    // Layer selection.
    var visible = {};
    var fs = controls.appendChild(el('fieldset', { 'class': 'sc-lab-fig04-layers' }));
    fs.appendChild(el('legend', null, 'Layers'));
    spec.layers.forEach(function (l) {
      visible[l.id] = true;
      var g = (spec.legend || []).filter(function (x) { return x.layer === l.id; })[0];
      var lab = fs.appendChild(el('label'));
      var box = lab.appendChild(el('input', { type: 'checkbox', checked: '', 'data-layer-toggle': l.id }));
      lab.appendChild(el('span', null, g ? g.label : l.id));
      box.addEventListener('change', function () { setLayerVisible(l.id, box.checked); });
    });

    // Time steps.
    var range = null, prev = null, next = null, stepOut = null;
    if (v11 && spec.time) {
      var tf = controls.appendChild(el('fieldset', { 'class': 'sc-lab-fig04-time' }));
      tf.appendChild(el('legend', null, spec.time.label));
      prev = tf.appendChild(el('button', { type: 'button', 'data-time-prev': '' }, 'Previous'));
      range = tf.appendChild(el('input', { type: 'range', min: '0', max: String(steps.length - 1), step: '1',
        value: String(steps.indexOf(step)), 'aria-label': spec.time.label, 'data-time-range': '' }));
      next = tf.appendChild(el('button', { type: 'button', 'data-time-next': '' }, 'Next'));
      stepOut = tf.appendChild(el('output', { 'data-time-output': '' }));
      range.addEventListener('input', function () { setStep(steps[Number(range.value)]); });
      prev.addEventListener('click', function () { var k = steps.indexOf(step); if (k > 0) { setStep(steps[k - 1]); } });
      next.addEventListener('click', function () { var k = steps.indexOf(step); if (k < steps.length - 1) { setStep(steps[k + 1]); } });
    }
    var compareBtn = null;
    if (v11 && spec.comparison) {
      compareBtn = controls.appendChild(el('button', { type: 'button', 'aria-pressed': 'false', 'data-compare-toggle': '' },
        'Compare ' + shortTime(spec.comparison.from) + ' with ' + shortTime(spec.comparison.to)));
      compareBtn.addEventListener('click', function () { setCompare(compareBtn.getAttribute('aria-pressed') !== 'true'); });
    }

    var objectsById = {};
    spec.objects.forEach(function (o) { objectsById[o.object_id] = o; });
    var dataLayers = spec.layers.reduce(function (ids, l) { return ids.concat(C.styleLayerIds(l)); }, []);
    var mapA = null, mapB = null, syncing = false;

    function record(url) {
      requests.push(String(url));
      slot.setAttribute('data-map-requests', String(requests.length));
      return { url: url };
    }
    function newMap(container, at) {
      var M = gl();
      var cam = spec.camera || {};
      var m = new M.Map({
        container: container, style: C.compileStyle(spec, at === null ? undefined : at),
        bounds: cam.bbox || bundle.spatial.bbox, fitBoundsOptions: { padding: 24 }, maxZoom: cam.maxZoom || 14,
        attributionControl: false, transformRequest: record, cooperativeGestures: false, localIdeographFontFamily: false
      });
      m.addControl(new M.NavigationControl({ showCompass: false }), 'top-right');
      m.on('click', function (ev) {
        var hit = m.queryRenderedFeatures(ev.point, { layers: dataLayers.filter(function (id) { return !/heat/.test(m.getLayer(id) && m.getLayer(id).type || ''); }) })[0];
        var o = hit && objectsById[hit.properties.sc_object_id];
        detail.textContent = o ? describe(o) : '';
      });
      return m;
    }
    function describe(o) {
      var p = o.properties || {}, f = spec.fields || {};
      return [p.title || o.object_id].concat(Object.keys(f).filter(function (k) { return p[k] !== undefined && p[k] !== null && f[k].unit !== 'category'; }).map(function (k) {
        var u = f[k].uncertainty && p[f[k].uncertainty];
        return f[k].label + ' ' + p[k] + (typeof u === 'number' ? ' ± ' + u : '') + ' ' + (f[k].unit === '1' ? '' : f[k].unit);
      })).concat([p[spec.evidenceField], shortTime(o.observed_at || '')]).join(' · ');
    }
    function applyFilters(m, at) {
      spec.layers.forEach(function (l) {
        var filter = C.layerFilter(spec, l, at);
        C.styleLayerIds(l).forEach(function (id) { m.setFilter(id, filter); });
      });
    }
    function setLayerVisible(id, on) {
      visible[id] = !!on;
      var l = spec.layers.filter(function (x) { return x.id === id; })[0];
      [mapA, mapB].forEach(function (m) {
        if (m) { C.styleLayerIds(l).forEach(function (sid) { m.setLayoutProperty(sid, 'visibility', on ? 'visible' : 'none'); }); }
      });
      var box = fs.querySelector('[data-layer-toggle="' + id + '"]');
      if (box && box.checked !== !!on) { box.checked = !!on; }
      slot.setAttribute('data-layers-visible', Object.keys(visible).filter(function (k) { return visible[k]; }).join(','));
    }
    function setStep(s) {
      if (steps.indexOf(s) < 0) { return; }
      step = s;
      if (mapA && !(compareBtn && compareBtn.getAttribute('aria-pressed') === 'true')) { applyFilters(mapA, s); }
      range.value = String(steps.indexOf(s));
      range.setAttribute('aria-valuetext', s);
      stepOut.textContent = shortTime(s) + ' (' + (steps.indexOf(s) + 1) + ' of ' + steps.length + ')';
      prev.disabled = steps.indexOf(s) === 0; next.disabled = steps.indexOf(s) === steps.length - 1;
      slot.setAttribute('data-time-step', s);
    }
    function sync(from, to) {
      from.on('move', function () {
        if (syncing || !to) { return; }
        syncing = true; to.jumpTo({ center: from.getCenter(), zoom: from.getZoom() }); syncing = false;
      });
    }
    function setCompare(on) {
      if (!compareBtn) { return; }
      compareBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
      if (range) { range.disabled = on; prev.disabled = on || steps.indexOf(step) === 0; next.disabled = on || steps.indexOf(step) === steps.length - 1; }
      slot.setAttribute('data-compare', on ? 'on' : 'off');
      if (!mapA) { return; }
      if (on) {
        applyFilters(mapA, spec.comparison.from);
        main.setAttribute('aria-label', 'Map at ' + shortTime(spec.comparison.from) + ': ' + bundle.rendering.altText);
        second = maps.appendChild(el('div', { 'class': 'sc-lab-fig04-map', role: 'region', 'data-map-pane': 'compare',
          'aria-label': 'Map at ' + shortTime(spec.comparison.to) + ': ' + bundle.rendering.altText }));
        maps.setAttribute('data-split', 'two');
        mapA.resize();
        mapB = newMap(second, spec.comparison.to);
        mapB.on('load', function () {
          Object.keys(visible).forEach(function (id) { if (!visible[id]) { setLayerVisible(id, false); } });
          mapB.jumpTo({ center: mapA.getCenter(), zoom: mapA.getZoom() });
          slot.setAttribute('data-compare-map', 'ready');
        });
        sync(mapA, mapB); sync(mapB, mapA);
        status.textContent = 'Comparing ' + shortTime(spec.comparison.from) + ' (left) with ' + shortTime(spec.comparison.to) + ' (right). The change table lists every matched ' + spec.comparison.match + '.';
      } else {
        if (mapB) { mapB.remove(); mapB = null; }
        if (second) { second.remove(); second = null; }
        maps.removeAttribute('data-split');
        slot.removeAttribute('data-compare-map');
        main.setAttribute('aria-label', 'Interactive map: ' + bundle.rendering.altText);
        mapA.resize();
        applyFilters(mapA, step);
        status.textContent = '';
      }
    }
    function exportState() {
      var on = !!(compareBtn && compareBtn.getAttribute('aria-pressed') === 'true' && mapB);
      var shown = Object.keys(visible).filter(function (k) { return visible[k]; });
      var labels = spec.layers.filter(function (l) { return visible[l.id]; }).map(function (l) {
        var g = (spec.legend || []).filter(function (x) { return x.layer === l.id; })[0]; return g ? g.label : l.id;
      });
      var desc = on ? 'Comparison: ' + shortTime(spec.comparison.from) + ' (left) with ' + shortTime(spec.comparison.to) + ' (right)'
        : (step ? spec.time.label + ': ' + shortTime(step) + ' (' + (steps.indexOf(step) + 1) + ' of ' + steps.length + ')' : 'All observations');
      return { timeStep: on ? null : step, comparison: on ? { from: spec.comparison.from, to: spec.comparison.to } : null,
        visibleLayers: shown, description: desc + '. Layers shown: ' + (labels.join('; ') || 'none') + '.',
        measurementStatus: bundle.scientificContext && bundle.scientificContext.measurementStatus || '',
        networkRequests: requests.length };
    }
    // Composes the PNG from the frames on screen and the verified legend, calculation and attribution text.
    function exportPng() {
      if (!mapA) { return Promise.reject(new Error('the interactive map is not drawn; use the data table')); }
      var state = exportState();
      var legend = legendLines(spec).filter(function (g) { return visible[g.layer]; });
      var calcLines = (Array.isArray(spec.calculations) ? spec.calculations : []).reduce(function (acc, c) {
        return acc.concat(calculationLines(c, bundle.calculationRefs[c.calculationRef]));
      }, []);
      var maps2 = state.comparison ? [mapA, mapB] : [mapA];
      return Promise.all(maps2.map(grabFrame)).then(function (frames) {
        var canvas = composeExport(spec, bundle, frames, state, legend, attribution.textContent, calcLines);
        var receipt = exportReceipt(spec, bundle, opts.bundleSha256, state, legend, attribution.textContent, calcLines, { width: canvas.width, height: canvas.height });
        return toPng(canvas).then(function (buf) {
          var png = withTextChunk(buf, EXPORT_KEYWORD, ascii(canonical(receipt)));
          return sha256(png).then(function (digest) {
            var name = fileStem(bundle, state);
            return { png: png, sha256: digest, fileName: name + '.png',
              receipt: { schema: 'sc-lab-static-map-export-receipt/1.0', receipt: receipt, png: { fileName: name + '.png', sha256: digest, bytes: png.length } } };
          });
        });
      });
    }
    // Exports, verifies the result against this figure, then offers the PNG and its receipt as links.
    function runExport() {
      exportBtn.disabled = true;
      exportOut.textContent = 'Exporting…';
      Array.prototype.slice.call(exportBox.querySelectorAll('a')).forEach(function (a) { URL.revokeObjectURL(a.href); a.remove(); });
      return exportPng().then(function (x) {
        return verifyExport(x.png, x.receipt, { spec: spec, bundle: bundle, bundleSha256: opts.bundleSha256 }).then(function (v) {
          if (!v.ok) { throw new Error(v.errors.join('; ')); }
          slot.setAttribute('data-export-state', 'verified');
          slot.setAttribute('data-export-sha256', x.sha256);
          exportBox.appendChild(el('a', { href: URL.createObjectURL(new Blob([x.png], { type: 'image/png' })), download: x.fileName, 'class': 'sc-lab-fig04-download', 'data-export-download': 'png' }, 'Download PNG'));
          exportBox.appendChild(el('a', { href: URL.createObjectURL(new Blob([JSON.stringify(x.receipt, null, 1)], { type: 'application/json' })),
            download: x.fileName.replace(/\.png$/, '.receipt.json'), 'class': 'sc-lab-fig04-download', 'data-export-download': 'receipt' }, 'Download export receipt'));
          exportOut.textContent = 'Verified PNG ' + x.png.length + ' bytes, SHA-256 ' + x.sha256 + '. Receipt embedded in the PNG; nothing uploaded or saved.';
          return x;
        });
      }).catch(function (err) {
        slot.setAttribute('data-export-state', 'refused');
        exportOut.textContent = 'Static export failed: ' + String(err && err.message || err) + '. The data table is the static alternative.';
        return { refused: [String(err && err.message || err)] };
      }).then(function (r) { exportBtn.disabled = false; return r; });
    }
    exportBtn.addEventListener('click', runExport);

    function unavailable(reason) {
      exportOut.textContent = 'Static export needs the drawn map; the data table is the static alternative.';
      slot.setAttribute('data-map-state', 'unavailable');
      main.appendChild(el('p', { 'class': 'sc-lab-figure-bundle-unavailable', role: 'note' }, 'Interactive map not drawn: ' + reason + '. The legend and data tables above and below describe the same verified data.'));
    }

    if (range) { setStep(step); }
    slot.setAttribute('data-layers-visible', Object.keys(visible).join(','));
    var e = libraryErrors();
    if (e.length) { unavailable(e.join('; ')); return Promise.resolve({ state: 'unavailable', reasons: e }); }
    if (!webglAvailable()) { unavailable('WebGL is not available in this browser'); return Promise.resolve({ state: 'unavailable', reasons: ['no WebGL'] }); }
    var handle = {
      state: 'ready', requests: requests,
      map: function () { return mapA; }, compareMap: function () { return mapB; },
      setStep: setStep, setLayerVisible: setLayerVisible, setCompare: setCompare,
      exportPng: exportPng, runExport: runExport,
      verifyExport: function (png, receipt) { return verifyExport(png, receipt, { spec: spec, bundle: bundle, bundleSha256: opts.bundleSha256 }); }
    };
    var cfg = config();
    return prepareWorker(opts.workerUrl || cfg.workerUrl, opts.workerIntegrity || cfg.workerIntegrity).then(function () {
      return new Promise(function (resolve) {
        try { mapA = newMap(main, step); } catch (err) { unavailable(String(err && err.message || err)); resolve({ state: 'unavailable' }); return; }
        mapA.on('error', function (ev) { slot.setAttribute('data-map-error', String(ev && ev.error && ev.error.message || 'error').slice(0, 200)); });
        mapA.on('load', function () {
          slot.setAttribute('data-map-state', 'ready');
          exportBtn.disabled = false;
          resolve(handle);
        });
      });
    }, function (reasons) {
      unavailable(Array.isArray(reasons) ? reasons.join('; ') : String(reasons));
      return { state: 'unavailable', reasons: reasons };
    });

  }

  return {
    version: VERSION, maplibreVersion: MAPLIBRE_VERSION, media: MEDIA,
    viewErrors: function (text, spec, bundle) { return contract().viewErrors(text, spec, bundle); },
    libraryErrors: libraryErrors, prepareWorker: prepareWorker, createMap: createMap,
    calculationLines: calculationLines, verifyExport: verifyExport, exportSchema: EXPORT_SCHEMA, exportKeyword: EXPORT_KEYWORD,
    _png: { chunks: pngChunks, withTextChunk: withTextChunk, crc32: crc32, canonical: canonical }
  };
}));
