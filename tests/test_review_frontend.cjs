const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/review.js', 'utf8');
const context = {document: {documentElement: {dataset: {}}, addEventListener() {}, getElementById() {return null;}}};
vm.createContext(context);
vm.runInContext(source, context);
assert.equal(context.reviewStatus('актуален', true).label, 'Действует');
assert.equal(context.reviewStatus('актуален', false).tone, 'warn');
assert.equal(context.reviewStatus('ошибка проверки', false).label, 'Не проверен');
assert.equal(context.reviewStatus('нет в ИПС', false).label, 'Не найден в реестре');
assert.notEqual(context.reviewStatus('', false).label, 'Не действует');
assert.equal(context.reviewStatus('отменён', true).tone, 'bad');
assert.equal(context.reviewStatus('', true, true).tone, 'good');
context.applyReviewTheme('dark'); assert.equal(context.document.documentElement.dataset.theme, 'dark');
context.applyReviewTheme('invalid'); assert.equal(context.document.documentElement.dataset.theme, 'light');
assert.match(source, /tableBox.hidden = true/); // таблица сохранена для экспорта
assert.match(source, /data-interactive-highlights/);
assert.doesNotMatch(source, /fetch\(/); // смена вида/темы не запускает проверку
console.log('Review UI: themes, source status semantics and export compatibility OK');
