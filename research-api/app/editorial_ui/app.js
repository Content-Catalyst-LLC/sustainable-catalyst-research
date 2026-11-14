'use strict';
(function () {
  const API = '/api/research/v1';
  const $ = (id) => document.getElementById(id);
  const DIMENSIONS = ['scientific', 'rights', 'ownership', 'publication'];
  let current = null;

  function el(tag, attrs, ...kids) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (k === 'onclick') node.addEventListener('click', v); else if (k === 'class') node.className = v; else node.setAttribute(k, v);
    });
    kids.flat().forEach((k) => node.append(k instanceof Node ? k : document.createTextNode(String(k ?? ''))));
    return node;
  }
  function log(msg, bad) {
    const line = el('div', { class: bad ? 'bad' : '' }, new Date().toISOString().slice(11, 19) + '  ' + msg);
    $('log').prepend(line);
  }
  function b64url(text) {
    return btoa(unescape(encodeURIComponent(text))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function headers() {
    const h = { 'Authorization': 'Bearer ' + $('token').value.trim(), 'X-SC-Owner-Scope': $('owner').value.trim(),
                'Content-Type': 'application/json' };
    if ($('actor').value.trim()) h['X-SC-Actor-Scope'] = $('actor').value.trim();
    if ($('att').value.trim()) h['X-SC-Editor-Attestation'] = b64url(JSON.stringify(JSON.parse($('att').value)));
    return h;
  }
  async function call(method, path, body) {
    const res = await fetch(API + path, { method, headers: headers(), body: body ? JSON.stringify(body) : undefined,
                                          credentials: 'omit', cache: 'no-store' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { const d = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail); throw new Error(res.status + ' ' + d); }
    return data;
  }
  async function act(label, fn) {
    try { const out = await fn(); log(label + ': ok'); return out; } catch (e) { log(label + ': ' + e.message, true); return null; }
  }

  async function gate() {
    const s = await fetch(API + '/editorial/status', { cache: 'no-store' }).then((r) => r.json()).catch(() => null);
    $('gate').textContent = s && s.privateWritesEnabled ? 'private writes: staging credential' : 'private writes disabled';
  }
  async function loadList() {
    const out = await act('list articles', () => call('GET', '/editorial/articles'));
    const ul = $('articles'); ul.replaceChildren();
    (out?.items || []).forEach((a) => ul.append(el('li', { onclick: () => open(a.slug) },
      el('strong', {}, a.title), el('div', { class: 'muted' }, `${a.area} · r${a.headRevision} · ${a.reviewStatus} · ${a.publicationStatus}`))));
  }
  function referencesTable(refs) {
    return el('table', {}, el('tr', {}, el('th', {}, 'Role'), el('th', {}, 'Object'), el('th', {}, 'Classification'), el('th', {}, 'Status')),
      refs.map((r) => el('tr', {}, el('td', {}, r.role), el('td', {}, r.title || '', el('br'), el('code', {}, r.objectId)),
        el('td', {}, r.classification || ''), el('td', { class: r.status === 'verified' ? 'ok' : 'bad' }, r.status,
          r.problems?.length ? el('div', { class: 'muted' }, r.problems.join(', ')) : ''))));
  }
  async function open(slug) {
    const a = await act('open ' + slug, () => call('GET', '/editorial/articles/' + encodeURIComponent(slug)));
    if (!a) return;
    current = a;
    const area = await act('area ' + a.area.slug, () => call('GET', '/editorial/areas/' + encodeURIComponent(a.area.slug)));
    const head = a.head || { title: '', summary: '', sections: [{ heading: 'Overview', body: '' }], references: [] };
    const wrap = $('article'); wrap.replaceChildren();
    wrap.append(el('div', { class: 'card' }, el('h2', {}, a.slug),
      el('div', {}, `Head r${a.headRevision} · in review: ${a.submittedRevision ?? '—'} · published: ${a.publishedRevision ?? '—'} · ${a.publicationStatus}`),
      el('div', { class: 'muted' }, `Workspace project: ${a.workspaceProjectId || '—'}`)));
    if (area) {
      wrap.append(el('div', { class: 'card' }, el('h2', {}, 'Area: ' + area.title),
        el('div', {}, `Visibility ${area.visibility} · steward ${area.steward || 'unassigned'}`),
        el('div', { class: 'muted' }, 'Subject for a Workspace area-publication approval:'), el('code', {}, area.subjectRef),
        el('label', { for: 'area-rec' }, 'Signed area-publication record (JSON)'), el('textarea', { id: 'area-rec' }),
        el('button', { onclick: () => act('publish area', () => call('POST', `/editorial/areas/${area.slug}/publish`, { record: JSON.parse($('area-rec').value) })).then(() => open(slug)) }, 'Publish area'),
        el('button', { class: 'secondary', onclick: () => { const reason = prompt('Reason for retracting the area'); if (reason) act('retract area', () => call('POST', `/editorial/areas/${area.slug}/retract`, { reason })).then(() => open(slug)); } }, 'Retract area')));
    }
    wrap.append(el('div', { class: 'card' }, el('h2', {}, 'Revisions'),
      el('table', {}, el('tr', {}, el('th', {}, 'Rev'), el('th', {}, 'Author'), el('th', {}, 'Subject ref'), el('th', {}, 'Signed approvals')),
        a.revisions.map((r) => el('tr', {}, el('td', {}, 'r' + r.revision), el('td', {}, r.author, el('div', { class: 'muted' }, r.authorRole)),
          el('td', {}, el('code', {}, r.subjectRef)),
          el('td', {}, DIMENSIONS.map((d) => el('div', { class: r.signedApprovals[d] ? 'ok' : 'muted' }, (r.signedApprovals[d] ? '✓ ' : '· ') + d))))))));
    const bib = head.publication && head.publication.bibliography;
    if (bib) wrap.append(el('div', { class: 'card' }, el('h2', {}, `Bibliography snapshot (${bib.style}, from Workspace)`),
      el('ol', {}, bib.items.map((it) => el('li', {}, it.reference, it.library ? el('div', { class: 'muted' }, 'Library ' + it.library.recordId) : '')))));
    if (a.headReferences.length) wrap.append(el('div', { class: 'card' }, el('h2', {}, 'Head references (publication checks)'), referencesTable(a.headReferences)));
    wrap.append(el('div', { class: 'card' }, el('h2', {}, 'Write revision r' + (a.headRevision + 1)),
      el('label', { for: 'r-title' }, 'Title'), el('input', { id: 'r-title', value: head.title }),
      el('label', { for: 'r-summary' }, 'Summary'), el('textarea', { id: 'r-summary' }, head.summary),
      el('label', { for: 'r-sections' }, 'Sections (JSON list of {heading, body})'), el('textarea', { id: 'r-sections', style: 'min-height:160px' }, JSON.stringify(head.sections, null, 2)),
      el('label', { for: 'r-refs' }, 'References (JSON list of {objectId, role, pin, caption, packageId?})'), el('textarea', { id: 'r-refs', style: 'min-height:120px' }, JSON.stringify(head.references, null, 2)),
      el('label', { for: 'r-pub' }, 'Publication (JSON: type, derivedFrom, and the bibliography snapshot Workspace returned; empty for none)'),
      el('textarea', { id: 'r-pub', style: 'min-height:100px' }, head.publication ? JSON.stringify(head.publication, null, 2) : ''),
      el('label', { for: 'r-change' }, 'Change summary'), el('input', { id: 'r-change' }),
      el('button', { onclick: () => act('write revision', () => call('POST', `/editorial/articles/${slug}/revisions`, {
        baseRevision: a.headRevision, title: $('r-title').value, summary: $('r-summary').value, changeSummary: $('r-change').value,
        sections: JSON.parse($('r-sections').value), references: JSON.parse($('r-refs').value),
        ...($('r-pub').value.trim() ? { publication: JSON.parse($('r-pub').value) } : {}) })).then(() => open(slug)) }, 'Save revision'),
      el('button', { class: 'secondary', disabled: a.headRevision ? null : 'disabled', onclick: () => act('submit r' + a.headRevision, () => call('POST', `/editorial/articles/${slug}/revisions/${a.headRevision}/submit`)).then(() => open(slug)) }, 'Submit head for review')));
    if (a.submittedRevision) {
      wrap.append(el('div', { class: 'card' }, el('h2', {}, 'Review of r' + a.submittedRevision),
        el('p', { class: 'muted' }, 'Request each approval in Workspace for the subject ref above, then paste the signed record Workspace returns.'),
        el('label', { for: 'ap-dim' }, 'Dimension'), el('select', { id: 'ap-dim' }, DIMENSIONS.map((d) => el('option', { value: d }, d))),
        el('label', { for: 'ap-rec' }, 'Signed approval record (JSON)'), el('textarea', { id: 'ap-rec' }),
        el('button', { onclick: () => act('record approval', () => call('POST', `/editorial/articles/${slug}/revisions/${a.submittedRevision}/approvals/${$('ap-dim').value}`, { record: JSON.parse($('ap-rec').value) })).then(() => open(slug)) }, 'Record approval'),
        el('button', { onclick: () => act('publish r' + a.submittedRevision, () => call('POST', `/editorial/articles/${slug}/revisions/${a.submittedRevision}/publish`)).then(() => open(slug)) }, 'Publish')));
    }
    if (a.publicationStatus === 'published') {
      wrap.append(el('div', { class: 'card' }, el('h2', {}, 'Published r' + a.publishedRevision),
        el('button', { class: 'secondary', onclick: () => { const rev = prompt('Restore which previously released revision?'); const reason = rev && prompt('Reason'); if (reason) act('roll back', () => call('POST', `/editorial/articles/${slug}/rollback`, { revision: Number(rev), reason })).then(() => open(slug)); } }, 'Roll back'),
        el('button', { class: 'secondary', onclick: () => { const reason = prompt('Reason for retraction'); if (reason) act('retract', () => call('POST', `/editorial/articles/${slug}/retract`, { reason })).then(() => open(slug)); } }, 'Retract')));
    }
    const audit = await act('audit', () => call('GET', '/editorial/audit?article=' + encodeURIComponent(slug)));
    if (audit) wrap.append(el('div', { class: 'card' }, el('h2', {}, `Audit (chain ${audit.chain.valid ? 'valid' : 'BROKEN'}, ${audit.chain.events} events)`),
      el('table', {}, audit.events.slice(-30).reverse().map((e) => el('tr', {}, el('td', {}, '#' + e.seq), el('td', {}, e.action), el('td', {}, e.actor), el('td', { class: 'muted' }, e.at))))));
  }

  $('connect').addEventListener('click', loadList);
  $('create').addEventListener('click', () => act('create article', () => call('POST', '/editorial/articles', {
    areaSlug: $('n-area').value.trim(), projectKey: $('n-key').value.trim(), workspaceProjectId: $('n-ws').value.trim(),
    slug: $('n-slug').value.trim(), title: $('n-title').value.trim() })).then(loadList));
  gate();
})();
