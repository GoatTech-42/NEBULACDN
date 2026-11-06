#!/usr/bin/env python3
"""Asset provenance and license-risk scan for NEBULA CDN.

Read-only. Reads games.json and every games/*.html wrapper, extracts the
external asset URLs each wrapper loads, groups them by source, and writes a
markdown report: totals, per-source counts, and a shortlist of the riskiest
entries (well-known commercial games, or ROMs/emulators, served from
third-party hosts).

What counts as "loaded":
  - <base href>, and relative src/href values resolved against it
  - src= on script, iframe, img, audio, video, source, embed, track;
    data= on object; href= on <link> (parsed with html.parser, so quoted
    and unquoted attribute values both work)
  - fetch(), XMLHttpRequest.open(), import(), new Worker() and quoted
    absolute URLs that point at a CDN or end in an asset extension, found
    in inline <script> bodies only
  - url(...) in <style> bodies and style="" attributes

Not counted: links in HTML comments, XML namespaces, plain <a href>
navigation links, and data-src or other non-loading attributes.

This is static analysis of the markup, not runtime certainty: it cannot see
URLs built at runtime, and it may flag URLs a page never actually loads.

A "source" is the host, except for jsDelivr / githack / raw.githubusercontent
GitHub mirrors (also github.com/o/r/raw/..., fastly/gcore jsdelivr, rawcdn
githack), where it is host/owner/repo so each upstream repo is its own
source and the report keeps the original host. Any mirror of
GoatTech-42/NEBULACDN is the repo's own; a mirror of another owner/repo is
third-party.

Usage:
  python3 scripts/asset_provenance.py [--repo PATH] [--out FILE] [--json FILE] [--top N] [--force]

Prints the markdown report to stdout (or writes it to --out). Refuses to overwrite an existing --out/--json file unless --force is given. Standard
library only. Heuristic: the risk list is for human review, not a verdict.
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

# Repo identity (owner/repo, lowercase) of this repo. Any supported mirror of
# it counts as own, whatever the host.
OWN_REPO = "goattech-42/nebulacdn"

MIRROR_HOSTS = {
    "cdn.jsdelivr.net",
    "fastly.jsdelivr.net",
    "gcore.jsdelivr.net",
    "rawcdn.githack.com",
    "raw.githack.com",
    "raw.githubusercontent.com",
    "github.com",
}

# Hosts that are generic infrastructure (fonts, analytics, ads), reported but
# not treated as the source of game content.
INFRA_HOSTS = {
    "fonts.googleapis.com", "fonts.gstatic.com", "www.gstatic.com",
    "static.cloudflareinsights.com", "www.googletagmanager.com",
    "www.google-analytics.com", "pagead2.googlesyndication.com",
    "cdnjs.cloudflare.com", "unpkg.com", "code.jquery.com",
    "ajax.googleapis.com",
}

ASSET_EXT = re.compile(
    r"\.(js|mjs|wasm|data|pck|unityweb|json|css|swf|png|jpe?g|gif|webp|svg|"
    r"mp3|ogg|wav|m4a|mp4|webm|zip|7z|bin|rom|nes|smc|sfc|gba|gbc|gb|n64|z64|"
    r"v64|nds|md|gen|smd|sms|gg|pce|iso|cue|chd|ttf|woff2?)(\?|#|$)",
    re.I,
)

ROM_EXT = re.compile(
    r"\.(nes|smc|sfc|gba|gbc|gb|n64|z64|v64|nds|smd|sms|pce|iso|cue|chd)"
    r"(\?|#|$)", re.I)

EMULATOR_MARKERS = re.compile(
    r"EJS_core|EJS_gameUrl|emulatorjs|retroarch|libretro|jsnes|\bnes\.js|"
    r"gba\.js|mgba|snes9x|mupen64|webretro|drag(ging)? in the rom", re.I)

# Franchises / publishers whose titles are almost always commercial IP.
COMMERCIAL_TERMS = [
    "mario", "luigi", "peach", "yoshi", "wario", "donkey kong", "donkeykong",
    "zelda", "metroid", "kirby", "pokemon", "pokémon", "smash", "nintendo",
    "sonic", "sega", "tails", "knuckles", "pac-man", "pacman", "pac man",
    "galaga", "tetris", "street fighter", "streetfighter", "mortal kombat",
    "castlevania", "mega man", "megaman", "contra", "final fantasy",
    "earthbound", "minecraft", "fnaf", "five nights", "geometry dash",
    "subway surfers", "cut the rope", "angry birds", "plants vs",
    "crash bandicoot", "spyro", "doom", "half-life", "portal", "gta",
    "grand theft", "rocket league", "among us", "fortnite", "roblox",
    "undertale", "deltarune", "celeste", "hollow knight", "terraria",
    "cuphead", "bloons", "papa's", "papas", "fifa", "nba", "madden",
]

CALLS = re.compile(
    r"(?:fetch|import|new\s+Worker|\.open\s*\(\s*[\"'][A-Z]+[\"']\s*,)\s*\(?\s*"
    r"([\"'`])(.*?)\1", re.S)
CSS_URL = re.compile(r"url\(\s*([\"']?)([^\"')]+)\1\s*\)", re.I)
QUOTED_ABS = re.compile(r"([\"'`])((?:https?:)?//[^\s\"'`<>]+)\1")
COMMENT = re.compile(r"<!--.*?-->", re.S)
NAMESPACE_HOSTS = {"www.w3.org", "w3.org", "www.w3c.org"}


def safe_split(url):
    try:
        parts = urlsplit(url)
        return parts, (parts.hostname or "").lower()
    except ValueError:
        return None, ""


def repo_identity(source):
    """owner/repo (lowercase) of a mirror source string, else None."""
    segs = source.split("/")
    host = segs[0]
    if host not in MIRROR_HOSTS:
        return None
    if host.endswith("jsdelivr.net"):
        if len(segs) >= 4 and segs[1] == "gh":
            return "{}/{}".format(segs[2], segs[3])
        return None
    if len(segs) >= 3:
        return "{}/{}".format(segs[1], segs[2])
    return None


def source_of(url):
    """Source key: host, or host/owner/repo for GitHub mirrors.

    The display host is kept (cdn.jsdelivr.net and fastly.jsdelivr.net stay
    distinct); repo_identity() normalises across hosts for ownership checks.
    Supported mirror forms: github.com/o/r/raw/..., raw.githubusercontent.com
    /o/r/..., jsdelivr (cdn/fastly/gcore) /gh/o/r@ref/..., rawcdn.githack.com
    and raw.githack.com /o/r/....
    """
    parts, host = safe_split(url)
    if not host:
        return None
    if host in MIRROR_HOSTS:
        segs = [s for s in parts.path.split("/") if s]
        if host.endswith("jsdelivr.net"):
            if len(segs) >= 3 and segs[0] == "gh":
                return "{}/gh/{}/{}".format(host, segs[1], segs[2].split("@")[0]).lower()
            if len(segs) >= 2 and segs[0] == "npm":
                pkg = segs[1] if not segs[1].startswith("@") or len(segs) < 3 else segs[1] + "/" + segs[2]
                return "{}/npm/{}".format(host, pkg.rsplit("@", 1)[0] if not pkg.startswith("@") or pkg.count("@") > 1 else pkg).lower()
            return host
        if len(segs) >= 2:
            return "{}/{}/{}".format(host, segs[0], segs[1].split("@")[0]).lower()
    return host


def classify(source):
    if source is None:
        return "own"
    if repo_identity(source) == OWN_REPO:
        return "own"
    host = source.split("/")[0]
    if host in INFRA_HOSTS:
        return "infra"
    if host in MIRROR_HOSTS:
        return "third-party mirror"
    return "third-party host"


SRC_TAGS = {"script", "iframe", "img", "audio", "video", "source", "embed", "track"}


class AssetParser(HTMLParser):
    """Collects load references from real tag attributes.

    Reads src on SRC_TAGS, data on object, href on link and base. Plain
    <a href>, data-src and other attributes are ignored. The URL-pattern
    fallbacks (fetch(), import(), quoted absolute URLs) only run on inline
    <script> bodies, and url(...) only on <style> bodies and style="..."
    attributes. Comments are skipped by the parser.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.base = None
        self.attr_urls = []
        self.script_urls = []
        self.css_urls = []
        self._in = None
        self._buf = []

    def _start(self, tag, attrs):
        a = {}
        for k, v in attrs:
            if k.lower() not in a and v is not None:
                a[k.lower()] = v
        if tag in SRC_TAGS and "src" in a:
            self.attr_urls.append(a["src"])
        elif tag == "object" and "data" in a:
            self.attr_urls.append(a["data"])
        elif tag == "link" and "href" in a:
            self.attr_urls.append(a["href"])
        elif tag == "base" and "href" in a and self.base is None:
            self.base = a["href"].strip()
        if "style" in a:
            self.css_urls += [m.group(2) for m in CSS_URL.finditer(a["style"])]

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs)
        if tag in ("script", "style"):
            self._in, self._buf = tag, []

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs)

    def handle_data(self, data):
        if self._in:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if self._in == tag:
            body = "".join(self._buf)
            if tag == "script":
                self.script_urls += [m.group(2) for m in CALLS.finditer(body)]
                for m in QUOTED_ABS.finditer(body):
                    u = m.group(2)
                    host = safe_split(u if not u.startswith("//") else "https:" + u)[1]
                    if host in MIRROR_HOSTS or ASSET_EXT.search(u):
                        self.script_urls.append(u)
                self.css_urls += [m.group(2) for m in CSS_URL.finditer(body)]
            else:
                self.css_urls += [m.group(2) for m in CSS_URL.finditer(body)]
            self._in, self._buf = None, []


def extract_urls(html):
    """Static analysis of one wrapper: URLs it appears to load. Not runtime."""
    parser = AssetParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # malformed markup must not abort the scan
        pass
    base = parser.base
    raw = parser.attr_urls + parser.script_urls + parser.css_urls

    urls = set()
    if base and base.startswith(("http://", "https://", "//")):
        urls.add(base if not base.startswith("//") else "https:" + base)
    for u in raw:
        u = u.strip()
        if not u or u.startswith(("data:", "blob:", "javascript:", "#", "about:", "mailto:")):
            continue
        if "${" in u or "+" in u and not u.startswith("http"):
            continue
        if u.startswith("//"):
            u = "https:" + u
        elif not u.startswith(("http://", "https://")):
            if base and base.startswith(("http://", "https://", "//")):
                try:
                    u = urljoin(base if not base.startswith("//") else "https:" + base, u)
                except ValueError:
                    continue
            else:
                continue  # relative to the repo itself
        host = safe_split(u)[1]
        if not host or host in NAMESPACE_HOSTS:
            continue
        urls.add(u)
    return urls


def commercial_hits(*texts):
    """Whole-word matches on the game's name/slug/filename/title.

    Slugs are often run together ("donkeykong64"), so terms of 5+ letters
    also match as a prefix of a run-together token. Short terms ("nba",
    "gta", "doom") must stand alone to avoid hits like "dragonballadvance".
    """
    blob = " ".join(t for t in texts if t).lower()
    tokens = re.findall(r"[a-z0-9']+", blob.replace("-", " ").replace("_", " "))
    joined = " " + " ".join(tokens) + " "
    hits = set()
    for term in COMMERCIAL_TERMS:
        t = term.lower()
        compact = re.sub(r"[^a-z0-9']", "", t)
        if " " + t + " " in joined or (" " + compact + " ") in joined:
            hits.add(term)
        elif len(compact) >= 5 and any(tok.startswith(compact) for tok in tokens):
            hits.add(term)
        elif any(re.fullmatch(re.escape(compact) + r"\d+", tok) for tok in tokens):
            hits.add(term)
    return sorted(hits)


def scan(repo):
    with open(os.path.join(repo, "games.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    games = data.get("games", []) if isinstance(data, dict) else data
    by_file = {}
    for g in games:
        f = (g.get("file") or "").split("/")[-1]
        if f:
            by_file[f] = g

    games_dir = os.path.join(repo, "games")
    files = sorted(n for n in os.listdir(games_dir)
                   if n.lower().endswith(".html") and os.path.isfile(os.path.join(games_dir, n)))

    results = []
    for name in files:
        with open(os.path.join(games_dir, name), encoding="utf-8", errors="replace") as fh:
            html = fh.read()
        urls = extract_urls(html)
        sources = defaultdict(set)
        for u in urls:
            sources[source_of(u)].add(u)
        external = {s: us for s, us in sources.items() if classify(s) in ("third-party mirror", "third-party host")}
        entry = by_file.get(name, {})
        title_m = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
        title = title_m.group(1).strip() if title_m else ""
        roms = sorted(u for u in urls if ROM_EXT.search(u))
        emulator = bool(EMULATOR_MARKERS.search(html)) or bool(roms)
        hits = commercial_hits(entry.get("name"), entry.get("slug"), name[:-5],
                               entry.get("category"), title)
        score = 0
        if external:
            score += 1
            if hits:
                score += 3
            if emulator:
                score += 2
            if roms:
                score += 1
        results.append({
            "file": "games/" + name,
            "name": entry.get("name") or name[:-5],
            "category": entry.get("category", "(not in catalog)"),
            "title": title,
            "external": {s: sorted(us) for s, us in external.items()},
            "all_sources": sorted(s for s in sources if s),
            "emulator": emulator,
            "roms": roms,
            "commercial": hits,
            "score": score,
        })
    return games, results


def render(games, results, top):
    with_ext = [r for r in results if r["external"]]
    source_games = Counter()
    source_kind = {}
    for r in results:
        for s in r["all_sources"]:
            source_games[s] += 1
            source_kind[s] = classify(s)
    emu = [r for r in with_ext if r["emulator"]]
    commercial = [r for r in with_ext if r["commercial"]]
    risky = sorted((r for r in with_ext if r["score"] >= 4),
                   key=lambda r: (-r["score"], r["name"].lower()))

    out = []
    out.append("# NEBULA CDN asset provenance report")
    out.append("")
    out.append("Heuristic scan for human review; not a legal determination.")
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append("| Metric | Count |")
    out.append("|---|---|")
    out.append("| Catalog entries (games.json) | {} |".format(len(games)))
    out.append("| Wrapper files scanned (games/*.html) | {} |".format(len(results)))
    out.append("| Wrappers loading third-party assets | {} |".format(len(with_ext)))
    out.append("| ...of which emulator / ROM based | {} |".format(len(emu)))
    out.append("| ...of which match commercial franchise terms | {} |".format(len(commercial)))
    out.append("| High-risk shortlist (score >= 4) | {} |".format(len(risky)))
    out.append("| Distinct asset sources | {} |".format(len(source_games)))
    out.append("")
    out.append("## Sources by number of games")
    out.append("")
    out.append("| Source | Type | Games |")
    out.append("|---|---|---|")
    for s, n in source_games.most_common(top):
        out.append("| `{}` | {} | {} |".format(s, source_kind[s], n))
    if len(source_games) > top:
        out.append("| ... {} more sources | | |".format(len(source_games) - top))
    out.append("")
    out.append("## Riskiest entries")
    out.append("")
    out.append("Score: +1 third-party assets, +3 commercial franchise match, "
               "+2 emulator, +1 direct ROM URL.")
    out.append("")
    out.append("| Score | Game | File | Matched terms | Emulator/ROM | Third-party sources |")
    out.append("|---|---|---|---|---|---|")
    for r in risky[:top]:
        er = "ROM" if r["roms"] else ("emulator" if r["emulator"] else "")
        out.append("| {} | {} | `{}` | {} | {} | {} |".format(
            r["score"], r["name"], r["file"], ", ".join(r["commercial"]) or "-",
            er or "-", ", ".join("`%s`" % s for s in sorted(r["external"]))))
    if len(risky) > top:
        out.append("")
        out.append("... {} more high-risk entries (use --top to show more).".format(len(risky) - top))
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    p.add_argument("--out", help="write the markdown report here instead of stdout")
    p.add_argument("--top", type=int, default=25, help="rows per table (default 25)")
    p.add_argument("--json", help="also write per-game results as JSON to this path")
    p.add_argument("--force", action="store_true",
                   help="allow --out/--json to overwrite existing files")
    a = p.parse_args(argv)
    if not a.force:
        for path in (a.out, a.json):
            if path and os.path.exists(path):
                sys.stderr.write("error: {} exists; use --force to overwrite\n".format(path))
                return 2
    games, results = scan(a.repo)
    report = render(games, results, a.top)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
    else:
        print(report)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
