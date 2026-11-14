# python3 tests/modal_focus_smoke.py [player.html] [outdir] : real-browser focus-management probe, ORIGINAL synthetic assets, network intercepted.
# Passing a pre-fix player.html runs it as the negative control (prints FAILS, exits 0 only with --expect-fail).
import json, sys, os
from playwright.sync_api import sync_playwright
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
args = [a for a in sys.argv[1:] if not a.startswith('--')]
expect_fail = '--expect-fail' in sys.argv
page_path = args[0] if args else os.path.join(root, 'player.html')
out = args[1] if len(args) > 1 else os.path.join(root, 'tests', 'smoke_screenshots')
os.makedirs(out, exist_ok=True)
def g(i, n): return {"id": str(i), "name": n, "slug": f"synth-{i}", "file": f"games/synth-{i}.html", "category": "Other", "tags": [], "description": "synthetic", "size": 200, "originalSize": 200, "hash": "0"}
CAT = {"name": "synthetic", "stats": {"totalGames": 2}, "categories": ["Other"], "games": [g(1, "Synthetic One"), g(2, "Synthetic Two")]}
LESSON = '<!doctype html><body style="margin:0;background:#145;color:#fff;font:32px sans-serif"><h1>LESSON</h1><button id="lb" onclick="document.title=\'clicked\';this.textContent=\'CLICKED\'">lesson button</button><script>addEventListener("keydown",e=>{if(e.key=="x")document.body.dataset.x="1"})</script></body>'
fails = []
def check(name, ok, info=''):
    print(('PASS ' if ok else 'FAIL ') + name, info)
    if not ok: fails.append(name)
ACT = """(()=>{const a=document.activeElement,o=document.getElementById('overlay');return {tag:a.tagName,id:a.id,cls:a.className,inDialog:o.contains(a),visible:a.getClientRects().length>0}})()"""
with sync_playwright() as p:
    b = p.chromium.launch(); ctx = b.new_context(viewport={'width': 900, 'height': 600}); pg = ctx.new_page()
    def route(r):
        u = r.request.url
        if u.endswith('games.json'): r.fulfill(status=200, content_type='application/json', body=json.dumps(CAT))
        elif 'games/synth-' in u: r.fulfill(status=200, content_type='text/html', body=LESSON)
        elif u.startswith('file://'): r.continue_()
        else: r.abort()
    ctx.route('**/*', route)
    pg.goto('file://' + os.path.abspath(page_path)); pg.wait_for_selector('text=Synthetic One')
    A = lambda: pg.evaluate(ACT)
    opens = lambda: pg.evaluate("document.getElementById('overlay').classList.contains('open')")
    btns = pg.locator('.btn-play')
    # 1 keyboard open
    btns.nth(0).focus(); pg.keyboard.press('Enter'); pg.wait_for_timeout(400)
    a = A(); check('keyboard open: modal open', opens())
    check('keyboard open: activeElement inside dialog on visible control', a['inDialog'] and a['visible'] and a['tag'] == 'BUTTON', str(a))
    pg.screenshot(path=f'{out}/focus_open.png')
    # focus indicator pixel check: close button focus ring colour (#ffd54a) present around it
    box = pg.locator('#btn-close').bounding_box()
    clip = {'x': box['x']-6, 'y': box['y']-6, 'width': box['width']+12, 'height': box['height']+12}
    pg.screenshot(path=f'{out}/focus_ring_close.png', clip=clip)
    from PIL import Image
    im = Image.open(f'{out}/focus_ring_close.png').convert('RGB')
    ring = sum(1 for px in im.getdata() if abs(px[0]-255) < 20 and abs(px[1]-213) < 25 and abs(px[2]-74) < 40)
    check('focus indicator visible (pixel-verified ring colour)', ring > 30, f'ring_pixels={ring}')
    # 2 background unreachable + inert
    inert = pg.evaluate("[...document.body.children].filter(e=>e.hasAttribute('inert')).map(e=>e.id||e.className||e.tagName)")
    check('background siblings inert', len(inert) >= 3, str(inert))
    seen = set(); allin = True
    for i in range(12):
        pg.keyboard.press('Tab'); x = A(); seen.add(x['id'] or x['tag']); allin &= x['inDialog']
    check('12 Tabs never leave dialog', allin, str(sorted(seen)))
    # wrap forward / back
    pg.wait_for_timeout(600)
    pg.frame_locator('#game-frame').locator('#lb').focus()  # last focusable inside the lesson
    pg.keyboard.press('Tab'); a = A(); check('Tab past last focusable (end of lesson) wraps to first control', a['id'] == 'btn-fullscreen', str(a))
    pg.locator('#btn-fullscreen').focus(); pg.keyboard.press('Shift+Tab'); a = A()
    check('Shift+Tab at first wraps to last (iframe)', a['id'] == 'game-frame', str(a))
    # iframe focus + interaction
    pg.wait_for_timeout(600)
    fl = pg.frame_locator('#game-frame'); fl.locator('#lb').click(); 
    check('iframe interaction works', fl.locator('#lb').inner_text() == 'CLICKED')
    fl.locator('#lb').focus(); pg.keyboard.press('x')
    check('iframe receives key events', fl.locator('body').get_attribute('data-x') == '1')
    # Escape from inside iframe: honest boundary
    pg.keyboard.press('Escape'); pg.wait_for_timeout(200)
    print('INFO Escape with focus inside sandboxed iframe -> modal still open =', opens(), '(parent window keydown not delivered from iframe)')
    iframe_escape_closes = not opens()
    if opens():
        pg.locator('#btn-close').focus()
    # 3 close paths
    pg.keyboard.press('Enter'); pg.wait_for_timeout(200)
    a = A(); check('close via keyboard: closed', not opens())
    check('close via keyboard: focus restored to invoker (card 1 Launch)', pg.evaluate("document.activeElement===document.querySelectorAll('.btn-play')[0]"), str(a))
    inert = pg.evaluate("[...document.body.children].filter(e=>e.hasAttribute('inert')).length"); check('inert removed on close', inert == 0)
    # backdrop
    pg.keyboard.press('Enter'); pg.wait_for_timeout(300)
    pg.evaluate("document.getElementById('overlay').dispatchEvent(new MouseEvent('click',{bubbles:true}))"); pg.wait_for_timeout(200)
    check('backdrop close restores invoker', (not opens()) and pg.evaluate("document.activeElement===document.querySelectorAll('.btn-play')[0]"), str(A()))
    # escape (focus on dialog control)
    pg.keyboard.press('Enter'); pg.wait_for_timeout(300); pg.keyboard.press('Escape'); pg.wait_for_timeout(200)
    check('Escape close restores invoker', (not opens()) and pg.evaluate("document.activeElement===document.querySelectorAll('.btn-play')[0]"), str(A()))
    # reopen from different card
    btns.nth(1).focus(); pg.keyboard.press('Enter'); pg.wait_for_timeout(300); pg.keyboard.press('Escape'); pg.wait_for_timeout(200)
    check('reopen from card 2 restores to card 2 control', pg.evaluate("document.activeElement===document.querySelectorAll('.btn-play')[1]"), str(A()))
    # detached trigger
    btns.nth(0).focus(); pg.keyboard.press('Enter'); pg.wait_for_timeout(300)
    pg.evaluate("document.querySelectorAll('.btn-play')[0].closest('.card, [class*=card]').remove()")
    pg.keyboard.press('Escape'); pg.wait_for_timeout(200)
    a = A(); check('detached invoker -> fallback visible control focused (search input)', a['id'] == 'search' and a['visible'], str(a))
    # pointer open (click) still works and restores
    btns.nth(0).click(); pg.wait_for_timeout(300); check('mouse open works + focus inside', opens() and A()['inDialog'])
    pg.keyboard.press('Escape'); pg.wait_for_timeout(200); check('mouse open/close restores invoker', pg.evaluate("document.activeElement===document.querySelectorAll('.btn-play')[0]"), str(A()))
    b.close()
print('RESULT', 'FAILS: ' + ', '.join(fails) if fails else 'ALL OK')
if expect_fail: sys.exit(0 if fails else 1)
sys.exit(1 if fails else 0)
