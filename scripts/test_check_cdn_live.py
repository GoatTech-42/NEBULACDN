#!/usr/bin/env python3
"""Tests for check_cdn_live.py with HTTP mocked. Standard library only.

Run: python3 scripts/test_check_cdn_live.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_cdn_live as cc  # noqa: E402


class NoWait:
    def wait(self):
        pass


def make_repo(test, games, raw=None):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    doc = {"cdn_base": "https://cdn.example/gh/o/r@main", "games": games}
    with open(os.path.join(tmp.name, "games.json"), "w") as fh:
        fh.write(raw if raw is not None else json.dumps(doc))
    return tmp.name


def games(n, size=10):
    return [{"id": str(i), "file": "games/%d.html" % i, "size": size}
            for i in range(n)]


def run_main(repo, fetch, extra=()):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cc.main(["--repo", repo] + list(extra), fetch=fetch,
                       limiter=NoWait())
    return code, out.getvalue(), err.getvalue()


def ok_fetch(url, method):
    if method == "HEAD":
        return 200, {"content-length": "10"}, None, b""
    return 200, {}, 10, b"x"


class Tests(unittest.TestCase):
    def test_all_ok_exits_zero(self):
        code, out, _ = run_main(make_repo(self, games(5)), ok_fetch)
        self.assertEqual(code, 0)
        self.assertIn("Problems found:       0", out)

    def test_http_error_and_size_mismatch_reported(self):
        def fetch(url, method):
            if url.endswith("/1.html"):
                return 404, {}, 0, b"nf"
            if url.endswith("/2.html"):
                return 200, {"content-length": "99"}, None, b""
            return ok_fetch(url, method)
        code, out, _ = run_main(make_repo(self, games(4)), fetch)
        self.assertEqual(code, 1)
        self.assertIn("HTTP-ERROR (1)", out)
        self.assertIn("SIZE-MISMATCH (1)", out)
        self.assertIn("bytes=99 expected=10", out)

    def test_head_refused_falls_back_to_get(self):
        calls = []

        def fetch(url, method):
            calls.append(method)
            if method == "HEAD":
                return 405, {}, None, b""
            return 200, {}, 10, b"x"
        code, _, _ = run_main(make_repo(self, games(1)), fetch)
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["HEAD", "GET"])

    def test_malformed_games_json_exits_two(self):
        code, _, err = run_main(make_repo(self, [], raw="{nope"), ok_fetch)
        self.assertEqual(code, 2)
        self.assertIn("games.json", err)

    def test_limit_honored(self):
        seen = []

        def fetch(url, method):
            seen.append(url)
            return ok_fetch(url, method)
        code, out, _ = run_main(make_repo(self, games(10)), fetch,
                                ["--limit", "3"])
        self.assertEqual(code, 0)
        self.assertEqual(len(set(seen)), 3)
        self.assertIn("Entries checked:      3", out)

    def test_429_stops_run_without_retry(self):
        seen = []

        def fetch(url, method):
            seen.append(url)
            return 429, {}, None, b""
        code, out, _ = run_main(make_repo(self, games(40)), fetch)
        self.assertEqual(code, 1)
        self.assertIn("RUN STOPPED EARLY", out)
        self.assertIn("429", out)
        # at most one in-flight request per worker, none repeated
        self.assertLessEqual(len(seen), cc.MAX_WORKERS)
        self.assertEqual(len(seen), len(set(seen)))

    def test_retry_after_header_stops_run(self):
        def fetch(url, method):
            return 503, {"retry-after": "30"}, None, b""
        code, out, _ = run_main(make_repo(self, games(20)), fetch)
        self.assertEqual(code, 1)
        self.assertIn("Retry-After", out)

    def test_challenge_page_stops_run(self):
        def fetch(url, method):
            return 403, {}, 50, b"<html>Please complete the CAPTCHA</html>"
        code, out, _ = run_main(make_repo(self, games(20)), fetch)
        self.assertEqual(code, 1)
        self.assertIn("RUN STOPPED EARLY", out)

    def test_one_retry_on_timeout_then_unreachable(self):
        calls = []

        def fetch(url, method):
            calls.append(method)
            raise TimeoutError("timed out")
        code, out, _ = run_main(make_repo(self, games(1)), fetch)
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 2)  # original + exactly one retry
        self.assertIn("UNREACHABLE (1)", out)

    def test_rate_limiter_spaces_requests(self):
        t = [0.0]
        sleeps = []
        rl = cc.RateLimiter(2.0, clock=lambda: t[0],
                            sleep=lambda s: sleeps.append(s))
        for _ in range(3):
            rl.wait()
        self.assertEqual(sleeps, [0.5, 1.0])


if __name__ == "__main__":
    unittest.main()
