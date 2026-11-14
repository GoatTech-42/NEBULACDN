#!/usr/bin/env python3
"""Regression tests for .github/catalog-gate.py. Standard library only.

Run: python3 -m unittest scripts/test_catalog_gate.py
Full local proof: python3 scripts/test_catalog_gate.py --proof
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW_GATE = os.path.join(ROOT, ".github", "catalog-gate.py")
OLD_GATE = os.path.join(ROOT, "scripts", "fixtures", "catalog-gate-old.py")
CHECKER = os.path.join(ROOT, "scripts", "check_catalog.py")
GOOD = b"<html>good</html>\n"


def digest(b):
    return hashlib.sha256(b.replace(b"\r\n", b"\n")).hexdigest()[:16]


class Gate(unittest.TestCase):
    def make(self, files, entries, known=(), checker=CHECKER):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "games"))
        os.makedirs(os.path.join(tmp, "scripts"))
        os.makedirs(os.path.join(tmp, ".github"))
        shutil.copy(checker, os.path.join(tmp, "scripts", "check_catalog.py"))
        for n, b in files.items():
            with open(os.path.join(tmp, "games", n), "wb") as f:
                f.write(b)
        with open(os.path.join(tmp, "games.json"), "w") as f:
            json.dump({"games": entries, "stats": {"totalGames": len(entries)}}, f)
        with open(os.path.join(tmp, ".github", "known-exclusions.txt"), "w") as f:
            f.write("".join(k + "\n" for k in known))
        return tmp

    def ent(self, i, raw=GOOD, **kw):
        d = {"id": i, "slug": "g%d" % i, "name": "G%d" % i,
             "file": "games/g%d.html" % i, "size": len(raw.replace(b"\r\n", b"\n")),
             "hash": digest(raw)}
        d.update(kw)
        return d

    def run_gate(self, repo, gate=NEW_GATE):
        r = subprocess.run([sys.executable, gate], cwd=repo,
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def one(self, raw_on_disk=GOOD, **kw):
        return self.make({"g1.html": raw_on_disk}, [self.ent(1, **kw)])

    def test_clean(self):
        self.assertEqual(self.run_gate(self.one())[0], 0)

    def test_size_corruption_red_old_green_new(self):
        repo = self.one(raw_on_disk=b"x" * 15, size=4, hash="good")
        self.assertEqual(self.run_gate(repo, OLD_GATE)[0], 0)  # old gate: false green
        code, out = self.run_gate(repo)
        self.assertEqual(code, 1)
        self.assertIn("size mismatch", out)

    def test_hash_corruption_only(self):
        repo = self.one(raw_on_disk=b"y" * len(GOOD))
        self.assertEqual(self.run_gate(repo, OLD_GATE)[0], 0)
        code, out = self.run_gate(repo)
        self.assertEqual(code, 1)
        self.assertIn("'hash mismatch': 1", out)

    def test_unreadable_file(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root can read chmod 000 files")
        repo = self.one()
        os.chmod(os.path.join(repo, "games", "g1.html"), 0)
        code, out = self.run_gate(repo)
        self.assertEqual(code, 1)
        self.assertIn("unreadable file", out)

    def test_missing_file_fails(self):
        repo = self.make({}, [self.ent(1)])
        self.assertEqual(self.run_gate(repo)[0], 1)

    def test_outside_games_fails(self):
        repo = self.make({}, [self.ent(1, file="../escape.html")])
        open(os.path.join(repo, "escape.html"), "wb").write(GOOD)
        self.assertEqual(self.run_gate(repo)[0], 1)

    def test_crlf_canonicalization(self):
        crlf = b"<a>\r\n<b>\r\n"
        lf = crlf.replace(b"\r\n", b"\n")
        repo = self.make({"g1.html": crlf}, [self.ent(1, raw=lf)])
        self.assertEqual(self.run_gate(repo)[0], 0)
        # metadata computed over CRLF bytes is wrong under LF-canonical semantics
        bad = self.make({"g1.html": crlf}, [self.ent(1, raw=crlf, size=len(crlf),
                                                      hash=hashlib.sha256(crlf).hexdigest()[:16])])
        self.assertEqual(self.run_gate(bad)[0], 1)

    def orphan_repo(self, known):
        files = {"g1.html": GOOD, "old.html": GOOD}
        return self.make(files, [self.ent(1)], known=known)

    def test_known_orphan_warning_only(self):
        code, out = self.run_gate(self.orphan_repo(["games/old.html"]))
        self.assertEqual(code, 0)
        self.assertIn("1 known intentional", out)

    def test_new_orphan_fails(self):
        self.assertEqual(self.run_gate(self.orphan_repo([]))[0], 1)

    def test_orphan_plus_corruption_fails(self):
        repo = self.make({"g1.html": b"zz", "old.html": GOOD}, [self.ent(1)],
                         known=["games/old.html"])
        self.assertEqual(self.run_gate(repo)[0], 1)

    def stub_repo(self, output, rc=1):
        repo = self.one()
        with open(os.path.join(repo, "scripts", "check_catalog.py"), "w") as f:
            f.write("import sys\nsys.stdout.write(%r)\nsys.exit(%d)\n" % (output, rc))
        return repo

    def test_malformed_output_fails_closed(self):
        code, out = self.run_gate(self.stub_repo("garbage\n"))
        self.assertEqual(code, 2)
        self.assertIn("fail-closed", out)

    def test_unknown_key_fails_closed(self):
        keys = ["missing file", "case mismatch", "filename field mismatch",
                "file outside games/", "no file field", "duplicate id",
                "duplicate slug", "duplicate file", "orphan file (not in catalog)",
                "stats.totalGames mismatch", "size mismatch", "hash mismatch",
                "unreadable file", "brand new category"]
        out = "Problems found:       1\n\nCounts by type:\n" + "".join(
            "  %-32s %d\n" % (k, 1 if k == "brand new category" else 0) for k in keys)
        code, text = self.run_gate(self.stub_repo(out))
        self.assertEqual(code, 2)
        self.assertIn("unrecognized", text)

    def test_old_style_ten_keys_now_fail_closed(self):
        out = "Problems found:       0\n\nCounts by type:\n  missing file                     0\n"
        self.assertEqual(self.run_gate(self.stub_repo(out))[0], 2)

    def test_checker_exit_2(self):
        self.assertEqual(self.run_gate(self.stub_repo("", rc=2))[0], 2)


def proof():
    cmds = [
        [sys.executable, "-m", "unittest", "scripts/test_check_catalog.py",
         "scripts/test_catalog_gate.py", "-v"],
        [sys.executable, ".github/catalog-gate.py"],
        ["git", "diff", "--stat", "origin/main", "--", "games", "games.json",
         ".github/known-exclusions.txt"],
    ]
    rc = 0
    for c in cmds:
        print("$", " ".join(c))
        rc |= subprocess.run(c, cwd=ROOT).returncode
    return rc


if __name__ == "__main__":
    if "--proof" in sys.argv:
        sys.exit(proof())
    unittest.main()
