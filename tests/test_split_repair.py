"""Tests for scripts/repair_split_games.py and the player's withAssetBase().
Browser cases need google-chrome; they are skipped when it is absent.
Run: python3 -m unittest tests/test_split_repair.py"""
import http.server, os, re, shutil, socketserver, subprocess, sys, tempfile, threading, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import repair_split_games as R

CHROME = shutil.which('google-chrome') or shutil.which('chromium')


def write(d, name, data):
    os.makedirs(os.path.dirname(os.path.join(d, name)), exist_ok=True)
    open(os.path.join(d, name), 'w').write(data)


def serve(d, status_for=None):
    status_for = status_for or {}
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(s, *a, **k): super().__init__(*a, directory=d, **k)
        def log_message(s, *a): pass
        def do_GET(s):
            code = status_for.get(s.path.lstrip('/'))
            if code:
                s.send_response(code); s.end_headers(); s.wfile.write(b'window.__ran_bad=1;'); return
            super().do_GET()
    srv = socketserver.TCPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def dom(url):
    r = subprocess.run([CHROME, '--headless=new', '--no-sandbox', '--disable-gpu', '--virtual-time-budget=8000',
                        '--dump-dom', url], capture_output=True, timeout=60)
    return r.stdout.decode('utf-8', 'replace')


class Classify(unittest.TestCase):
    def repair(self, first_chunk, rest=''):
        d = tempfile.mkdtemp()
        write(d, 'assets/t.code0.js', first_chunk)
        if rest: write(d, 'assets/t.code1.js', rest)
        tags = '<script src="assets/t.code0.js"></script>' + ('<script src="assets/t.code1.js"></script>' if rest else '')
        write(d, 't.html', '<html><head>%s</head></html>' % tags)
        R.SKIPPED.clear(); R.main(d)
        return open(os.path.join(d, 't.html')).read()

    def test_single_quoted_data_is_data(self):
        t = self.repair("<script type='application/octet-stream' id='data'>AA", 'BB')
        self.assertIn('textContent', t); self.assertNotIn('createElement', t)

    def test_unquoted_unknown_type_is_data(self):
        t = self.repair('<script type=text/x-foo id=z>QQ')
        self.assertIn('textContent', t); self.assertNotIn('createElement', t)

    def test_classic_js_runs_as_one_script(self):
        t = self.repair('<script type="text/javascript">var a=', '1;')
        self.assertIn('createElement', t)

    def test_module_and_defer_left_untouched(self):
        for first in ['<script type="module" id="m">export const a=1;', '<script defer>x=1;']:
            t = self.repair(first, 'y=2;')
            self.assertIn('assets/t.code0.js', t); self.assertNotIn('createElement', t)
            self.assertTrue(R.SKIPPED)

    def test_idempotent(self):
        d = tempfile.mkdtemp()
        write(d, 'assets/t.code0.js', '<script>var a=1;'); write(d, 't.html', '<script src="assets/t.code0.js"></script>')
        R.main(d); a = open(os.path.join(d, 't.html')).read(); R.main(d)
        self.assertEqual(a, open(os.path.join(d, 't.html')).read())


@unittest.skipUnless(CHROME, 'chrome not available')
class Browser(unittest.TestCase):
    def build(self):
        d = tempfile.mkdtemp()
        write(d, 'assets/t.code0.js', '<script>window.__a=1;')
        write(d, 'assets/t.code1.js', 'window.__ran_ok=(window.__a===1);')
        write(d, 'assets/t.code2.js', '<script type="application/octet-stream" id="blob">DATA')
        write(d, 't.html', '<html><head><script src="assets/t.code0.js"></script><script src="assets/t.code1.js"></script>'
              '<script src="assets/t.code2.js"></script></head><body><pre id="o"></pre><script>'
              'document.getElementById("o").textContent="RAN="+window.__ran_ok+";BAD="+window.__ran_bad+";BLOB="+(document.getElementById("blob")||{}).textContent;'
              '</script></body></html>')
        R.main(d); return d

    def test_positive(self):
        d = self.build(); s = serve(d)
        out = dom('http://127.0.0.1:%d/t.html' % s.server_address[1]); s.shutdown()
        self.assertIn('RAN=true', out); self.assertIn('BLOB=DATA', out)

    def test_hostile_404_and_500_fail_closed(self):
        for code in (404, 500):
            d = self.build(); s = serve(d, {'assets/t.code1.js': code})
            out = dom('http://127.0.0.1:%d/t.html' % s.server_address[1]); s.shutdown()
            self.assertNotIn('RAN=true', out, code)   # no partial boot
            self.assertNotIn('BAD=1', out, code)      # error body never executed
            self.assertIn('RAN=undefined', out, code)


@unittest.skipUnless(CHROME, 'chrome not available')
class Base(unittest.TestCase):
    def test_base_guard_dom(self):
        import json
        out = dom('file://' + os.path.join(ROOT, 'tests', 'base_guard_dom.html'))
        res = json.loads(re.search(r'RESULT (\{.*?\})</pre>', out, re.S).group(1))
        want = 'https://cdn.example/gh/x@main/games/'
        for k in ('plain', 'commentedBase', 'commentedHeadFirst', 'scriptStringBase'):
            self.assertEqual(res[k]['bases'], [want], k)       # exactly one injected base
            self.assertEqual(res[k]['inHead'], 1, k)
        self.assertEqual(res['realBase']['bases'], ['https://real/'])  # real base is kept
        self.assertFalse(res['realBase']['changed'])
        self.assertFalse(res['noAssets']['changed'])


if __name__ == '__main__':
    unittest.main()
