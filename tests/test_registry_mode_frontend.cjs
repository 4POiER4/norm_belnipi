const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/app.js', 'utf8');
const start = source.indexOf('function updateRegistryModeUI(');
const end = source.indexOf('async function loadSystemStatus(', start);
const badge = {classList: {toggle(name, value) {this[name] = value;}}};
const welcome = {};
const step = {};
const context = {document: {
  getElementById(id) {return id === 'normacs-mode-badge' ? badge : null;},
  querySelector(selector) {return selector.includes('welcome-sub') ? welcome : step;},
}};
vm.createContext(context);
vm.runInContext('let currentRegistryStatus = null;\n' + source.slice(start, end), context);
vm.runInContext("currentRegistryStatus = {stn:{enabled:true,configured:true},normacs:{enabled:true,configured:true}}; updateRegistryModeUI();", context);
assert.match(badge.textContent, /Стройдок · РБ \+ NormaCS · РФ/);
assert.match(welcome.textContent, /Стройдок · РБ \+ NormaCS · РФ/);
assert.match(step.textContent, /Стройдок · РБ \+ NormaCS · РФ/);
assert.equal(badge.classList.warn, false);
vm.runInContext("currentRegistryStatus = {stn:{enabled:false},normacs:{enabled:true,configured:true}}; updateRegistryModeUI();", context);
assert.equal(badge.textContent, 'Проверка: NormaCS · РФ');
console.log('Registry mode UI: dual and NormaCS-only labels OK');
