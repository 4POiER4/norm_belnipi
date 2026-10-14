const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/app.js', 'utf8');
const context = {document: {getElementById: () => null}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function consumeNormacsRequest('), source.indexOf('function getSelectedModel(')), context);

const fields = [];
const form = {append: (...args) => fields.push(args)};
assert.equal(context.consumeNormacsRequest(form, true), true);
assert.equal(context.consumeNormacsRequest(form, true), true);
assert.equal(context.consumeNormacsRequest(form, false), false);
assert.deepEqual(fields, [['normacs', '1'], ['normacs', '1'], ['normacs', '0']]);
assert.doesNotMatch(fs.readFileSync('index.html', 'utf8'), /id="normacs-request-btn"/);
console.log('NormaCS: enabled for every normative request, no opt-in button');
