// node tests/catalog_init_failover_test.js : runs the real init() from player.html in a Node VM with a stub DOM and fault-injecting fetch.
const fs = require('fs'), assert = require('assert');
const file = process.argv[2] || __dirname + '/../player.html';
const src = fs.readFileSync(file, 'utf8');
const grab = (re, n) => { const m = src.match(re); assert(m, 'not found: ' + n); return m[0]; };
const initSrc = grab(/async function init\(\) \{[\s\S]*?\n\}\n/, 'init');
const helpers = (src.match(/const CATALOG_TIMEOUT_MS[^\n]*\n/) || [''])[0] + (src.match(/function isValidCatalog[\s\S]*?\n\}\n/) || [''])[0];
const SOURCES = ['https://primary/games.json', 'games.json'];
function make(handlers) {
  const requested = []; const els = {}; let renders = 0, errors = 0, empties = 0;
  const el = () => ({ textContent: '', innerHTML: '', style: {} });
  const document = { getElementById: id => els[id] || (els[id] = el()) };
  const fetchImpl = (u, o) => { requested.push(u); return handlers[u](o); };
  const f = new Function('document', 'fetch', 'CATALOG_SOURCES', 'AbortController', 'setTimeout', 'clearTimeout', 'startParallax', 'showSkeletons', 'buildSidebar', 'renderGrid',
    'let allGames=[],filtered=[],displayedN=0;const CATALOG_TIMEOUT_MS_DEFAULT=0;' + helpers.replace(/const CATALOG_TIMEOUT_MS = \d+;/, 'const CATALOG_TIMEOUT_MS = 50;') + ';' + (helpers ? '' : 'const CATALOG_TIMEOUT_MS=50;function isValidCatalog(){return true}') + initSrc + ';return {init, state:()=>({allGames})};');
  const api = f(document, fetchImpl, SOURCES, AbortController, setTimeout, clearTimeout, () => {}, () => {}, () => {}, () => { renders++; });
  return { api, requested, els, renders: () => renders };
}
const hang = () => new Promise(() => {});
const json = d => () => Promise.resolve({ ok: true, json: () => Promise.resolve(d) });
const good = { games: [{ name: 'B', slug: 'b' }, { name: 'A', slug: 'a' }], categories: [], stats: {} };
const isErr = t => /Catalog Unavailable/.test((t.els['game-grid'] || {}).innerHTML || '');
const tests = {
  async primary_hang_falls_back() { const t = make({ [SOURCES[0]]: hang, [SOURCES[1]]: json(good) }); await t.api.init(); assert.deepStrictEqual(t.requested, SOURCES); assert.strictEqual(t.renders(), 1); assert(!isErr(t)); },
  async body_hang_falls_back() { const t = make({ [SOURCES[0]]: () => Promise.resolve({ ok: true, json: hang }), [SOURCES[1]]: json(good) }); await t.api.init(); assert.deepStrictEqual(t.requested, SOURCES); assert.strictEqual(t.renders(), 1); },
  async malformed_games_string() { const t = make({ [SOURCES[0]]: json({ games: 'oops' }), [SOURCES[1]]: json(good) }); await t.api.init(); assert.deepStrictEqual(t.requested, SOURCES); assert.strictEqual(t.renders(), 1); },
  async malformed_entry_no_name() { const t = make({ [SOURCES[0]]: json({ games: [{ slug: 'x' }] }), [SOURCES[1]]: json(good) }); await t.api.init(); assert.deepStrictEqual(t.requested, SOURCES); assert.strictEqual(t.renders(), 1); },
  async non_object_json() { const t = make({ [SOURCES[0]]: json(null), [SOURCES[1]]: json(7) }); await t.api.init(); assert(isErr(t)); assert.strictEqual(t.renders(), 0); },
  async empty_object_falls_back() { const t = make({ [SOURCES[0]]: json({}), [SOURCES[1]]: json(good) }); await t.api.init(); assert.deepStrictEqual(t.requested, SOURCES); assert.strictEqual(t.renders(), 1); },
  async all_fail_error_ui_once() { const t = make({ [SOURCES[0]]: hang, [SOURCES[1]]: json({ games: 'x' }) }); await t.api.init(); assert(isErr(t)); assert.strictEqual(t.els['results-info'].textContent, 'Error loading catalog'); assert.strictEqual(t.renders(), 0); },
  async legit_empty_catalog_empty_state() { const t = make({ [SOURCES[0]]: json({ games: [] }), [SOURCES[1]]: json(good) }); await t.api.init(); assert.deepStrictEqual(t.requested, [SOURCES[0]]); assert(!isErr(t)); assert(/No Lessons Yet/.test(t.els['game-grid'].innerHTML)); },
  async control_rejection_falls_back() { const t = make({ [SOURCES[0]]: () => Promise.reject(new Error('net')), [SOURCES[1]]: json(good) }); await t.api.init(); assert.deepStrictEqual(t.requested, SOURCES); assert.strictEqual(t.renders(), 1); },
  async control_happy_primary() { const t = make({ [SOURCES[0]]: json(good), [SOURCES[1]]: hang }); await t.api.init(); assert.deepStrictEqual(t.requested, [SOURCES[0]]); assert.strictEqual(t.renders(), 1); },
};
(async () => { let f = 0; for (const [k, v] of Object.entries(tests)) { try { await Promise.race([v(), new Promise((_, r) => setTimeout(() => r(new Error('test hung (init never settled)')), 1500))]); console.log('ok   ' + k); } catch (e) { f++; console.log('FAIL ' + k + ': ' + e.message); } } process.exit(f ? 1 : 0); })();
