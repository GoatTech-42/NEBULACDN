#!/usr/bin/env python3
"""Live CDN availability check for NEBULA CDN.

Read-only. For each games.json entry, requests cdn_base + "/" + file (HEAD,
falling back to GET when HEAD is refused or gives no length) and records the
HTTP status, bytes received vs the catalog size, and a verdict:
ok / http-error / size-mismatch / unreachable.

Prints a plain-text report (counts first, then each problem entry).

Site-safe behavior:
  - at most 4 concurrent requests and at most 2 requests/second overall
  - one retry on timeout, no other retries
  - on HTTP 429, any Retry-After header, or a captcha/challenge/bot-check
    response the run stops immediately, reports what was seen, and does not
    retry or work around it
  - default python urllib user agent, no header spoofing
Meant for occasional manual runs (use --limit for samples), not for CI.

Usage:
  python3 scripts/check_cdn_live.py [--repo PATH] [--limit N]

Exit code: 0 all ok, 1 any problem (or stopped early), 2 games.json unreadable.
Standard library only.
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

MAX_WORKERS = 4
MAX_RPS = 2.0
TIMEOUT = 30
CHALLENGE_MARKERS = (b"captcha", b"cf-challenge", b"challenge-platform",
                     b"bot check", b"bot-check", b"verify you are human",
                     b"attention required")


class CatalogError(Exception):
    pass


class StopRun(Exception):
    """Rate-limit or challenge signal: stop the whole run."""


def load_catalog(repo):
    path = os.path.join(repo, "games.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        games = data["games"] if isinstance(data, dict) else data
        if not isinstance(games, list):
            raise ValueError("'games' must be a list")
        base = data.get("cdn_base") if isinstance(data, dict) else None
        if not isinstance(base, str) or not base:
            raise ValueError("missing cdn_base")
        for i, e in enumerate(games):
            if not isinstance(e, dict) or not isinstance(e.get("file"), str) \
                    or not e["file"]:
                raise ValueError("entry #{} has no usable 'file'".format(i))
    except (OSError, ValueError, KeyError) as exc:
        raise CatalogError("cannot read games.json: {}".format(exc))
    return base.rstrip("/"), games


class RateLimiter:
    """Global cap of `rps` request starts per second across all threads."""

    def __init__(self, rps, clock=time.monotonic, sleep=time.sleep):
        self.interval = 1.0 / rps
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()
        self.next_slot = 0.0

    def wait(self):
        with self.lock:
            now = self.clock()
            slot = max(now, self.next_slot)
            self.next_slot = slot + self.interval
        if slot > now:
            self.sleep(slot - now)


def http_fetch(url, method):
    """Return (status, headers_dict_lowercase, body_len, body_head).
    Raises OSError/TimeoutError for network trouble."""
    req = urllib.request.Request(url, method=method)  # default urllib UA
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        resp = exc
    with resp:
        headers = {k.lower(): v for k, v in resp.headers.items()}
        status = resp.status if hasattr(resp, "status") else resp.code
        if method == "HEAD":
            return status, headers, None, b""
        total, head = 0, b""
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            if len(head) < 4096:
                head += chunk[:4096 - len(head)]
            total += len(chunk)
        return status, headers, total, head


def check_stop_signals(status, headers, head):
    if status == 429:
        raise StopRun("HTTP 429 received")
    if "retry-after" in headers:
        raise StopRun("Retry-After header received: {}".format(
            headers["retry-after"]))
    if headers.get("cf-mitigated"):
        raise StopRun("challenge response (cf-mitigated: {})".format(
            headers["cf-mitigated"]))
    low = head.lower()
    if status != 200 and any(m in low for m in CHALLENGE_MARKERS):
        raise StopRun("captcha/challenge page detected (HTTP {})".format(status))


def request_once(url, method, fetch, stop):
    """One request with at most one retry on timeout."""
    for attempt in (0, 1):
        if stop.is_set():
            raise StopRun("stopped")
        try:
            result = fetch(url, method)
        except (TimeoutError, OSError) as exc:
            timed_out = isinstance(exc, TimeoutError) or "timed out" in str(exc)
            if timed_out and attempt == 0:
                continue
            raise
        check_stop_signals(result[0], result[1], result[3])
        return result


def check_entry(entry, base, fetch, limiter, stop):
    url = base + "/" + urllib.parse.quote(entry["file"])
    rec = {"id": entry.get("id"), "file": entry["file"], "url": url,
           "status": None, "bytes": None, "expected": entry.get("size"),
           "verdict": None, "note": ""}
    try:
        limiter.wait()
        status, headers, nbytes, head = request_once(url, "HEAD", fetch, stop)
        length = headers.get("content-length")
        if status != 200 or length is None or not length.isdigit():
            limiter.wait()
            status, headers, nbytes, head = request_once(url, "GET", fetch, stop)
        else:
            nbytes = int(length)
    except StopRun:
        raise
    except Exception as exc:  # network failure after the allowed retry
        rec["verdict"] = "unreachable"
        rec["note"] = "{}: {}".format(type(exc).__name__, exc)
        return rec
    rec["status"], rec["bytes"] = status, nbytes
    if status != 200:
        rec["verdict"] = "http-error"
    elif rec["expected"] is not None and nbytes != rec["expected"]:
        rec["verdict"] = "size-mismatch"
    else:
        rec["verdict"] = "ok"
    return rec


def run(repo, limit=None, fetch=http_fetch, limiter=None):
    base, games = load_catalog(repo)
    if limit is not None:
        games = games[:max(limit, 0)]
    limiter = limiter or RateLimiter(MAX_RPS)
    stop = threading.Event()
    stop_reason = []
    results = []

    def work(entry):
        try:
            return check_entry(entry, base, fetch, limiter, stop)
        except StopRun as exc:
            if not stop.is_set():
                stop_reason.append(str(exc))
            stop.set()
            return None

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for rec in pool.map(work, games):
            if rec is not None:
                results.append(rec)
    return games, results, (stop_reason[0] if stop_reason else None)


VERDICTS = ["ok", "http-error", "size-mismatch", "unreachable"]


def report(games, results, stop_reason):
    counts = {v: 0 for v in VERDICTS}
    for r in results:
        counts[r["verdict"]] += 1
    lines = ["NEBULA CDN live availability report", "=" * 35,
             "Entries selected:     {}".format(len(games)),
             "Entries checked:      {}".format(len(results))]
    problems = [r for r in results if r["verdict"] != "ok"]
    lines.append("Problems found:       {}".format(len(problems)))
    if stop_reason:
        lines.append("RUN STOPPED EARLY:    {}".format(stop_reason))
        lines.append("  (not retried or worked around; {} entries unchecked)"
                     .format(len(games) - len(results)))
    lines += ["", "Counts by verdict:"]
    for v in VERDICTS:
        lines.append("  {:<16} {}".format(v, counts[v]))
    for v in VERDICTS[1:]:
        items = [r for r in results if r["verdict"] == v]
        if not items:
            continue
        lines += ["", "{} ({})".format(v.upper(), len(items))]
        for r in items:
            lines.append("  - id={!r} file={!r} status={} bytes={} expected={}{}"
                         .format(r["id"], r["file"], r["status"], r["bytes"],
                                 r["expected"],
                                 " " + r["note"] if r["note"] else ""))
    return "\n".join(lines)


def main(argv=None, fetch=http_fetch, limiter=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser.add_argument("--repo", default=default_repo,
                        help="path to the NEBULACDN checkout (default: this repo)")
    parser.add_argument("--limit", type=int, default=None,
                        help="only check the first N catalog entries")
    args = parser.parse_args(argv)
    try:
        games, results, stop_reason = run(args.repo, args.limit, fetch, limiter)
    except CatalogError as exc:
        sys.stderr.write("error: {}\n".format(exc))
        return 2
    print(report(games, results, stop_reason))
    bad = stop_reason or any(r["verdict"] != "ok" for r in results)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
