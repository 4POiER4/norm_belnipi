const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
const context = {TextDecoder, DOMException, setTimeout, clearTimeout};
vm.createContext(context);
const start = source.indexOf('function createSseDecoder(');
const end = source.indexOf('async function waitForNormativeJob(', start);
vm.runInContext(source.slice(start, end), context);
const expected = [{status:'225 листов'}, {text:'ГОСТ 21105-87 — Частично действует'}, {job_id:'id'}];
const frames = expected.map(v => `data: ${JSON.stringify(v)}\r\n\r\n`).join('') + 'data: [DONE]\n\n';
const bytes = new TextEncoder().encode(frames);
for (const size of [1, 2, 3, 7, 50, 900]) {
  const received = [];
  const decoder = context.createSseDecoder(v => received.push(v));
  let done = false;
  for (let i = 0; i < bytes.length; i += size) done = decoder.feed(bytes.slice(i,i+size));
  assert.equal(done, true);
  assert.deepEqual(JSON.parse(JSON.stringify(received)), expected);
}
(async () => {
  const data = [];
  await context.readSseResponse({ok:true,body:new ReadableStream({start(controller) {
    for (let i = 0; i < bytes.length; i += 3) controller.enqueue(bytes.slice(i,i+3));
    controller.close();
  }})},v=>data.push(v));
  assert.deepEqual(JSON.parse(JSON.stringify(data)),expected);
  await assert.rejects(context.readSseResponse({ok:true,body:new ReadableStream({start(c){c.close();}})},()=>{}),/прервалось/);
  await assert.rejects(context.readSseResponse({ok:false,status:409,json:async()=>({error:'Уже запущено'})},()=>{}),/Уже запущено/);
  console.log('SSE: byte-split UTF-8/JSON, DONE, truncated stream and HTTP errors OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
