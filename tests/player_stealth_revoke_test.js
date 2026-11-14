// node tests/player_stealth_revoke_test.js [path-to-player.html]
// Runs the real window.stealthGame source (extracted from player.html) in a Node VM with stubbed window.open/timers.
// Pass a pre-fix player.html as argv[2] for the negative control (expects the leak tests to FAIL).
const fs = require('fs'), assert = require('assert');
const file = process.argv[2] || __dirname + '/../player.html';
const src = fs.readFileSync(file, 'utf8');
let code = (src.match(/\/\* stealthGame:begin \*\/[\s\S]*?\/\* stealthGame:end \*\//) || [])[0];
if (!code) { const a = src.indexOf('window.stealthGame = function'), b = src.indexOf('/* ─── Close modal'); assert(a > 0 && b > a, 'source not found'); code = src.slice(a, b); }
function make(fetchImpl, opts = {}) {
  const created = [], revoked = []; let n = 0; const wins = []; const timers = new Map(); let tid = 0; const handlers = {};
  const URLs = { createObjectURL: () => { const u = 'blob:' + (++n); created.push(u); return u; }, revokeObjectURL: u => revoked.push(u) };
  const mkwin = () => { const w = { closed: false, html: [], document: { open() { if (opts.writeThrows) throw new Error('cross-origin'); }, write(h) { w.html.push(h); }, close() {} } }; wins.push(w); return w; };
  const window = { open: () => opts.blocked ? null : mkwin(), addEventListener: (e, f) => { handlers[e] = f; } };
  const setI = f => { const id = ++tid; timers.set(id, f); return id; }, clrI = id => timers.delete(id);
  const f = new Function('window', 'URL', 'Blob', 'allGames', 'fetchTextRetry', 'assetUrl', 'withAssetBase', 'esc', 'AbortController', 'setInterval', 'clearInterval', 'alert',
    code + ';return window.stealthGame;');
  const stealth = f(window, URLs, class { }, [{ slug: 'a', name: 'A' }, { slug: 'b', name: 'B' }], fetchImpl, () => 'u', h => h, s => s, AbortController, setI, clrI, () => { });
  const poll = () => { for (const t of Array.from(timers.values())) t(); };
  return { stealth, created, revoked, wins, poll, watchers: () => timers.size, handlers };
}
const tick = (ms = 20) => new Promise(r => setTimeout(r, ms));
const ok = () => Promise.resolve('<html></html>');
const tests = {
  async open_load_close_x3() { const t = make(ok); for (let i = 0; i < 3; i++) { t.stealth('a'); await tick(); t.wins[i].closed = true; t.poll(); }
    assert.deepStrictEqual([t.created.length, t.revoked.length, t.created.length - t.revoked.length, t.watchers()], [3, 3, 0, 0]); },
  async leak_shape_while_open() { const t = make(ok); t.stealth('a'); await tick(); t.poll(); assert.deepStrictEqual([t.created.length, t.revoked.length, t.watchers()], [1, 0, 1]); },
  async two_popups_close_A_keep_B() { const t = make(ok); t.stealth('a'); t.stealth('b'); await tick(); t.wins[0].closed = true; t.poll();
    assert.deepStrictEqual(t.revoked, ['blob:1']); assert.strictEqual(t.watchers(), 1); t.wins[1].closed = true; t.poll(); assert.deepStrictEqual(t.revoked, ['blob:1', 'blob:2']); },
  async close_before_fetch_aborts() { let rej; const t = make(() => new Promise((_, r) => { rej = r; })); t.stealth('a'); t.wins[0].closed = true; t.poll(); rej({ cancelled: true }); await tick();
    assert.deepStrictEqual([t.created.length, t.revoked.length, t.watchers()], [0, 0, 0]); },
  async late_completion_after_close_creates_nothing() { let res; const t = make(() => new Promise(r => { res = r; })); t.stealth('a'); t.wins[0].closed = true; t.poll(); res('<html>'); await tick();
    assert.deepStrictEqual([t.created.length, t.revoked.length, t.watchers()], [0, 0, 0]); },
  async fetch_failure_unchanged() { const t = make(() => Promise.reject(new Error('boom'))); t.stealth('a'); await tick();
    assert.strictEqual(t.created.length, 0); assert(/Failed to Load Lesson/.test(t.wins[0].html.join('')) && /boom/.test(t.wins[0].html.join(''))); assert.strictEqual(t.watchers(), 0); },
  async popup_blocked_unchanged() { const t = make(ok, { blocked: true }); t.stealth('a'); await tick(); assert.deepStrictEqual([t.created.length, t.watchers(), t.wins.length], [0, 0, 0]); },
  async write_exception_revokes() { const t = make(ok, { writeThrows: true }); t.stealth('a'); await tick(); assert.deepStrictEqual([t.created.length, t.revoked.length, t.watchers()], [1, 1, 0]); },
  async opener_pagehide_revokes_outstanding() { const t = make(ok); t.stealth('a'); t.stealth('b'); await tick(); assert(t.handlers.pagehide, 'pagehide handler'); t.handlers.pagehide(); assert.deepStrictEqual(t.revoked.sort(), ['blob:1', 'blob:2']); assert.strictEqual(t.watchers(), 0); },
};
(async () => { let f = 0; for (const [k, v] of Object.entries(tests)) { try { await v(); console.log('ok   ' + k); } catch (e) { f++; console.log('FAIL ' + k + ': ' + e.message.split('\n')[0]); } } process.exit(f ? 1 : 0); })();
