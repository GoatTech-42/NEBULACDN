#!/usr/bin/env python3
"""Repair games that split_large_games.py (older versions) split incorrectly.

Bug 1 (pattern d): an oversized inline <script> was cut into byte chunks that
were loaded as separate <script src> tags. A JS file cut mid-statement cannot
run as separate scripts, the first chunk still began with the literal opening
tag text, and non-JS data scripts (octet-stream payloads) were chunked as code.
Fix: one loader joins the chunks with synchronous XHR. Code goes into one
script element, data goes into the original (now empty) element's textContent.

Bug 2 (pattern e): the XHR injector was placed INSIDE <script id="payload">, so
it was parsed as text and never ran. Fix: close the payload element first.

Usage: repair_split_games.py GAMES_DIR   (GAMES_DIR holds <slug>.html + assets/)
Idempotent: already repaired files are left alone.
"""
import json, os, re, sys

TAG_RE = re.compile(r'<script src="(assets/[\w.-]+\.code\d+\.js)"></script>')
OPEN_RE = re.compile(r'^\s*<script([^>]*)>')
ATTR_RE = re.compile(r"([\w:-]+)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>\"']+)))?")
JS_TYPES = ('', 'text/javascript', 'application/javascript', 'application/x-javascript', 'text/ecmascript', 'module')


def parse_attrs(tag):
    return {m.group(1).lower(): next((v for v in m.groups()[1:] if v is not None), '') for m in ATTR_RE.finditer(tag)}
XHR = ("function g(u){var x=new XMLHttpRequest();x.open('GET',u,false);"
       "x.send(null);if(x.status<200||x.status>299)throw new Error('nebula chunk '+u+' HTTP '+x.status);return x.responseText}")
OLD_G = "x.send(null);return x.responseText}"
NEW_G = "x.send(null);if(x.status<200||x.status>299)throw new Error('nebula chunk '+u+' HTTP '+x.status);return x.responseText}"


UNSUPPORTED = {'defer','async','nomodule','integrity','nonce','crossorigin','referrerpolicy','src'}
SKIPPED = []


def head(path, n=400):
    with open(path, 'rb') as f:
        return f.read(n).decode('utf-8', 'replace')


def repair_code(gdir, txt):
    tags = list(TAG_RE.finditer(txt))
    if not tags:
        return txt
    groups = []  # list of (first_match, [urls], attrs, is_new)
    for m in tags:
        h = OPEN_RE.match(head(os.path.join(gdir, m.group(1))))
        if h or not groups:
            attrs = parse_attrs(h.group(1)) if h else {}
            groups.append([m, [m.group(1)], attrs, bool(h)])
        else:
            groups[-1][1].append(m.group(1))
    repl = {}
    skip = set()
    for m, urls, attrs, has_tag in groups:
        t = attrs.get('type', '').strip().lower().split(';')[0]
        # Dynamic insertion changes module/defer/currentScript semantics: only
        # classic JS and data scripts are supported. Leave the rest untouched.
        if (t == 'module' and has_tag) or (UNSUPPORTED & set(attrs)):
            SKIPPED.append((urls[0], 'unsupported script form: ' + (t or ','.join(sorted(UNSUPPORTED & set(attrs))))))
            skip.update(u for u in urls)
            continue
        arr = json.dumps(urls)
        strip = "t=t.replace(/^\\s*<script[^>]*>/,'');" if has_tag else ''
        is_data = attrs.get('type', '').strip().lower().split(';')[0] not in JS_TYPES  # any non-JS type is data, never executed
        if is_data:
            el = '<script %s></script>' % ' '.join('%s="%s"' % (k, v.replace('"', '&quot;')) for k, v in attrs.items())
            js = ("<script>(function(){" + XHR + "var t=" + arr + ".map(g).join('');" + strip +
                  "document.getElementById(%s).textContent=t})();</script>" % json.dumps(attrs.get('id', '')))
            repl[m.start()] = el + js
        else:
            js = ("<script>(function(){" + XHR + "var t=" + arr + ".map(g).join('');" + strip +
                  "var s=document.createElement('script');var a=" + json.dumps(attrs) +
                  ";for(var k in a)s.setAttribute(k,a[k]);s.text=t;"
                  "(document.currentScript.parentNode||document.head).insertBefore(s,document.currentScript)})();</script>")
            repl[m.start()] = js
    out, prev = [], 0
    for m in tags:
        out.append(txt[prev:m.start()])
        out.append(m.group(0) if m.group(1) in skip else repl.get(m.start(), ''))
        prev = m.end()
    out.append(txt[prev:])
    return ''.join(out)


PAY_RE = re.compile(r'(<script id="payload"[^>]*>)(<script>\(function\(\)\{function g\(u\).*?\}\)\(\);</script>)(</script>)', re.S)


def repair_payload(txt):
    return PAY_RE.sub(lambda m: m.group(1) + m.group(3) + m.group(2), txt)


def main(gdir):
    for fn in sorted(os.listdir(gdir)):
        if not fn.endswith('.html'):
            continue
        p = os.path.join(gdir, fn)
        txt = open(p, encoding='utf-8', errors='surrogateescape').read()
        new = repair_payload(repair_code(gdir, txt)).replace(OLD_G, NEW_G)
        if new != txt:
            open(p, 'w', encoding='utf-8', errors='surrogateescape').write(new)
            print('repaired', fn)
    for url, why in SKIPPED:
        print('SKIPPED', url, why)


if __name__ == '__main__':
    main(sys.argv[1])
