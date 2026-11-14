# python3 tests/player_blob_smoke.py [outdir] : real-browser smoke (needs `pip install playwright && playwright install chromium`).
# Loads player.html with the catalog and one game asset replaced by a tiny ORIGINAL synthetic page (no network, no real games).
import json, sys, os
from playwright.sync_api import sync_playwright
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, 'tests', 'smoke_screenshots')
os.makedirs(out, exist_ok=True)
CAT = {"name": "synthetic", "stats": {"totalGames": 1}, "categories": ["Other"], "games": [
  {"id": "1", "name": "Synthetic Demo", "slug": "synthetic-demo", "file": "games/synthetic-demo.html", "category": "Other", "tags": [], "description": "synthetic", "size": 200, "originalSize": 200, "hash": "0"}]}
PAGE = '<!doctype html><body style="margin:0;background:#0a7;color:#fff;font:48px sans-serif"><h1>SYNTHETIC SMOKE PAGE</h1><p>original test asset</p></body>'
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={'width': 1000, 'height': 700})
    pg.add_init_script("""window.__c=0;window.__r=0;const C=URL.createObjectURL,R=URL.revokeObjectURL;
      URL.createObjectURL=function(o){window.__c++;return C.call(URL,o)};URL.revokeObjectURL=function(u){window.__r++;return R.call(URL,u)};""")
    def route(r):
        u = r.request.url
        if u.endswith('games.json'): r.fulfill(status=200, content_type='application/json', body=json.dumps(CAT))
        elif u.endswith('synthetic-demo.html'): r.fulfill(status=200, content_type='text/html', body=PAGE)
        elif u.startswith('file://'): r.continue_()
        else: r.abort()
    pg.route('**/*', route)
    pg.goto('file://' + os.path.join(root, 'player.html')); pg.wait_for_selector('text=Synthetic Demo')
    for i in (1, 2):
        pg.evaluate("playGame('synthetic-demo')")
        fr = pg.frame_locator('#game-frame'); fr.locator('h1').wait_for(timeout=10000)
        txt = fr.locator('h1').inner_text(); assert txt == 'SYNTHETIC SMOKE PAGE', txt
        pg.wait_for_timeout(800); pg.screenshot(path=f'{out}/open_{i}.png')
        print(f'open {i}: iframe renders "{txt}"; created={pg.evaluate("__c")} revoked={pg.evaluate("__r")}')
        pg.click("#btn-close")
        print(f'close {i}: created={pg.evaluate("__c")} revoked={pg.evaluate("__r")}')
    assert pg.evaluate("__c") == 2 and pg.evaluate("__r") == 2
    pg.screenshot(path=f'{out}/closed.png'); b.close(); print('SMOKE OK')
