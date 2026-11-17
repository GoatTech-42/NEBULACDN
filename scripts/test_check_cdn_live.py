#!/usr/bin/env python3
"""Tests for check_cdn_live.py with HTTP mocked. Standard library only.

Run: python3 scripts/test_check_cdn_live.py
"""
import contextlib
import http.server
import threading
import time
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

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


class CountingLimiter:
    def __init__(self, on_wait=None):
        self.waits = 0
        self.on_wait = on_wait

    def wait(self):
        self.waits += 1
        if self.on_wait:
            self.on_wait()


class LimiterRetryTests(unittest.TestCase):
    def test_normal_request_waits_once(self):
        lim = CountingLimiter()
        rec = cc.check_entry({"id": 1, "file": "a.html", "size": 1},
                             "http://x", lambda u, m: (200, {"content-length": "1"}, None, b""),
                             lim, threading.Event())
        self.assertEqual(rec["verdict"], "ok")
        self.assertEqual(lim.waits, 1)

    def test_timeout_retry_waits_again(self):
        lim = CountingLimiter()
        calls = []

        def fetch(url, method):
            calls.append(method)
            if len(calls) == 1:
                raise TimeoutError("timed out")
            return 200, {"content-length": "1"}, None, b""
        rec = cc.check_entry({"id": 1, "file": "a.html", "size": 1},
                             "http://x", fetch, lim, threading.Event())
        self.assertEqual(rec["verdict"], "ok")
        self.assertEqual(lim.waits, 2)

    def test_stop_during_wait_prevents_send(self):
        stop = threading.Event()
        lim = CountingLimiter(on_wait=stop.set)
        sent = []
        with self.assertRaises(cc.StopRun):
            cc.request_once("http://x/a", "HEAD",
                            lambda u, m: sent.append(m), stop, lim)
        self.assertEqual(sent, [])

    def test_loopback_retry_spacing(self):
        starts = []
        count = [0]

        class H(http.server.BaseHTTPRequestHandler):
            def do_HEAD(self):
                starts.append(time.monotonic())
                count[0] += 1
                if count[0] == 1:
                    time.sleep(0.12)
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", "1")
                    self.end_headers()
                except OSError:
                    pass

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        old = cc.TIMEOUT
        cc.TIMEOUT = 0.05
        env = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1",
                                           "no_proxy": "127.0.0.1"})
        env.start()
        try:
            url = "http://127.0.0.1:{}".format(srv.server_address[1])
            rec = cc.check_entry({"id": 1, "file": "a.html", "size": 1},
                                 url, cc.http_fetch, cc.RateLimiter(2.0),
                                 threading.Event())
        finally:
            env.stop()
            cc.TIMEOUT = old
            srv.shutdown()
            srv.server_close()
        self.assertEqual(rec["verdict"], "ok")
        self.assertEqual(len(starts), 2)
        self.assertGreaterEqual(starts[1] - starts[0], 0.49)


if __name__ == "__main__":
    unittest.main()
