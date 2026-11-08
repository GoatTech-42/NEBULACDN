#!/usr/bin/env python3
"""Regression tests for check_catalog.py. Standard library only.

Run: python3 -m unittest scripts/test_check_catalog.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_catalog  # noqa: E402


def entry(i, file=None, **kw):
    d = {"id": i, "slug": "g%s" % i, "name": "G%s" % i,
         "file": file if file is not None else "games/g%s.html" % i}
    d.update(kw)
    return d


class Base(unittest.TestCase):
    def make(self, catalog, files=("g1.html", "g2.html"), raw=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.makedirs(os.path.join(tmp.name, "games"))
        for f in files:
            open(os.path.join(tmp.name, "games", f), "w").close()
        with open(os.path.join(tmp.name, "games.json"), "w") as fh:
            fh.write(raw if raw is not None else json.dumps(catalog))
        return tmp.name

    def run_main(self, repo):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = check_catalog.main(["--repo", repo])
        return code, out.getvalue(), err.getvalue()

    def problems(self, repo):
        return check_catalog.check(repo)[2]


class HappyPath(Base):
    def test_clean_repo_exits_zero(self):
        repo = self.make({"games": [entry(1), entry(2)],
                          "stats": {"totalGames": 2}})
        code, out, _ = self.run_main(repo)
        self.assertEqual(code, 0)
        self.assertIn("Problems found:       0", out)

    def test_bare_list_catalog_ok(self):
        repo = self.make([entry(1), entry(2)])
        self.assertEqual(self.run_main(repo)[0], 0)


class Malformed(Base):
    def assert_rejected(self, repo):
        code, out, err = self.run_main(repo)
        self.assertNotEqual(code, 0)
        self.assertTrue(err.startswith("error:"), err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "")

    def test_empty_object(self):
        self.assert_rejected(self.make({}))

    def test_games_not_list(self):
        self.assert_rejected(self.make({"games": {"a": 1}}))
        self.assert_rejected(self.make({"games": "x"}))

    def test_entries_not_objects(self):
        self.assert_rejected(self.make({"games": [entry(1), "g2.html", 3, None]}))

    def test_file_empty_missing_or_nonstring(self):
        for bad in ("", "   "):
            self.assert_rejected(self.make({"games": [entry(1, file=bad)]}))
        e = entry(1)
        del e["file"]
        self.assert_rejected(self.make({"games": [e]}))
        for bad in (5, None, ["games/g1.html"], {"a": 1}, True):
            e = entry(1)
            e["file"] = bad
            self.assert_rejected(self.make({"games": [e]}))

    def test_invalid_json(self):
        self.assert_rejected(self.make(None, raw="{not json"))

    def test_missing_games_json(self):
        repo = self.make({"games": []})
        os.remove(os.path.join(repo, "games.json"))
        self.assert_rejected(repo)


class Detection(Base):
    def test_planted_missing_file(self):
        repo = self.make({"games": [entry(1), entry(2), entry(3)]})
        p = self.problems(repo)
        self.assertEqual(len(p["missing file"]), 1)
        self.assertEqual(self.run_main(repo)[0], 1)

    def test_case_mismatch(self):
        repo = self.make({"games": [entry(1, file="games/G1.html")]},
                         files=("g1.html",))
        p = self.problems(repo)
        self.assertEqual(len(p["case mismatch"]), 1)
        self.assertFalse(p.get("missing file"))
        self.assertEqual(self.run_main(repo)[0], 1)

    def test_orphan(self):
        repo = self.make({"games": [entry(1)]}, files=("g1.html", "x.html"))
        self.assertEqual(len(self.problems(repo)["orphan file (not in catalog)"]), 1)
        self.assertEqual(self.run_main(repo)[0], 1)

    def test_duplicates(self):
        a, b = entry(1), entry(1)
        repo = self.make({"games": [a, b]}, files=("g1.html",))
        p = self.problems(repo)
        for key in ("duplicate id", "duplicate slug", "duplicate file"):
            self.assertEqual(len(p[key]), 1, key)
        self.assertEqual(self.run_main(repo)[0], 1)

    def test_filename_field_mismatch(self):
        repo = self.make({"games": [entry(1, filename="other.html")]},
                         files=("g1.html",))
        self.assertEqual(len(self.problems(repo)["filename field mismatch"]), 1)

    def test_stats_mismatch(self):
        repo = self.make({"games": [entry(1)], "stats": {"totalGames": 9}},
                         files=("g1.html",))
        self.assertEqual(len(self.problems(repo)["stats.totalGames mismatch"]), 1)


class Integrity(Base):
    BODY = b"<html>hello</html>"

    def make_with_bodies(self, **kw):
        import hashlib
        good = hashlib.sha256(self.BODY).hexdigest()[:16]
        e = entry(1, size=kw.get("size", len(self.BODY)),
                  hash=kw.get("hash", good))
        repo = self.make({"games": [e]}, files=("g1.html",))
        with open(os.path.join(repo, "games", "g1.html"), "wb") as fh:
            fh.write(self.BODY)
        return repo

    def run_verify(self, repo):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = check_catalog.main(["--repo", repo, "--verify-integrity"])
        return code, out.getvalue()

    def test_matching_passes(self):
        code, out = self.run_verify(self.make_with_bodies())
        self.assertEqual(code, 0)
        self.assertIn("Problems found:       0", out)

    def test_wrong_size_reported(self):
        code, out = self.run_verify(self.make_with_bodies(size=5))
        self.assertEqual(code, 1)
        self.assertIn("SIZE MISMATCH (1)", out)
        self.assertIn("expected=5 actual=%d" % len(self.BODY), out)

    def test_wrong_hash_reported(self):
        code, out = self.run_verify(self.make_with_bodies(hash="0" * 16))
        self.assertEqual(code, 1)
        self.assertIn("HASH MISMATCH (1)", out)

    def _crlf_repo(self, **kw):
        import hashlib
        lf = b"<html>\nline\n</html>\n"
        e = entry(1, size=kw.get("size", len(lf)),
                  hash=kw.get("hash", hashlib.sha256(lf).hexdigest()[:16]))
        repo = self.make({"games": [e]}, files=("g1.html",))
        with open(os.path.join(repo, "games", "g1.html"), "wb") as fh:
            fh.write(kw.get("body", lf.replace(b"\n", b"\r\n")))
        return repo

    def test_crlf_working_tree_with_lf_metadata_passes(self):
        code, out = self.run_verify(self._crlf_repo())
        self.assertEqual(code, 0)
        self.assertIn("Problems found:       0", out)

    def test_genuine_drift_still_caught_with_crlf(self):
        code, out = self.run_verify(self._crlf_repo(body=b"<html>x\r\ny\r\n</html>\r\n"))
        self.assertEqual(code, 1)
        self.assertIn("HASH MISMATCH (1)", out)
        self.assertIn("SIZE MISMATCH (1)", out)

    def test_mode_off_ignores_bad_size_and_hash(self):
        repo = self.make_with_bodies(size=5, hash="0" * 16)
        self.assertEqual(self.run_main(repo)[0], 0)
        p = check_catalog.check(repo)[2]
        self.assertFalse(p.get("size mismatch") or p.get("hash mismatch"))


class IntegrityContainment(Base):
    def test_outside_entry_reported_and_never_opened(self):
        repo = self.make({"games": [entry(1, file="games/../secret.html",
                                          size=1, hash="x")]},
                         files=("g1.html",))
        canary = os.path.join(repo, "secret.html")
        open(canary, "w").write("canary")
        real_open = open
        opened = []

        def spy(path, *a, **k):
            opened.append(os.path.realpath(str(path)))
            return real_open(path, *a, **k)

        orig = check_catalog.open if hasattr(check_catalog, "open") else None
        check_catalog.open = spy
        try:
            p = check_catalog.check(repo, integrity=True)[2]
        finally:
            if orig is None:
                del check_catalog.open
            else:
                check_catalog.open = orig
        self.assertTrue(p["file outside games/"])
        self.assertNotIn(os.path.realpath(canary), opened)
        self.assertFalse(p["size mismatch"])
        self.assertFalse(p["hash mismatch"])


if __name__ == "__main__":
    unittest.main()
