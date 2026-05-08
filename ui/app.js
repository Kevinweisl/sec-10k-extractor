// SEC 10-K Item Extractor — vanilla JS, no framework.
// Talks to the FastAPI backend mounted at the same origin.

const API = {
  filings: '/demo/filings',
  result: (slug) => `/demo/result/${encodeURIComponent(slug)}`,
  extract: '/extract',
};

const els = {
  goldRow: document.querySelector('#demo-gold .demo-buttons'),
  silverRow: document.querySelector('#demo-silver .demo-buttons'),
  form: document.querySelector('#extract-form'),
  cik: document.querySelector('#cik'),
  accession: document.querySelector('#accession'),
  runBtn: document.querySelector('#run-btn'),
  result: document.querySelector('#result'),
  status: document.querySelector('#status'),
  filingMeta: document.querySelector('#filing-meta'),
  itemsList: document.querySelector('#items-list'),
  footerMeta: document.querySelector('#footer-meta'),
};

// Items from the most recently rendered result, looked up by toggleItemDetail.
let currentItems = [];

(async function init() {
  try {
    await loadDemoButtons();
  } catch (err) {
    showMessage(`Failed to load demo filings: ${err.message}`, 'error');
  }
  els.form.addEventListener('submit', onLiveExtract);
})();

async function loadDemoButtons() {
  const resp = await fetch(API.filings);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  for (const f of data.filings) {
    const btn = renderDemoButton(f);
    (f.source === 'gold' ? els.goldRow : els.silverRow).appendChild(btn);
  }
}

function renderDemoButton(f) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'demo';
  btn.dataset.slug = f.slug;
  btn.innerHTML = `
    <span class="label">${escapeHtml(f.label)}</span>
    <span class="meta">${escapeHtml(f.form_type)} · CIK ${escapeHtml(f.cik)} · period ${escapeHtml(f.period_ending || 'n/a')}</span>
    <span class="characteristic">${escapeHtml(f.characteristic || '')}</span>
  `;
  btn.addEventListener('click', () => onDemoClick(f, btn));
  return btn;
}

async function onDemoClick(filing, btn) {
  btn.disabled = true;
  showMessage(`Loading cached extraction for ${filing.label}…`);
  try {
    const resp = await fetch(API.result(filing.slug));
    if (resp.status === 503) {
      const detail = await resp.json().catch(() => ({}));
      showMessage(detail.detail || `Demo cache for "${filing.slug}" not yet built.`, 'error');
      return;
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    renderResult(data, { source: 'cache', label: filing.label });
  } catch (err) {
    showMessage(err.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

async function onLiveExtract(ev) {
  ev.preventDefault();
  const cik = els.cik.value.trim();
  const accession = els.accession.value.trim();

  els.runBtn.disabled = true;
  showMessage(`Live extracting CIK ${cik} accession ${accession}… (3-30 sec)`);
  const t0 = performance.now();
  try {
    const resp = await fetch(API.extract, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cik, accession }),
    });
    if (!resp.ok) {
      const detail = await resp.json().catch(() => ({}));
      showMessage(detail.detail || `HTTP ${resp.status}`, 'error');
      return;
    }
    const data = await resp.json();
    const ms = Math.round(performance.now() - t0);
    renderResult(data, { source: 'live', label: `CIK ${cik}`, clientMs: ms });
  } catch (err) {
    showMessage(err.message, 'error');
  } finally {
    els.runBtn.disabled = false;
  }
}

function renderResult(data, { source, label, clientMs }) {
  els.result.hidden = false;
  currentItems = data.items || [];

  const { filing, items, meta, xbrl_validation } = data;

  els.status.className = '';
  els.status.textContent = formatStatusLine(source, items.length, meta, clientMs);

  els.filingMeta.innerHTML = renderFilingHeader(filing, label);

  els.itemsList.innerHTML = renderItems(items);
  els.itemsList.querySelectorAll('.item-row').forEach((row) => {
    row.addEventListener('click', () => toggleItemDetail(row));
  });

  els.footerMeta.innerHTML = renderFooterMeta(meta, xbrl_validation);

  els.result.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function formatStatusLine(source, itemCount, meta, clientMs) {
  const sourceTag = source === 'cache' ? 'cached' : 'live Phase 1';
  const parts = [sourceTag, `${itemCount} items`];
  if (meta?.server_elapsed_ms != null) parts.push(`${meta.server_elapsed_ms} ms server`);
  else if (meta?.extraction_time_ms != null) parts.push(`${meta.extraction_time_ms} ms extraction`);
  if (clientMs) parts.push(`${clientMs} ms client`);
  return parts.join(' · ');
}

function renderFilingHeader(filing, fallbackLabel) {
  const cover = filing.cover_page_incorporates;
  const ibrNote = cover
    ? `<div class="ibr-note">Cover page incorporates by reference: ${escapeHtml(cover.target_form || 'DEF 14A')}${cover.expected_year ? ` (expected year ${cover.expected_year})` : ''}</div>`
    : '';
  return `
    <div class="filing-title">${escapeHtml(fallbackLabel)}</div>
    <div class="filing-meta-row">
      <span><b>CIK:</b> ${escapeHtml(filing.cik)}</span>
      <span><b>Accession:</b> ${escapeHtml(filing.accession)}</span>
      <span><b>Form:</b> ${escapeHtml(filing.form_type)}</span>
      <span><b>Filed:</b> ${escapeHtml(filing.filing_date || '')}</span>
      <span><b>Period:</b> ${escapeHtml(filing.period_ending || '')}</span>
      ${filing.is_inline_xbrl ? `<span><b>iXBRL:</b> yes</span>` : ''}
      ${filing.is_abs_filing ? `<span><b>ABS:</b> yes</span>` : ''}
    </div>
    ${ibrNote}
  `;
}

function renderItems(items) {
  // Group by part. Part 0 = synthetic (cover record); Parts 1-4 = standard.
  const groups = new Map();
  for (const it of items) {
    const part = it.part ?? 0;
    if (!groups.has(part)) groups.set(part, []);
    groups.get(part).push(it);
  }
  return [0, 1, 2, 3, 4]
    .filter((p) => groups.has(p))
    .map((p) => renderPartGroup(p, groups.get(p)))
    .join('');
}

function renderPartGroup(part, items) {
  const partLabel = part === 0 ? 'Cover / synthetic' : `Part ${roman(part)}`;
  const rows = items.map(renderItemRow).join('');
  return `<div class="part-group"><h4>${escapeHtml(partLabel)}</h4>${rows}</div>`;
}

function renderItemRow(item) {
  const dim = !item.applicable_in_era && item.status === 'extracted' ? ' dim' : '';
  // status class names mirror the Status enum in src/workers/extractor/schema.py.
  return `
    <div class="item-row" data-item-num="${escapeHtml(item.item_number)}">
      <span class="item-num">${escapeHtml(item.item_number)}</span>
      <span class="item-title${dim}">${escapeHtml(item.item_title || '(no title)')}</span>
      <span class="status-badge status-${escapeHtml(item.status)}">${escapeHtml(item.status)}</span>
    </div>
  `;
}

function toggleItemDetail(row) {
  const next = row.nextElementSibling;
  if (next && next.classList.contains('item-detail')) {
    next.remove();
    row.classList.remove('expanded');
    return;
  }
  const itemNum = row.dataset.itemNum;
  const item = currentItems.find((it) => String(it.item_number) === itemNum);
  const detail = document.createElement('div');
  detail.className = 'item-detail';
  detail.innerHTML = item ? renderItemDetail(item) : '(item detail unavailable)';
  row.after(detail);
  row.classList.add('expanded');
}

function renderItemDetail(item) {
  const excerpt = (item.content_text || '').slice(0, 1500);
  const truncated = (item.content_text || '').length > 1500 ? ' (truncated to 1500 chars)' : '';
  const charRange = item.char_range_text
    ? `text [${item.char_range_text[0]}, ${item.char_range_text[1]}]${item.char_range_html ? ` · html [${item.char_range_html[0]}, ${item.char_range_html[1]}]` : ''}`
    : '(no char range — alignment failed or by-reference body)';
  const ref = item.references
    ? `<div class="detail-row"><b>References</b><div class="reference">${escapeHtml(item.references.target_form || 'DEF 14A')}${item.references.expected_year ? ` · expected year ${item.references.expected_year}` : ''}</div></div>`
    : '';
  const segs = (item.segments || []).length
    ? `<div class="detail-row"><b>Segments</b>${item.segments.length} (mixed inline + by-reference)</div>`
    : '';
  return `
    ${excerpt
      ? `<div class="detail-row"><b>Content excerpt${truncated}</b><pre class="content-excerpt">${escapeHtml(excerpt)}</pre></div>`
      : `<div class="detail-row"><b>Content</b><span class="char-range">(empty — see status)</span></div>`}
    <div class="detail-row"><b>Char range</b><span class="char-range">${escapeHtml(charRange)}</span></div>
    ${ref}
    ${segs}
  `;
}

function renderFooterMeta(meta, xbrl) {
  const bits = [];
  if (xbrl) {
    if (xbrl.has_xbrl_data) {
      const ok = xbrl.item_8_status_consistent && xbrl.period_aligned;
      bits.push(`<span class="check ${ok ? 'ok' : 'warn'}">XBRL: ${xbrl.total_facts_for_accession} facts${ok ? '' : ' (inconsistencies — see warnings)'}</span>`);
    } else {
      bits.push(`<span class="check warn">XBRL: not present (pre-iXBRL or amendment)</span>`);
    }
  }
  if (meta?.parser_version) bits.push(`<span>parser ${escapeHtml(meta.parser_version)}</span>`);
  if (meta?.cost_usd != null) bits.push(`<span>cost $${meta.cost_usd.toFixed(4)}</span>`);

  const warnings = (meta?.warnings || []).concat(xbrl?.warnings || []);
  const warningsBlock = warnings.length
    ? `<div class="warnings"><b>Warnings (${warnings.length})</b><ul>${warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join('')}</ul></div>`
    : '';

  return bits.join('') + warningsBlock;
}

function showMessage(msg, kind = '') {
  els.result.hidden = false;
  els.status.className = kind;
  els.status.textContent = msg;
  els.filingMeta.innerHTML = '';
  els.itemsList.innerHTML = '';
  els.footerMeta.innerHTML = '';
}

function roman(n) {
  return ['', 'I', 'II', 'III', 'IV'][n] || String(n);
}

function escapeHtml(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
