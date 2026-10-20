/* Рабочий просмотр: реальные результаты из отчёта, без демонстрационных данных.
 * Исходная таблица остаётся в DOM для старых отчётов, копирования и PDF-экспорта. */
const reviewWorkspaces = new WeakMap();

function applyReviewTheme(theme) {
  const dark = theme === 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  const button = document.getElementById('theme-toggle');
  if (button) {
    button.textContent = dark ? '☀ Светлая тема' : '☾ Тёмная тема';
    button.setAttribute('aria-pressed', String(dark));
    button.setAttribute('aria-label', dark ? 'Переключить на светлую тему' : 'Переключить на тёмную тему');
  }
}

function reviewStatus(status, found, legacyActive = false) {
  const value = String(status || '').trim().toLowerCase();
  if ((found && value === 'актуален') || (!value && legacyActive)) return {label: 'Действует', tone: 'good'};
  if (found && /^(отмен[её]н|не действует)/.test(value)) return {label: 'Не действует', tone: 'bad'};
  if (found && /^не введ[её]н/.test(value)) return {label: 'Не введён', tone: 'warn'};
  if (found && /^замен/.test(value)) return {label: 'Заменён', tone: 'warn'};
  if (/ошиб|нет связи|вход|ips|пропущ/.test(value)) return {label: 'Не проверен', tone: 'warn'};
  if (/нет в|не найден/.test(value)) return {label: 'Не найден в реестре', tone: 'neutral'};
  return {label: 'Требует проверки', tone: 'warn'};
}

function reviewElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function reviewButton(label, action) {
  const node = reviewElement('button', 'review-button', label);
  node.type = 'button';
  node.dataset.reviewAction = action;
  return node;
}

function reviewReadRecords(workspace) {
  const table = workspace.querySelector('.normative-table-container table');
  if (!table) return [];
  const headings = [...table.querySelectorAll('thead th')].map(th => th.textContent.replace(/\s+/g, ' ').trim());
  const introduced = headings.findIndex(h => /введ[её]н/i.test(h));
  const canceled = headings.findIndex(h => /отмен[её]н/i.test(h));
  const stnPresent = headings.some(h => /^(?:стройдок|стройпрофи)$/i.test(h));
  const ncColumn = headings.findIndex(h => /статус.*normacs/i.test(h));
  return [...table.querySelectorAll('tbody tr')].map((row, index) => {
    const cells = [...row.querySelectorAll('td')];
    const cell = i => cells[i]?.textContent.replace(/\s+/g, ' ').trim() || '—';
    const modern = row.hasAttribute('data-stn-found');
    const found = modern ? row.dataset.stnFound === '1' : !!cells[1]?.querySelector('a');
    const ncCell = ncColumn >= 0 ? cells[ncColumn] : null;
    const ncFirst = ncCell?.firstChild?.textContent.trim() || '';
    const ncTone = ncCell?.classList.contains('normacs-status-1') ? 'good'
      : ncCell?.classList.contains('normacs-status-0') ? 'bad'
      : /не найден/i.test(ncFirst) ? 'neutral' : ncCell ? 'warn' : 'neutral';
    const ncStatus = ncFirst || (ncColumn >= 0 ? 'Не проверен' : 'Не запрошен');
    const stnStatus = stnPresent
      ? reviewStatus(row.dataset.stnStatus, found, !modern && row.classList.contains('row-active'))
      : null;
    const status = row.dataset.overallLabel
      ? {label: row.dataset.overallLabel, tone: row.dataset.overallTone || 'neutral'}
      : stnStatus || {label: ncStatus, tone: ncTone};
    const rbLabel = row.dataset.rbProvider === 'stroiprofi' ? 'Стройпрофи · РБ' : 'Стройдок · РБ';
    return {index, row, stnPresent, rbLabel, ref: row.dataset.normativeRef || cell(0), title: row.dataset.normativeTitle || '',
      stnCode: row.dataset.stnCode || '',
      status, stnStatus, overallNote: row.dataset.overallNote || '',
      conflict: row.dataset.overallConflict === '1',
      error: row.dataset.stnError || cells[1]?.querySelector('[title]')?.title || '',
      intro: cell(introduced), cancel: cell(canceled),
      pages: (row.dataset.previewPages || row.dataset.previewPage || '').split(',').map(Number).filter(p => Number.isInteger(p) && p > 0),
      stnLink: stnPresent ? cells[1]?.querySelector('a') : null,
      ncLink: ncColumn >= 0 ? cells[ncColumn - 1]?.querySelector('a') : null,
      ncStatus, ncTone,
      ncDetail: ncCell?.getAttribute('title') || cell(ncColumn)};
  });
}

function reviewSourceLink(link) {
  if (!link || !/^https?:\/\//i.test(link.href)) return null;
  const node = link.cloneNode(true);
  node.target = '_blank'; node.rel = 'noopener noreferrer';
  return node;
}

function initReviewWorkspace(workspace) {
  if (reviewWorkspaces.has(workspace)) return;
  const records = reviewReadRecords(workspace);
  if (!records.length) return;
  workspace.classList.add('review-workspace');
  const heading = workspace.previousElementSibling;
  if (heading?.tagName === 'H2') heading.textContent = 'Документ и нормативы';
  workspace.querySelectorAll('.review-generated').forEach(node => node.remove());
  const rail = workspace.querySelector('.normative-workspace-list');
  const tableBox = workspace.querySelector('.normative-table-container');
  tableBox.hidden = true;
  const controls = reviewElement('div', 'review-controls review-generated');
  controls.append(reviewElement('h3', '', `Нормативы · ${records.length}`));
  const views = reviewElement('div', 'review-view-buttons');
  const listButton = reviewButton('Список', 'list'); const cardsButton = reviewButton('Карточки', 'cards');
  views.append(listButton, cardsButton); controls.append(views);
  const list = reviewElement('div', 'review-list review-generated');
  list.setAttribute('aria-label', 'Найденные нормативы');
  const detail = reviewElement('aside', 'review-detail review-generated');
  detail.setAttribute('aria-label', 'Подробности выбранного норматива'); detail.setAttribute('aria-live', 'polite');
  tableBox.before(controls, list, detail);
  const model = {records, list, detail, selected: -1, rail};
  reviewWorkspaces.set(workspace, model);
  records.forEach(record => {
    const card = reviewButton('', 'select'); card.className = 'review-item'; card.dataset.reviewIndex = record.index;
    card.setAttribute('aria-label', `${record.ref} — ${record.status.label}, показать в документе`);
    const name = reviewElement('span', 'review-item-name', record.ref);
    const meta = reviewElement('span', 'review-item-meta');
    meta.append(reviewElement('span', `review-status ${record.status.tone}`, record.status.label),
      reviewElement('span', 'review-pages-count', record.pages.length ? `Листы ${record.pages.join(', ')}` : 'Лист не определён'));
    if (record.conflict) meta.append(reviewElement('span', 'review-status warn', '⚠ Реестры расходятся'));
    card.append(name, meta, reviewElement('span', 'review-item-source', record.stnPresent
      ? `РБ: ${record.stnStatus.label}${record.ncStatus !== 'Не запрошен' ? ' · РФ: '+record.ncStatus : ''}`
      : 'РФ · NormaCS'));
    list.append(card);
  });
  workspace.dataset.reviewView = workspace.dataset.reviewView || 'list';
  setReviewView(workspace, workspace.dataset.reviewView);
  const viewer = workspace.querySelector('.normative-workspace-preview');
  const shell = viewer?.querySelector('.normative-preview-shell');
  if (shell) {
    const filename = [...rail.querySelectorAll('.normative-workspace-meta p')].find(p => /Файл:/.test(p.textContent));
    const heading = viewer.querySelector('.normative-preview-heading');
    if (heading) heading.textContent = filename ? filename.textContent.replace(/^.*Файл:\s*/, '') : 'Документ с выделениями';
    const toolbar = reviewElement('div', 'review-viewer-tools review-generated');
    const picker = reviewElement('select', 'review-page-picker'); picker.setAttribute('aria-label', 'Лист документа');
    shell.querySelectorAll('.normative-preview-page').forEach(page => {
      const option = reviewElement('option', '', `Лист ${page.dataset.page}`); option.value = page.dataset.page; picker.append(option);
      const box = page.querySelector('.pdf-preview-container');
      const image = box?.querySelector('.pdf-preview-img');
      if (image) {image.loading = page.classList.contains('is-active') ? 'eager' : 'lazy'; image.decoding = 'async';}
      if (box?.dataset.interactiveHighlights === '1') {
        const stage = ensurePreviewStage(box);
        const overlay = reviewElement('div', 'review-overlay review-generated');
        let marks = []; try { marks = JSON.parse(box.dataset.reviewMarks || '[]'); } catch (_) { /* старое превью */ }
        marks.forEach(mark => {
          const record = records.find(r => r.ref === mark.ref);
          if (!record || ![mark.x, mark.y, mark.w, mark.h].every(Number.isFinite)
              || mark.x < 0 || mark.y < 0 || mark.w <= 0 || mark.h <= 0
              || mark.x + mark.w > 100.01 || mark.y + mark.h > 100.01) return;
          const hit = reviewButton('', 'highlight'); hit.className = 'review-highlight'; hit.dataset.reviewIndex = record.index;
          hit.setAttribute('aria-label', 'Открыть карточку '+record.ref);
          hit.style.left = mark.x+'%'; hit.style.top = mark.y+'%'; hit.style.width = mark.w+'%'; hit.style.height = mark.h+'%';
          overlay.append(hit);
        });
        if (stage) stage.append(overlay);
      }
    });
    const toggle = reviewButton('Подсветка', 'highlights');
    const interactive = !!shell.querySelector('[data-interactive-highlights="1"]');
    toggle.disabled = !interactive; toggle.setAttribute('aria-pressed', 'true');
    toggle.title = interactive ? 'Показать или скрыть найденные упоминания' : 'Старый отчёт: выделения сохранены внутри изображения';
    toolbar.append(picker, toggle, reviewButton('К упоминанию', 'focus')); shell.prepend(toolbar);
    const legend = reviewElement('div', 'review-legend review-generated');
    legend.append(reviewElement('span', 'review-found-legend', 'Найдено в тексте'), reviewElement('span', 'review-selected-legend', 'Выбранный норматив'));
    const hint = reviewElement('div', 'review-preview-hint review-generated', interactive
      ? 'Нажмите на выделение, чтобы открыть карточку. Подсветка не означает актуальность.'
      : 'Для клика по выделению и отключения подсветки повторите проверку старого файла.');
    viewer.append(legend, hint);
  }
  const savedRef = workspace.dataset.reviewSelected;
  selectReviewRecord(workspace, records.find(r => r.ref === savedRef)?.index || 0, false);
  syncReviewPage(workspace);
}

function setReviewView(workspace, view) {
  workspace.dataset.reviewView = view === 'cards' ? 'cards' : 'list';
  workspace.querySelectorAll('[data-review-action="list"], [data-review-action="cards"]').forEach(button =>
    button.setAttribute('aria-pressed', String(button.dataset.reviewAction === workspace.dataset.reviewView)));
}

function selectReviewRecord(workspace, index, jump = true) {
  const model = reviewWorkspaces.get(workspace); const record = model?.records[index];
  if (!record) return;
  model.selected = index; workspace.dataset.reviewSelected = record.ref;
  workspace.querySelectorAll('.review-item, .review-highlight').forEach(node => {
    node.setAttribute('aria-pressed', String(Number(node.dataset.reviewIndex) === index));
  });
  // Прокручиваем только список, не уводя пользователя от листа PDF.
  const selectedCard = model.list.querySelector(`[data-review-index="${index}"]`);
  if (selectedCard && model.list.isConnected) {
    const cardBox = selectedCard.getBoundingClientRect();
    const listBox = model.list.getBoundingClientRect();
    if (cardBox.top < listBox.top) model.list.scrollTop += cardBox.top - listBox.top;
    else if (cardBox.bottom > listBox.bottom) model.list.scrollTop += cardBox.bottom - listBox.bottom;
  }
  const detail = model.detail; detail.replaceChildren();
  detail.append(reviewElement('div', 'review-detail-eyebrow', 'Выбранный норматив'),
    reviewElement('h3', '', record.ref), reviewElement('p', 'review-detail-title', record.title || 'Обозначение из загруженного документа'));
  detail.append(reviewElement('span', `review-status ${record.status.tone}`, record.status.label));
  if (record.overallNote) detail.append(reviewElement('p', record.conflict ? 'review-error' : 'review-note', record.overallNote));
  if (record.stnPresent) {
    const rb = reviewElement('section', 'review-source');
    const rbHead = reviewElement('div', 'review-source-head'); rbHead.append(reviewElement('span', '', record.rbLabel), reviewElement('span', `review-status ${record.stnStatus.tone}`, record.stnStatus.label));
    rb.append(rbHead, reviewElement('p', '', 'Введён: '+record.intro), reviewElement('p', '', 'Отменён: '+record.cancel));
    if (record.stnCode && record.rbLabel.startsWith('Стройпрофи')) rb.append(reviewElement('p', '', 'Последняя редакция: '+record.stnCode));
    if (record.error) rb.append(reviewElement('p', 'review-error', record.error));
    const rbLink = reviewSourceLink(record.stnLink); if (rbLink) rb.append(rbLink);
    detail.append(rb);
  }
  const rf = reviewElement('section', 'review-source'); const rfHead = reviewElement('div', 'review-source-head');
  rfHead.append(reviewElement('span', '', 'NormaCS · РФ'), reviewElement('span', `review-status ${record.ncTone}`, record.ncStatus));
  rf.append(rfHead);
  rf.append(reviewElement('p', '', record.ncStatus === 'Не запрошен' ? 'Проверка NormaCS не выполнялась для этого отчёта.' : record.ncDetail));
  const rfLink = reviewSourceLink(record.ncLink); if (rfLink) rf.append(rfLink);
  detail.append(rf);
  const locations = reviewElement('div', 'review-locations'); locations.append(reviewElement('p', '', 'Перейти к упоминанию на листе'));
  const chips = reviewElement('div', 'review-page-chips');
  record.pages.forEach(page => {const chip = reviewButton(String(page), 'page'); chip.dataset.reviewPage = page; chip.setAttribute('aria-label', `Показать ${record.ref} на листе ${page}`); chips.append(chip);});
  if (!record.pages.length) chips.append(reviewElement('span', 'review-note', 'Место в документе не определено'));
  locations.append(chips); detail.append(locations);
  if (jump && record.pages.length) {
    const shell = workspace.querySelector('.normative-preview-shell');
    const active = shell?.querySelector('.normative-preview-page.is-active');
    if (shell && !record.pages.includes(Number(active?.dataset.page))) {
      const available = record.pages.find(page => shell.querySelector(`.normative-preview-page[data-page="${page}"]`));
      if (available !== undefined) showNormativePreviewPage(shell.dataset.previewGroup, available);
    }
  }
  syncReviewPage(workspace);
}

function syncReviewPage(workspace) {
  const model = reviewWorkspaces.get(workspace); if (!model) return;
  const active = workspace.querySelector('.normative-preview-page.is-active'); const page = active?.dataset.page;
  const picker = workspace.querySelector('.review-page-picker'); if (picker && page) picker.value = page;
  workspace.querySelectorAll('[data-review-page]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.reviewPage === page)));
  const record = model.records[model.selected]; const hint = workspace.querySelector('.review-preview-hint');
  const matching = active?.querySelector(`.review-highlight[data-review-index="${model.selected}"]`);
  const focus = workspace.querySelector('[data-review-action="focus"]'); if (focus) focus.disabled = !matching;
  if (hint && record && active?.querySelector('[data-interactive-highlights="1"]')) {
    hint.textContent = record.pages.includes(Number(page)) ? (matching
      ? `Выбран ${record.ref} · лист ${page}. Подсветка показывает упоминание, а не статус.`
      : `${record.ref} распознан на листе ${page}, но точное место выделить не удалось.`)
      : `На листе ${page} выбранного норматива нет. Перейдите по номеру листа в карточке.`;
  }
}

function focusReviewHighlight(workspace) {
  const page = workspace.querySelector('.normative-preview-page.is-active');
  const hit = page?.querySelector('.review-highlight[aria-pressed="true"]');
  const img = page?.querySelector('.pdf-preview-img'); const box = img?.closest('.pdf-preview-container');
  if (!hit || !img || !box) return;
  const x = (parseFloat(hit.style.left) + parseFloat(hit.style.width) / 2) / 100;
  const y = (parseFloat(hit.style.top) + parseFloat(hit.style.height) / 2) / 100;
  setPreviewZoom(img, Math.max(3, Number(img.dataset.scale || 1)), null);
  const bounds = img.getBoundingClientRect();
  const boxBounds = box.getBoundingClientRect();
  box.scrollLeft += bounds.left - boxBounds.left + x * bounds.width - box.clientWidth / 2;
  box.scrollTop += bounds.top - boxBounds.top + y * bounds.height - box.clientHeight / 2;
}

function updateReviewOverlay(img) {
  const stage = img.closest('.pdf-preview-stage'); const overlay = stage?.querySelector('.review-overlay');
  if (!overlay || !img.complete || !img.naturalWidth) return;
  const bounds = img.getBoundingClientRect(); const parent = stage.getBoundingClientRect();
  overlay.style.left = (bounds.left - parent.left)+'px'; overlay.style.top = (bounds.top - parent.top)+'px';
  overlay.style.width = bounds.width+'px'; overlay.style.height = bounds.height+'px';
}

document.addEventListener('click', event => {
  if (event.target.closest('#theme-toggle')) {
    const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; applyReviewTheme(theme);
    try { localStorage.setItem('belener-theme', theme); } catch (_) { /* приватный режим */ }
    return;
  }
  const button = event.target.closest('[data-review-action]'); const workspace = button?.closest('.normative-workspace');
  if (!button || !workspace || button.disabled) return;
  const action = button.dataset.reviewAction;
  if (action === 'select' || action === 'highlight') selectReviewRecord(workspace, Number(button.dataset.reviewIndex));
  if (action === 'list' || action === 'cards') setReviewView(workspace, action);
  if (action === 'focus') focusReviewHighlight(workspace);
  if (action === 'page') {const shell = workspace.querySelector('.normative-preview-shell'); if (shell) showNormativePreviewPage(shell.dataset.previewGroup, button.dataset.reviewPage);}
  if (action === 'highlights') {
    const off = workspace.classList.toggle('review-highlights-off'); button.setAttribute('aria-pressed', String(!off));
  }
});
document.addEventListener('change', event => {
  if (!event.target.matches('.review-page-picker')) return;
  const shell = event.target.closest('.normative-preview-shell');
  if (shell) showNormativePreviewPage(shell.dataset.previewGroup, event.target.value);
});
applyReviewTheme(document.documentElement.dataset.theme);
