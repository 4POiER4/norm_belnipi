let currentFile = null, isStreaming = false;
let abortController = null;
let currentConvId = null;
let autoModelEnabled = true;
let userOverrodeModel = false;
let _modelMap = {}; // id → label
let userScrolled = false;
let currentPreviewUrl = '';
let lastRetryRequest = null;
const GOST_DEFAULT_QUESTION = 'Проверка ГОСТ на листе';
const DRAWING_FILE_RE = /\.(pdf|png|jpe?g|bmp|gif|webp|tiff?)$/i;

function consumeNormacsRequest(fd, isGostCheck) {
  const requested = !!isGostCheck;
  fd.append('normacs', requested ? '1' : '0');
  return requested;
}

function getSelectedModel() {
  const el = document.getElementById('model-select');
  return (el && el.value) ? el.value : 'gemma3:4b';
}

const sendIcon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>`;
const stopIcon  = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>`;
const cpIco = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>`;
const okIco  = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;

// ── copy ─────────────────────────────────────────────────────────────────────

function htmlToReadable(html) {
  const d = document.createElement('div');
  d.innerHTML = html;
  return nodeToText(d).replace(/\n{3,}/g, '\n\n').trim();
}

function nodeToText(n) {
  if (n.nodeType === Node.TEXT_NODE) return n.textContent;
  // Новые карточки дублируют каноническую таблицу: копируем исходный отчёт,
  // а не элементы управления просмотром.
  if (n.classList?.contains('review-generated')) return '';
  const t = n.tagName ? n.tagName.toLowerCase() : '';
  const ch = () => Array.from(n.childNodes).map(nodeToText).join('');
  if (t === 'pre') {
    const c = n.querySelector('code');
    const tx = (c || n).innerText;
    const lg = (c && c.className.match(/language-(\w+)/)?.[1]) || '';
    return '\n```' + lg + '\n' + tx.trimEnd() + '\n```\n';
  }
  if (t === 'code') return '`' + n.textContent + '`';
  if (t === 'table') return tblAscii(n) + '\n';
  if (['thead','tbody','tfoot','tr','th','td'].includes(t)) return ch();
  if (t === 'h1') return '\n# '   + ch().trim() + '\n';
  if (t === 'h2') return '\n## '  + ch().trim() + '\n';
  if (t === 'h3') return '\n### ' + ch().trim() + '\n';
  if (t === 'ul') return '\n' + Array.from(n.children).map(li => '• ' + nodeToText(li).trim()).join('\n') + '\n';
  if (t === 'ol') return '\n' + Array.from(n.children).map((li, i) => (i+1) + '. ' + nodeToText(li).trim()).join('\n') + '\n';
  if (t === 'li') return ch();
  if (['p','div','blockquote','section','article'].includes(t)) return '\n' + ch().trim() + '\n';
  if (t === 'br') return '\n';
  if (t === 'hr') return '\n' + '─'.repeat(36) + '\n';
  return ch();
}

function tblAscii(el) {
  const rows = [];
  el.querySelectorAll('tr').forEach(r =>
    rows.push(Array.from(r.querySelectorAll('th,td')).map(c => c.innerText.replace(/\n/g,' ').trim()))
  );
  if (!rows.length) return '';
  const cols = Math.max(...rows.map(r => r.length));
  rows.forEach(r => { while (r.length < cols) r.push(''); });
  const ws = Array.from({length: cols}, (_, c) => Math.max(...rows.map(r => r[c].length), 3));
  const sep = '+-' + ws.map(w => '-'.repeat(w)).join('-+-') + '-+';
  const row = cs => '| ' + cs.map((c,i) => c.padEnd(ws[i])).join(' | ') + ' |';
  const ls = [sep];
  rows.forEach((r,i) => { ls.push(row(r)); if (i===0) ls.push(sep); });
  ls.push(sep);
  return '\n' + ls.join('\n') + '\n';
}

function tblPlain(el) {
  const rows = [];
  el.querySelectorAll('tr').forEach(r =>
    rows.push(Array.from(r.querySelectorAll('th,td')).map(c => c.innerText.replace(/\n+/g,' ').trim()).join('\t'))
  );
  return rows.join('\n').trim();
}

function doCopy(text, btn) {
  const iconOnly = btn.classList.contains('tcbtn');
  const ok = () => {
    btn.classList.add('ok');
    btn.innerHTML = iconOnly ? okIco : okIco + ' скопировано';
    setTimeout(() => {
      btn.classList.remove('ok');
      btn.innerHTML = iconOnly ? cpIco : cpIco + (btn.dataset.lbl || ' копировать');
    }, 2000);
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(ok).catch(() => fbCopy(text, ok));
  } else {
    fbCopy(text, ok);
  }
}

function fbCopy(text, cb) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.cssText = 'position:fixed;top:-9999px;opacity:0';
  document.body.appendChild(ta);
  ta.focus(); ta.select();
  try { document.execCommand('copy'); cb(); } catch(e) {}
  document.body.removeChild(ta);
}

function colorizeStnStatus(el) {
  el.querySelectorAll('table tr').forEach(tr => {
    if (tr.closest('.normative-table-normacs')) return;
    tr.classList.remove('row-stn-cancel', 'row-stn-warn');
    const cells = tr.querySelectorAll('td');
    if (cells.length < 5) return;
    const st = cells[4].textContent.trim().toLowerCase();
    cells[4].classList.remove('stn-ok', 'stn-cancel', 'stn-warn', 'stn-miss');
    if (st === 'актуален') {
      cells[4].classList.add('stn-ok');
    } else if (st === 'отменён' || st === 'отменен') {
      cells[4].classList.add('stn-cancel');
      tr.classList.add('row-stn-cancel');
    } else if (st.includes('не введён') || st.includes('не введен')) {
      cells[4].classList.add('stn-warn');
      tr.classList.add('row-stn-warn');
    } else if (st.includes('нет') || st.includes('ошибка')) {
      cells[4].classList.add('stn-miss');
    }
  });
}

function addCodeBtns(el) {
  el.querySelectorAll('pre').forEach(pre => {
    if (pre.parentElement.classList.contains('code-wrap')) return;
    const w = document.createElement('div');
    w.className = 'code-wrap';
    pre.parentNode.insertBefore(w, pre);
    w.appendChild(pre);
    const b = document.createElement('button');
    b.className = 'cbtn'; b.dataset.lbl = ' копировать';
    b.innerHTML = cpIco + ' копировать';
    b.onclick = () => doCopy(pre.innerText.trimEnd(), b);
    w.appendChild(b);
  });

  el.querySelectorAll('table').forEach(table => {
    if (table.closest('.normative-table-container, .normative-workspace')) return;
    if (table.parentElement.classList.contains('table-wrap')) return;
    const outer = document.createElement('div');
    outer.className = 'table-outer';
    table.parentNode.insertBefore(outer, table);
    const tools = document.createElement('div');
    tools.className = 'table-tools';
    outer.appendChild(tools);
    const w = document.createElement('div');
    w.className = 'table-wrap';
    outer.appendChild(w);
    w.appendChild(table);
    const b = document.createElement('button');
    b.className = 'tcbtn';
    b.title = 'Копировать таблицу';
    b.setAttribute('aria-label', 'Копировать таблицу');
    b.innerHTML = cpIco;
    b.onclick = () => doCopy(tblPlain(table), b);
    tools.appendChild(b);
  });
  colorizeStnStatus(el);
}

function addMsgCopy(el, html) {
  const e = el.querySelector('.mcopy'); if (e) e.remove();
  const b = document.createElement('button');
  b.className = 'mcopy'; b.dataset.lbl = ' копировать ответ';
  b.innerHTML = cpIco + ' копировать ответ';
  b.onclick = () => doCopy(htmlToReadable(html), b);
  el.appendChild(b);
}

function addReportActions(el, html) {
  const old = el.querySelector('.report-actions'); if (old) old.remove();
  // Disabled by request: no extra report download button.
}

function downloadReportPdf(text) {
  if (!window.pdfMake) {
    showToast('PDF-генератор не загрузился, обновите страницу', true);
    return;
  }
  const lines = String(text || '').split('\n');
  const content = lines.map(line => ({
    text: line.length ? line : ' ',
    margin: [0, 0, 0, 2],
  }));
  const docDefinition = {
    pageSize: 'A4',
    pageMargins: [36, 40, 36, 40],
    defaultStyle: {
      fontSize: 10.5,
      lineHeight: 1.28,
    },
    content: [
      {text: 'Отчёт БелнипиAI', style: 'title', margin: [0, 0, 0, 10]},
      ...content,
    ],
    styles: {
      title: {fontSize: 16, bold: true},
    },
  };
  window.pdfMake.createPdf(docDefinition).download('belener-gost-report.pdf');
}

function createProgress() {
  const box = document.createElement('div');
  box.className = 'progress-card';
  box.innerHTML = `
    <div class="progress-head">
      <span class="progress-stage">Подготовка</span>
      <span class="progress-value" style="display: none;"></span>
    </div>
    <div class="progress-track"><div class="progress-fill indeterminate"></div></div>
    <div class="progress-status">Ожидаю ответ сервера...</div>`;
  return box;
}

function updateProgress(box, payload) {
  if (!box) return;
  const progress = Number.isFinite(payload.progress) ? payload.progress : null;
  if (payload.stage) box.querySelector('.progress-stage').textContent = payload.stage;
  if (progress !== null) {
    const fill = box.querySelector('.progress-fill');
    fill.classList.remove('indeterminate');
    fill.style.width = `${Math.max(0, Math.min(progress, 100))}%`;
    const val = box.querySelector('.progress-value');
    val.style.display = 'inline';
    val.textContent = `${Math.max(0, Math.min(progress, 100))}%`;
  }
  if (payload.status) box.querySelector('.progress-status').textContent = payload.status;
}

function retryLastMessage() {
  if (!lastRetryRequest || isStreaming) return;
  ta.value = lastRetryRequest.question || '';
  ta.dispatchEvent(new Event('input'));
  if (lastRetryRequest.file) setFile(lastRetryRequest.file, {skipAutostart: true});
  sendMessage();
}

function confirmDialog(text, title = 'Подтверждение') {
  return new Promise(resolve => {
    const modal = document.getElementById('confirm-modal');
    const titleEl = document.getElementById('confirm-title');
    const textEl = document.getElementById('confirm-text');
    const ok = document.getElementById('confirm-ok');
    const cancel = document.getElementById('confirm-cancel');
    titleEl.textContent = title;
    textEl.textContent = text;
    modal.hidden = false;
    const finish = value => {
      modal.hidden = true;
      ok.onclick = cancel.onclick = null;
      resolve(value);
    };
    ok.onclick = () => finish(true);
    cancel.onclick = () => finish(false);
    modal.onclick = e => { if (e.target === modal) finish(false); };
  });
}

// ── markdown ─────────────────────────────────────────────────────────────────

function fixMarkdown(md) {
  return md.replace(/^```[^\n]*\n([\s\S]*?)^```/gm, (match, inner) => {
    const lines = inner.trim().split('\n');
    const isTable = lines.length >= 2
      && lines[0].trim().startsWith('|')
      && /^\|[\s|:\-]+\|$/.test(lines[1].trim());
    return isTable ? inner.trim() : match;
  });
}

function _normativeRowFillColor(tr) {
  const cls = (tr && tr.classList) ? tr.classList : null;
  if (!cls) return null;
  if (cls.contains('row-active')) return '#e8f5ec';
  if (cls.contains('row-canceled')) return '#fce9ea';
  return null;
}

function _cellBold(td) {
  return !!(td && td.querySelector && td.querySelector('strong'));
}

function _cellText(td) {
  const t = (td && td.textContent) ? td.textContent : '';
  return String(t || '').replace(/\s+/g, ' ').trim() || '—';
}

function buildNormativeTablePdfDocDefinition(workspaceEl) {
  const tableEl = workspaceEl ? workspaceEl.querySelector('.normative-table-container table') : null;
  if (!tableEl) return null;

  const metaEl = workspaceEl.querySelector('.normative-workspace-meta');
  const metaText = metaEl ? metaEl.textContent.replace(/\s+/g, ' ').trim() : '';

  const headCells = [...tableEl.querySelectorAll('thead th')].map((th) => {
    const spans = [...th.querySelectorAll('span')].map(s => s.textContent.trim()).filter(Boolean);
    if (spans.length >= 2) return spans.slice(0, 2);
    const lines = String(th.innerText || th.textContent || '')
      .split(/\n+/)
      .map(s => s.trim())
      .filter(Boolean);
    if (lines.length >= 2) return lines.slice(0, 2);
    return [(th.textContent || '—').replace(/\s+/g, ' ').trim() || '—'];
  });
  const headerRow = headCells.map(parts => ({
    text: parts.length >= 2
      ? parts.map((p, i) => ({ text: p + (i < parts.length - 1 ? '\n' : ''), bold: true }))
      : parts[0],
    bold: true,
    fontSize: 9,
    alignment: 'center',
    fillColor: '#f3f4f6',
    color: '#111827',
  }));

  const rows = [...tableEl.querySelectorAll('tbody tr')];
  const bodyRows = rows.map(tr => {
    const fill = _normativeRowFillColor(tr);
    const tds = [...tr.querySelectorAll('td')];
    return tds.map((td, colIdx) => {
      const a = td.querySelector('a.stn-link[href]');
      const isBold = _cellBold(td);
      if (a && a.href) {
        return {
          text: a.textContent.trim() || 'Открыть',
          link: a.href,
          color: '#2563eb',
          decoration: 'underline',
          bold: isBold,
          fontSize: 9,
          fillColor: fill || undefined,
        };
      }
      return {
        text: _cellText(td),
        bold: isBold,
        fontSize: 9,
        fillColor: fill || undefined,
      };
    });
  });

  const docDefinition = {
    pageSize: 'A4',
    pageOrientation: 'portrait',
    pageMargins: [18, 18, 18, 18],
    defaultStyle: {
      font: 'Roboto',
      fontSize: 9,
      lineHeight: 1.2,
    },
    content: [
      { text: 'Таблица нормативов (ГОСТ/СП/СН и др.)', style: 'title', margin: [0, 0, 0, 8] },
      ...(metaText ? [{ text: metaText, margin: [0, 0, 0, 10] }] : []),
      {
        table: {
          headerRows: 1,
          widths: headerRow.map(() => '*'),
          body: [headerRow, ...bodyRows],
        },
        layout: {
          hLineWidth: () => 0.4,
          vLineWidth: () => 0.4,
          hLineColor: () => '#e5e7eb',
          vLineColor: () => '#e5e7eb',
          paddingLeft: () => 4,
          paddingRight: () => 4,
          paddingTop: () => 3,
          paddingBottom: () => 3,
        },
      },
    ],
    styles: {
      title: { fontSize: 12, bold: true },
    },
  };
  return docDefinition;
}

function buildNormativeTablePdfPayload(workspaceEl) {
  const tableEl = workspaceEl ? workspaceEl.querySelector('.normative-table-container table') : null;
  if (!tableEl) return null;
  const metaEl = workspaceEl.querySelector('.normative-workspace-meta');
  const filenameEl = metaEl && [...metaEl.querySelectorAll('p')].find(p => /Файл:/i.test(p.textContent || ''));
  const filenameText = filenameEl ? filenameEl.textContent.replace(/^.*Файл:\s*/i, '').trim() : 'belener-gost-table';
  const headers = [...tableEl.querySelectorAll('thead th')].map((th) => {
    const spans = [...th.querySelectorAll('span')].map(s => s.textContent.trim()).filter(Boolean);
    if (spans.length >= 2) return spans.slice(0, 2).join('\n');
    // innerText сохраняет перевод строки от <br>
    const lines = String(th.innerText || th.textContent || '')
      .split(/\n+/)
      .map(s => s.trim())
      .filter(Boolean);
    if (lines.length >= 2) return lines.slice(0, 2).join('\n');
    return (th.textContent || '—').replace(/\s+/g, ' ').trim() || '—';
  });
  const rows = [...tableEl.querySelectorAll('tbody tr')].map(tr => ({
    fill: tr.classList.contains('row-active')
      ? 'active'
      : tr.classList.contains('row-canceled')
        ? 'canceled'
        : '',
    cells: [...tr.querySelectorAll('td')].map(td => {
      const a = td.querySelector('a.stn-link[href]');
      return {
        text: a ? (a.textContent.trim() || _cellText(td)) : _cellText(td),
        href: a ? a.href : '',
        bold: _cellBold(td),
      };
    }),
  }));
  let meta = metaEl
    ? [...metaEl.querySelectorAll('p')].map(p => p.textContent.replace(/\s+/g, ' ').trim()).filter(Boolean)
    : [];
  const pagesTotal = (() => {
    const line = meta.find(l => /^Листов в файле:\s*(\d+)/i.test(l));
    if (line) return line.match(/^Листов в файле:\s*(\d+)/i)[1];
    const previewPages = workspaceEl.querySelectorAll('.normative-preview-page').length;
    if (previewPages > 1) return String(previewPages);
    return '';
  })();
  meta = meta.filter(l => !/^Лист:/i.test(l) && !/^Листов:/i.test(l));
  if (pagesTotal) {
    meta.push(`Листов: ${pagesTotal}`);
  } else {
    meta.push('Листов: 1');
  }
  const listEl = workspaceEl.querySelector('.normative-workspace-list');
  const summaryEl =
    workspaceEl.querySelector('.normative-table-summary')
    || (listEl && [...listEl.querySelectorAll('p')].find(p => /Всего в документе:/i.test(p.textContent || '')));
  let summary = summaryEl ? summaryEl.innerText.replace(/\r\n/g, '\n').trim() : '';
  if (summary) {
    summary = summary.split('\n').map(l => l.replace(/\s+/g, ' ').trim()).filter(Boolean).join('\n');
  }
  if (!summary) {
    const m = (workspaceEl.innerText || '').match(
      /Всего в документе:\s*\d+;\s*найдено в Стройдок:\s*\d+;(?:\s*найдено в ТНПА:\s*\d+;)?\s*актуально:\s*\d+/i
    );
    if (m) summary = m[0].replace(/\s+/g, ' ').trim();
  }
  if (!summary && rows.length && headers.some(h => /normacs/i.test(h)) && !headers.some(h => /стройдок/i.test(h))) {
    const ncLinkIndex = headers.findIndex(h => /^normacs$/i.test(h));
    const ncStatusIndex = headers.findIndex(h => /статус.*normacs/i.test(h));
    const found = rows.filter(r => r.cells[ncLinkIndex]?.href).length;
    const active = rows.filter(r => /^действует$/i.test(r.cells[ncStatusIndex]?.text || '')).length;
    summary = `Всего в документе: ${rows.length}; найдено в NormaCS: ${found};\nдействует: ${active}`;
  }
  if (!summary && rows.length) {
    const foundStn = rows.filter(r => r.cells[1] && r.cells[1].href).length;
    const active = rows.filter(r => r.fill === 'active').length;
    const hasTnpa = headers.some(h => /тнпа/i.test(h));
    if (hasTnpa) {
      const foundTnpa = rows.filter(r => r.cells[2] && r.cells[2].href).length;
      summary = `Всего в документе: ${rows.length}; найдено в Стройдок: ${foundStn}; найдено в ТНПА: ${foundTnpa};\nактуально: ${active}`;
    } else {
      summary = `Всего в документе: ${rows.length}; найдено в Стройдок: ${foundStn};\nактуально: ${active}`;
    }
  }
  summary = summary.replace(/ИПС/gi, 'Стройдок');
  const hasTnpaCol = headers.some(h => /тнпа/i.test(h));
  const hasNormacsCol = headers.some(h => /normacs/i.test(h));
  return {
    title: 'Таблица нормативов (ГОСТ/СП/СН и др.)',
    filename: `${filenameText.replace(/\.[^.]+$/, '')}-normatives.pdf`,
    meta,
    summary,
    headers,
    rows,
    widths: hasNormacsCol ? headers.map(() => 30) : hasTnpaCol ? [70, 24, 22, 40, 40] : [80, 30, 45, 45],
  };
}

async function downloadNormativeTablePdf(btn) {
  const workspaceEl = btn && btn.closest('.normative-workspace');
  if (!workspaceEl || btn.disabled) return;
  try {
    const payload = buildNormativeTablePdfPayload(workspaceEl);
    if (!payload) {
      showToast('PDF: таблица не найдена', true);
      return;
    }
    btn.disabled = true;
    btn.textContent = 'Готовлю PDF...';
    const resp = await fetch('/api/export-normative-pdf', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = payload.filename || 'belener-gost-table.pdf';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    showToast('PDF скачан');
  } catch (e) {
    console.error(e);
    showToast('PDF: ошибка экспорта', true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Скачать таблицу в PDF';
  }
}
// Нужно для inline onclick — он переживает сериализацию через innerHTML.
window.downloadNormativeTablePdf = downloadNormativeTablePdf;

function ensureNormativeTablePdfDownloads(root) {
  if (!root) return;

  for (const workspaceEl of root.querySelectorAll('.normative-workspace')) {
    let btn = workspaceEl.querySelector('.normative-table-pdf-btn');
    if (!btn) {
      const tableEl = workspaceEl.querySelector('.normative-table-container table');
      if (!tableEl) continue;

      btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn btn-primary btn-sm normative-table-pdf-btn';
      btn.style.marginTop = '10px';
      btn.textContent = 'Скачать таблицу в PDF';

      const container = workspaceEl.querySelector('.normative-table-container');
      if (container && container.parentNode) {
        container.parentNode.insertBefore(btn, container.nextSibling);
      } else {
        workspaceEl.appendChild(btn);
      }
    }
    // Inline-атрибут сохраняется при return box.innerHTML → ac.innerHTML.
    btn.setAttribute('onclick', 'downloadNormativeTablePdf(this); return false;');
  }
}

/** marked не парсит ** внутри HTML — починка уже отрисованного ответа (текущий чат). */
function unwrapNormativeTableCopy(root) {
  if (!root) return;
  root.querySelectorAll('.normative-table-container').forEach(container => {
    const outer = container.querySelector(':scope > .table-outer');
    if (!outer) return;
    const table = outer.querySelector('table');
    if (table) container.appendChild(table);
    outer.remove();
  });
}

const NORMATIVE_TABLE_COL_WIDTHS = [280, 120, 100, 218, 218];
const NORMATIVE_ROW_BG = {
  'row-active': '#e8f5ec',
  'row-canceled': '#fce9ea',
};

function applyNormativeTableLayout(root) {
  if (!root) return;
  root.querySelectorAll('.normative-table-container table').forEach(table => {
    const normacs = table.classList.contains('normative-table-normacs');
    const count = table.querySelectorAll('thead th').length;
    const widths = normacs
      ? [210, ...Array(Math.max(0, count - 2)).fill(140), 280]
      : NORMATIVE_TABLE_COL_WIDTHS;
    const tableWidth = `${widths.reduce((sum, width) => sum + width, 0)}px`;
    table.style.width = tableWidth;
    table.style.minWidth = tableWidth;
    table.style.maxWidth = tableWidth;
    table.style.tableLayout = 'fixed';
    let cg = table.querySelector('colgroup');
    if (!cg) {
      cg = document.createElement('colgroup');
      const head = table.querySelector('thead');
      table.insertBefore(cg, head || table.firstChild);
    }
    cg.replaceChildren(...widths.map(w => {
      const col = document.createElement('col');
      col.style.width = `${w}px`;
      return col;
    }));
  });
}

/** Цвета строк как в PDF (#e8f5ec / #fce9ea / #fff8e6) — inline, чтобы перебить generic .msg-content table. */
function applyNormativeRowColors(root) {
  if (!root) return;
  root.querySelectorAll('.normative-table-container').forEach(container => {
    container.style.backgroundColor = '#ffffff';
    container.querySelectorAll('thead th').forEach(th => {
      th.style.setProperty('background-color', '#0f766e', 'important');
      th.style.setProperty('color', '#ffffff', 'important');
    });
    container.querySelectorAll('tbody tr').forEach(tr => {
      let bg = '#ffffff';
      if (tr.classList.contains('row-active')) bg = NORMATIVE_ROW_BG['row-active'];
      else if (tr.classList.contains('row-canceled')) bg = NORMATIVE_ROW_BG['row-canceled'];
      tr.querySelectorAll('td').forEach(td => {
        let cellBg = bg;
        if (td.classList.contains('normacs-status')) {
          cellBg = td.classList.contains('normacs-status-1') ? '#e8f5ec'
            : td.classList.contains('normacs-status-0') ? '#fce9ea'
            : td.classList.contains('normacs-status-2') || td.classList.contains('normacs-status-4')
              ? '#fff4d6' : '#ffffff';
        }
        td.style.setProperty('background-color', cellBg, 'important');
      });
    });
  });
}

function beautifyNormativeHtml(root) {
  if (!root) return;
  unwrapNormativeTableCopy(root);
  applyNormativeTableLayout(root);
  applyNormativeRowColors(root);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const t = node.nodeValue;
    if (!t || t.indexOf('**') < 0 && t.indexOf('*') < 0) continue;
    if (!node.parentElement || node.parentElement.closest('code, pre, a, script')) continue;
    let html = t
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
    if (html === t) continue;
    const wrap = document.createElement('span');
    wrap.innerHTML = html;
    node.parentNode.replaceChild(wrap, node);
  }
  root.querySelectorAll('.normative-preview-shell').forEach(shell => {
    const group = shell.dataset.previewGroup;
    if (!group) return;
    const pages = [...shell.querySelectorAll(`.normative-preview-page[data-group="${group}"]`)];
    const active = pages.find(p => p.classList.contains('is-active')) || pages[0];
    const label = root.querySelector(`.preview-page-label[data-group="${group}"]`);
    if (active && label) {
      const idx = pages.indexOf(active) + 1;
      label.textContent = `${idx} / ${pages.length} · лист ${active.dataset.page}`;
    }
  });

  root.querySelectorAll('.normative-workspace').forEach(workspace => {
    workspace.querySelectorAll('.normative-table-container tbody td:nth-child(2) a.stn-link').forEach(a => {
      if (!a.textContent.trim()) a.textContent = 'Стройдок';
    });
    workspace.querySelectorAll('.normative-table-summary').forEach(el => {
      el.innerHTML = (el.innerHTML || '').replace(/ИПС/gi, 'Стройдок');
    });
    if (typeof initReviewWorkspace === 'function') initReviewWorkspace(workspace);
  });

  // renderAssistantMarkdown сериализует промежуточный DOM. Обработчики и
  // ResizeObserver привязываем только к живым узлам, не к его временной копии.
  if (root.isConnected) {
  root.querySelectorAll('.pdf-preview-container').forEach(box => {
    ensurePreviewStage(box);
    bindPreviewPan(box);
    box.scrollTop = 0;
    box.scrollLeft = 0;
    bindPreviewResizeObserver(box);
  });
  root.querySelectorAll('.pdf-preview-img').forEach(img => {
    if (!img.dataset.fitBound) {
      img.dataset.fitBound = '1';
      img.draggable = false;
      img.addEventListener('dragstart', (e) => e.preventDefault());
      img.addEventListener('load', () => {
        delete img.dataset.fitKey;
        fitPreviewImage(img);
      });
      img.addEventListener('error', () => {
        const box = img.closest('.pdf-preview-container');
        if (!box || box.dataset.previewMissing === '1') return;
        box.dataset.previewMissing = '1';
        box.innerHTML =
          '<p class="preview-missing-note">Превью недоступно (файл устарел или удалён). Запустите проверку снова.</p>';
      });
    }
    fitPreviewImage(img);
  });
  }

  ensureNormativeTablePdfDownloads(root);
}

function ensurePreviewStage(box) {
  if (!box) return null;
  box.querySelector('.pdf-preview-scrollpad')?.remove();
  box.querySelector('.pdf-preview-inner')?.remove();
  let stage = box.querySelector('.pdf-preview-stage');
  const img = box.querySelector('.pdf-preview-img');
  if (!img) return null;
  if (!stage) {
    stage = document.createElement('div');
    stage.className = 'pdf-preview-stage';
    img.replaceWith(stage);
    stage.appendChild(img);
  }
  return stage;
}

function _previewAvailSize(box) {
  return {
    w: Math.max(box.clientWidth, 1),
    h: Math.max(box.clientHeight, 1),
  };
}

function _previewBaseSize(img, box) {
  const { w: availW, h: availH } = _previewAvailSize(box);
  const fit = Math.min(availW / img.naturalWidth, availH / img.naturalHeight);
  return {
    w: img.naturalWidth * fit,
    h: img.naturalHeight * fit,
  };
}

function _capturePreviewZoomAnchor(img, box, anchor) {
  if (!anchor || !box || !img) return;
  const rect = box.getBoundingClientRect();
  const viewX = anchor.clientX - rect.left;
  const viewY = anchor.clientY - rect.top;
  img.dataset.zoomViewX = String(viewX);
  img.dataset.zoomViewY = String(viewY);
  const oldScale = Number(img.dataset.scale || '1');
  if (oldScale <= 1.01) {
    const imgRect = img.getBoundingClientRect();
    if (imgRect.width > 0 && imgRect.height > 0) {
      const fracX = (anchor.clientX - imgRect.left) / imgRect.width;
      const fracY = (anchor.clientY - imgRect.top) / imgRect.height;
      img.dataset.zoomFracX = String(Math.min(1, Math.max(0, fracX)));
      img.dataset.zoomFracY = String(Math.min(1, Math.max(0, fracY)));
    }
    return;
  }
  const base = _previewBaseSize(img, box);
  const oldDisplayW = base.w * oldScale;
  const oldDisplayH = base.h * oldScale;
  if (oldDisplayW > 0 && oldDisplayH > 0) {
    const fracX = (box.scrollLeft + viewX) / oldDisplayW;
    const fracY = (box.scrollTop + viewY) / oldDisplayH;
    img.dataset.zoomFracX = String(Math.min(1, Math.max(0, fracX)));
    img.dataset.zoomFracY = String(Math.min(1, Math.max(0, fracY)));
  }
}

function _applyPreviewZoomScroll(img, box, scale) {
  if (!img || !box || !img.dataset.zoomFracX) return;
  const fracX = Number(img.dataset.zoomFracX);
  const fracY = Number(img.dataset.zoomFracY);
  const viewX = Number(img.dataset.zoomViewX || 0);
  const viewY = Number(img.dataset.zoomViewY || 0);
  if (!Number.isFinite(fracX) || !Number.isFinite(fracY)) return;
  const base = _previewBaseSize(img, box);
  const newDisplayW = base.w * scale;
  const newDisplayH = base.h * scale;
  const maxScrollL = Math.max(0, box.scrollWidth - box.clientWidth);
  const maxScrollT = Math.max(0, box.scrollHeight - box.clientHeight);
  box.scrollLeft = Math.min(maxScrollL, Math.max(0, fracX * newDisplayW - viewX));
  box.scrollTop = Math.min(maxScrollT, Math.max(0, fracY * newDisplayH - viewY));
  delete img.dataset.zoomFracX;
  delete img.dataset.zoomFracY;
  delete img.dataset.zoomViewX;
  delete img.dataset.zoomViewY;
}

function _previewZoomAnchorFromBox(box) {
  if (!box) return null;
  const rect = box.getBoundingClientRect();
  return { clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 };
}

function setPreviewZoom(img, nextScale, anchor) {
  if (!img) return;
  const box = img.closest('.pdf-preview-container');
  const scale = Math.min(4, Math.max(1, Number(nextScale) || 1));
  if (anchor && scale > 1.01) {
    _capturePreviewZoomAnchor(img, box, anchor);
  } else {
    delete img.dataset.zoomFracX;
    delete img.dataset.zoomFracY;
    delete img.dataset.zoomViewX;
    delete img.dataset.zoomViewY;
  }
  img.dataset.scale = String(Math.round(scale * 1000) / 1000);
  delete img.dataset.fitKey;
  if (box) box.dataset.previewZooming = '1';
  fitPreviewImage(img);
  if (box && scale > 1.01 && img.dataset.zoomFracX) {
    void box.offsetHeight;
    _applyPreviewZoomScroll(img, box, scale);
  } else if (box && scale <= 1.01) {
    box.scrollTop = 0;
    box.scrollLeft = 0;
  }
  if (box) delete box.dataset.previewZooming;
}

function fitPreviewImage(img) {
  if (!img) return;
  const previewPage = img.closest('.normative-preview-page');
  if (!img.isConnected || (previewPage && previewPage.hidden)) return;
  const box = img.closest('.pdf-preview-container');
  if (!box) return;
  const stage = ensurePreviewStage(box);
  if (!stage) return;

  const userScale = Math.max(0.5, Number(img.dataset.scale || '1') || 1);
  const resetButton = previewPage?.querySelector('.preview-zoom-btn[data-action="reset"]');
  if (resetButton) {
    resetButton.textContent = `${Math.round(userScale * 100)}%`;
    resetButton.setAttribute('aria-label', 'Вписать весь лист');
  }

  if (!img.complete || !img.naturalWidth || box.clientWidth < 40 || box.clientHeight < 40) {
    if (!img.dataset.fitRetry) {
      img.dataset.fitRetry = '1';
      requestAnimationFrame(() => {
        img.dataset.fitRetry = '';
        fitPreviewImage(img);
      });
    }
    return;
  }

  const fitKey = `${box.clientWidth}x${box.clientHeight}x${userScale}`;
  if (img.dataset.fitKey === fitKey) return;
  img.dataset.fitKey = fitKey;

  const base = _previewBaseSize(img, box);
  img.dataset.baseW = String(base.w);
  img.dataset.baseH = String(base.h);

  img.draggable = false;
  img.style.margin = '0';
  img.style.objectFit = 'contain';
  img.style.position = 'static';
  img.style.maxWidth = 'none';
  img.style.maxHeight = 'none';
  img.style.willChange = userScale > 1.01 ? 'transform' : '';

  if (userScale <= 1.01) {
    stage.style.position = 'absolute';
    stage.style.inset = '0';
    stage.style.width = '100%';
    stage.style.height = '100%';
    stage.style.minWidth = '0';
    stage.style.minHeight = '0';
    stage.style.margin = '0';
    stage.style.transform = 'none';
    stage.style.display = 'flex';
    stage.style.alignItems = 'center';
    stage.style.justifyContent = 'center';
    img.style.width = 'auto';
    img.style.height = 'auto';
    img.style.maxWidth = '100%';
    img.style.maxHeight = '100%';
    img.style.transform = 'none';
    img.style.transformOrigin = '';
    img.style.cursor = 'default';
    box.style.overflow = 'hidden';
    box.scrollTop = 0;
    box.scrollLeft = 0;
    if (typeof updateReviewOverlay === 'function') updateReviewOverlay(img);
    return;
  }

  stage.style.position = 'relative';
  stage.style.inset = 'auto';
  stage.style.left = '';
  stage.style.top = '';
  stage.style.margin = '0';
  stage.style.transform = 'none';

  const displayW = base.w * userScale;
  const displayH = base.h * userScale;
  const { w: availW, h: availH } = _previewAvailSize(box);

  stage.style.display = 'block';
  stage.style.alignItems = '';
  stage.style.justifyContent = '';
  stage.style.width = `${Math.max(displayW, availW)}px`;
  stage.style.height = `${Math.max(displayH, availH)}px`;
  stage.style.minWidth = `${Math.max(displayW, availW)}px`;
  stage.style.minHeight = `${Math.max(displayH, availH)}px`;
  img.style.width = `${base.w}px`;
  img.style.height = `${base.h}px`;
  img.style.transform = `scale(${userScale})`;
  img.style.transformOrigin = '0 0';
  img.style.cursor = 'grab';
  box.style.overflow = 'auto';
  if (typeof updateReviewOverlay === 'function') updateReviewOverlay(img);
}

const _previewPanState = new WeakMap();
const _previewWheelPending = new WeakMap();
let _previewPanBox = null;

function queuePreviewWheelZoom(img, delta, anchor) {
  let pending = _previewWheelPending.get(img);
  if (!pending) {
    pending = {
      scale: Number(img.dataset.scale || '1'),
      anchor: { clientX: anchor.clientX, clientY: anchor.clientY },
      rafId: 0,
    };
    _previewWheelPending.set(img, pending);
  } else {
    pending.anchor.clientX = anchor.clientX;
    pending.anchor.clientY = anchor.clientY;
  }
  const intensity = Math.min(2.5, Math.max(0.35, Math.abs(delta) / 120));
  const factor = delta < 0 ? (1 + 0.045 * intensity) : (1 / (1 + 0.045 * intensity));
  pending.scale = Math.min(4, Math.max(0.5, pending.scale * factor));
  if (pending.rafId) return;
  pending.rafId = requestAnimationFrame(() => {
    const p = _previewWheelPending.get(img);
    if (!p) return;
    p.rafId = 0;
    _previewWheelPending.delete(img);
    setPreviewZoom(img, p.scale, p.anchor);
  });
}

function initPreviewPanGlobal() {
  if (window._previewPanGlobalInit) return;
  window._previewPanGlobalInit = true;
  window.addEventListener('mousemove', (e) => {
    const box = _previewPanBox;
    if (!box) return;
    const state = _previewPanState.get(box);
    if (!state || !state.active) return;
    const dx = e.clientX - state.startX;
    const dy = e.clientY - state.startY;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) state.moved = true;
    box.scrollLeft = state.scrollL - dx;
    box.scrollTop = state.scrollT - dy;
  });
  const endPan = () => {
    const box = _previewPanBox;
    if (!box) return;
    const state = _previewPanState.get(box);
    if (!state || !state.active) return;
    const img = box.querySelector('.pdf-preview-img');
    if (state.moved && img) img.dataset.panMoved = '1';
    state.active = false;
    box.classList.remove('is-panning');
    delete box.dataset.previewPanning;
    if (img) img.style.cursor = Number(img.dataset.scale || '1') > 1.01 ? 'grab' : 'default';
    _previewPanBox = null;
  };
  window.addEventListener('mouseup', endPan);
  window.addEventListener('blur', endPan);
}

function initPreviewWheelGlobal() {
  if (window._previewWheelInit) return;
  window._previewWheelInit = true;
  document.addEventListener('wheel', (e) => {
    const previewRoot = e.target.closest('.pdf-preview-container, .normative-workspace-preview');
    if (!previewRoot) return;
    const box = previewRoot.classList.contains('pdf-preview-container')
      ? previewRoot
      : previewRoot.querySelector('.pdf-preview-container');
    if (!box) return;
    const img = box.querySelector('.pdf-preview-img');
    if (!img) return;
    e.preventDefault();
    e.stopPropagation();
    const delta = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 100 : 1);
    if (Math.abs(delta) < 1) return;
    queuePreviewWheelZoom(img, delta, { clientX: e.clientX, clientY: e.clientY });
  }, { passive: false, capture: true });
}

function bindPreviewPan(box) {
  if (!box || box.dataset.panBound) return;
  box.dataset.panBound = '1';
  initPreviewPanGlobal();
  initPreviewWheelGlobal();
  box.addEventListener('dragstart', (e) => e.preventDefault());
  box.addEventListener('mousedown', (e) => {
    if (e.button !== 0 || e.target.closest('.preview-zoom-btn, a, button')) return;
    const img = box.querySelector('.pdf-preview-img');
    if (!img) return;
    const scale = Number(img.dataset.scale || '1');
    if (scale <= 1.01) return;
    box.dataset.previewPanning = '1';
    _previewPanState.set(box, {
      active: true,
      moved: false,
      startX: e.clientX,
      startY: e.clientY,
      scrollL: box.scrollLeft,
      scrollT: box.scrollTop,
    });
    _previewPanBox = box;
    box.classList.add('is-panning');
    img.style.cursor = 'grabbing';
    e.preventDefault();
  });
  box.addEventListener('dblclick', (e) => {
    if (e.target.closest('.preview-zoom-btn, a, button')) return;
    const img = box.querySelector('.pdf-preview-img');
    if (!img) return;
    const current = Number(img.dataset.scale || '1');
    setPreviewZoom(img, current <= 1.01 ? 2 : 1, { clientX: e.clientX, clientY: e.clientY });
    e.preventDefault();
  });
}

const _previewResizeObservers = new WeakMap();
const _previewResizeTimers = new WeakMap();

function bindPreviewResizeObserver(box) {
  if (!box || _previewResizeObservers.has(box)) return;
  const img = box.querySelector('.pdf-preview-img');
  if (!img) return;
  const ro = new ResizeObserver(() => {
    if (box.dataset.previewPanning || box.dataset.previewZooming) return;
    clearTimeout(_previewResizeTimers.get(box));
    _previewResizeTimers.set(box, setTimeout(() => {
      delete img.dataset.fitKey;
      fitPreviewImage(img);
    }, 120));
  });
  ro.observe(box);
  _previewResizeObservers.set(box, ro);
}

function renderAssistantMarkdown(md) {
  // marked ломает вложенный HTML workspace (кнопки зума → «-100%+», таблица в ASCII).
  const src = String(md || '');
  const marker = '<div class="normative-workspace">';
  const parts = [];
  let cursor = 0;
  while (true) {
    const idx = src.indexOf(marker, cursor);
    if (idx < 0) {
      const tail = src.slice(cursor);
      if (tail.trim()) parts.push({ html: false, text: tail });
      break;
    }
    const head = src.slice(cursor, idx);
    if (head.trim()) parts.push({ html: false, text: head });
    // Закрывающий корень workspace — три </div> в конце блока.
    let end = idx + marker.length;
    let depth = 1;
    while (end < src.length && depth > 0) {
      const nextOpen = src.indexOf('<div', end);
      const nextClose = src.indexOf('</div>', end);
      if (nextClose < 0) { end = src.length; break; }
      if (nextOpen >= 0 && nextOpen < nextClose) {
        depth += 1;
        end = nextOpen + 4;
      } else {
        depth -= 1;
        end = nextClose + 6;
      }
    }
    parts.push({ html: true, text: src.slice(idx, end) });
    cursor = end;
  }
  let html = '';
  for (const p of parts) {
    html += p.html ? p.text : marked.parse(fixMarkdown(p.text));
  }
  if (!parts.length) html = marked.parse(fixMarkdown(src));
  const box = document.createElement('div');
  box.innerHTML = html;
  beautifyNormativeHtml(box);
  return box.innerHTML;
}

// ── conversations ─────────────────────────────────────────────────────────────

async function loadConversations() {
  try {
    const data = await fetch('/api/conversations').then(r => r.json());
    renderConvList(data.conversations || []);
  } catch(e) {
    console.error(e);
  }
}

function renderConvList(convs) {
  const list = document.getElementById('chats-list');
  if (!convs.length) {
    list.innerHTML = '<div class="chats-empty">Нет чатов</div>';
    return;
  }
  list.innerHTML = convs.map(c => `
    <div class="chat-item ${c.id === currentConvId ? 'active' : ''}" data-id="${c.id}" onclick="openChat('${c.id}')">
      <div class="chat-item-title">${esc(c.title)}</div>
      <button class="chat-item-del" onclick="deleteChat(event,'${c.id}')" title="Удалить">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
  `).join('');
}

async function newChat() {
  const model = getSelectedModel();
  const data = await fetch('/api/conversations', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({model})
  }).then(r => r.json());

  currentConvId = data.id;
  clearFeed();
  await loadConversations();
  highlightActiveChat();
}

async function openChat(id) {
  currentConvId = id;
  clearFeed();
  highlightActiveChat();

  const data = await fetch(`/api/conversations/${id}/messages`).then(r => r.json());
  const msgs = data.messages || [];
  if (!msgs.length) return;

  hideEmpty();
  for (const m of msgs) {
    const ac = addMessage(m.role, m.role === 'user' ? m.content : '', m.file_name || null, m.file_url || null);
    if (m.role === 'assistant') {
      ac.innerHTML = renderAssistantMarkdown(m.content);
      beautifyNormativeHtml(ac);
      addCodeBtns(ac);
      addMsgCopy(ac, ac.innerHTML);
      addReportActions(ac, ac.innerHTML);
    }
  }
  userScrolled = false;
  scrollEnd(true);
  // Незавершённая серверная проверка восстанавливается без повторной загрузки PDF.
  const jobData = {job: data.job};
  if (currentConvId !== id || !jobData.job || jobData.job.state !== 'running') return;
  if (msgs.some(m => m.role === 'assistant'
    && Date.parse(m.created_at) >= jobData.job.started_at * 1000 - 1000)) return;
  const ac = addMessage('assistant', '');
  const progress = createProgress();
  ac.appendChild(progress);
  try {
    const report = await waitForNormativeJob(id, progress, null, jobData.job.id);
    if (report !== null && currentConvId === id) {
      ac.innerHTML = renderAssistantMarkdown(report);
      beautifyNormativeHtml(ac);
      addCodeBtns(ac); addMsgCopy(ac, ac.innerHTML); addReportActions(ac, ac.innerHTML);
      await loadConversations();
    }
  } catch (error) {
    if (currentConvId === id) showInlineError(ac, error.message);
  }
}

function createSseDecoder(onData) {
  const decoder = new TextDecoder();
  let buffer = '';
  let finished = false;
  return {
    feed(bytes, final = false) {
      buffer += decoder.decode(bytes, {stream: !final});
      let newline;
      while (!finished && (newline = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newline).replace(/\r$/, '');
        buffer = buffer.slice(newline + 1);
        if (!line.startsWith('data: ')) continue;
        const value = line.slice(6);
        if (value === '[DONE]') { finished = true; break; }
        onData(JSON.parse(value));
      }
      return finished;
    },
  };
}

async function readSseResponse(response, onData) {
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `Сервер вернул HTTP ${response.status}`);
  }
  if (!response.body) throw new Error('Сервер не вернул поток ответа');
  const reader = response.body.getReader();
  const decoder = createSseDecoder(onData);
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (decoder.feed(value, done)) return;
      if (done) throw new Error('Соединение прервалось до завершения проверки');
    }
  } finally {
    reader.releaseLock();
  }
}

function abortableDelay(ms, signal) {
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', abort);
      reject(new DOMException('Получение остановлено', 'AbortError'));
    };
    const timer = setTimeout(() => {
      if (signal) signal.removeEventListener('abort', abort);
      resolve();
    }, ms);
    if (signal) {
      if (signal.aborted) abort();
      else signal.addEventListener('abort', abort, {once: true});
    }
  });
}

async function waitForNormativeJob(convId, progress, signal, expectedId = null) {
  let failures = 0;
  while (currentConvId === convId) {
    let data;
    try {
      const response = await fetch(`/api/conversations/${convId}/job`, {signal, cache: 'no-store'});
      if (!response.ok) throw new Error('Не удалось получить состояние проверки');
      data = await response.json();
      failures = 0;
    } catch (error) {
      if (error.name === 'AbortError') throw error;
      updateProgress(progress, {status: 'Связь потеряна. Восстанавливаю получение статуса; PDF повторно не загружается…'});
      if (++failures >= 20) throw new Error('Сервер недоступен. После восстановления связи открой этот чат: готовый отчёт сохраняется в истории.');
      await abortableDelay(3000, signal);
      continue;
    }
    const job = data.job;
    if (!job) throw new Error('Активная проверка не найдена. Проверь историю чата; при перезапуске сервера незавершённая задача прерывается.');
    if (expectedId && job.id !== expectedId) return null;
    if (job.state === 'running') {
      updateProgress(progress, {status: `Проверка продолжается на сервере. ${job.status || ''}`});
      await abortableDelay(3000, signal);
      continue;
    }
    const response = await fetch(`/api/conversations/${convId}/messages`, {signal, cache: 'no-store'});
    if (!response.ok) throw new Error('Не удалось загрузить сохранённый отчёт');
    const messages = (await response.json()).messages || [];
    const result = messages.filter(m => m.role === 'assistant'
      && Date.parse(m.created_at) >= job.started_at * 1000 - 1000).pop();
    if (result) return result.content;
    throw new Error(job.error || 'Проверка завершилась без сохранённого отчёта');
  }
  return null;
}

async function deleteChat(e, id) {
  e.stopPropagation();
  const confirmed = await confirmDialog('Удалить этот чат? Это действие нельзя отменить.', 'Удаление чата');
  if (!confirmed) return;
  await fetch(`/api/conversations/${id}`, {method: 'DELETE'});
  if (currentConvId === id) {
    currentConvId = null;
    clearFeed();
    document.getElementById('empty-state').style.display = '';
  }
  loadConversations();
}

function highlightActiveChat() {
  document.querySelectorAll('.chat-item').forEach(el => {
    el.classList.toggle('active', el.dataset.id === currentConvId);
  });
}

function clearFeed() {
  const inner = document.getElementById('chat-inner');
  inner.innerHTML = '';
  const welcome = document.createElement('div');
  welcome.className = 'welcome';
  welcome.id = 'empty-state';
  welcome.innerHTML = `
    <div class="welcome-hero">
      <div class="welcome-badge">Локальная обработка · данные не уходят в облако</div>
      <h1 class="welcome-title">Проверка ГОСТ<br>на чертеже</h1>
      <p class="welcome-sub">Загрузите PDF или скан — система найдёт нормативы на всех листах и проверит их статус в NormaCS (РФ). Стройдок временно отключён.</p>
    </div>
    <div class="steps">
      <div class="step-card"><div class="step-num">1</div><div class="step-body"><div class="step-title">Загрузите файл</div><div class="step-desc">PDF, скан или изображение листа</div></div></div>
      <div class="step-card"><div class="step-num">2</div><div class="step-body"><div class="step-title">OCR находит нормативы</div><div class="step-desc">Поиск по всему листу, без подстановок</div></div></div>
      <div class="step-card"><div class="step-num">3</div><div class="step-body"><div class="step-title">Проверка актуальности</div><div class="step-desc">NormaCS · РФ</div></div></div>
    </div>
    <div class="welcome-actions">
      <button type="button" class="action-card action-card-primary" onclick="document.getElementById('file-input').click()">
        <span class="action-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg></span>
        <span class="action-text"><strong>Загрузить чертёж</strong><small>PDF или скан листа (PNG, JPG…)</small></span>
      </button>
      <button type="button" class="action-card" onclick="setPrompt('Проверка ГОСТ на листе')">
        <span class="action-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg></span>
        <span class="action-text"><strong>Все ГОСТ на листе</strong><small>Быстрый запрос без ввода текста</small></span>
      </button>
    </div>`;
  inner.appendChild(welcome);
}

// ── models ────────────────────────────────────────────────────────────────────

async function loadModels() {
  // Model selector is intentionally hidden in UI now.
  if (!document.getElementById('model-select')) return;
  try {
    const d = await fetch('/api/models').then(r => r.json());
    const models = d.models && d.models.length ? d.models : [{id: 'gemma3:4b', label: 'Базовая'}];
    _modelMap = {};
    models.forEach(m => {
      const id = typeof m === 'string' ? m : m.id;
      const label = typeof m === 'string' ? m : m.label;
      _modelMap[id] = label;
    });
    renderModelDrop(models);
    if (models.length) {
      const first = typeof models[0] === 'string' ? models[0] : models[0].id;
      selectModel(first);
    }
  } catch {
    const statusText = document.getElementById('stat-status');
    const statusDot = document.getElementById('status-dot');
    if (statusText) statusText.textContent = 'Нет соединения';
    if (statusDot) statusDot.classList.add('off');
  }
}

async function loadSystemStatus() {
  try {
    const data = await fetch('/api/status').then(r => r.json());
    const banner = document.getElementById('system-banner');
    const modelSection = document.getElementById('model-section');
    if (data.gost_only && modelSection) modelSection.hidden = true;

    const stn = data.stn || {};
    const normacs = data.normacs || {};
    const dateField = document.getElementById('stn-check-date-field');
    if (dateField) dateField.hidden = !stn.enabled;
    const modeBadge = document.getElementById('normacs-mode-badge');
    if (modeBadge) {
      const ready = normacs.enabled && normacs.configured;
      modeBadge.textContent = ready ? 'Проверка: NormaCS · РФ' : 'NormaCS не настроен · проверьте Интеграции';
      modeBadge.classList.toggle('warn', !ready);
    }
    if (!banner) return;
    if (!stn.enabled) {
      banner.hidden = false;
      banner.className = 'system-banner warn';
      banner.textContent = normacs.enabled && normacs.configured
        ? 'Проверка Стройдока отключена. По умолчанию проверяется только NormaCS.'
        : 'Стройдок отключён, а NormaCS не настроен. Укажите логин и пароль в Интеграциях.';
    } else if (!stn.configured) {
      banner.hidden = false;
      banner.className = 'system-banner warn';
      banner.textContent = 'STN включён, но логин и пароль не настроены (админка → Интеграции или .env). Актуальность может быть недоступна.';
    } else {
      banner.hidden = false;
      banner.className = 'system-banner ok';
      banner.textContent = 'STN подключён: актуальность нормативов будет проверяться на normy.stn.by.';
    }
    if (normacs.enabled) {
      banner.textContent += normacs.configured
        ? ' NormaCS (РФ) запускается автоматически для каждого документа.'
        : ' Для NormaCS укажите логин и пароль в Интеграциях или .env.';
      if (!normacs.configured) banner.className = 'system-banner warn';
    }
  } catch (e) {
    const banner = document.getElementById('system-banner');
    if (banner) {
      banner.hidden = false;
      banner.className = 'system-banner warn';
      banner.textContent = 'Не удалось получить состояние сервера. Проверьте подключение.';
    }
  }
}

function renderModelDrop(models) {
  const drop = document.getElementById('model-sel-drop');
  if (!drop) return;
  drop.innerHTML = '';
  models.forEach(m => {
    const id = typeof m === 'string' ? m : m.id;
    const label = typeof m === 'string' ? m : m.label;
    const opt = document.createElement('div');
    opt.className = 'csel-opt';
    opt.dataset.id = id;
    opt.textContent = label;
    opt.addEventListener('click', () => { selectModel(id, true); closeDrop(); });
    drop.appendChild(opt);
  });
}

function selectModel(id, userChose = false) {
  const hidden = document.getElementById('model-select');
  const valueEl = document.getElementById('model-sel-val');
  if (hidden) hidden.value = id;
  if (valueEl) valueEl.textContent = _modelMap[id] || id;
  document.querySelectorAll('.csel-opt').forEach(opt => {
    opt.classList.toggle('active', opt.dataset.id === id);
  });
  if (userChose) {
    userOverrodeModel = true;
    clearAutoModelBadge();
  }
}

function toggleDrop() {
  const drop = document.getElementById('model-sel-drop');
  const trigger = document.getElementById('model-sel-trigger');
  if (!drop || !trigger) return;
  const isOpen = drop.classList.toggle('open');
  trigger.classList.toggle('open', isOpen);
}

function closeDrop() {
  const drop = document.getElementById('model-sel-drop');
  const trigger = document.getElementById('model-sel-trigger');
  if (drop) drop.classList.remove('open');
  if (trigger) trigger.classList.remove('open');
}

document.addEventListener('click', e => {
  if (!e.target.closest('#model-sel-wrap')) closeDrop();
});

// ── textarea ──────────────────────────────────────────────────────────────────

const ta = document.getElementById('input-field');
ta.addEventListener('input', () => {
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 180) + 'px';
});
ta.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

// ── file handling ─────────────────────────────────────────────────────────────

document.getElementById('file-input').addEventListener('change', e => {
  const f = e.target.files[0];
  if (!f) return;
  if (!isDrawingFile(f)) {
    e.target.value = '';
    showToast('Поддерживаются только PDF и сканы (изображения)', true);
    return;
  }
  setFile(f);
});

document.addEventListener('paste', e => {
  const items = (e.clipboardData || e.originalEvent?.clipboardData)?.items;
  if (!items) return;
  for (const item of items) {
    if (item.kind === 'file') {
      const f = item.getAsFile();
      if (!f) break;
      if (!isDrawingFile(f)) {
        showToast('Поддерживаются только PDF и сканы (изображения)', true);
        break;
      }
      setFile(f); showToast('Файл вставлен: ' + f.name);
      break;
    }
  }
});

function setFile(f, options = {}) {
  if (!isDrawingFile(f)) {
    showToast('Поддерживаются только PDF и сканы (изображения)', true);
    return;
  }
  currentFile = f;
  document.getElementById('file-preview-name').textContent = f.name;
  document.getElementById('file-preview-size').textContent = fmtSize(f.size);
  document.getElementById('file-preview').classList.add('show');
  renderFilePreview(f);
  if (autoModelEnabled && !userOverrodeModel) {
    detectFileAndSelectModel(f);
  }
}

function isDrawingFile(f) {
  if (!f) return false;
  if (DRAWING_FILE_RE.test(f.name || '')) return true;
  const mime = String(f.type || '').toLowerCase();
  return mime === 'application/pdf' || mime.startsWith('image/');
}

function renderFilePreview(f) {
  const box = document.getElementById('file-preview-render');
  if (!box) return;
  if (currentPreviewUrl) {
    URL.revokeObjectURL(currentPreviewUrl);
    currentPreviewUrl = '';
  }
  box.innerHTML = '';
  if (!isDrawingFile(f)) return;
  currentPreviewUrl = URL.createObjectURL(f);
  const mime = String(f.type || '').toLowerCase();
  if (mime.startsWith('image/') || /\.(png|jpe?g|bmp|gif|webp|tif?f)$/i.test(f.name)) {
    box.innerHTML = `<img src="${currentPreviewUrl}" alt="Предпросмотр файла">`;
  } else if (mime === 'application/pdf' || /\.pdf$/i.test(f.name)) {
    box.innerHTML = `<iframe src="${currentPreviewUrl}#page=1&view=Fit" title="Предпросмотр PDF"></iframe>`;
  } else {
    box.innerHTML = '<div class="preview-note">Предпросмотр для этого формата недоступен, файл будет обработан как изображение.</div>';
  }
}

async function detectFileAndSelectModel(f) {
  const ext = '.' + f.name.split('.').pop().toLowerCase();
  if (ext !== '.pdf') {
    await autoSelectModel(ext, document.getElementById('input-field').value.trim());
    return;
  }
  // PDF: отправляем на сервер — он проверит скан это или текст
  try {
    const fd = new FormData();
    fd.append('file', f);
    const data = await fetch('/api/detect-file', { method: 'POST', body: fd }).then(r => r.json());
    if (data.model && _modelMap[data.model]) {
      selectModel(data.model);
      showAutoModelBadge(data.model, data.reason);
    }
    if (data.page_count > 1 && data.budget_human) {
      const sizeEl = document.getElementById('file-preview-size');
      if (sizeEl) {
        sizeEl.textContent = `${fmtSize(f.size)} · ${data.page_count} лист. · ${data.budget_human}`;
      }
    }
  } catch(e) {
    await autoSelectModel(ext, document.getElementById('input-field').value.trim());
  }
}

function removeFile(options = {}) {
  const preservePreviewUrl = Boolean(options.preservePreviewUrl);
  currentFile = null;
  document.getElementById('file-input').value = '';
  document.getElementById('file-preview').classList.remove('show');
  const box = document.getElementById('file-preview-render');
  if (box) box.innerHTML = '';
  if (currentPreviewUrl && !preservePreviewUrl) {
    URL.revokeObjectURL(currentPreviewUrl);
    currentPreviewUrl = '';
  }
  clearAutoModelBadge();
}

function fmtSize(b) {
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b/1024).toFixed(1) + ' KB';
  return (b/1048576).toFixed(1) + ' MB';
}

// ── drag & drop ───────────────────────────────────────────────────────────────

let dragN = 0;

function _isFileDropZone(el) {
  return !!(el && el.closest('.pdf-preview-container, .normative-workspace-preview, .normative-preview-shell, .normative-workspace, .msg-content'));
}

document.addEventListener('dragenter', (e) => {
  e.preventDefault();
  if (_isFileDropZone(e.target)) return;
  dragN += 1;
  document.getElementById('drop-overlay').classList.add('show');
});
document.addEventListener('dragleave', (e) => {
  if (_isFileDropZone(e.target)) return;
  dragN -= 1;
  if (dragN <= 0) {
    dragN = 0;
    document.getElementById('drop-overlay').classList.remove('show');
  }
});
document.addEventListener('dragover', (e) => {
  e.preventDefault();
  if (_isFileDropZone(e.target)) return;
});
document.addEventListener('drop', (e) => {
  e.preventDefault(); dragN = 0;
  document.getElementById('drop-overlay').classList.remove('show');
  const f = e.dataTransfer.files[0];
  if (!f) return;
  if (!isDrawingFile(f)) {
    showToast('Поддерживаются только PDF и сканы (изображения)', true);
    return;
  }
  setFile(f);
  showToast('Файл прикреплён: ' + f.name);
});

// ── messages ──────────────────────────────────────────────────────────────────

function hideEmpty() {
  const el = document.getElementById('empty-state');
  if (el) el.style.display = 'none';
}

function escAttr(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function addMessage(role, content, badge, fileUrl) {
  hideEmpty();
  const wrap = document.getElementById('chat-inner');
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  const av = role === 'user'
    ? `<div class="msg-avatar">↑</div>`
    : `<div class="msg-avatar"><img src="/ico.png" alt="" style="width:20px;height:20px;object-fit:contain;border-radius:6px;"></div>`;
  const bd = badge
    ? `<div class="msg-user-stack"><div class="msg-file-badge"><span class="msg-file-name">${esc(badge)}</span>${fileUrl ? `<a class="msg-file-open" href="${escAttr(fileUrl)}" target="_blank" rel="noopener">Открыть</a>` : ''}</div><div class="msg-user-text">${esc(content)}</div></div>`
    : esc(content);
  div.innerHTML = `${av}<div class="msg-body"><div class="msg-role">${role === 'user' ? 'Вы' : 'БелнипиAI'}</div><div class="msg-content">${role === 'user' ? bd : ''}</div></div>`;
  wrap.appendChild(div);
  return div.querySelector('.msg-content');
}

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function scrollEnd(force = false) {
  const a = document.getElementById('chat-area');
  if (force || !userScrolled) {
    a.scrollTop = a.scrollHeight;
  }
}

// ── stop ──────────────────────────────────────────────────────────────────────

function stopStreaming() {
  if (abortController) { abortController.abort(); abortController = null; }
}

// ── send ──────────────────────────────────────────────────────────────────────

async function sendMessage() {
  if (isStreaming) return;
  const qRaw = ta.value.trim();
  if (!currentFile) {
    showToast('Прикрепите PDF или скан чертежа', true);
    return;
  }
  const isDrawing = isDrawingFile(currentFile);
  if (!isDrawing) {
    showToast('Поддерживаются только PDF и сканы (изображения)', true);
    return;
  }
  const q = qRaw || GOST_DEFAULT_QUESTION;
  if (!q) return;

  // Авто-выбор для вопроса без файла (сложные запросы)
  if (autoModelEnabled && !userOverrodeModel && !currentFile) {
    await autoSelectModel('', q);
  }

  const model = getSelectedModel();
  const modelWasOverridden = userOverrodeModel;
  userOverrodeModel = false;
  clearAutoModelBadge();

  // Создаём чат если нет
  if (!currentConvId) {
    const data = await fetch('/api/conversations', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({model})
    }).then(r => r.json());
    currentConvId = data.id;
    await loadConversations();
    highlightActiveChat();
  }

  isStreaming = true;
  abortController = new AbortController();
  const btn = document.getElementById('send-btn');
  btn.classList.add('stop');
  btn.innerHTML = stopIcon;
  btn.onclick = stopStreaming;
  ta.value = ''; ta.style.height = 'auto';

  const fc = currentFile, fn = fc ? fc.name : null;
  const localFileUrl = currentPreviewUrl || null;
  lastRetryRequest = {question: q, file: fc, filename: fn};
  if (fc) removeFile({preservePreviewUrl: !!localFileUrl});

  userScrolled = false;
  addMessage('user', q, fn, localFileUrl); scrollEnd(true);
  if (localFileUrl && localFileUrl.startsWith('blob:')) {
    setTimeout(() => {
      try { URL.revokeObjectURL(localFileUrl); } catch (e) {}
    }, 10 * 60 * 1000);
  }
  const ac = addMessage('assistant', '');
  const progress = createProgress();
  ac.appendChild(progress);
  scrollEnd(true);

  const fd = new FormData();
  fd.append('question', q); fd.append('model', model);
  const isGostCheck = !/извлеч|весь текст|прочитай/i.test(q);
  if (isGostCheck) fd.append('mode', 'gost');
  lastRetryRequest.normacs = consumeNormacsRequest(fd, isGostCheck);
  const checkDateEl = document.getElementById('check-date');
  if (checkDateEl && checkDateEl.value) fd.append('check_date', checkDateEl.value);
  if (modelWasOverridden) fd.append('model_override', '1');
  if (fc) fd.append('file', fc);

  let raw = '', first = true, jobId = null;
  const requestConvId = currentConvId;
  const requestController = abortController;
  try {
    const res = await fetch(`/api/conversations/${requestConvId}/chat`, {
      method: 'POST', body: fd, signal: requestController.signal
    });
    await readSseResponse(res, o => {
          if (o.job_id) jobId = o.job_id;
          if (o.error) {
            showToast(o.error, true);
            showInlineError(ac, o.error);
            first = false;
            return;
          }
          if (o.title) {
            updateChatTitle(requestConvId, o.title);
          }
          if (o.status) {
            updateProgress(progress, o);
            scrollEnd();
          }
          if (o.text) {
            if (first) { progress.remove(); first = false; }
            raw += o.text;
            ac.innerHTML = renderAssistantMarkdown(raw) + '<span class="cursor"></span>';
            addCodeBtns(ac); scrollEnd();
          }
    });
  } catch(err) {
    if (err.name !== 'AbortError') {
      if (isGostCheck && currentConvId === requestConvId) {
        if (!progress.isConnected) ac.appendChild(progress);
        updateProgress(progress, {status: 'Соединение прервалось. Проверяю серверную задачу без повторной загрузки PDF…'});
        try {
          const report = await waitForNormativeJob(requestConvId, progress, requestController.signal, jobId);
          if (report !== null) { raw = report; first = false; progress.remove(); }
        } catch (recoveryError) {
          if (recoveryError.name !== 'AbortError') {
            showInlineError(ac, recoveryError.message);
            showToast(recoveryError.message, true);
          }
        }
      } else {
        showInlineError(ac, err.message || 'Ошибка соединения с сервером');
        showToast('Ошибка соединения', true);
      }
    } else if (jobId) {
      showToast('Получение ответа остановлено. Проверка продолжится на сервере; результат появится в истории чата.');
    }
  }

  const cur = ac.querySelector('.cursor'); if (cur) cur.remove();
  if (first) { progress.remove(); }
  if (raw) {
    ac.innerHTML = renderAssistantMarkdown(raw);
    beautifyNormativeHtml(ac);
    addCodeBtns(ac);
    addMsgCopy(ac, ac.innerHTML);
    addReportActions(ac, ac.innerHTML);
  }

  abortController = null;
  isStreaming = false;
  btn.classList.remove('stop');
  btn.innerHTML = sendIcon;
  btn.onclick = sendMessage;
  ta.focus(); scrollEnd();
}

function showInlineError(el, message) {
  el.innerHTML = `
    <div class="inline-error">
      <strong>${esc(message)}</strong>
      <button class="report-btn retry-btn" onclick="retryLastMessage()">Повторить</button>
    </div>`;
}

function updateChatTitle(id, title) {
  const item = document.querySelector(`.chat-item[data-id="${id}"] .chat-item-title`);
  if (item) item.textContent = title;
}

// ── auto model ────────────────────────────────────────────────────────────────

async function autoSelectModel(fileExt, question) {
  if (!autoModelEnabled) return;
  try {
    const data = await fetch('/api/suggest-model', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({file_ext: fileExt, question})
    }).then(r => r.json());

    if (data.model && _modelMap[data.model]) {
      selectModel(data.model);
      showAutoModelBadge(data.model, data.reason);
    }
  } catch(e) {}
}

function showAutoModelBadge(model, reason) {
  const label = _modelMap[model] || model;

  let badge = document.getElementById('auto-model-badge');
  if (!badge) {
    badge = document.createElement('span');
    badge.id = 'auto-model-badge';
    const anchor = document.getElementById('model-sel-wrap') || document.querySelector('.field-label');
    anchor.appendChild(badge);
  }
  badge.className = 'auto-model-badge';
  badge.title = reason;
  badge.innerHTML = `<span class="auto-model-label">авто</span><span class="auto-model-name">${esc(label)}</span>`;
  badge.style.display = 'inline-flex';
}

function clearAutoModelBadge() {
  const badge = document.getElementById('auto-model-badge');
  if (badge) badge.style.display = 'none';
}

// ── helpers ───────────────────────────────────────────────────────────────────

function setPrompt(t) { ta.value = t; ta.focus(); ta.dispatchEvent(new Event('input')); }

function showToast(msg, err) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show' + (err ? ' error' : '');
  setTimeout(() => t.classList.remove('show'), 3000);
}

async function loadCurrentUser() {
  try {
    const data = await fetch('/api/me').then(r => r.json());
    const login = (data.username || '').trim();
    const display = (data.display_name || '').trim();
    const userEl = document.getElementById('current-user');
    if (!userEl) return;
    if (login || display) {
      userEl.textContent = login ? `@${login}` : display;
      userEl.title = display && login ? `${display} (${login})` : (display || login);
    } else {
      userEl.textContent = 'Гость';
      userEl.title = 'Гость';
    }
  } catch (e) {
    const userEl = document.getElementById('current-user');
    if (userEl) {
      userEl.textContent = 'Гость';
      userEl.title = 'Гость';
    }
  }
}

// ── nav collapse ──────────────────────────────────────────────────────────────

function collapseNav() {
  document.querySelector('.shell').classList.add('nav-collapsed');
  document.querySelector('.nav')?.classList.add('collapsed');
  localStorage.setItem('navCollapsed', 'true');
}

function expandNav() {
  document.querySelector('.shell').classList.remove('nav-collapsed');
  document.querySelector('.nav')?.classList.remove('collapsed');
  localStorage.setItem('navCollapsed', 'false');
}

function toggleNav() {
  const shell = document.querySelector('.shell');
  const nav = document.querySelector('.nav');
  const isNowCollapsed = shell.classList.toggle('nav-collapsed');
  nav?.classList.toggle('collapsed', isNowCollapsed);
  localStorage.setItem('navCollapsed', isNowCollapsed ? 'true' : 'false');
}

const NAV_COLLAPSE_BP = 860; // совпадает с CSS @media (max-width: 860px)

function initNavState() {
  const saved = localStorage.getItem('navCollapsed');
  const isSmall = window.innerWidth <= NAV_COLLAPSE_BP;
  if (saved === 'true') collapseNav();
  else if (saved === 'false') expandNav();
  else if (isSmall) collapseNav();
  else expandNav();
}

let _wasSmallViewport = window.innerWidth <= NAV_COLLAPSE_BP;
window.addEventListener('resize', () => {
  const isSmall = window.innerWidth <= NAV_COLLAPSE_BP;
  if (isSmall !== _wasSmallViewport) {
    if (isSmall) collapseNav();
    else expandNav();
    _wasSmallViewport = isSmall;
  }
  document.querySelectorAll('.pdf-preview-img').forEach(fitPreviewImage);
});

// ── delete all chats ──────────────────────────────────────────────────────────

async function deleteAllChats() {
  const items = document.querySelectorAll('.chat-item');
  if (!items.length) return;
  const confirmed = await confirmDialog('Удалить все чаты? Это действие нельзя отменить.', 'Удаление истории');
  if (!confirmed) return;
  const ids = Array.from(items).map(el => el.dataset.id);
  await Promise.all(ids.map(id => fetch(`/api/conversations/${id}`, { method: 'DELETE' })));
  currentConvId = null;
  clearFeed();
  loadConversations();
  showToast('Все чаты удалены');
}

function initCheckDate() {
  const el = document.getElementById('check-date');
  if (!el) return;
  const today = new Date();
  const y = today.getFullYear();
  const m = String(today.getMonth() + 1).padStart(2, '0');
  const d = String(today.getDate()).padStart(2, '0');
  el.value = `${y}-${m}-${d}`;
}

// ── init ──────────────────────────────────────────────────────────────────────

marked.use({ gfm: true });
initNavState();
initPreviewWheelGlobal();
initCheckDate();
loadModels();
loadConversations();
loadCurrentUser();
loadSystemStatus();
ta.focus();
// Починить уже открытый ответ (жирный текст, подпись листа) без нового скана.
document.querySelectorAll('.msg.assistant .msg-content').forEach(beautifyNormativeHtml);

// Останавливаем автоскролл если пользователь уходит вверх
document.getElementById('chat-area').addEventListener('scroll', () => {
  const a = document.getElementById('chat-area');
  userScrolled = a.scrollHeight - a.scrollTop - a.clientHeight > 80;
});

function showNormativePreviewPage(groupId, pageNo) {
  const pages = [...document.querySelectorAll(`.normative-preview-page[data-group="${groupId}"]`)];
  if (!pages.length) return false;
  let target = pages.find(p => String(p.dataset.page) === String(pageNo));
  if (!target) return false;
  pages.forEach(p => {
    const on = p === target;
    p.classList.toggle('is-active', on);
    p.hidden = !on;
  });
  const label = document.querySelector(`.preview-page-label[data-group="${groupId}"]`);
  if (label) {
    const idx = pages.indexOf(target) + 1;
    label.textContent = `${idx} / ${pages.length} · лист ${target.dataset.page}`;
  }
  const box = target.querySelector('.pdf-preview-container');
  if (box) {
    box.scrollTop = 0;
    box.scrollLeft = 0;
  }
  const img = target.querySelector('.pdf-preview-img');
  if (img) img.loading = 'eager';
  const workspace = target.closest('.normative-workspace');
  if (workspace && typeof syncReviewPage === 'function') syncReviewPage(workspace);
  if (img) {
    img.dataset.scale = '1';
    delete img.dataset.fitKey;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => fitPreviewImage(img));
    });
  }
  return true;
}

function shiftNormativePreview(groupId, delta) {
  const pages = [...document.querySelectorAll(`.normative-preview-page[data-group="${groupId}"]`)];
  if (!pages.length) return;
  const cur = pages.findIndex(p => p.classList.contains('is-active'));
  const next = (cur + delta + pages.length) % pages.length;
  showNormativePreviewPage(groupId, pages[next].dataset.page);
}

document.addEventListener('click', (e) => {
  const zoomBtn = e.target.closest('.preview-zoom-btn');
  if (zoomBtn) {
    const targetId = zoomBtn.dataset.target;
    const img = document.getElementById(targetId);
    if (!img) return;
    const action = zoomBtn.dataset.action;
    const current = Number(img.dataset.scale || '1');
    const box = img.closest('.pdf-preview-container');
    let next = current;
    if (action === 'in') next = Math.min(4, current * 1.12);
    if (action === 'out') next = Math.max(0.5, current / 1.12);
    if (action === 'reset') next = 1;
    const anchor = action === 'reset' ? null : _previewZoomAnchorFromBox(box);
    setPreviewZoom(img, next, anchor);
    return;
  }

  const pageBtn = e.target.closest('.preview-page-btn');
  if (pageBtn) {
    const group = pageBtn.dataset.group;
    if (!group) return;
    shiftNormativePreview(group, pageBtn.dataset.action === 'prev' ? -1 : 1);
    return;
  }

  const pdfBtn = e.target.closest('.normative-table-pdf-btn');
  if (pdfBtn) {
    e.preventDefault();
    downloadNormativeTablePdf(pdfBtn);
    return;
  }

  const row = e.target.closest('.normative-table-container tr[data-preview-page]');
  if (row && !e.target.closest('a')) {
    const pageNo = row.dataset.previewPage;
    const workspace = row.closest('.normative-workspace');
    const shell = workspace && workspace.querySelector('.normative-preview-shell');
    const group = shell && shell.dataset.previewGroup;
    if (group && pageNo) {
      const ok = showNormativePreviewPage(group, pageNo);
      if (ok) {
        workspace.querySelectorAll('.normative-table-container tr.row-preview-focus')
          .forEach(tr => tr.classList.remove('row-preview-focus'));
        row.classList.add('row-preview-focus');
      }
    }
  }
});
