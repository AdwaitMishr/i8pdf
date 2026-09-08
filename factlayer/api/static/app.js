'use strict';

const RELATION_ORDER = [
  'corroborates', 'contradicts', 'reconciled_by_context',
  'consistent_across_context', 'part_of',
];
const state = { tab: 'relations', collection: '', relation: '', cross: false, offset: 0 };

const $ = (id) => document.getElementById(id);
const escape = (s) => String(s ?? '').replace(/[&<>"]/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json();
}

/** Evidence with the value (and any separately located caption) highlighted. */
function evidenceHtml(fact) {
  const { text, value_offset: span, label } = fact.evidence;
  const [start, end] = span;
  let html;
  if (start >= 0 && end > start && end <= text.length) {
    html = escape(text.slice(0, start)) + '<mark>' + escape(text.slice(start, end)) +
           '</mark>' + escape(text.slice(end));
  } else {
    html = escape(text);
  }
  if (label) {
    html += `<br><span class="meta">caption, same page:</span> <mark class="label">${escape(label.text)}</mark>`;
  }
  return html;
}

function contextTags(fact) {
  const tags = Object.entries(fact.context || {})
    .map(([k, v]) => `<span class="tag">${escape(k)}: ${escape(v)}</span>`);
  if (fact.period) tags.unshift(`<span class="tag">period: ${escape(fact.period)}</span>`);
  tags.push(`<span class="tag">confidence ${fact.confidence}</span>`);
  return `<div class="tags">${tags.join('')}</div>`;
}

function sideHtml(fact) {
  const doc = fact.document || {};
  return `<div class="side">
    <div class="cite">${escape(doc.title || doc.filename || fact.doc_id)} &middot; page ${fact.page_no}</div>
    <div><span class="value">${escape(fact.display)}</span>
      <span class="meta">${escape(fact.metric)}</span></div>
    ${contextTags(fact)}
    <blockquote>${evidenceHtml(fact)}</blockquote>
  </div>`;
}

function relationCard(item) {
  const dims = JSON.parse(item.dimensions || '[]');
  const extra = [
    item.basis === 'value_bridge' ? 'found by value bridge' : null,
    dims.length ? `differs on: ${dims.join(', ')}` : null,
  ].filter(Boolean).join(' &middot; ');
  return `<article class="card">
    <div class="head">
      <span class="badge ${escape(item.relation)}">${escape(item.relation.replace(/_/g, ' '))}</span>
      <span class="meta">confidence ${item.confidence}${extra ? ' &middot; ' + extra : ''}</span>
    </div>
    <p class="why">${escape(item.explanation)}</p>
    <div class="sides">${sideHtml(item.left)}${sideHtml(item.right)}</div>
  </article>`;
}

async function loadStats() {
  const stats = await api('/api/stats');
  $('chips').innerHTML = [
    ['documents', stats.documents], ['pages', stats.pages],
    ['facts', stats.facts], ['relationships', stats.relations],
    ['concepts', stats.concepts],
  ].map(([k, v]) => `<span class="chip"><b>${v}</b> ${k}</span>`).join('');

  const select = $('collection');
  const current = select.value;
  select.innerHTML = '<option value="">all collections</option>' +
    stats.collections.map((c) => `<option value="${escape(c)}">${escape(c)}</option>`).join('');
  select.value = current;
  return stats;
}

async function loadRelations(append) {
  if (!append) state.offset = 0;
  const params = new URLSearchParams({ limit: '25', offset: String(state.offset) });
  if (state.relation) params.set('type', state.relation);
  if (state.collection) params.set('collection', state.collection);
  if (state.cross) params.set('cross_document', 'true');
  const data = await api('/api/relations?' + params);

  const counts = data.counts || {};
  $('relFilters').innerHTML = [
    `<button class="chip" data-rel="" aria-pressed="${!state.relation}">all</button>`,
    ...RELATION_ORDER.filter((r) => counts[r]).map((r) =>
      `<button class="chip" data-rel="${r}" aria-pressed="${state.relation === r}">` +
      `${r.replace(/_/g, ' ')} <b>${counts[r]}</b></button>`),
  ].join('');

  const html = data.items.map(relationCard).join('');
  if (append) $('relList').insertAdjacentHTML('beforeend', html);
  else $('relList').innerHTML = html || '<p class="empty">No relationships yet. Add a PDF above.</p>';
  state.offset += data.items.length;
  $('more').hidden = data.items.length < 25;
}

async function loadFacts() {
  const params = new URLSearchParams({ limit: '60' });
  const query = $('factQuery').value.trim();
  if (query) params.set('q', query);
  if (state.collection) params.set('collection', state.collection);
  const facts = await api('/api/facts?' + params);
  $('factList').innerHTML = facts.map((f) => `<article class="card">
      <div class="row">
        <div><span class="value">${escape(f.display)}</span>
          <span class="meta"> ${escape(f.metric)} &middot; ${escape(f.subject)}</span></div>
        <span class="meta">${escape((f.document || {}).title || f.doc_id)} p.${f.page_no}</span>
      </div>
      ${contextTags(f)}
      <blockquote>${evidenceHtml(f)}</blockquote>
    </article>`).join('') || '<p class="empty">No facts matched.</p>';
}

async function loadDocuments() {
  const docs = await api('/api/documents' + (state.collection ? `?collection=${encodeURIComponent(state.collection)}` : ''));
  $('docList').innerHTML = docs.map((d) => `<article class="card">
      <div class="row">
        <div><b>${escape(d.title || d.filename)}</b>
          <div class="meta">${escape(d.filename)} &middot; ${d.n_pages} pages &middot;
            ${d.n_facts} facts &middot; collection <b>${escape(d.collection)}</b> &middot;
            subject <b>${escape(d.subject || 'unknown')}</b>
            ${d.published_on ? '&middot; published ' + escape(d.published_on) : ''}</div>
        </div>
        <button data-delete="${escape(d.doc_id)}">Remove</button>
      </div>
    </article>`).join('') || '<p class="empty">No documents yet.</p>';
}

async function loadConcepts() {
  const [concepts, aliases] = await Promise.all([api('/api/concepts'), api('/api/aliases')]);
  const aliasHtml = aliases.length
    ? `<article class="card"><b>Learned aliases</b>
        <div class="meta">Wordings the value bridge saw resolve to the same measure often
        enough to merge into one concept.</div>
        <div class="tags">${aliases.map((a) =>
          `<span class="tag">${escape(a.left_key)} &harr; ${escape(a.right_key)} (${a.votes})</span>`).join('')}</div>
      </article>` : '';
  $('conceptList').innerHTML = aliasHtml + concepts.map((c) => `<article class="card">
      <div class="row"><b>${escape(c.label)}</b><span class="meta">${c.n_facts} facts</span></div>
    </article>`).join('');
}

const LOADERS = { relations: () => loadRelations(false), facts: loadFacts,
                  documents: loadDocuments, concepts: loadConcepts };

async function refresh() {
  await loadStats();
  await LOADERS[state.tab]();
}

document.addEventListener('click', async (event) => {
  const tab = event.target.closest('#tabs button');
  if (tab) {
    state.tab = tab.dataset.tab;
    document.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('active', b === tab));
    document.querySelectorAll('.tab').forEach((s) => s.classList.toggle('active', s.id === state.tab));
    await LOADERS[state.tab]();
    return;
  }
  const filter = event.target.closest('[data-rel]');
  if (filter) { state.relation = filter.dataset.rel; await loadRelations(false); return; }
  const remove = event.target.closest('[data-delete]');
  if (remove) {
    await api('/api/documents/' + remove.dataset.delete, { method: 'DELETE' });
    await refresh();
  }
});

$('more').addEventListener('click', () => loadRelations(true));
$('crossOnly').addEventListener('change', (e) => { state.cross = e.target.checked; loadRelations(false); });
$('collection').addEventListener('change', (e) => { state.collection = e.target.value; refresh(); });
$('factSearch').addEventListener('click', loadFacts);
$('factQuery').addEventListener('keydown', (e) => { if (e.key === 'Enter') loadFacts(); });

$('upload').addEventListener('submit', async (event) => {
  event.preventDefault();
  const file = $('pdf').files[0];
  if (!file) return;
  const body = new FormData();
  body.append('file', file);
  body.append('collection', $('uploadCollection').value.trim() || 'default');
  if ($('uploadSubject').value.trim()) body.append('subject', $('uploadSubject').value.trim());
  $('uploadStatus').textContent = `processing ${file.name}…`;
  try {
    const report = await api('/api/documents', { method: 'POST', body });
    $('uploadStatus').textContent = report.skipped
      ? `skipped: ${report.reason}`
      : `${report.n_facts} facts, ${report.n_relations} relationships in ${report.seconds}s`;
    await refresh();
  } catch (error) {
    $('uploadStatus').textContent = 'failed: ' + error.message;
  }
});

refresh();
