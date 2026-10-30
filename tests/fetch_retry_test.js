// node tests/fetch_retry_test.js : extracts fetchTextRetry from player.html and exercises it.
const fs = require('fs'), assert = require('assert');
const src = fs.readFileSync(__dirname + '/../player.html', 'utf8');
const m = src.match(/\/\* fetchTextRetry:begin \*\/([\s\S]*?)\/\* fetchTextRetry:end \*\//);
assert(m, 'markers not found');
const mk = (script) => {
  const calls = [];
  const fetch = (url, o) => { calls.push(url); return script(calls.length, o); };
  const fn = new Function('fetch', 'AbortController', 'setTimeout', 'clearTimeout',
    m[1] + '; return fetchTextRetry;')(fetch, AbortController, setTimeout, clearTimeout);
  return { fn, calls };
};
const resp = (status, body, h) => Promise.resolve({ ok: status >= 200 && status < 300, status, headers: { get: k => (h && h[k.toLowerCase()]) != null ? h[k.toLowerCase()] : null }, text: () => Promise.resolve(body || '') });
const FAST = { timeoutMs: 80, delayMs: 5 };
const tests = {
  async ok_first() { const t = mk(() => resp(200, 'hi')); assert.strictEqual(await t.fn('u', null, FAST), 'hi'); assert.strictEqual(t.calls.length, 1); },
  async retry_403_then_ok() { const t = mk(n => n === 1 ? resp(403) : resp(200, 'ok')); assert.strictEqual(await t.fn('u', null, FAST), 'ok'); assert.strictEqual(t.calls.length, 2); assert.strictEqual(t.calls[0], t.calls[1]); },
  async two_403_fail_with_status() { const t = mk(() => resp(403)); await assert.rejects(t.fn('u', null, FAST), /HTTP 403/); assert.strictEqual(t.calls.length, 2); },
  async no_retry_404() { const t = mk(() => resp(404)); await assert.rejects(t.fn('u', null, FAST), /HTTP 404/); assert.strictEqual(t.calls.length, 1); },
  async retry_network_error() { const t = mk(n => n === 1 ? Promise.reject(new TypeError('net')) : resp(200, 'ok')); assert.strictEqual(await t.fn('u', null, FAST), 'ok'); assert.strictEqual(t.calls.length, 2); },
  async timeout_then_ok() { const t = mk((n, o) => n === 1 ? new Promise((_, rej) => o.signal.addEventListener('abort', () => rej(new Error('aborted')))) : resp(200, 'ok')); assert.strictEqual(await t.fn('u', null, FAST), 'ok'); assert.strictEqual(t.calls.length, 2); },
  async timeout_twice_message() { const t = mk((n, o) => new Promise((_, rej) => o.signal.addEventListener('abort', () => rej(new Error('aborted'))))); await assert.rejects(t.fn('u', null, FAST), /timed out/); assert.strictEqual(t.calls.length, 2); },
  async cancel_before_start() { const c = new AbortController(); c.abort(); const t = mk(() => resp(200)); await assert.rejects(t.fn('u', c.signal, FAST), e => e.cancelled); assert.strictEqual(t.calls.length, 0); },
  async cancel_in_flight() { const c = new AbortController(); const t = mk((n, o) => new Promise((_, rej) => o.signal.addEventListener('abort', () => rej(new Error('aborted'))))); const p = t.fn('u', c.signal, { timeoutMs: 1000, delayMs: 5 }); setTimeout(() => c.abort(), 10); await assert.rejects(p, e => e.cancelled); assert.strictEqual(t.calls.length, 1); },
  async cancel_during_retry_delay() { const c = new AbortController(); const t = mk(() => resp(403)); const p = t.fn('u', c.signal, { timeoutMs: 1000, delayMs: 200 }); setTimeout(() => c.abort(), 30); await assert.rejects(p, e => e.cancelled); assert.strictEqual(t.calls.length, 1); },
  async retry_after_seconds_honoured() { const t = mk(n => n === 1 ? resp(503, '', { 'retry-after': '1' }) : resp(200, 'ok')); const t0 = Date.now(); assert.strictEqual(await t.fn('u', null, { timeoutMs: 500, delayMs: 5 }), 'ok'); assert(Date.now() - t0 >= 950, 'waited less than Retry-After'); assert.strictEqual(t.calls.length, 2); },
  async retry_after_too_long_no_retry() { const t = mk(() => resp(429, '', { 'retry-after': '120' })); await assert.rejects(t.fn('u', null, FAST), /retry in 120s/); assert.strictEqual(t.calls.length, 1); },
  async retry_after_http_date() { const d = new Date(Date.now() + 1000).toUTCString(); const t = mk(n => n === 1 ? resp(429, '', { 'retry-after': d }) : resp(200, 'ok')); const t0 = Date.now(); assert.strictEqual(await t.fn('u', null, { timeoutMs: 500, delayMs: 5 }), 'ok'); assert(Date.now() - t0 >= 100); assert.strictEqual(t.calls.length, 2); },
  async bare_429_waits_default_5s_floor() { const t = mk(n => n === 1 ? resp(429) : resp(200, 'ok')); const t0 = Date.now(); const p = t.fn('u', null, { timeoutMs: 500, delayMs: 5 }); assert.strictEqual(await p, 'ok'); assert(Date.now() - t0 >= 4900, 'bare 429 retried too fast'); },
  async listeners_balanced_after_retry() { let adds = 0, removes = 0; const c = new AbortController(); const outer = { get aborted() { return c.signal.aborted; }, addEventListener(...a) { adds++; c.signal.addEventListener(...a); }, removeEventListener(...a) { removes++; c.signal.removeEventListener(...a); } }; const t = mk(n => n === 1 ? resp(403) : resp(200, 'ok')); await t.fn('u', outer, { timeoutMs: 500, delayMs: 5 }); assert.strictEqual(adds, removes, 'abort listeners leaked: ' + adds + ' added, ' + removes + ' removed'); },
  async error_body_cancel_rejection_swallowed() { let un = 0; const h = () => un++; process.on('unhandledRejection', h); const t = mk(n => n === 1 ? Promise.resolve({ ok: false, status: 503, headers: { get: () => null }, body: { cancel() { return Promise.reject(new Error('cancel failed')); } } }) : resp(200, 'ok')); await t.fn('u', null, FAST); const t2 = mk(() => Promise.resolve({ ok: false, status: 404, headers: { get: () => null }, body: { cancel() { return Promise.reject(new Error('x')); } } })); await assert.rejects(t2.fn('u', null, FAST)); await new Promise(r => setTimeout(r, 30)); process.removeListener('unhandledRejection', h); assert.strictEqual(un, 0); },
  async error_body_released() { let cancelled = 0; const t = mk(n => n === 1 ? Promise.resolve({ ok: false, status: 503, headers: { get: () => null }, body: { cancel() { cancelled++; } } }) : resp(200, 'ok')); await t.fn('u', null, FAST); assert.strictEqual(cancelled, 1); },
  async never_more_than_two() { const t = mk(() => resp(503)); await assert.rejects(t.fn('u', null, FAST)); assert.strictEqual(t.calls.length, 2); },
};
(async () => { let f = 0; for (const [k, v] of Object.entries(tests)) { try { await v(); console.log('ok  ', k); } catch (e) { f++; console.log('FAIL', k, e.message); } } console.log(f ? 'FAILED ' + f : 'ALL PASS'); process.exit(f ? 1 : 0); })();
