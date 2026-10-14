const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/app.js', 'utf8');
const start = source.indexOf('function _previewAvailSize(');
const end = source.indexOf('function _previewZoomAnchorFromBox(');
const context = {};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);

const box = {clientWidth: 1000, clientHeight: 500, scrollWidth: 1200,
  scrollHeight: 2000, scrollLeft: 0, scrollTop: 0,
  getBoundingClientRect() {return {left: 0, top: 0};}};
const base = {w: 300, h: 500};
const small = context._previewZoomLayout(base, 1.1, box);
assert.equal(small.stageW, 1000);
assert.equal(small.padX, 335); // узкий лист остаётся по центру
const large = context._previewZoomLayout(base, 4, box);
assert.equal(large.stageW, 1200);
assert.equal(large.padX, 0); // большой лист можно прокручивать

const img = {naturalWidth: 300, naturalHeight: 500, dataset: {},
  getBoundingClientRect() {return {left: 350, top: 0, width: 300, height: 500};}};
context._capturePreviewZoomAnchor(img, box, {clientX: 500, clientY: 250});
assert.equal(Number(img.dataset.zoomFracX), 0.5);
context._applyPreviewZoomScroll(img, box, 4);
assert.equal(box.scrollLeft, 100); // центр листа остаётся под центром окна
assert.equal(box.scrollTop, 750);
console.log('Preview zoom: centering and anchored scrolling OK');
