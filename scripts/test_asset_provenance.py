#!/usr/bin/env python3
"""Regression tests for asset_provenance.py. Standard library only.

Run: python3 -m unittest scripts/test_asset_provenance.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asset_provenance as ap  # noqa: E402

U = "https://x.test/game.js"


class Extraction(unittest.TestCase):
    def test_script_src_quoted(self):
        self.assertEqual(ap.extract_urls('<script src="%s"></script>' % U), {U})
        self.assertEqual(ap.extract_urls("<script src='%s'></script>" % U), {U})

    def test_script_src_unquoted(self):
        self.assertEqual(ap.extract_urls("<script src=%s></script>" % U), {U})

    def test_anchor_href_excluded(self):
        self.assertEqual(ap.extract_urls('<a href="%s">download</a>' % U), set())

    def test_data_src_excluded(self):
        self.assertEqual(ap.extract_urls('<script data-src="%s"></script>' % U), set())

    def test_other_tags(self):
        html = ('<img src="https://x.test/a.png"><link rel=stylesheet href=https://x.test/s.css>'
                '<object data="https://x.test/o.swf"></object>'
                '<iframe src="https://x.test/f.html"></iframe>')
        self.assertEqual(len(ap.extract_urls(html)), 4)

    def test_comment_ignored(self):
        self.assertEqual(ap.extract_urls('<!-- <script src="%s"></script> -->' % U), set())

    def test_inline_script_fallbacks(self):
        html = '<script>var c={url:"%s"}; fetch("https://y.test/d.json")</script>' % U
        self.assertEqual(ap.extract_urls(html), {U, "https://y.test/d.json"})

    def test_quoted_url_fallback_not_in_markup_text(self):
        # Quoted URL in body text or an attribute must not count.
        self.assertEqual(ap.extract_urls('<p>"%s"</p>' % U), set())
        self.assertEqual(ap.extract_urls('<div title="%s"></div>' % U), set())

    def test_css_url_in_style(self):
        self.assertEqual(ap.extract_urls('<style>a{background:url(https://x.test/b.png)}</style>'),
                         {"https://x.test/b.png"})

    def test_base_href_resolves_relative(self):
        html = '<base href="https://cdn.test/dir/"><script src="m.js"></script>'
        self.assertIn("https://cdn.test/dir/m.js", ap.extract_urls(html))

    def test_relative_without_base_is_own(self):
        self.assertEqual(ap.extract_urls('<script src="local.js"></script>'), set())

    def test_namespace_ignored(self):
        self.assertEqual(ap.extract_urls('<svg xmlns="http://www.w3.org/2000/svg"></svg>'), set())


OWN_URLS = [
    "https://github.com/GoatTech-42/NEBULACDN/raw/main/a.js",
    "https://raw.githubusercontent.com/GoatTech-42/NEBULACDN/main/a.js",
    "https://cdn.jsdelivr.net/gh/GoatTech-42/NEBULACDN@main/a.js",
    "https://fastly.jsdelivr.net/gh/GoatTech-42/NEBULACDN@main/a.js",
    "https://gcore.jsdelivr.net/gh/goattech-42/nebulacdn/a.js",
    "https://rawcdn.githack.com/GoatTech-42/NEBULACDN/main/a.js",
    "https://raw.githack.com/GoatTech-42/NEBULACDN/main/a.js",
]
OTHER_URLS = [
    "https://github.com/GoatTech-42/OtherRepo/raw/main/a.js",
    "https://github.com/Someone/NEBULACDN/raw/main/a.js",
    "https://raw.githubusercontent.com/Someone/NEBULACDN/main/a.js",
    "https://cdn.jsdelivr.net/gh/Someone/NEBULACDN@main/a.js",
    "https://fastly.jsdelivr.net/gh/GoatTech-42/NEBULACDN-fork@main/a.js",
    "https://rawcdn.githack.com/Someone/NEBULACDN/main/a.js",
    "https://example.com/GoatTech-42/NEBULACDN/a.js",
]


class Mirrors(unittest.TestCase):
    def test_own_mirrors_are_own(self):
        for u in OWN_URLS:
            self.assertEqual(ap.classify(ap.source_of(u)), "own", u)

    def test_other_repos_are_third_party(self):
        for u in OTHER_URLS:
            self.assertIn(ap.classify(ap.source_of(u)),
                          ("third-party mirror", "third-party host"), u)

    def test_display_host_preserved(self):
        self.assertTrue(ap.source_of(OWN_URLS[3]).startswith("fastly.jsdelivr.net/"))
        self.assertTrue(ap.source_of(OWN_URLS[5]).startswith("rawcdn.githack.com/"))

    def test_extract_then_classify_end_to_end(self):
        html = "".join('<script src="%s"></script>' % u for u in OWN_URLS + OTHER_URLS)
        kinds = [ap.classify(ap.source_of(u)) for u in ap.extract_urls(html)]
        self.assertEqual(kinds.count("own"), len(OWN_URLS))
        self.assertEqual(len(kinds), len(OWN_URLS) + len(OTHER_URLS))


class Cli(unittest.TestCase):
    def make_repo(self, tmp):
        os.makedirs(os.path.join(tmp, "games"))
        with open(os.path.join(tmp, "games.json"), "w") as fh:
            json.dump({"games": [{"id": 1, "slug": "g", "name": "G", "file": "games/g.html"}]}, fh)
        with open(os.path.join(tmp, "games", "g.html"), "w") as fh:
            fh.write('<script src="https://x.test/a.js"></script>')

    def run_main(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ap.main(args)
        return code, err.getvalue()

    def test_out_and_json_refuse_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_repo(tmp)
            for flag in ("--out", "--json"):
                target = os.path.join(tmp, "existing.txt")
                with open(target, "w") as fh:
                    fh.write("KEEP")
                code, err = self.run_main(["--repo", tmp, flag, target])
                self.assertEqual(code, 2)
                self.assertIn("--force", err)
                self.assertEqual(open(target).read(), "KEEP")

    def test_force_overwrites_and_new_file_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_repo(tmp)
            target = os.path.join(tmp, "r.md")
            self.assertEqual(self.run_main(["--repo", tmp, "--out", target])[0], 0)
            self.assertIn("asset provenance", open(target).read())
            open(target, "w").write("OLD")
            self.assertEqual(self.run_main(["--repo", tmp, "--out", target, "--force"])[0], 0)
            self.assertNotEqual(open(target).read(), "OLD")


if __name__ == "__main__":
    unittest.main()
