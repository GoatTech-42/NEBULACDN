# python3 tests/catalog_init_failover_smoke.py [outdir] : real-browser smoke for catalog init failover, ORIGINAL synthetic catalog only.
import json, sys, os, threading, http.server, functools
from playwright.sync_api import sync_playwright
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, 'tests', 'smoke_screenshots')
os.makedirs(out, exist_ok=True)
def g(i, n): return {"id": str(i), "name": n, "slug": f"synth-{i}", "file": f"games/synth-{i}.html", "category": "Other", "tags": [], "description": "synthetic", "size": 200, "originalSize": 200, "hash": "0"}
CAT = {"name": "synthetic", "stats": {"totalGames": 2}, "categories": ["Other"], "games": [g(1, "Synthetic One"), g(2, "Synthetic Two")]}
# serve the repo over http so the relative 'games.json' fallback is fetchable (fetch() does not support file://)
class Q(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Q, directory=root))
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f'http://127.0.0.1:{srv.server_address[1]}'
def run(name, primary, local, expect):
    with sync_playwright() as p:
        b = p.chromium.launch(); ctx = b.new_context(viewport={'width': 900, 'height': 600}); pg = ctx.new_page()
        def route(r):
            u = r.request.url
            if 'jsdelivr' in u and u.endswith('games.json'):
                if primary == 'hang': return  # never answered
                r.fulfill(status=200, content_type='application/json', body=json.dumps(primary))
            elif u == BASE + '/games.json':
                r.fulfill(status=200, content_type='application/json', body=json.dumps(local))
            elif u.startswith(BASE): r.continue_()
            else: r.abort()
        ctx.route('**/*', route)
        pg.goto(BASE + '/player.html')
        pg.wait_for_selector(f'text={expect}', timeout=30000)
        pg.screenshot(path=f'{out}/catalog_{name}.png'); print('ok', name, '->', expect); b.close()
run('primary_hang_fallback', 'hang', CAT, 'Synthetic One')
run('primary_malformed_fallback', {"games": "oops"}, CAT, 'Synthetic One')
run('all_malformed_error', {"games": "oops"}, {}, 'Catalog Unavailable')
run('happy_primary', CAT, {}, 'Synthetic Two')
print('SMOKE OK')
