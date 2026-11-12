/*! Sustainable Catalyst Lab — MapLibre map-view contract and style compiler (SC-FIG-03A preparation, SC-FIG-04).
 *
 * Pure contract: no DOM, no network. Drawing is done by graph-studio-maplibre-renderer-fig04.js with the
 * vendored MapLibre GL JS 5.24.0 CSP build. The contract uses Site Intelligence's existing interchange object
 * (sc-site-intelligence-spatial-evidence-object/2.0) unchanged:
 *
 * - every object's SI content digest is recomputed from the file's own number spellings and must match;
 * - geometry is WGS84 (EPSG:4326) as SI normalised it, with the bbox SI derived; Lab never reprojects,
 *   and an object whose source CRS differs must carry SI's recorded transformation;
 * - vector geometry only (no RasterFootprint: rasters would need tiles);
 * - no basemap, tiles, glyphs, sprites or any URL: the provider allow-list is empty until a provider
 *   has approved provenance, so the compiled style contains one inline GeoJSON source and nothing else;
 * - styles are constants (colours, widths), never MapLibre expressions supplied by the author;
 * - every object carries an evidence class; observed objects only in an observed bundle;
 * - the accessible table lists every object with its source, licence, time and units.
 *
 * View 1.1 (SC-FIG-04) adds, all as validated constants that the compiler turns into MapLibre expressions:
 * graduated classes on a declared numeric field, kernel-density heat maps of point weights, time steps over
 * SI's observed_at, declared uncertainty properties, a side-by-side comparison of two time steps with
 * propagated uncertainty, Workspace calculation results tied to the bundle's calculationRefs, and an
 * Earth-observation raster gate that refuses every provider until one is approved (none is).
 *
 * SC-FIG-05.3 adds uncertainty layers: a points/polygons layer with encode "uncertainty" classifies a field's
 * declared uncertainty property instead of its value, on an ordered run of one colour-vision-safe sequential
 * ramp, and must be bound to a Workspace run (uncertaintyRef -> a bundle calculationRef with uncertaintySha256).
 * Objects without the property are drawn grey and listed as "uncertainty not reported"; nothing is imputed.
 */
(function (root, factory) {
  'use strict';
  var api = factory(root);
  if (typeof module === 'object' && module.exports) { module.exports = api; }
  else { root.SCLabMapLibreFIG03A = api; }
}(typeof self !== 'undefined' ? self : this, function (root) {
  'use strict';

  var VERSION = '0.1.0';
  var VIEW_SCHEMA = 'sc-lab-maplibre-view/1.0';
  var VIEW_SCHEMA_11 = 'sc-lab-maplibre-view/1.1';
  var MEDIA = 'application/vnd.sc.maplibre-view.v1+json';
  var SI_OBJECT = 'sc-site-intelligence-spatial-evidence-object/2.0';
  var CRS = 'EPSG:4326';
  var PROVIDERS = Object.freeze({}); // No basemap, tile or imagery provider is approved.
  // Earth-observation candidates (SC-FIG-04): described so the gate can name what approval needs; none is approved.
  var EO_CANDIDATES = Object.freeze({
    'copernicus-sentinel-2-l2a': Object.freeze({ label: 'Copernicus Sentinel-2 Level-2A surface reflectance', status: 'candidate-not-approved',
      licence: 'Copernicus Sentinel data terms (free, full and open; attribution required)', attribution: 'Contains modified Copernicus Sentinel data',
      needs: ['owner approval of the provider', 'verified licence in the bundle sourceRefs', 'an approved tile host', 'product id, acquisition time and processing baseline per layer', 'a hash of the tile manifest'] }),
    'copernicus-sentinel-1-grd': Object.freeze({ label: 'Copernicus Sentinel-1 GRD backscatter', status: 'candidate-not-approved',
      licence: 'Copernicus Sentinel data terms (free, full and open; attribution required)', attribution: 'Contains modified Copernicus Sentinel data',
      needs: ['owner approval of the provider', 'verified licence in the bundle sourceRefs', 'an approved tile host', 'product id, acquisition time and processing baseline per layer', 'a hash of the tile manifest'] }),
    'copernicus-land-monitoring-clc': Object.freeze({ label: 'Copernicus Land Monitoring Service CORINE Land Cover', status: 'candidate-not-approved',
      licence: 'Copernicus Land Monitoring Service data policy (attribution required)', attribution: 'European Union, Copernicus Land Monitoring Service, European Environment Agency (EEA)',
      needs: ['owner approval of the provider', 'verified licence in the bundle sourceRefs', 'an approved tile host', 'product id and reference year', 'a hash of the tile manifest'] })
  });
  var GEOMETRY = { point: ['Point', 'MultiPoint'], line: ['LineString', 'MultiLineString'], polygon: ['Polygon', 'MultiPolygon'] };
  var EVIDENCE = ['observed', 'literature-derived', 'modelled', 'hypothetical', 'conceptual', 'status-only'];
  var TOP_KEYS = ['schema', 'title', 'objects', 'evidenceField', 'fields', 'layers', 'legend', 'basemap', 'camera'];
  var TOP_KEYS_11 = TOP_KEYS.concat(['time', 'comparison', 'calculations', 'rasterLayers']);
  var CALC_KEYS = ['label', 'value', 'unit', 'method', 'calculationRef', 'uncertainty', 'uncertaintyKind', 'interval', 'assumptions'];
  var INTERVAL_KEYS = 'computedBy,consistency,degreesOfFreedom,distribution,level,lower,method,semantics,upper';
  var INTERVAL_CONSISTENCY = ['estimate-plus-minus-t-standard-error', 'not-checked'];
  var STYLE_KEYS = { point: ['color', 'radius', 'opacity', 'strokeColor', 'strokeWidth'],
    line: ['color', 'width', 'opacity'], polygon: ['color', 'opacity', 'outlineColor', 'outlineWidth'],
    heatmap: ['radius', 'intensity', 'opacity'] };
  var RAMPS = Object.freeze({ viridis: ['#440154', '#3b528b', '#21918c', '#5ec962', '#fde725'],
    cividis: ['#00224e', '#414d6b', '#7c7b78', '#bcaf6f', '#fee838'], magma: ['#000004', '#51127c', '#b73779', '#fc8961', '#fcfdbf'] });
  var UNCERTAINTY_KINDS = ['standard-error', 'standard-deviation', 'ci95-half-width'];
  var NOT_REPORTED = '#bdbdbd';
  var COLOR = /^#[0-9a-fA-F]{6}$/;
  var ID = /^[a-z][a-z0-9-]{0,47}$/;
  var FIELD = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
  var URLISH = /(?:^|[^a-z])(?:https?|ftp|file|data|blob|javascript|mapbox|pmtiles):|\{[xyz]\}|\/\//i;
  var MAX_OBJECTS = 5000;

  function lib() {
    var f = root.SCLabPlotlyPreviewFIG02A || (typeof globalThis !== 'undefined' && globalThis.SCLabPlotlyPreviewFIG02A);
    if (!f) { throw new Error('FIG-02A primitives are required'); }
    return f;
  }
  function isObj(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
  function has(list, v) { return list.indexOf(v) >= 0; }
  function num(v, lo, hi) { return typeof v === 'number' && isFinite(v) && v >= lo && v <= hi; }

  // ------------------------------------------------------------------
  // Site Intelligence evidence objects
  // ------------------------------------------------------------------
  function member(node, key) {
    if (!node || node.t !== 'o') { return null; }
    for (var j = 0; j < node.v.length; j++) { if (node.v[j][0] === key) { return node.v[j][1]; } }
    return null;
  }

  // SI digest: "sha256:" + SHA-256 of canonical JSON of the object without content_digest.
  function siDigest(objectNode) {
    var F = lib();
    var basis = { t: 'o', v: objectNode.v.filter(function (kv) { return kv[0] !== 'content_digest'; }) };
    return F.sha256Hex(F.canonical(basis)).then(function (h) { return 'sha256:' + h; });
  }

  function positions(coords, out) {
    if (Array.isArray(coords) && coords.length >= 2 && typeof coords[0] === 'number' && typeof coords[1] === 'number') {
      out.push([coords[0], coords[1]]);
    } else if (Array.isArray(coords)) { coords.forEach(function (c) { positions(c, out); }); }
    return out;
  }

  function objectErrors(o, i) {
    var e = [], at = 'objects[' + i + ']';
    if (!isObj(o) || o.schema !== SI_OBJECT) { return [at + ' is not a Site Intelligence ' + SI_OBJECT]; }
    if (o.crs !== CRS) { e.push(at + ' crs must be ' + CRS + ' (Lab does not reproject)'); }
    var prov = isObj(o.provenance) ? o.provenance : {};
    if (prov.source_crs !== CRS && !(Array.isArray(prov.transformations) && prov.transformations.length)) {
      e.push(at + ' source CRS ' + prov.source_crs + ' has no recorded Site Intelligence transformation');
    }
    var g = o.geometry;
    var kinds = [].concat(GEOMETRY.point, GEOMETRY.line, GEOMETRY.polygon);
    if (!isObj(g) || !has(kinds, g.type)) { e.push(at + ' geometry ' + (g && g.type) + ' is not a supported vector type'); return e; }
    var pts = positions(g.coordinates, []);
    if (!pts.length || pts.some(function (p) { return !num(p[0], -180, 180) || !num(p[1], -90, 90); })) {
      e.push(at + ' coordinates are not WGS84 longitude/latitude');
    } else {
      var lons = pts.map(function (p) { return p[0]; }), lats = pts.map(function (p) { return p[1]; });
      var bb = [Math.min.apply(null, lons), Math.min.apply(null, lats), Math.max.apply(null, lons), Math.max.apply(null, lats)];
      if (!Array.isArray(o.bbox) || o.bbox.length !== 4 || o.bbox.some(function (v, k) { return v !== bb[k]; })) {
        e.push(at + ' bbox does not match its geometry');
      }
    }
    var src = isObj(o.source) ? o.source : {};
    if (!src.source_id) { e.push(at + ' has no source_id'); }
    if (o.visibility !== 'public') { e.push(at + ' visibility ' + o.visibility + ' is not public (private objects need the Workspace path)'); }
    if (!/^sha256:[0-9a-f]{64}$/.test(String(o.content_digest))) { e.push(at + ' has no Site Intelligence content digest'); }
    return e;
  }

  // ------------------------------------------------------------------
  // View policy
  // ------------------------------------------------------------------
  function urlErrors(value, path, out) {
    if (typeof value === 'string') {
      if (URLISH.test(value) && !/\.objects\[\d+\]\.source\.url$/.test(path)) { out.push(path + ' contains a URL or tile template'); }
    } else if (Array.isArray(value)) { value.forEach(function (v, k) { urlErrors(v, path + '[' + k + ']', out); }); }
    else if (isObj(value)) { Object.keys(value).forEach(function (k) { urlErrors(value[k], path + '.' + k, out); }); }
    return out;
  }

  function isV11(spec) { return isObj(spec) && spec.schema === VIEW_SCHEMA_11; }
  function numericOrAbsent(v) { return v === null || v === undefined || (typeof v === 'number' && isFinite(v)); }

  // Earth-observation rasters: refused unless the provider is approved, licensed in the bundle, served from
  // its approved host and described by product provenance. The default registry approves nothing.
  function eoLayerErrors(layer, bundle, providers) {
    var reg = providers || PROVIDERS, e = [], at = 'rasterLayers[' + (layer && layer.id) + ']';
    if (!isObj(layer) || !ID.test(String(layer.id))) { return ['raster layer needs an id']; }
    var p = reg[layer.provider];
    if (!p) {
      var c = EO_CANDIDATES[layer.provider];
      return [at + ' provider ' + JSON.stringify(layer.provider) + ' is not approved' + (c ? ' (' + c.label + ' is a candidate; approval needs ' + c.needs.join(', ') + ')' : '') + '; no imagery is fetched'];
    }
    var cited = ((bundle && bundle.sourceRefs) || []).filter(function (r) { return r.sourceId === layer.provider; });
    if (!cited.length || cited.some(function (r) { return r.licenceStatus !== 'verified-for-use'; })) { e.push(at + ' provider licence is not verified-for-use in the bundle sourceRefs'); }
    var tiles = Array.isArray(layer.tiles) ? layer.tiles : [];
    if (!tiles.length || tiles.some(function (t) {
      var m = /^https:\/\/([a-z0-9.-]+)\/[A-Za-z0-9_.\/{}-]*\{z\}\/\{x\}\/\{y\}[A-Za-z0-9_.\/-]*$/.exec(String(t));
      return !m || (p.hosts || []).indexOf(m[1]) < 0;
    })) { e.push(at + ' tiles must be HTTPS {z}/{x}/{y} templates on the provider\'s approved hosts'); }
    var prov = isObj(layer.provenance) ? layer.provenance : {};
    ['productId', 'acquiredAt', 'processingBaseline'].forEach(function (k) { if (typeof prov[k] !== 'string' || !prov[k]) { e.push(at + ' provenance.' + k + ' is required'); } });
    if (!/^[0-9a-f]{64}$/.test(String(prov.tileManifestSha256))) { e.push(at + ' provenance.tileManifestSha256 is required'); }
    if (typeof layer.attribution !== 'string' || layer.attribution.indexOf(p.attribution) < 0) { e.push(at + ' attribution must include "' + p.attribution + '"'); }
    return e;
  }

  function specErrors(spec, bundle) {
    var e = [];
    if (!isObj(spec) || (spec.schema !== VIEW_SCHEMA && spec.schema !== VIEW_SCHEMA_11)) { return ['view is not ' + VIEW_SCHEMA + ' or ' + VIEW_SCHEMA_11]; }
    var v11 = isV11(spec);
    Object.keys(spec).forEach(function (k) { if (!has(v11 ? TOP_KEYS_11 : TOP_KEYS, k)) { e.push('top-level property ' + k + ' is not allowed'); } });
    if (spec.basemap !== null && spec.basemap !== undefined) {
      var id = isObj(spec.basemap) ? spec.basemap.provider : spec.basemap;
      if (!PROVIDERS[id]) { e.push('basemap ' + JSON.stringify(id) + ' is not an approved provider (none are approved yet; no tile fetch)'); }
    }
    if (v11 && spec.rasterLayers !== undefined) {
      if (!Array.isArray(spec.rasterLayers)) { e.push('rasterLayers must be a list'); }
      else { spec.rasterLayers.forEach(function (l) { e = e.concat(eoLayerErrors(l, bundle)); }); }
    }
    // Raster tile templates are judged by eoLayerErrors (approved hosts only); every other string must hold no URL.
    urlErrors(Object.keys(spec).reduce(function (m, k) { if (k !== 'rasterLayers') { m[k] = spec[k]; } return m; }, {}), 'view', e);
    var objs = spec.objects;
    if (!Array.isArray(objs) || !objs.length || objs.length > MAX_OBJECTS) { e.push('objects must hold 1 to ' + MAX_OBJECTS + ' evidence objects'); return e; }
    objs.forEach(function (o, i) { e = e.concat(objectErrors(o, i)); });
    var ids = objs.map(function (o) { return o && o.object_id; });
    if (ids.some(function (x, k) { return ids.indexOf(x) !== k; })) { e.push('object_id values must be unique'); }
    // Evidence: one class per object, never promoted to observed.
    var ef = spec.evidenceField;
    if (!FIELD.test(String(ef))) { e.push('evidenceField must name the property holding each object\'s evidence class'); return e; }
    var classes = [];
    objs.forEach(function (o, i) {
      var c = o && isObj(o.properties) ? o.properties[ef] : undefined;
      if (!has(EVIDENCE, c)) { e.push('objects[' + i + '] evidence class ' + JSON.stringify(c) + ' is not recognised'); return; }
      if (classes.indexOf(c) < 0) { classes.push(c); }
      if (c === 'observed' && bundle && bundle.evidenceClass !== 'observed') { e.push('objects[' + i + '] is observed inside a ' + bundle.evidenceClass + ' figure (no promotion to observed)'); }
    });
    // Fields shown in the table need labels and units; 1.1 may name the property holding each value's uncertainty.
    var fields = isObj(spec.fields) ? spec.fields : {};
    Object.keys(fields).forEach(function (f) {
      var d = fields[f];
      if (!FIELD.test(f) || !isObj(d) || typeof d.label !== 'string' || !d.label || typeof d.unit !== 'string' || !d.unit) { e.push('field ' + f + ' needs a label and a unit'); return; }
      Object.keys(d).forEach(function (k) { if (!has(v11 ? ['label', 'unit', 'uncertainty', 'uncertaintyKind'] : ['label', 'unit'], k)) { e.push('field ' + f + '.' + k + ' is not allowed'); } });
      if (d.uncertainty !== undefined) {
        if (!FIELD.test(String(d.uncertainty)) || !has(UNCERTAINTY_KINDS, d.uncertaintyKind)) { e.push('field ' + f + ' uncertainty needs a property name and a kind (' + UNCERTAINTY_KINDS.join(', ') + ')'); }
        else if (objs.some(function (o) { var u = (o.properties || {})[d.uncertainty]; return !(numericOrAbsent(u) && !(u < 0)); })) { e.push('field ' + f + ' uncertainty values must be non-negative numbers'); }
      }
    });
    function numericField(f, where) {
      if (!fields[f]) { e.push(where + ' field ' + JSON.stringify(f) + ' is not a declared field'); return false; }
      if (objs.some(function (o) { return !numericOrAbsent((o.properties || {})[f]); })) { e.push(where + ' field ' + f + ' must be numeric'); return false; }
      return true;
    }
    // Layers: allow-listed geometry, constant styles, filters on the evidence field only.
    var layers = spec.layers, drawn = {};
    if (!Array.isArray(layers) || !layers.length || layers.length > 12) { e.push('layers must hold 1 to 12 layers'); return e; }
    layers.forEach(function (l, i) {
      var at = 'layers[' + i + ']';
      var kinds = v11 ? ['point', 'line', 'polygon', 'heatmap'] : ['point', 'line', 'polygon'];
      if (!isObj(l) || !ID.test(String(l.id)) || !has(kinds, l.geometry)) { e.push(at + ' needs an id and geometry ' + kinds.join(', ')); return; }
      var keys = ['id', 'geometry', 'where', 'style'].concat(v11 ? (l.geometry === 'heatmap' ? ['weight', 'ramp'] : ['classify', 'encode', 'uncertaintyRef']) : []);
      Object.keys(l).forEach(function (k) { if (!has(keys, k)) { e.push(at + '.' + k + ' is not allowed'); } });
      if (l.where !== undefined && !(isObj(l.where) && l.where.property === ef && has(EVIDENCE, l.where.equals) && Object.keys(l.where).length === 2)) {
        e.push(at + '.where may only select one evidence class');
      }
      var st = isObj(l.style) ? l.style : {};
      Object.keys(st).forEach(function (k) {
        var v = st[k];
        var bound = { opacity: [0, 1], intensity: [0.1, 5], radius: [0, l.geometry === 'heatmap' ? 60 : 24] }[k] || [0, 24];
        if (!has(STYLE_KEYS[l.geometry], k)) { e.push(at + '.style.' + k + ' is not allowed for ' + l.geometry); }
        else if (/color$/i.test(k) ? !COLOR.test(String(v)) : !num(v, bound[0], bound[1])) {
          e.push(at + '.style.' + k + ' must be a constant (' + (/color$/i.test(k) ? '#rrggbb' : 'bounded number') + '), not an expression');
        }
      });
      if (l.classify !== undefined) {
        var c = l.classify;
        if (l.geometry === 'line' || !isObj(c) || Object.keys(c).sort().join() !== 'breaks,colors,field') { e.push(at + '.classify needs field, breaks and colors (points and polygons only)'); }
        else if (numericField(c.field, at + '.classify')) {
          var b = c.breaks;
          if (!Array.isArray(b) || !b.length || b.length > 8 || b.some(function (x, k) { return !num(x, -1e12, 1e12) || (k && x <= b[k - 1]); })) { e.push(at + '.classify.breaks must be 1 to 8 ascending numbers'); }
          else if (!Array.isArray(c.colors) || c.colors.length !== b.length + 1 || c.colors.some(function (x) { return !COLOR.test(String(x)); })) { e.push(at + '.classify.colors must be ' + (b.length + 1) + ' #rrggbb constants'); }
        }
      }
      if (l.encode !== undefined || l.uncertaintyRef !== undefined) {
        var uc = isObj(l.classify) ? l.classify : {}, ud = fields[uc.field] || {};
        var refs = (bundle && bundle.calculationRefs) || [];
        if (l.encode !== 'uncertainty' || !isObj(l.classify)) { e.push(at + ' uncertainty layer needs encode "uncertainty" and classify'); }
        else if (!ud.uncertainty) { e.push(at + ' field ' + uc.field + ' declares no uncertainty property to draw'); }
        else if (!isRampRun(uc.colors)) { e.push(at + ' uncertainty classes must be an ordered run of the ' + Object.keys(RAMPS).join(', ') + ' ramp'); }
        if (!(Number.isInteger(l.uncertaintyRef) && refs[l.uncertaintyRef] && /^[0-9a-f]{64}$/.test(String(refs[l.uncertaintyRef].uncertaintySha256)))) {
          e.push(at + ' uncertainty layer must be bound to a Workspace run (calculationRef with uncertaintySha256)');
        }
      }
      if (l.geometry === 'heatmap') {
        var w = l.weight;
        if (!isObj(w) || Object.keys(w).sort().join() !== 'field,max' || !num(w.max, 1e-12, 1e12)) { e.push(at + '.weight needs a declared numeric field and a positive max'); }
        else { numericField(w.field, at + '.weight'); }
        if (!RAMPS[l.ramp]) { e.push(at + '.ramp must be one of ' + Object.keys(RAMPS).join(', ')); }
      }
      var geoms = l.geometry === 'heatmap' ? GEOMETRY.point : GEOMETRY[l.geometry];
      objs.forEach(function (o) {
        if (o && isObj(o.geometry) && has(geoms, o.geometry.type) && (!l.where || (o.properties || {})[ef] === l.where.equals)) { drawn[o.object_id] = true; }
      });
    });
    var undrawn = objs.filter(function (o) { return o && !drawn[o.object_id]; }).length;
    if (undrawn) { e.push(undrawn + ' object(s) are not drawn by any layer'); }
    if (classes.length > 1 && !classes.every(function (c) { return layers.some(function (l) { return l.where && l.where.equals === c; }); })) {
      e.push('objects mix ' + classes.length + ' evidence classes but each class needs its own layer');
    }
    var legend = Array.isArray(spec.legend) ? spec.legend : [];
    layers.forEach(function (l) {
      if (!legend.some(function (g) { return g && g.layer === l.id && typeof g.label === 'string' && g.label.length > 3; })) { e.push('layer ' + l.id + ' has no legend label'); }
    });
    // Time steps: exactly the distinct SI observed_at values, in order.
    var steps = timeSteps(spec);
    if (v11 && spec.time !== undefined) {
      var t = spec.time, seen = [];
      objs.forEach(function (o) { if (seen.indexOf(o.observed_at) < 0) { seen.push(o.observed_at); } });
      seen.sort();
      if (!isObj(t) || t.property !== 'observed_at' || typeof t.label !== 'string' || !t.label || !Array.isArray(t.steps) || Object.keys(t).sort().join() !== 'initial,label,property,steps') { e.push('time needs property observed_at, a label, steps and initial'); }
      else if (seen.some(function (x) { return typeof x !== 'string'; }) || JSON.stringify(t.steps) !== JSON.stringify(seen)) { e.push('time.steps must be exactly the objects\' distinct observed_at values in order'); }
      else if (t.steps.indexOf(t.initial) < 0) { e.push('time.initial must be one of the steps'); }
    }
    if (v11 && spec.comparison !== undefined) {
      var cp = spec.comparison;
      if (!isObj(cp) || Object.keys(cp).sort().join() !== 'field,from,label,match,to') { e.push('comparison needs field, from, to, match and label'); }
      else {
        numericField(cp.field, 'comparison');
        if (!FIELD.test(String(cp.match)) || typeof cp.label !== 'string' || cp.label.length < 4) { e.push('comparison needs a match property and a label'); }
        if (steps.indexOf(cp.from) < 0 || steps.indexOf(cp.to) < 0 || cp.from >= cp.to) { e.push('comparison from and to must be two time steps, earlier first'); }
      }
    }
    if (v11 && spec.calculations !== undefined) {
      var refs = (bundle && bundle.calculationRefs) || [];
      if (!Array.isArray(spec.calculations) || spec.calculations.length > 8) { e.push('calculations must be a list of at most 8'); }
      else {
        spec.calculations.forEach(function (c, i) {
          var at = 'calculations[' + i + ']';
          if (!isObj(c) || typeof c.label !== 'string' || !c.label || !num(c.value, -1e15, 1e15) || typeof c.unit !== 'string' || !c.unit || typeof c.method !== 'string' || !c.method) { e.push(at + ' needs label, numeric value, unit and method'); return; }
          if (c.uncertainty !== undefined && !(num(c.uncertainty, 0, 1e15) && has(UNCERTAINTY_KINDS, c.uncertaintyKind))) { e.push(at + ' uncertainty needs a non-negative value and a kind'); }
          Object.keys(c).forEach(function (k) { if (!has(CALC_KEYS, k)) { e.push(at + '.' + k + ' is not allowed'); } });
          // SC-FIG-04.1: an interval is the runtime's own record; it must contain the value and is never derived here.
          if (c.interval !== undefined) {
            var iv = c.interval;
            if (!isObj(iv) || Object.keys(iv).sort().join() !== INTERVAL_KEYS || iv.semantics !== 'confidence-interval' || iv.computedBy !== 'runtime' ||
                !num(iv.level, 0.5, 0.999999) || !num(iv.degreesOfFreedom, 1, 1e12) || !num(iv.lower, -1e15, 1e15) || !num(iv.upper, -1e15, 1e15) ||
                !(iv.lower <= c.value && c.value <= iv.upper) || typeof iv.method !== 'string' || !iv.method || typeof iv.distribution !== 'string' ||
                !has(INTERVAL_CONSISTENCY, iv.consistency)) {
              e.push(at + ' interval must be the runtime confidence-interval record containing the value');
            } else if (iv.consistency === 'estimate-plus-minus-t-standard-error' && c.uncertaintyKind !== 'standard-error') {
              e.push(at + ' interval says it was checked against a standard error the calculation does not report');
            } else if (!(refs[c.calculationRef] && /^[0-9a-f]{64}$/.test(String(refs[c.calculationRef].intervalsSha256)))) {
              e.push(at + ' interval must be bound by the calculationRef (intervalsSha256)');
            }
          }
          if (c.assumptions !== undefined) {
            var as = c.assumptions;
            if (!isObj(as) || Object.keys(as).sort().join() !== 'checked,declaredBy,statements' || as.declaredBy !== 'model-revision' || as.checked !== false ||
                !Array.isArray(as.statements) || !as.statements.length || as.statements.length > 12 ||
                !as.statements.every(function (x) { return typeof x === 'string' && x.length > 0 && x.length <= 240; })) {
              e.push(at + ' assumptions must be the model revision\'s declared, unchecked statements');
            } else if (!(refs[c.calculationRef] && Array.isArray(refs[c.calculationRef].assumptionRefs) && refs[c.calculationRef].assumptionRefs.length)) {
              e.push(at + ' assumptions must be bound by the calculationRef (assumptionRefs)');
            }
          }
          if (!(Number.isInteger(c.calculationRef) && refs[c.calculationRef] && /^[0-9a-f]{64}$/.test(String(refs[c.calculationRef].outputSha256)))) { e.push(at + ' must point at a bundle calculationRef with an output hash'); }
        });
      }
    }
    // Bundle: a spatial-map in EPSG:4326 whose bbox covers every object; every SI source cited.
    if (bundle) {
      var sp = bundle.spatial || {};
      if (bundle.figureType !== 'spatial-map' || sp.crs !== CRS) { e.push('bundle must be a spatial-map in ' + CRS); }
      var bb = sp.bbox;
      if (!Array.isArray(bb) || bb.length !== 4) { e.push('bundle spatial.bbox is required'); }
      else {
        objs.forEach(function (o, i) {
          var b = o && o.bbox;
          if (Array.isArray(b) && (b[0] < bb[0] || b[1] < bb[1] || b[2] > bb[2] || b[3] > bb[3])) { e.push('objects[' + i + '] lies outside the bundle bbox'); }
        });
      }
      var cited = (bundle.sourceRefs || []).map(function (s) { return s.sourceId; });
      objs.forEach(function (o, i) {
        var sid = o && o.source && o.source.source_id;
        if (sid && cited.indexOf(sid) < 0) { e.push('objects[' + i + '] source ' + sid + ' is not cited in the bundle sourceRefs'); }
      });
      if (sp.geometryStatus === 'source-verified' && bundle.sourceRefs.some(function (s) { return s.identityStatus !== 'source-hash-verified'; })) {
        e.push('geometryStatus source-verified needs hash-verified sources');
      }
      // Source rights: a figure is drawn only from sources whose licence is verified for use.
      if (v11) {
        (bundle.sourceRefs || []).forEach(function (s) {
          if (s.licenceStatus !== 'verified-for-use') { e.push('source ' + s.sourceId + ' licence is ' + s.licenceStatus + ', not verified-for-use'); }
        });
      }
    }
    return e;
  }

  function isRampRun(colors) {
    if (!Array.isArray(colors) || colors.length < 2) { return false; }
    var c = colors.map(function (x) { return String(x).toLowerCase(); }).join();
    return Object.keys(RAMPS).some(function (k) {
      var r = RAMPS[k];
      for (var i = 0; i + colors.length <= r.length; i++) { if (r.slice(i, i + colors.length).join() === c) { return true; } }
      return false;
    });
  }

  function timeSteps(spec) {
    return isV11(spec) && isObj(spec.time) && Array.isArray(spec.time.steps) ? spec.time.steps.slice() : [];
  }

  // Resolves [] when acceptable (policy + every SI digest recomputed from the view's own text).
  function viewErrors(text, spec, bundle) {
    var e = specErrors(spec, bundle);
    if (e.length) { return Promise.resolve(e); }
    var objs = member(lib().parseLexical(text), 'objects');
    return Promise.all(objs.v.map(siDigest)).then(function (digests) {
      return digests.map(function (d, i) {
        return d === spec.objects[i].content_digest ? null : 'objects[' + i + '] content does not match its Site Intelligence digest';
      }).filter(Boolean);
    });
  }

  // ------------------------------------------------------------------
  // MapLibre style (v8): one inline GeoJSON source, no glyphs, sprite or URL anywhere. Every expression is
  // generated here from validated constants; nothing an author wrote is passed to MapLibre as an expression.
  // ------------------------------------------------------------------
  function layerFilter(spec, l, step) {
    var ef = spec.evidenceField, types = l.geometry === 'heatmap' ? GEOMETRY.point : GEOMETRY[l.geometry];
    var filter = ['all', ['in', ['geometry-type'], ['literal', types]]];
    if (l.where) { filter.push(['==', ['get', ef], l.where.equals]); }
    if (step !== undefined && step !== null) { filter.push(['==', ['get', 'sc_time'], step]); }
    return filter;
  }

  // Heat maps sit on a light background, so density runs from the ramp's light end (low) to its dark end (high).
  function heatRamp(name) { return RAMPS[name].slice().reverse(); }

  // prop: the property classified (the field itself, or its declared uncertainty property for an uncertainty layer).
  function classColor(c, prop) {
    var p = prop || c.field;
    var expr = ['step', ['to-number', ['get', p]], c.colors[0]];
    c.breaks.forEach(function (b, k) { expr.push(b, c.colors[k + 1]); });
    return ['case', ['==', ['typeof', ['get', p]], 'number'], expr, NOT_REPORTED];
  }
  function classProp(spec, l) { return l.encode === 'uncertainty' ? spec.fields[l.classify.field].uncertainty : l.classify.field; }

  // Style layer ids per view layer (the renderer toggles and filters them together).
  function styleLayerIds(l) { return l.geometry === 'polygon' ? [l.id, l.id + '-outline'] : [l.id]; }

  function compileStyle(spec, step) {
    var ef = spec.evidenceField, v11 = isV11(spec), fields = isObj(spec.fields) ? spec.fields : {};
    var features = spec.objects.map(function (o) {
      var props = { sc_object_id: o.object_id };
      props[ef] = o.properties[ef];
      if (v11) {
        Object.keys(fields).forEach(function (f) {
          var v = o.properties[f];
          if (typeof v === 'number' || typeof v === 'string') { props[f] = v; }
          var u = fields[f].uncertainty && o.properties[fields[f].uncertainty];
          if (typeof u === 'number') { props[fields[f].uncertainty] = u; }
        });
        if (spec.time) { props.sc_time = o.observed_at; }
      }
      return { type: 'Feature', id: o.object_id, geometry: o.geometry, properties: props };
    });
    var at = v11 && spec.time ? (step === undefined ? spec.time.initial : step) : null;
    var layers = [{ id: 'sc-background', type: 'background', paint: { 'background-color': '#f6f6f3' } }];
    spec.layers.forEach(function (l) {
      var s = l.style || {}, filter = layerFilter(spec, l, at);
      if (l.geometry === 'polygon') {
        layers.push({ id: l.id, type: 'fill', source: 'sc-evidence', filter: filter,
          paint: { 'fill-color': l.classify ? classColor(l.classify, classProp(spec, l)) : (s.color || '#4d4d4d'), 'fill-opacity': s.opacity === undefined ? 0.3 : s.opacity } });
        layers.push({ id: l.id + '-outline', type: 'line', source: 'sc-evidence', filter: filter,
          paint: { 'line-color': s.outlineColor || s.color || '#4d4d4d', 'line-width': s.outlineWidth === undefined ? 1 : s.outlineWidth } });
      } else if (l.geometry === 'line') {
        layers.push({ id: l.id, type: 'line', source: 'sc-evidence', filter: filter,
          paint: { 'line-color': s.color || '#4d4d4d', 'line-width': s.width === undefined ? 2 : s.width, 'line-opacity': s.opacity === undefined ? 1 : s.opacity } });
      } else if (l.geometry === 'heatmap') {
        var ramp = heatRamp(l.ramp);
        layers.push({ id: l.id, type: 'heatmap', source: 'sc-evidence', filter: filter,
          paint: { 'heatmap-weight': ['interpolate', ['linear'], ['to-number', ['get', l.weight.field], 0], 0, 0, l.weight.max, 1],
            'heatmap-radius': s.radius === undefined ? 30 : s.radius, 'heatmap-intensity': s.intensity === undefined ? 1 : s.intensity,
            'heatmap-opacity': s.opacity === undefined ? 0.8 : s.opacity,
            'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'], 0, 'rgba(0,0,0,0)', 0.2, ramp[0], 0.4, ramp[1], 0.6, ramp[2], 0.8, ramp[3], 1, ramp[4]] } });
      } else {
        layers.push({ id: l.id, type: 'circle', source: 'sc-evidence', filter: filter,
          paint: { 'circle-color': l.classify ? classColor(l.classify, classProp(spec, l)) : (s.color || '#4d4d4d'), 'circle-radius': s.radius === undefined ? 5 : s.radius,
            'circle-opacity': s.opacity === undefined ? 1 : s.opacity, 'circle-stroke-color': s.strokeColor || '#ffffff',
            'circle-stroke-width': s.strokeWidth === undefined ? 1 : s.strokeWidth } });
      }
    });
    return {
      version: 8, name: spec.title,
      sources: { 'sc-evidence': { type: 'geojson', data: { type: 'FeatureCollection', features: features } } },
      layers: layers
    };
  }

  function fmt(v) { return typeof v === 'number' ? String(Math.round(v * 1e6) / 1e6) : String(v); }
  function unitLabel(d) { return ['1', 'category'].indexOf(d.unit) >= 0 ? d.label : d.label + ' (' + d.unit + ')'; }
  var KIND_LABEL = { 'standard-error': '± standard error', 'standard-deviation': '± standard deviation', 'ci95-half-width': '± 95% CI half-width' };

  // Scientific legend: one entry per layer, with graduated classes (bounds and unit), the heat-map ramp and
  // what it means, and the "not reported" swatch where values can be missing.
  function legendModel(spec) {
    var fields = isObj(spec.fields) ? spec.fields : {};
    return spec.layers.map(function (l) {
      var g = (spec.legend || []).filter(function (x) { return x.layer === l.id; })[0] || {};
      var s = l.style || {}, entry = { layer: l.id, label: g.label, geometry: l.geometry, evidenceClass: l.where ? l.where.equals : null };
      if (l.classify) {
        var d = fields[l.classify.field], b = l.classify.breaks, isU = l.encode === 'uncertainty';
        entry.field = isU ? d.label + ' ' + KIND_LABEL[d.uncertaintyKind] + ' (' + d.unit + ')' : unitLabel(d);
        entry.classes = l.classify.colors.map(function (c, k) {
          return { color: c, label: (k === 0 ? '< ' + fmt(b[0]) : k === b.length ? '≥ ' + fmt(b[k - 1]) : fmt(b[k - 1]) + ' to < ' + fmt(b[k])) + ' ' + d.unit };
        }).concat([{ color: NOT_REPORTED, label: isU ? 'uncertainty not reported (none drawn or imputed)' : 'not reported' }]);
        if (isU) {
          var prop = d.uncertainty, missing = spec.objects.filter(function (o) {
            var g = GEOMETRY[l.geometry] || [];
            return has(g, o.geometry.type) && (!l.where || (o.properties || {})[spec.evidenceField] === l.where.equals) && typeof (o.properties || {})[prop] !== 'number';
          }).length;
          entry.encode = 'uncertainty';
          entry.note = 'Uncertainty as computed by the bound Workspace run (' + d.uncertaintyKind + '); darker = smaller, lighter = larger.' +
            (missing ? ' ' + missing + ' object(s) have no reported uncertainty.' : '');
        }
      } else if (l.geometry === 'heatmap') {
        var w = fields[l.weight.field];
        entry.field = unitLabel(w);
        entry.ramp = heatRamp(l.ramp);
        entry.rampLabels = ['low', 'high'];
        entry.note = 'Kernel density of point weights (' + w.label + ', scaled 0 to ' + fmt(l.weight.max) + ' ' + w.unit + '); a visual density, not interpolated values.';
      } else {
        entry.color = s.color || '#4d4d4d';
      }
      return entry;
    });
  }

  // Plain-data accessible table: one row per object; uncertainty columns follow their fields.
  function tableModel(spec, caption) {
    var fields = isObj(spec.fields) ? spec.fields : {}, names = Object.keys(fields), ef = spec.evidenceField;
    var cols = [];
    names.forEach(function (f) {
      cols.push(unitLabel(fields[f]));
      if (fields[f].uncertainty) { cols.push(fields[f].label + ' ' + KIND_LABEL[fields[f].uncertaintyKind] + ' (' + fields[f].unit + ')'); }
    });
    var columns = ['Object', 'Geometry', 'Evidence class'].concat(cols)
      .concat(['Longitude, latitude (EPSG:4326 bbox centre)', 'Observed or valid time', 'Source', 'Licence', 'SI digest']);
    var rows = spec.objects.map(function (o) {
      var p = o.properties || {}, b = o.bbox, s = o.source || {}, vals = [];
      names.forEach(function (f) {
        var v = p[f]; vals.push(v === null || v === undefined ? 'not reported' : String(v));
        if (fields[f].uncertainty) { var u = p[fields[f].uncertainty]; vals.push(u === null || u === undefined ? 'not reported' : String(u)); }
      });
      return [o.object_id, o.geometry.type, String(p[ef])].concat(vals).concat([((b[0] + b[2]) / 2).toFixed(5) + ', ' + ((b[1] + b[3]) / 2).toFixed(5),
        o.observed_at || (o.valid_time && o.valid_time.start) || 'not reported',
        s.source_id + (s.authority && s.authority !== s.source_id ? ' (' + s.authority + ')' : ''),
        s.license || 'not recorded', o.content_digest]);
    });
    return { caption: caption, columns: columns, rows: rows };
  }

  // Side-by-side comparison of two time steps: per matched site, the change and its propagated uncertainty
  // (root sum of squares of the two declared uncertainties, which assumes they are independent).
  function comparisonModel(spec) {
    var cp = spec.comparison, d = spec.fields[cp.field], u = d.uncertainty;
    function at(step) {
      var m = {};
      spec.objects.forEach(function (o) {
        var key = (o.properties || {})[cp.match];
        if (o.observed_at === step && (typeof key === 'string' || typeof key === 'number')) { m[String(key)] = o; }
      });
      return m;
    }
    var a = at(cp.from), b = at(cp.to);
    var keys = Object.keys(a).filter(function (k) { return b[k]; }).sort();
    var columns = [cp.match, d.label + ' at ' + cp.from + ' (' + d.unit + ')', d.label + ' at ' + cp.to + ' (' + d.unit + ')', 'Change (' + d.unit + ')']
      .concat(u ? ['Change ' + KIND_LABEL[d.uncertaintyKind] + ' (' + d.unit + ', propagated, independence assumed)'] : []);
    var rows = keys.map(function (k) {
      var pa = a[k].properties, pb = b[k].properties, va = pa[cp.field], vb = pb[cp.field];
      var both = typeof va === 'number' && typeof vb === 'number';
      var row = [k, both || typeof va === 'number' ? fmt(va) : 'not reported', typeof vb === 'number' ? fmt(vb) : 'not reported', both ? fmt(vb - va) : 'not reported'];
      if (u) { var ua = pa[u], ub = pb[u]; row.push(both && typeof ua === 'number' && typeof ub === 'number' ? fmt(Math.sqrt(ua * ua + ub * ub)) : 'not reported'); }
      return row;
    });
    return { caption: cp.label, columns: columns, rows: rows, unmatched: Object.keys(a).length + Object.keys(b).length - 2 * keys.length };
  }

  return {
    version: VERSION, viewSchema: VIEW_SCHEMA, viewSchemas: [VIEW_SCHEMA, VIEW_SCHEMA_11], media: MEDIA, siObjectSchema: SI_OBJECT, crs: CRS,
    approvedProviders: function () { return Object.keys(PROVIDERS); },
    eoCandidates: function () { return JSON.parse(JSON.stringify(EO_CANDIDATES)); },
    siDigest: siDigest, objectErrors: objectErrors, specErrors: specErrors, viewErrors: viewErrors, eoLayerErrors: eoLayerErrors,
    compileStyle: compileStyle, layerFilter: layerFilter, styleLayerIds: styleLayerIds, timeSteps: timeSteps,
    legendModel: legendModel, tableModel: tableModel, comparisonModel: comparisonModel, isRampRun: isRampRun
  };
}));
