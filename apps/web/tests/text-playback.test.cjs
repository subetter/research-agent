const {test} = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const fs = require('node:fs');
const vm = require('node:vm');

function harness() {
  let scheduled;
  const exports = {};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync('components/text-playback.ts', 'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS, target:ts.ScriptTarget.ES2020}}).outputText, {
    exports,
    setTimeout(callback) {scheduled = callback; return 1;},
    clearTimeout() {scheduled = undefined;},
  });
  let output = '';
  const playback = exports.createTextPlayback((id,text) => {assert.equal(id,'reply'); output += text;});
  return {playback, value:()=>output, tick:()=>{const fn = scheduled; scheduled = undefined; fn?.();}};
}

test('a complete response arriving at once remains visibly incremental until drained', async () => {
  const h = harness();
  h.playback.enqueue('reply','你好😀世界');
  let done = false;
  const drained = h.playback.drain().then(()=>{done=true;});
  assert.equal(h.value(),'');
  h.tick(); await Promise.resolve();
  assert.equal(h.value(),'你'); assert.equal(done,false);
  h.tick(); h.tick();
  assert.equal(h.value(),'你好😀');
  h.tick(); h.tick(); await drained;
  assert.equal(h.value(),'你好😀世界'); assert.equal(done,true);
});

test('network chunks are joined in order and cancel stops remaining visual output', async () => {
  const h = harness();
  h.playback.enqueue('reply','第一'); h.tick();
  h.playback.enqueue('reply','第二');
  h.tick(); h.tick(); h.tick();
  await h.playback.drain();
  assert.equal(h.value(),'第一第二');
  h.playback.enqueue('reply','不应该继续');
  const pending = h.playback.drain();
  h.playback.cancel(); h.tick(); await pending;
  assert.equal(h.value(),'第一第二');
});
