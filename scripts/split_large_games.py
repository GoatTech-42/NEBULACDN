#!/usr/bin/env python3
"""split_large_games.py - make >20MB single-file games servable through jsDelivr.

jsDelivr refuses individual files over ~20MB. Three known packing patterns get
rewritten so the HTML shell stays small and the bulk moves to chunked assets
under games/assets/ (each chunk safely under the limit):

A. Eaglercraft-style: giant data:application/octet-stream;base64 blobs that the
   in-page loader retrieves via fetch(). Blob is extracted; if it exceeds the
   limit it is split into byte chunks and a tiny fetch() interceptor (injected
   before the game script) reassembles it at load time.
B. compressedString-style: one giant single-quoted JS string literal consumed
   synchronously. Payload moves into external chunk scripts (each a string
   piece, pushed onto a global array) loaded before the inline script; the
   literal is replaced with a join of the pieces.
C. TurboWarp/Scratch-style: thousands of <script data="...">decodeChunk(65536)
   </script> tags. The base85 payloads move to external text parts, reassembled
   by an inline loader that feeds each original chunk to the same decoder.

Files that match no known pattern are left untouched and reported.

Usage: python3 scripts/split_large_games.py [file ...]   (default: all >20MB)
"""
import json, base64
import os
import re
import sys
import subprocess

GAMES = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'games')
LIMIT = 19_000_000          # keep every emitted file under jsDelivr's ~20MB wall
CHUNK = 15_000_000          # raw chunk size (bytes for blobs, chars for strings)
SEP = '\n@@NBCHUNK@@\n'

OCT_RE = re.compile(r'data:application/(?:octet-stream|x-shockwave-flash);base64,([A-Za-z0-9+/=\s]{100000,})')
def find_big_string_literal(txt, min_len=1000000):
    """Locate `let|var|const NAME = '<huge payload>'` with a manual scan
    (regex backtracking over 50MB+ literals OOMs). Returns (var, start, end,
    payload) where start/end bracket the quoted literal INCLUDING quotes."""
    head_re = re.compile(r"(let|var|const)\s+([A-Za-z_$][\w$]*)\s*=\s*'")
    m = head_re.search(txt)
    while m:
        var = m.group(2)
        i = m.end() - 1  # position of opening quote
        j = i + 1
        n = len(txt)
        while j < n:
            c = txt[j]
            if c == chr(92):
                j += 2
                continue
            if c == "'":
                break
            j += 1
        if j < n and j - i > min_len:
            return var, i, j + 1, txt[i + 1:j]
        m = head_re.search(txt, j + 1 if j < n else n)
    return None
B85_RE = re.compile(r'<script data="([^"]{1000,})">decodeChunk\(65536\)</script>')

FETCH_SHIM = """<script>window.__NBCHUNKS=%s;(function(){var of=window.fetch.bind(window);window.fetch=function(u,o){try{var k=typeof u==='string'?u:(u&&u.url)||'';for(var key in window.__NBCHUNKS){if(k.indexOf(key)!==-1){return Promise.all(window.__NBCHUNKS[key].map(function(p){return of(p,{cache:'force-cache'}).then(function(r){return r.arrayBuffer()})})).then(function(bs){var n=0,i;for(i=0;i<bs.length;i++)n+=bs[i].byteLength;var out=new Uint8Array(n),off=0;for(i=0;i<bs.length;i++){out.set(new Uint8Array(bs[i]),off);off+=bs[i].byteLength}return new Response(out)})}}}catch(e){}return of(u,o)}})();</script>"""


def rel_assets(slug):
    return os.path.join(GAMES, 'assets')


def safe_cuts(s, size):
    """Split string s into <=size pieces, never right after an odd backslash run
    (which would split a JS escape across chunk literals)."""
    out, i, n = [], 0, len(s)
    while i < n:
        j = min(i + size, n)
        if j < n:
            k = j
            while k > i:
                bs = 0
                p = k - 1
                while p >= 0 and s[p] == '\\':
                    bs += 1
                    p -= 1
                if bs % 2 == 0:
                    break
                k -= 1
            if k == i:
                k = j  # pathological; accept the split
            j = k
        out.append(s[i:j])
        i = j
    return out


def pattern_a(slug, txt):
    """Extract octet-stream blobs. Returns (new_txt, manifest, files) or None."""
    blobs = list(OCT_RE.finditer(txt))
    if not blobs:
        return None
    # Only safe when the page fetches the blob (eaglercraft loader marker).
    low = txt.lower()
    if not re.search(r'fetch|assetsuri|ruffle|xmlhttprequest|createobjecturl', low):
        return None
    manifest = {}
    files = {}          # relpath -> bytes
    segs = []
    prev = 0
    for i, m in enumerate(blobs):
        raw = base64.b64decode(re.sub(r'\s', '', m.group(1)))
        base = f'assets/{slug}.blob{i}.bin'
        if len(raw) <= LIMIT:
            files[base] = raw
        else:
            names = []
            for c, o in enumerate(range(0, len(raw), CHUNK)):
                p = f'{base}.{c}'
                files[p] = raw[o:o + CHUNK]
                names.append(p)
            manifest[base] = names
        del raw
        segs.append(txt[prev:m.start()])
        segs.append(base)
        prev = m.end()
    segs.append(txt[prev:])
    out = ''.join(segs)
    del segs, blobs
    if manifest:
        shim = FETCH_SHIM % __import__('json').dumps(manifest)
        j = out.find('<script')
        out = out[:j] + shim + out[j:]
    return out, files


def pattern_b(slug, txt):
    m = find_big_string_literal(txt)
    if not m:
        return None
    var, lit_start, lit_end, payload = m
    pieces = safe_cuts(payload, CHUNK)
    arr = f'__NBSTR_{var}'
    files = {}
    tags = []
    for i, piece in enumerate(pieces):
        p = f'assets/{slug}.str{i}.js'
        files[p] = (f"window.{arr}=(window.{arr}||[]);window.{arr}.push('" + piece + "');\n").encode()
        tags.append(f'<script src="{p}"></script>')
    out = txt[:lit_start] + f'window.{arr}.join("")' + txt[lit_end:]
    anchor = out.rfind('<script', 0, lit_start)
    if anchor == -1:
        return None
    out = out[:anchor] + ''.join(tags) + out[anchor:]
    return out, files


def pattern_c(slug, txt):
    runs = list(B85_RE.finditer(txt))
    if len(runs) < 3:
        return None
    attrs = [m.group(1) for m in runs]
    joined = SEP.join(attrs)
    parts = []
    buf, cur = [], 0
    for a in attrs:
        add = len(a) + (len(SEP) if buf else 0)
        if cur + add > LIMIT and buf:
            parts.append(SEP.join(buf))
            buf, cur = [], 0
        buf.append(a)
        cur += add
    if buf:
        parts.append(SEP.join(buf))
    files = {}
    for i, p in enumerate(parts):
        files[f'assets/{slug}.b85.{i}'] = p.encode()
    urls = ','.join(f"'assets/{slug}.b85.{i}'" for i in range(len(parts)))
    sep_js = "'" + SEP.replace('\\', '\\\\').replace('\n', '\\n') + "'"
    loader = ("<script>(function(){function g(u){var x=new XMLHttpRequest();x.open('GET',u,false);"
              "x.send(null);if(x.status<200||x.status>299)throw new Error('nebula chunk '+u+' HTTP '+x.status);return x.responseText}var t=[" + urls + "].map(g).join(" + sep_js + ").split(" + sep_js + ");"
              "for(var i=0;i<t.length;i++){base85decode(t[i],projectDecodeBuffer,projectDecodeIndex);projectDecodeIndex+=65536}})();</script>")
    out = txt[:runs[0].start()] + loader + txt[runs[-1].end():]
    return out, files




PAYLOAD_RE = re.compile(r'(<script id="payload"[^>]*>)([A-Za-z0-9+/=\s]{1000000,})(</script>)')


def pattern_e(slug, txt):
    """Eaglercraft payload-element packs: <script id="payload">B64</script> in
    <head>, read later by a body script via textContent. Externalize the base64
    into chunk files; an injector right after the element refills textContent
    with synchronous XHR before the reader runs."""
    m = PAYLOAD_RE.search(txt)
    if not m:
        return None
    payload = m.group(2)
    pieces = [payload[o:o + CHUNK] for o in range(0, len(payload), CHUNK)]
    files = {}
    urls = []
    for i, piece in enumerate(pieces):
        p = f'assets/{slug}.payload.{i}'
        files[p] = piece.encode()
        urls.append(p)
    url_js = ','.join(f"'{u}'" for u in urls)
    injector = ("<script>(function(){function g(u){var x=new XMLHttpRequest();x.open('GET',u,false);"
                "x.send(null);if(x.status<200||x.status>299)throw new Error('nebula chunk '+u+' HTTP '+x.status);return x.responseText}document.getElementById('payload').textContent=["
                + url_js + "].map(g).join('')})();</script>")
    # Close the payload element first; an injector placed inside it is parsed as
    # text and never runs.
    out = txt[:m.end(1)] + '</script>' + injector + txt[m.end(3):]
    return out, files


ATTR_RE = re.compile(r"([\w:-]+)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>\"']+)))?")
JS_TYPES = ('', 'text/javascript', 'application/javascript', 'application/x-javascript', 'text/ecmascript', 'module')


UNSUPPORTED = {'defer', 'async', 'nomodule', 'integrity', 'nonce', 'crossorigin', 'referrerpolicy', 'src'}


def parse_attrs(tag):
    return {m.group(1).lower(): next((v for v in m.groups()[1:] if v is not None), '') for m in ATTR_RE.finditer(tag)}


def is_js_tag(tag):
    a = parse_attrs(tag)
    t = a.get('type', '').strip().lower().split(';')[0]
    # Only classic JS can be split and re-run by the loader. Data scripts, modules
    # and defer/async/integrity/etc. forms are left untouched.
    return t in JS_TYPES and t != 'module' and not (UNSUPPORTED & set(a))

BIG_SCRIPT_RE = re.compile(r'<script(?![^>]*\bsrc\b)[^>]*>')


def pattern_d(slug, txt):
    """Fallback: plain-code inline scripts (e.g. a ModAPI dump) too big for the
    CDN. Externalize inline scripts largest-first (splitting big ones at
    newline boundaries into ordered chunk scripts) until the file fits.
    Execution order is preserved; scripts read via innerHTML are skipped."""
    if len(txt.encode('utf-8', errors='replace')) <= LIMIT:
        return None
    spans = []
    for m in BIG_SCRIPT_RE.finditer(txt):
        close = txt.find('</script>', m.end())
        if close == -1:
            break
        size = close - m.end()
        if size >= 4_000_000:
            body = txt[m.end():close]
            if re.search(r'currentScript\s*\.\s*(textContent|innerHTML|text|src)', body):
                continue
            spans.append((m.start(), close + len('</script>'), size, m.start()))
    if not spans:
        return None
    spans.sort(key=lambda t: -t[2])
    total = len(txt.encode('utf-8', errors='replace'))
    files = {}
    replacements = {}
    counter = 0
    for start, end, size, tag_end in spans:
        if total <= 19_000_000 and counter > 0:
            break
        open_end = txt.find('>', tag_end) + 1
        if not is_js_tag(txt[tag_end:open_end]):
            continue  # data script (any non-JS type) read by id; leave untouched
        body = txt[open_end:end - len('</script>')]
        if re.search(r'getElementById\([^)]*\)\s*\.\s*innerHTML', body):
            continue
        pieces = []
        i = 0
        while i < len(body):
            j = min(i + CHUNK, len(body))
            if j < len(body):
                nl = body.rfind('\n', i, j)
                if nl > i:
                    j = nl + 1
            pieces.append(body[i:j])
            i = j
        urls = []
        for piece in pieces:
            p = f'assets/{slug}.code{counter}.js'
            counter += 1
            files[p] = piece.encode('utf-8', errors='replace')
            urls.append(p)
        # Chunks are fragments of ONE script, so join them and run once.
        attrs = parse_attrs(txt[tag_end:open_end])
        loader = ("<script>(function(){function g(u){var x=new XMLHttpRequest();x.open('GET',u,false);"
                  "x.send(null);if(x.status<200||x.status>299)throw new Error('nebula chunk '+u+' HTTP '+x.status);return x.responseText}var t=" + json.dumps(urls) + ".map(g).join('');"
                  "var s=document.createElement('script');var a=" + json.dumps(attrs) +
                  ";for(var k in a)s.setAttribute(k,a[k]);s.text=t;"
                  "document.currentScript.parentNode.insertBefore(s,document.currentScript)})();</script>")
        replacements[(start, end)] = loader
        total -= size - len(loader)
    if not replacements:
        return None
    out = []
    prev = 0
    for (start, end) in sorted(replacements):
        out.append(txt[prev:start])
        out.append(replacements[(start, end)])
        prev = end
    out.append(txt[prev:])
    return ''.join(out), files




OPTION_RE = re.compile(r'(<option[^>]*?)value="([A-Za-z0-9+/=]{100000,})"')


def pattern_f(slug, txt):
    """Launcher packs: giant base64 in <option value=...>, read via .value at
    game start. Externalize each giant value into chunk files; an injector
    refills the tagged option values via synchronous XHR before any click."""
    matches = list(OPTION_RE.finditer(txt))
    if not matches:
        return None
    files = {}
    out = []
    prev = 0
    inject = []
    n = 0
    for m in matches:
        payload = m.group(2)
        pieces = [payload[o:o + CHUNK] for o in range(0, len(payload), CHUNK)]
        urls = []
        for piece in pieces:
            p = f'assets/{slug}.pack{n}'
            files[p] = piece.encode()
            urls.append(p)
            n += 1
        inject.append(urls)
        tag = m.group(1) + f'data-nbp="{len(inject)-1}" value="'
        out.append(txt[prev:m.start()])
        out.append(tag)
        prev = m.end(2)
    out.append(txt[prev:])
    body = ''.join(out)
    idx = body.find('</select>')
    if idx == -1:
        return None
    arr = '[' + ','.join('[' + ','.join('"%s"' % u for u in urls) + ']' for urls in inject) + ']'
    injector = ('<script>(function(){function g(u){var x=new XMLHttpRequest();x.open("GET",u,false);'
                'x.send(null);if(x.status<200||x.status>299)throw new Error("nebula chunk "+u+" HTTP "+x.status);return x.responseText}var d=' + arr + ';'
                'document.querySelectorAll("option[data-nbp]").forEach(function(o){'
                'o.value=d[+o.getAttribute("data-nbp")].map(g).join("")})})();</script>')
    body = body[:idx + len('</select>')] + injector + body[idx + len('</select>'):]
    return body, files


def process(path):
    slug = os.path.basename(path)[:-5]
    txt = open(path, encoding='utf-8', errors='replace').read()
    if len(txt.encode('utf-8', errors='replace')) <= LIMIT:
        return None
    cur = txt
    all_files = {}
    used = []
    for fn in (pattern_a, pattern_b, pattern_c, pattern_e, pattern_f, pattern_d):
        if len(cur.encode('utf-8', errors='replace')) <= LIMIT:
            break
        r = fn(slug, cur)
        if r:
            cur, files = r
            all_files.update(files)
            used.append(fn.__name__)
    if not used:
        return ('NOMATCH', 0, 0)
    adir = rel_assets(slug)
    os.makedirs(adir, exist_ok=True)
    for rel, data in all_files.items():
        with open(os.path.join(GAMES, rel), 'wb') as fh:
            fh.write(data)
    open(path, 'w', encoding='utf-8').write(cur)
    return ('+'.join(used), len(all_files), sum(len(v) for v in all_files.values()))


def main():
    if len(sys.argv) > 1:
        targets = sys.argv[1:]
        if len(targets) > 1 and os.environ.get('NB_SPLIT_CHILD') != '1':
            # batch mode: one subprocess per file so a memory spike kills one file, not the run
            env = dict(os.environ, NB_SPLIT_CHILD='1')
            for t in targets:
                subprocess.run([sys.executable, os.path.abspath(__file__), t], env=env)
            return
    else:
        targets = [os.path.join(GAMES, f) for f in os.listdir(GAMES)
                   if f.endswith('.html') and os.path.getsize(os.path.join(GAMES, f)) > 20_000_000]
    for path in sorted(targets):
        slug = os.path.basename(path)
        before = os.path.getsize(path)
        r = process(path)
        if r is None:
            print(f'SKIP   {slug} (already small)')
        elif r[0] == 'NOMATCH':
            print(f'NOMATCH {slug} ({before/1e6:.1f}MB) - needs manual pattern analysis')
        else:
            after = os.path.getsize(path)
            print(f'{r[0]:10s} {slug}: html {before/1e6:.1f}MB -> {after/1e6:.2f}MB, {r[1]} asset files ({r[2]/1e6:.1f}MB)')


if __name__ == '__main__':
    main()
