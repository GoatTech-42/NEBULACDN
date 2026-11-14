# python3 tests/player_stealth_smoke.py [outdir] : real-browser smoke for window.stealthGame with ORIGINAL synthetic assets only.
import json, sys, os
from playwright.sync_api import sync_playwright
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, 'tests', 'smoke_screenshots')
os.makedirs(out, exist_ok=True)
def g(i, n): return {"id": str(i), "name": n, "slug": f"synth-{i}", "file": f"games/synth-{i}.html", "category": "Other", "tags": [], "description": "synthetic", "size": 200, "originalSize": 200, "hash": "0"}
CAT = {"name": "synthetic", "stats": {"totalGames": 2}, "categories": ["Other"], "games": [g(1, "Synthetic One"), g(2, "Synthetic Two")]}
PAGE = lambda t, c: f'<!doctype html><body style="margin:0;background:{c};color:#fff;font:48px sans-serif"><h1>{t}</h1><p>original test asset</p></body>'
with sync_playwright() as p:
    b = p.chromium.launch(); ctx = b.new_context(viewport={'width': 800, 'height': 500}); pg = ctx.new_page()
    pg.add_init_script("""window.__c=0;window.__r=0;const C=URL.createObjectURL,R=URL.revokeObjectURL;
      URL.createObjectURL=function(o){window.__c++;return C.call(URL,o)};URL.revokeObjectURL=function(u){window.__r++;return R.call(URL,u)};""")
    def route(r):
        u = r.request.url
        if u.endswith('games.json'): r.fulfill(status=200, content_type='application/json', body=json.dumps(CAT))
        elif u.endswith('synth-1.html'): r.fulfill(status=200, content_type='text/html', body=PAGE('SYNTH ONE', '#0a7'))
        elif u.endswith('synth-2.html'): r.fulfill(status=200, content_type='text/html', body=PAGE('SYNTH TWO', '#a30'))
        elif u.startswith('file://'): r.continue_()
        else: r.abort()
    ctx.route('**/*', route)
    pg.goto('file://' + os.path.join(root, 'player.html')); pg.wait_for_selector('text=Synthetic One')
    c = lambda: f'created={pg.evaluate("__c")} revoked={pg.evaluate("__r")}'
    with ctx.expect_page() as e1: pg.evaluate("stealthGame('synth-1')")
    w1 = e1.value
    with ctx.expect_page() as e2: pg.evaluate("stealthGame('synth-2')")
    w2 = e2.value
    for w, t in ((w1, 'SYNTH ONE'), (w2, 'SYNTH TWO')):
        w.frame_locator('iframe').locator('h1').wait_for(timeout=10000); assert w.frame_locator('iframe').locator('h1').inner_text() == t
    print('both popups open and rendering;', c()); w1.screenshot(path=f'{out}/stealth_popup1_open.png'); w2.screenshot(path=f'{out}/stealth_popup2_open.png')
    assert pg.evaluate("__c") == 2 and pg.evaluate("__r") == 0
    w1.close(); pg.wait_for_timeout(1500)
    print('closed popup 1 (popup 2 kept);', c()); assert pg.evaluate("__r") == 1
    assert w2.frame_locator('iframe').locator('h1').inner_text() == 'SYNTH TWO'
    w2.screenshot(path=f'{out}/stealth_popup2_survivor_after_close1.png'); print('popup 2 still renders "SYNTH TWO" after popup 1 closed')
    w2.close(); pg.wait_for_timeout(1500); print('closed popup 2;', c()); assert pg.evaluate("__c") == 2 and pg.evaluate("__r") == 2
    print('SMOKE OK'); b.close()
