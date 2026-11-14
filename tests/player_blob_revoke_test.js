// node tests/player_blob_revoke_test.js : runs the real player.html modal functions in a Node VM with a stub DOM.
const fs = require('fs'), assert = require('assert');
const src = fs.readFileSync(__dirname + '/../player.html', 'utf8');
const grab = (re, n) => { const m = src.match(re); assert(m, 'not found: ' + n); return m[0]; };
const rel = grab(/\/\* revokeFrameBlob:begin \*\/[\s\S]*?\/\* revokeFrameBlob:end \*\//, 'release');
const play = grab(/window\.playGame = function[\s\S]*?\n};\n/, 'playGame');
const close = grab(/function closePlayer\(\) \{[\s\S]*?\n\}\n/, 'closePlayer');
const focusFns = grab(/let playInvoker = null;[\s\S]*?\nfunction restoreFocus\(inv\) \{[\s\S]*?\n\}\n/, 'focus helpers');
function make(fetchImpl) {
  const created = [], revoked = []; let n = 0;
  const el = () => ({ src: '', textContent: '', style: {}, classList: { add() {}, remove() {}, toggle() {}, contains() { return true; } }, focus() {}, addEventListener() {}, matches() { return false; }, querySelector() { return null; }, setAttribute() {}, removeAttribute() {} });
  const els = {}; const document = { getElementById: id => els[id] || (els[id] = el()), body: { style: {}, children: [] }, querySelectorAll: () => [] };
  const URLs = { createObjectURL: () => { const u = 'blob:' + (++n); created.push(u); return u; }, revokeObjectURL: u => revoked.push(u) };
  const frame = document.getElementById('game-frame');
  const f = new Function('document', 'URL', 'Blob', 'allGames', 'fetchTextRetry', 'assetUrl', 'withAssetBase', 'esc', 'AbortController', 'setTimeout',
    'let currentGame=null,playAbort=null,frameBlob=null;' + rel.replace(/^/, '') + ';' + focusFns + ';' + play + ';' + close + ';return {playGame:window.playGame, closePlayer};');
  const window = {}; global.window = window;
  const api = f(document, URLs, class { }, [{ slug: 'a', name: 'A', category: 'c' }, { slug: 'b', name: 'B', category: 'c' }],
    fetchImpl, () => 'u', h => h, s => s, AbortController, fn => setTimeout(fn, 0));
  return { api, created, revoked, frame };
}
const tick = (ms = 30) => new Promise(r => setTimeout(r, ms));
const ok = () => Promise.resolve('<html></html>');
const tests = {
  async open_close_x3() { const t = make(ok); for (let i = 0; i < 3; i++) { t.api.playGame('a'); await tick(); t.api.closePlayer(); } assert.deepStrictEqual([t.created.length, t.revoked.length], [3, 3]); assert.deepStrictEqual(t.created, t.revoked); },
  async displayed_not_revoked() { const t = make(ok); t.api.playGame('a'); await tick(); assert.strictEqual(t.frame.src, 'blob:1'); assert.strictEqual(t.revoked.length, 0); t.api.closePlayer(); assert.deepStrictEqual(t.revoked, ['blob:1']); assert.strictEqual(t.frame.src, ''); },
  async supersede_A_to_B() { const t = make(ok); t.api.playGame('a'); await tick(); t.api.playGame('b'); await tick(); assert.deepStrictEqual(t.revoked, ['blob:1']); assert.strictEqual(t.frame.src, 'blob:2'); t.api.closePlayer(); assert.deepStrictEqual(t.revoked, ['blob:1', 'blob:2']); },
  async abort_during_fetch() { let rej; const t = make((u, sig) => new Promise((_, r) => { rej = r; })); t.api.playGame('a'); await tick(); t.api.closePlayer(); rej({ cancelled: true }); await tick(); assert.deepStrictEqual([t.created.length, t.revoked.length], [0, 0]); },
  async late_resolve_after_close_creates_nothing() { let res; const t = make(() => new Promise(r => { res = r; })); t.api.playGame('a'); await tick(); t.api.closePlayer(); res('<html>'); await tick(); assert.deepStrictEqual([t.created.length, t.revoked.length], [0, 0]); },
  async double_close_no_double_revoke() { const t = make(ok); t.api.playGame('a'); await tick(); t.api.closePlayer(); t.api.closePlayer(); assert.strictEqual(t.revoked.length, 1); },
};
(async () => { let f = 0; for (const [k, v] of Object.entries(tests)) { try { await v(); console.log('ok   ' + k); } catch (e) { f++; console.log('FAIL ' + k + ': ' + e.message); } } process.exit(f ? 1 : 0); })();
