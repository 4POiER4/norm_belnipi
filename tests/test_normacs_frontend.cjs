const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
function functionCode(name, next) {
  return source.slice(source.indexOf(`function ${name}(`), source.indexOf(`function ${next}(`));
}
function element(classes = []) {
  return { classList: { contains: cls => classes.includes(cls) },
    style: { setProperty(key, value) { this[key] = value; } } };
}
const context = {
  NORMATIVE_TABLE_COL_WIDTHS: [280, 120, 100, 218, 218],
  NORMATIVE_ROW_BG: { 'row-active': '#e8f5ec', 'row-canceled': '#fce9ea' },
  document: { createElement: () => element() },
};
vm.createContext(context);
vm.runInContext(functionCode('applyNormativeTableLayout', 'applyNormativeRowColors'), context);
vm.runInContext(functionCode('applyNormativeRowColors', 'beautifyNormativeHtml'), context);
const status = element(['normacs-status', 'normacs-status-2']);
const missing = element(['normacs-status']);
const stn = element();
const row = { ...element(['row-canceled']), querySelectorAll: () => [stn, status, missing] };
const container = { style: {}, querySelectorAll: selector => selector === 'thead th' ? [] : [row] };
context.applyNormativeRowColors({ querySelectorAll: () => [container] });
assert.equal(stn.style['background-color'], '#fce9ea');
assert.equal(status.style['background-color'], '#fff4d6');
assert.equal(missing.style['background-color'], '#ffffff');
const cg = { replaceChildren(...cols) { this.cols = cols; } };
const table = { ...element(['normative-table-normacs']),
  querySelectorAll: () => Array(6).fill({}), querySelector: () => cg };
context.applyNormativeTableLayout({ querySelectorAll: () => [table] });
assert.equal(table.style.width, '1050px');
assert.equal(cg.cols.length, 6);
assert.equal(cg.cols[5].style.width, '280px');
console.log('NormaCS frontend: independent colors and six-column layout OK');
