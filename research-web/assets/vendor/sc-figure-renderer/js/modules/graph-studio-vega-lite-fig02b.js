/*! Sustainable Catalyst Lab — FIG-02B Vega-Lite views and verified SVG export for Graph Studio.
 *
 * Used by the generic sc.figure-bundle.v1 renderer (SC-FIG-01); not a second Graph Studio and not a
 * figure store. A Vega-Lite view is accepted only if it stays inside a restricted grammar (inline data,
 * allow-listed marks and transforms, no expressions, parameters, links or remote loads) and carries the
 * `usermeta.sc` scientific contract: the dataset hash (which must be one of the bundle's sources or
 * calculation outputs), every encoded field's label and unit, the uncertainty definition, and a per-row
 * evidence class that can never claim more than the bundle does. Rendering uses the vendored, hash-pinned
 * Vega 6.4.0 / Vega-Lite 6.5.0 with the CSP-safe expression interpreter (no eval or new Function) and a
 * loader that refuses every load. SVG exports are validated element by element, carry their provenance
 * in <metadata>, are re-verified after serialisation and come with an export receipt.
 */
(function (root, factory) {
  'use strict';
  var api = factory(root);
  if (typeof module === 'object' && module.exports) { module.exports = api; }
  else { root.SCLabVegaLiteFIG02B = api; }
}(typeof self !== 'undefined' ? self : this, function (root) {
  'use strict';

  var VERSION = '0.1.0';
  var PROFILE = 'sc-vega-lite-v1';
  var SVG_PROVENANCE = 'sc.figure-svg-provenance.v1';
  var RECEIPT = 'sc.figure-export-receipt.v1';
  var MEDIA = { vl5: 'application/vnd.vegalite.v5+json', vl6: 'application/vnd.vegalite.v6+json', svg: 'image/svg+xml' };
  var SCHEMAS = ['https://vega.github.io/schema/vega-lite/v5.json', 'https://vega.github.io/schema/vega-lite/v6.json'];
  var EXPECTED = { vega: '6.4.0', vegaLite: '6.5.0', interpreter: '2.3.2' };
  var VENDOR_SHA256 = {
    'vega-6.4.0/vega.min.js': '8f6a3587cf8d4f42c7e08120e3eb05d067e746d554e39d2dcf52acc0bd5ba28f',
    'vega-lite-6.5.0/vega-lite.min.js': '83e215e84e308ee8688fa6503ba0f1cf8a3c05c0d61df2841a0b92bf86809e00',
    'vega-interpreter-2.3.2/vega-interpreter.classic.js': '3bba66a9912c02968723f46d4fbf340e32ea752b6ccfdc1a321458ce9ea195d7'
  };
  var MAX_ROWS = 20000;
  var MAX_TABLE_ROWS = 500;
  var HASH = /^[0-9a-f]{64}$/;
  var FIELD = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
  var EVIDENCE = ['observed', 'literature-derived', 'modelled', 'hypothetical', 'conceptual', 'status-only'];
  var UNCERTAINTY = ['none', 'sd', 'se', 'ci90', 'ci95', 'pi95', 'range'];
  var MARKS = ['point', 'circle', 'square', 'tick', 'line', 'area', 'bar', 'rect', 'rule', 'text', 'errorbar', 'errorband', 'boxplot'];
  var TOP_KEYS = ['$schema', 'title', 'description', 'data', 'mark', 'encoding', 'layer', 'facet', 'spec', 'hconcat', 'vconcat',
    'resolve', 'transform', 'width', 'height', 'autosize', 'padding', 'config', 'usermeta', 'background', 'columns', 'spacing', 'bounds', 'align', 'center'];
  var TRANSFORMS = ['filter', 'fold', 'bin', 'aggregate', 'timeUnit', 'stack'];
  var AGGREGATES = ['count', 'valid', 'missing', 'sum', 'mean', 'median', 'min', 'max', 'q1', 'q3', 'stdev', 'variance', 'ci0', 'ci1', 'stderr'];
  // Keys that carry expressions, interaction, links or loads anywhere in a spec.
  var DENY_KEYS = { url: 1, href: 1, expr: 1, signal: 1, signals: 1, params: 1, selection: 1, loader: 1, datasets: 1, repeat: 1,
    calculate: 1, lookup: 1, impute: 1, regression: 1, loess: 1, density: 1, quantile: 1, pivot: 1, window: 1, joinaggregate: 1,
    flatten: 1, sample: 1, extent: 1, sequence: 1, graticule: 1, sphere: 1, projection: 1, cursor: 1 };
  var COMPOSITE_UNCERTAINTY_MARKS = ['errorbar', 'errorband', 'rule', 'area', 'rect'];

  function isObj(v) { return !!v && typeof v === 'object' && !Array.isArray(v); }
  function nonEmpty(v, min) { return typeof v === 'string' && v.length >= (min || 1); }

  function lib() {
    var f = root.SCLabPlotlyPreviewFIG02A;
    if (!f && typeof require === 'function') { f = require('./graph-studio-plotly-preview-fig02a.js'); }
    if (!f) { throw ['FIG-02A primitives (hashing and content policy) are not loaded']; }
    return f;
  }

  function markType(m) { return typeof m === 'string' ? m : (isObj(m) ? m.type : undefined); }

  // ------------------------------------------------------------------
  // Restricted grammar.
  // ------------------------------------------------------------------
  function walk(value, path, fn) {
    if (Array.isArray(value)) { value.forEach(function (v, k) { walk(v, path + '[' + k + ']', fn); }); return; }
    if (isObj(value)) { Object.keys(value).forEach(function (k) { fn(k, value[k], path); walk(value[k], path + '.' + k, fn); }); }
  }

  // Every unit view (top level, layer members, facet spec, concat members) visited with its path.
  function unitViews(spec, path, out) {
    out = out || [];
    if (!isObj(spec)) { return out; }
    if (spec.mark !== undefined) { out.push({ view: spec, path: path }); }
    (spec.layer || []).forEach(function (s, k) { unitViews(s, path + '.layer[' + k + ']', out); });
    (spec.hconcat || []).forEach(function (s, k) { unitViews(s, path + '.hconcat[' + k + ']', out); });
    (spec.vconcat || []).forEach(function (s, k) { unitViews(s, path + '.vconcat[' + k + ']', out); });
    if (isObj(spec.spec)) { unitViews(spec.spec, path + '.spec', out); }
    return out;
  }

  function encodedFields(spec) {
    var fields = {};
    function visitEncoding(enc) {
      if (!isObj(enc)) { return; }
      Object.keys(enc).forEach(function (ch) {
        var defs = Array.isArray(enc[ch]) ? enc[ch] : [enc[ch]];
        defs.forEach(function (d) { if (isObj(d) && typeof d.field === 'string') { fields[d.field] = (fields[d.field] || []).concat(ch); } });
      });
    }
    unitViews(spec, '', []).forEach(function (u) { visitEncoding(u.view.encoding); });
    visitEncoding(spec.encoding);
    if (isObj(spec.facet)) { ['row', 'column', 'facet'].forEach(function (k) { if (isObj(spec.facet[k]) && spec.facet[k].field) { fields[spec.facet[k].field] = ['facet']; } }); if (spec.facet.field) { fields[spec.facet.field] = ['facet']; } }
    return fields;
  }

  function specPolicyErrors(spec) {
    var e = [];
    if (!isObj(spec)) { return ['Vega-Lite view is not a JSON object']; }
    if (SCHEMAS.indexOf(spec.$schema) < 0) { e.push('$schema must be Vega-Lite v5 or v6 (got ' + String(spec.$schema) + ')'); }
    Object.keys(spec).forEach(function (k) { if (TOP_KEYS.indexOf(k) < 0) { e.push('top-level property ' + k + ' is not allowed'); } });
    walk(spec, '', function (key, value, path) {
      if (DENY_KEYS[key]) { e.push(path + '.' + key + ' is not allowed (expressions, interaction, links and loads are refused)'); }
      if (/Expr$/.test(key)) { e.push(path + '.' + key + ' is an expression and is refused'); }
      if (key === 'test' && typeof value === 'string') { e.push(path + '.test: string predicates are expressions and are refused'); }
      if (key === 'filter' && typeof value === 'string') { e.push(path + '.filter: string predicates are expressions and are refused'); }
    });
    lib().walkStrings(spec, function (s, p) { if (lib().forbiddenText.test(s)) { e.push('forbidden markup or link in view' + p); } }, '');
    // Data: one inline dataset at the top level, flat rows of primitives.
    var d = spec.data;
    if (!isObj(d) || Object.keys(d).some(function (k) { return k !== 'values' && k !== 'name'; }) || !Array.isArray(d.values)) {
      e.push('data must be inline values at the top level ({"values": [...]})');
    } else {
      if (!d.values.length || d.values.length > MAX_ROWS) { e.push('data.values must have 1 to ' + MAX_ROWS + ' rows'); }
      for (var i = 0; i < d.values.length; i++) {
        var row = d.values[i];
        if (!isObj(row) || Object.keys(row).some(function (k) { var v = row[k]; return !FIELD.test(k) || (v !== null && ['string', 'number', 'boolean'].indexOf(typeof v) < 0); })) {
          e.push('data.values[' + i + '] must be a flat object of strings, numbers, booleans or null with simple field names'); break;
        }
      }
    }
    var units = unitViews(spec, '', []);
    if (!units.length) { e.push('view has no mark'); }
    units.forEach(function (u) {
      if (u.path !== '' && u.view.data !== undefined) { e.push(u.path + '.data: only the top-level dataset is allowed'); }
      if (MARKS.indexOf(markType(u.view.mark)) < 0) { e.push((u.path || 'view') + '.mark ' + String(markType(u.view.mark)) + ' is not in the allowed set'); }
    });
    function checkTransforms(list, path) {
      (list || []).forEach(function (t, k) {
        var kind = isObj(t) ? Object.keys(t).filter(function (x) { return TRANSFORMS.indexOf(x) >= 0; }) : [];
        if (kind.length !== 1) { e.push(path + '.transform[' + k + '] is not an allowed transform (' + TRANSFORMS.join(', ') + ')'); return; }
        if (kind[0] === 'aggregate') {
          (t.aggregate || []).forEach(function (a) { if (!isObj(a) || AGGREGATES.indexOf(a.op) < 0) { e.push(path + '.transform[' + k + '].aggregate op ' + String(a && a.op) + ' is not allowed'); } });
        }
        if (kind[0] === 'filter' && !isObj(t.filter)) { e.push(path + '.transform[' + k + '].filter must be a field predicate object'); }
      });
    }
    checkTransforms(spec.transform, '');
    units.forEach(function (u) { if (u.path) { checkTransforms(u.view.transform, u.path); } });
    walk(spec, '', function (key, value, path) {
      if (key === 'aggregate' && typeof value === 'string' && AGGREGATES.indexOf(value) < 0) { e.push(path + '.aggregate ' + value + ' is not allowed'); }
    });
    return e;
  }

  // ------------------------------------------------------------------
  // Scientific contract (usermeta.sc) against the bundle.
  // ------------------------------------------------------------------
  function scMeta(spec) { return isObj(spec) && isObj(spec.usermeta) && isObj(spec.usermeta.sc) ? spec.usermeta.sc : null; }

  function scMetaErrors(spec, bundle, datasetSha256) {
    var e = [], m = scMeta(spec);
    if (!m) { return ['usermeta.sc (profile ' + PROFILE + ') is required: dataset identity, field units, uncertainty and evidence']; }
    if (m.profile !== PROFILE) { e.push('usermeta.sc.profile must be ' + PROFILE); }
    if (!nonEmpty(m.datasetId, 3)) { e.push('usermeta.sc.datasetId is missing'); }
    if (!HASH.test(String(m.datasetSha256 || ''))) { e.push('usermeta.sc.datasetSha256 is not a SHA-256'); }
    else if (datasetSha256 && m.datasetSha256 !== datasetSha256) {
      e.push('data.values do not match usermeta.sc.datasetSha256 (canonical hash ' + datasetSha256.slice(0, 12) + ')');
    }
    var known = [];
    (bundle.sourceRefs || []).forEach(function (s) { if (s && s.sha256) { known.push(s.sha256); } });
    (bundle.calculationRefs || []).forEach(function (c) { if (c && c.outputSha256) { known.push(c.outputSha256); } });
    if (m.datasetSha256 && known.indexOf(m.datasetSha256) < 0) {
      e.push('dataset ' + String(m.datasetSha256).slice(0, 12) + ' is not one of the bundle sourceRefs or calculationRefs outputs');
    }
    var fields = isObj(m.fields) ? m.fields : null;
    if (!fields) { e.push('usermeta.sc.fields must describe every encoded field'); fields = {}; }
    Object.keys(fields).forEach(function (f) {
      var d = fields[f];
      if (!FIELD.test(f) || !isObj(d) || !nonEmpty(d.label) || typeof d.unit !== 'string' || !d.unit.length) {
        e.push('usermeta.sc.fields.' + f + ' needs a label and a unit ("1" for dimensionless, "category" for labels)');
      }
    });
    var enc = encodedFields(spec);
    Object.keys(enc).forEach(function (f) { if (!fields[f]) { e.push('encoded field ' + f + ' has no label and unit in usermeta.sc.fields'); } });
    // Explicit axis/legend titles must keep the unit visible.
    unitViews(spec, '', []).concat([{ view: spec, path: '' }]).forEach(function (u) {
      var en = u.view.encoding;
      if (!isObj(en)) { return; }
      Object.keys(en).forEach(function (ch) {
        var def = en[ch];
        if (isObj(def) && typeof def.field === 'string' && typeof def.title === 'string' && fields[def.field] &&
            ['1', 'category'].indexOf(fields[def.field].unit) < 0 && def.title.indexOf(fields[def.field].unit) < 0) {
          e.push('title of ' + ch + ' (' + def.field + ') hides its unit ' + fields[def.field].unit);
        }
      });
    });
    var rows = isObj(spec.data) && Array.isArray(spec.data.values) ? spec.data.values : [];
    var u = m.uncertainty;
    if (!isObj(u) || UNCERTAINTY.indexOf(u.kind) < 0 || !nonEmpty(u.statement, 10)) {
      e.push('usermeta.sc.uncertainty needs kind (' + UNCERTAINTY.join(', ') + ') and a statement');
    } else if (u.kind !== 'none') {
      var uf = [u.lowerField, u.upperField].filter(Boolean);
      if (!uf.length && u.field) { uf = [u.field]; }
      if (!uf.length) { e.push('uncertainty ' + u.kind + ' needs lowerField/upperField (or field)'); }
      uf.forEach(function (f) {
        if (!fields[f]) { e.push('uncertainty field ' + f + ' has no unit in usermeta.sc.fields'); }
        if (!enc[f]) { e.push('uncertainty field ' + f + ' is declared but not drawn (encode it, e.g. errorbar/errorband)'); }
        if (rows.some(function (r) { return r[f] !== null && r[f] !== undefined && typeof r[f] !== 'number'; })) { e.push('uncertainty field ' + f + ' must be numeric or null'); }
      });
      var drawn = unitViews(spec, '', []).some(function (x) {
        return COMPOSITE_UNCERTAINTY_MARKS.indexOf(markType(x.view.mark)) >= 0 && uf.some(function (f) { return (encodedFields(x.view)[f] || []).length; });
      });
      if (uf.length && !drawn) { e.push('uncertainty fields must be drawn by an errorbar, errorband, rule, area or rect layer'); }
    }
    // Evidence: rows may never claim more than the bundle (no modelled or literature values shown as observed).
    var ef = m.evidenceField;
    if (!nonEmpty(ef) || !rows.every(function (r) { return typeof r[ef] === 'string'; })) {
      e.push('usermeta.sc.evidenceField must name a field that gives every row its evidence class');
    } else {
      if (!enc[ef] && !fields[ef]) { e.push('evidenceField ' + ef + ' is not described'); }
      var classes = {};
      rows.forEach(function (r) { classes[r[ef]] = 1; });
      Object.keys(classes).forEach(function (c) {
        if (EVIDENCE.indexOf(c) < 0) { e.push('row evidence class ' + c + ' is not recognised'); }
        else if (c === 'observed' && bundle.evidenceClass !== 'observed') {
          e.push('rows labelled observed inside a ' + bundle.evidenceClass + ' figure (no promotion to observed)');
        }
      });
      var distinct = Object.keys(classes).length;
      var visual = (enc[ef] || []).filter(function (ch) { return ['color', 'shape', 'strokeDash', 'opacity', 'fill', 'facet', 'row', 'column'].indexOf(ch) >= 0; });
      if (distinct > 1 && !visual.length) { e.push('rows mix ' + distinct + ' evidence classes but ' + ef + ' is not encoded (colour, shape, dash or facet)'); }
    }
    return e;
  }

  // Canonical hash of data.values, from the view file's own text (Python float spelling preserved).
  function datasetSha256FromText(text) {
    var tree = lib().parseLexical(text);
    var data = member(tree, 'data'), values = member(data, 'values');
    if (!values) { return Promise.resolve(null); }
    return lib().sha256Hex(lib().canonical(values));
  }

  function member(node, key) {
    if (!node || node.t !== 'o') { return null; }
    for (var j = 0; j < node.v.length; j++) { if (node.v[j][0] === key) { return node.v[j][1]; } }
    return null;
  }

  // Resolves [] when the view is acceptable for this bundle, else the list of reasons.
  function viewErrors(text, spec, bundle) {
    var e = specPolicyErrors(spec);
    if (e.length) { return Promise.resolve(e); }
    return datasetSha256FromText(text).then(function (h) {
      var errors = scMetaErrors(spec, bundle, h);
      return errors;
    });
  }

  // ------------------------------------------------------------------
  // Display spec: unit-bearing titles added where the author left none. The verified original is kept.
  // ------------------------------------------------------------------
  function displaySpec(spec, bundle) {
    var s = JSON.parse(JSON.stringify(spec)), m = scMeta(s);
    function titled(view) {
      if (!isObj(view.encoding)) { return; }
      Object.keys(view.encoding).forEach(function (ch) {
        var def = view.encoding[ch], f = isObj(def) && def.field && m.fields[def.field];
        if (f && def.title === undefined) { def.title = ['1', 'category'].indexOf(f.unit) >= 0 ? f.label : f.label + ' (' + f.unit + ')'; }
      });
    }
    titled(s);
    unitViews(s, '', []).forEach(function (u) { titled(u.view); });
    if (bundle && bundle.rendering && !s.description) { s.description = bundle.rendering.altText; }
    delete s.usermeta;
    return s;
  }

  function columns(spec) {
    var m = scMeta(spec), cols = Object.keys(m.fields).map(function (f) {
      var d = m.fields[f];
      return { field: f, title: ['1', 'category'].indexOf(d.unit) >= 0 ? d.label : d.label + ' (' + d.unit + ')' };
    });
    if (m.evidenceField && !m.fields[m.evidenceField]) { cols.push({ field: m.evidenceField, title: 'Evidence class' }); }
    return cols;
  }

  // Plain-data model of the accessible table (rendered with text nodes by tableElement).
  function tableModel(spec, caption) {
    var cols = columns(spec), rows = spec.data.values;
    var m = scMeta(spec);
    return {
      caption: caption, columns: cols.map(function (c) { return c.title; }),
      rows: rows.slice(0, MAX_TABLE_ROWS).map(function (r) {
        return cols.map(function (c) { var v = r[c.field]; return v === null || v === undefined || (typeof v === 'number' && !isFinite(v)) ? 'not reported' : String(v); });
      }),
      truncated: rows.length > MAX_TABLE_ROWS ? rows.length - MAX_TABLE_ROWS : 0,
      uncertainty: m.uncertainty.kind === 'none' ? 'Uncertainty: none reported. ' + m.uncertainty.statement : 'Uncertainty (' + m.uncertainty.kind + '): ' + m.uncertainty.statement,
      dataset: m.datasetId + ' — SHA-256 ' + m.datasetSha256
    };
  }

  // ------------------------------------------------------------------
  // Runtime (browser): versions, refusing loader, eval-free view.
  // ------------------------------------------------------------------
  function runtimeErrors() {
    var e = [], vg = root.vega, vl = root.vegaLite, vi = root.vegaInterpreter;
    if (!vg || vg.version !== EXPECTED.vega) { e.push('vega ' + EXPECTED.vega + ' is not loaded'); }
    if (!vl || vl.version !== EXPECTED.vegaLite) { e.push('vega-lite ' + EXPECTED.vegaLite + ' is not loaded'); }
    if (!vi || !vi.expressionInterpreter || typeof vi.expressionInterpreter.parameter !== 'function' || typeof vi.expressionInterpreter.encode !== 'function' || vi.version !== EXPECTED.interpreter) { e.push('vega-interpreter ' + EXPECTED.interpreter + ' is not loaded'); }
    return e;
  }

  function refusingLoader(vg) {
    var l = vg.loader();
    function refuse(uri) { return Promise.reject(new Error('FIG-02B refuses all loads (' + String(uri).slice(0, 60) + ')')); }
    l.load = refuse; l.sanitize = refuse; l.http = refuse; l.file = refuse;
    return l;
  }

  function compiledErrors(vgSpec) {
    var e = [];
    walk(vgSpec, '', function (key, value, path) {
      if (key === 'url' || key === 'href') { e.push('compiled Vega spec contains ' + key + ' at ' + path); }
      if (key === 'on' && Array.isArray(value) && value.length) { e.push('compiled Vega spec contains event handlers at ' + path); }
    });
    return e;
  }

  function createView(container, spec, bundle) {
    var errs = runtimeErrors();
    if (errs.length) { return Promise.reject(errs); }
    var vg = root.vega, vl = root.vegaLite, vi = root.vegaInterpreter;
    var compiled = vl.compile(displaySpec(spec, bundle)).spec;
    errs = compiledErrors(compiled);
    if (errs.length) { return Promise.reject(errs); }
    var view = new vg.View(vg.parse(compiled, null, { ast: true }), {
      expr: vi.expressionInterpreter, loader: refusingLoader(vg), renderer: 'svg', container: container, hover: false, logLevel: vg.Warn
    });
    return view.runAsync().then(function () { return view; });
  }

  // ------------------------------------------------------------------
  // SVG validation (strict; never silently stripped), provenance and receipts.
  // ------------------------------------------------------------------
  var SVG_NS = 'http://www.w3.org/2000/svg';
  var SVG_ELEMENTS = ['svg', 'g', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'text', 'tspan', 'title', 'desc',
    'defs', 'clipPath', 'linearGradient', 'radialGradient', 'stop', 'metadata', 'style', 'pattern', 'mask', 'symbol'];
  var URL_REF = /url\(\s*(['"]?)([^)'"]*)\1\s*\)/gi;

  function badValue(v) {
    if (/javascript:|data:|vbscript:|@import|expression\s*\(/i.test(v)) { return true; }
    var m, re = new RegExp(URL_REF.source, 'gi');
    while ((m = re.exec(v))) { if (m[2].charAt(0) !== '#') { return true; } }
    return false;
  }

  // errors for a parsed SVG document (element allow-list, no handlers, only local references).
  function svgTreeErrors(doc) {
    var e = [];
    var rootEl = doc && doc.documentElement;
    if (!rootEl || rootEl.localName !== 'svg' || rootEl.namespaceURI !== SVG_NS || doc.getElementsByTagName('parsererror').length) { return ['not a well-formed SVG document']; }
    var all = [rootEl].concat(Array.prototype.slice.call(rootEl.getElementsByTagName('*')));
    all.forEach(function (n) {
      if (n.namespaceURI !== SVG_NS || SVG_ELEMENTS.indexOf(n.localName) < 0) {
        if (!(n.parentNode && n.parentNode.localName === 'metadata')) { e.push('element <' + n.localName + '> is not allowed in a verified SVG'); }
        return;
      }
      if (n.localName === 'style' && badValue(n.textContent || '')) { e.push('<style> references something outside the SVG'); }
      Array.prototype.forEach.call(n.attributes, function (a) {
        var name = a.name.toLowerCase();
        if (/^on/.test(name)) { e.push('event handler attribute ' + name + ' on <' + n.localName + '>'); }
        else if ((name === 'href' || name === 'xlink:href') && a.value.charAt(0) !== '#') { e.push(name + ' to ' + a.value.slice(0, 40) + ' is not local'); }
        else if (name !== 'xmlns' && name !== 'xmlns:xlink' && badValue(a.value)) { e.push('attribute ' + name + ' on <' + n.localName + '> references something outside the SVG'); }
      });
    });
    return e;
  }

  function parseSvg(text) {
    if (typeof DOMParser === 'undefined') { throw ['SVG verification needs a browser DOM parser']; }
    return new DOMParser().parseFromString(text, 'image/svg+xml');
  }

  function provenanceFor(bundle, viewArtifact, spec) {
    var m = spec ? scMeta(spec) : null;
    var vg = root.vega, vl = root.vegaLite, P = root.Plotly;
    return {
      schema: SVG_PROVENANCE, figureId: bundle.figureId, figureVersion: bundle.version || 1, title: bundle.title,
      evidenceClass: bundle.evidenceClass, measurementStatus: bundle.scientificContext.measurementStatus,
      viewArtifactSha256: viewArtifact.sha256, viewMediaType: viewArtifact.mediaType,
      dataset: m ? { datasetId: m.datasetId, sha256: m.datasetSha256 } : null,
      fields: m ? m.fields : null, uncertainty: m ? m.uncertainty : null,
      sourceRefs: (bundle.sourceRefs || []).map(function (s) {
        return { sourceId: s.sourceId, provider: s.provider, citationId: s.citationId || null, doi: s.doi || null, sha256: s.sha256 || null, licenceStatus: s.licenceStatus };
      }),
      calculationRefs: (bundle.calculationRefs || []).map(function (c) { return { system: c.system, runRef: c.runRef, method: c.method, outputSha256: c.outputSha256 }; }),
      reproducibility: bundle.provenance.reproducibility, generationRunId: bundle.provenance.generationRunId,
      renderer: viewArtifact.mediaType === 'application/vnd.plotly.v1+json'
        ? { name: 'plotly.js', version: P && P.version }
        : { name: 'vega-lite', vegaLite: vl && vl.version, vega: vg && vg.version, expressionInterpreter: EXPECTED.interpreter, vendorSha256: VENDOR_SHA256 }
    };
  }

  // Add <title>, <desc> and <metadata> to a validated SVG and serialise it.
  function withProvenance(svgText, bundle, provenance) {
    var doc = parseSvg(svgText);
    var errs = svgTreeErrors(doc);
    if (errs.length) { throw errs; }
    var svg = doc.documentElement;
    Array.prototype.slice.call(svg.childNodes).forEach(function (c) {
      if (c.nodeType === 1 && ['title', 'desc', 'metadata'].indexOf(c.localName) >= 0) { svg.removeChild(c); }
    });
    var meta = doc.createElementNS(SVG_NS, 'metadata');
    meta.setAttribute('id', 'sc-figure-provenance');
    meta.setAttribute('data-schema', SVG_PROVENANCE);
    meta.textContent = JSON.stringify(provenance);
    var desc = doc.createElementNS(SVG_NS, 'desc'); desc.textContent = bundle.rendering.altText;
    var title = doc.createElementNS(SVG_NS, 'title'); title.textContent = bundle.title;
    svg.insertBefore(meta, svg.firstChild); svg.insertBefore(desc, svg.firstChild); svg.insertBefore(title, svg.firstChild);
    svg.setAttribute('role', 'img');
    return new XMLSerializer().serializeToString(doc);
  }

  function embeddedProvenance(doc) {
    var meta = doc.getElementById ? doc.getElementById('sc-figure-provenance') : null;
    if (!meta) { return null; }
    try { return JSON.parse(meta.textContent); } catch (e) { return { invalid: true }; }
  }

  // Verify SVG bytes: well-formed, policy-clean, provenance (if any) consistent with the bundle.
  // Resolves {sha256, provenance} or rejects with reasons.
  function verifySvg(bytes, bundle, expected) {
    var text;
    try { text = new TextDecoder('utf-8', { fatal: true }).decode(bytes); } catch (x) { return Promise.reject(['SVG is not UTF-8']); }
    var doc;
    try { doc = parseSvg(text); } catch (x) { return Promise.reject(Array.isArray(x) ? x : [String(x)]); }
    var errs = svgTreeErrors(doc);
    var prov = embeddedProvenance(doc);
    if (prov && prov.invalid) { errs.push('embedded provenance is not JSON'); }
    if (prov && !prov.invalid) {
      if (prov.schema !== SVG_PROVENANCE) { errs.push('embedded provenance schema is ' + String(prov.schema)); }
      if (prov.figureId !== bundle.figureId || prov.figureVersion !== (bundle.version || 1)) { errs.push('embedded provenance is for ' + prov.figureId + ' v' + prov.figureVersion + ', not this figure'); }
      if (prov.evidenceClass !== bundle.evidenceClass) { errs.push('embedded evidence class ' + prov.evidenceClass + ' differs from the bundle (' + bundle.evidenceClass + ')'); }
      if (!bundle.artifacts.some(function (a) { return a.sha256 === prov.viewArtifactSha256; })) { errs.push('embedded provenance names a view that is not in the bundle'); }
    }
    if (expected && JSON.stringify(prov) !== JSON.stringify(expected)) { errs.push('embedded provenance differs from the export'); }
    if (errs.length) { return Promise.reject(errs); }
    return sha256Bytes(bytes).then(function (h) { return { sha256: h, provenance: prov, bytes: bytes.length }; });
  }

  function sha256Bytes(bytes) {
    var subtle = root && root.crypto && root.crypto.subtle;
    if (!subtle && typeof require === 'function') { subtle = require('crypto').webcrypto.subtle; }
    if (!subtle) { return Promise.reject(['integrity-unverifiable: Web Crypto is unavailable']); }
    return subtle.digest('SHA-256', bytes).then(function (buf) {
      return Array.prototype.map.call(new Uint8Array(buf), function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
    });
  }

  // Export: SVG text from a renderer -> validated, provenance-bearing bytes -> re-verified -> receipt.
  function exportSvg(svgText, bundle, bundleSha256, viewArtifact, spec, now) {
    var prov = provenanceFor(bundle, viewArtifact, spec);
    var out;
    try { out = withProvenance(svgText, bundle, prov); } catch (x) { return Promise.reject(Array.isArray(x) ? x : [String(x)]); }
    var bytes = new TextEncoder().encode(out);
    return verifySvg(bytes, bundle, prov).then(function (v) {
      return {
        bytes: bytes,
        receipt: {
          schema: RECEIPT, figureId: bundle.figureId, figureVersion: bundle.version || 1, bundleSha256: bundleSha256,
          viewArtifactSha256: viewArtifact.sha256, viewMediaType: viewArtifact.mediaType, evidenceClass: bundle.evidenceClass,
          format: 'svg', mediaType: MEDIA.svg, sha256: v.sha256, bytes: v.bytes, provenanceEmbedded: true,
          renderer: prov.renderer, exportedAt: (now || new Date()).toISOString(),
          verification: { reparsed: true, elementPolicy: 'passed', provenanceMatchesBundle: true },
          artifactEntry: { format: 'svg', artifactRef: bundle.figureId + '.v' + (bundle.version || 1) + '.' + v.sha256.slice(0, 12) + '.svg',
            sha256: v.sha256, mediaType: MEDIA.svg, status: 'local-review' },
          note: 'Add artifactEntry to the next bundle revision through the owning producer; Lab does not store or publish exports.'
        }
      };
    });
  }

  return {
    version: VERSION, profile: PROFILE, media: MEDIA, expected: EXPECTED, vendorSha256: VENDOR_SHA256,
    isVegaLiteMedia: function (t) { return t === MEDIA.vl5 || t === MEDIA.vl6; },
    specPolicyErrors: specPolicyErrors, scMetaErrors: scMetaErrors, viewErrors: viewErrors, datasetSha256FromText: datasetSha256FromText,
    encodedFields: encodedFields, displaySpec: displaySpec, tableModel: tableModel, runtimeErrors: runtimeErrors, createView: createView,
    compiledErrors: compiledErrors, svgTreeErrors: svgTreeErrors, provenanceFor: provenanceFor, verifySvg: verifySvg, exportSvg: exportSvg
  };
}));
