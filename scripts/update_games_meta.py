#!/usr/bin/env python3
"""Refresh size/hash in games.json for the given games from the exact files that
will be published. hash = first 16 hex of sha256 of the file bytes (same as
process_games.py, which hashes the utf-8 text). originalSize is left alone.
Also recomputes stats.totalSizeBytes/MB as the sum of per-game sizes.
Usage: update_games_meta.py GAMES_JSON GAMES_DIR slug [slug ...]"""
import hashlib, json, os, sys

path, gdir, slugs = sys.argv[1], sys.argv[2], sys.argv[3:]
raw = json.load(open(path, encoding='utf-8'))
games = raw['games'] if isinstance(raw, dict) else raw
by = {g['slug']: g for g in games}
for s in slugs:
    b = open(os.path.join(gdir, s + '.html'), 'rb').read()
    g = by[s]
    print(s, g['size'], '->', len(b), g['hash'], '->', hashlib.sha256(b).hexdigest()[:16])
    g['size'], g['hash'] = len(b), hashlib.sha256(b).hexdigest()[:16]
if isinstance(raw, dict) and 'stats' in raw:
    tot = sum(g['size'] for g in games)  # stats were the exact sum of per-game sizes
    raw['stats']['totalSizeBytes'], raw['stats']['totalSizeMB'] = tot, round(tot / 1e6, 1)
json.dump(raw, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
