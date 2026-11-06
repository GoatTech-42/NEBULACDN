#!/usr/bin/env python3
"""Catalog integrity check for NEBULA CDN.

Read-only. Compares games.json against the files in games/ and prints a
plain-text report: counts first, then each problem entry.

Checks:
  - catalog entries whose file does not exist (missing file)
  - catalog entries whose file only matches a real file when case is ignored
    (case mismatch; jsDelivr and GitHub paths are case-sensitive)
  - catalog entries whose `filename` field disagrees with `file`
  - game files in games/ that no catalog entry points to (orphans)
  - duplicate entries (same id, slug, or file appearing more than once)

Usage:
  python3 scripts/check_catalog.py [--repo PATH] [--limit N]

Exit code is 0 when no problems are found, 1 when problems are found, and
2 when games.json is malformed (concise error on stderr).
Standard library only.
"""

import argparse
import json
import os
import sys
from collections import defaultdict


class CatalogError(Exception):
    """games.json is malformed; the check cannot give a trustworthy answer."""


def load_catalog(repo):
    path = os.path.join(repo, "games.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise CatalogError("cannot read games.json: {}".format(exc))
    if isinstance(data, dict):
        if "games" not in data:
            raise CatalogError("games.json has no 'games' key")
        games = data["games"]
    else:
        games = data
    if not isinstance(games, list):
        raise CatalogError("'games' must be a list, got {}".format(
            type(games).__name__))
    for index, entry in enumerate(games):
        if not isinstance(entry, dict):
            raise CatalogError("entry #{} is not an object (got {})".format(
                index, type(entry).__name__))
        file_field = entry.get("file")
        if not isinstance(file_field, str) or not file_field.strip():
            raise CatalogError(
                "entry #{} has a missing, empty, or non-string 'file' field"
                .format(index))
    return data, games


def list_game_files(repo):
    """Top-level *.html files in games/ (games/assets/ holds split chunks)."""
    games_dir = os.path.join(repo, "games")
    return sorted(
        name for name in os.listdir(games_dir)
        if name.lower().endswith(".html")
        and os.path.isfile(os.path.join(games_dir, name))
    )


def entry_label(entry, index):
    return "#{idx} id={id!r} name={name!r} file={file!r}".format(
        idx=index,
        id=entry.get("id"),
        name=entry.get("name"),
        file=entry.get("file"),
    )


def check(repo):
    data, games = load_catalog(repo)
    files = list_game_files(repo)
    file_set = set(files)
    lower_map = defaultdict(list)
    for name in files:
        lower_map[name.lower()].append(name)

    problems = defaultdict(list)
    referenced = set()

    for index, entry in enumerate(games):
        label = entry_label(entry, index)
        file_field = entry.get("file") or ""
        filename_field = entry.get("filename")

        if not file_field:
            problems["no file field"].append(label)
            continue

        if not file_field.startswith("games/") or "/" in file_field[len("games/"):]:
            problems["file outside games/"].append(label)

        base = file_field.split("/")[-1]

        if filename_field is not None and filename_field != base:
            problems["filename field mismatch"].append(
                "{} filename={!r}".format(label, filename_field))

        if base in file_set:
            referenced.add(base)
        elif base.lower() in lower_map:
            actual = lower_map[base.lower()]
            referenced.update(actual)
            problems["case mismatch"].append(
                "{} actual={}".format(label, ", ".join(actual)))
        else:
            problems["missing file"].append(label)

    for key in ("id", "slug", "file"):
        seen = defaultdict(list)
        for index, entry in enumerate(games):
            value = entry.get(key)
            if value is not None:
                seen[value].append(index)
        for value, indexes in sorted(seen.items(), key=lambda kv: str(kv[0])):
            if len(indexes) > 1:
                problems["duplicate " + key].append(
                    "{}={!r} at entries {}".format(
                        key, value, ", ".join("#%d" % i for i in indexes)))

    for name in files:
        if name not in referenced:
            problems["orphan file (not in catalog)"].append("games/" + name)

    stats = data.get("stats", {}) if isinstance(data, dict) else {}
    declared = stats.get("totalGames")
    if declared is not None and declared != len(games):
        problems["stats.totalGames mismatch"].append(
            "stats.totalGames={} but games has {} entries".format(declared, len(games)))

    return games, files, problems


ORDER = [
    "missing file",
    "case mismatch",
    "filename field mismatch",
    "file outside games/",
    "no file field",
    "duplicate id",
    "duplicate slug",
    "duplicate file",
    "orphan file (not in catalog)",
    "stats.totalGames mismatch",
]


def report(games, files, problems, limit):
    lines = []
    lines.append("NEBULA CDN catalog integrity report")
    lines.append("=" * 36)
    lines.append("Catalog entries:      {}".format(len(games)))
    lines.append("Game files (games/):  {}".format(len(files)))
    total = sum(len(v) for v in problems.values())
    lines.append("Problems found:       {}".format(total))
    lines.append("")
    lines.append("Counts by type:")
    for kind in ORDER:
        lines.append("  {:<32} {}".format(kind, len(problems.get(kind, []))))
    for kind in ORDER:
        items = problems.get(kind, [])
        if not items:
            continue
        lines.append("")
        lines.append("{} ({})".format(kind.upper(), len(items)))
        shown = items if limit is None else items[:limit]
        for item in shown:
            lines.append("  - " + item)
        if len(shown) < len(items):
            lines.append("  ... {} more".format(len(items) - len(shown)))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser.add_argument("--repo", default=default_repo,
                        help="path to the NEBULACDN checkout (default: this repo)")
    parser.add_argument("--limit", type=int, default=None,
                        help="max entries to print per problem type")
    args = parser.parse_args(argv)

    try:
        games, files, problems = check(args.repo)
    except (CatalogError, OSError) as exc:
        sys.stderr.write("error: {}\n".format(exc))
        return 2
    print(report(games, files, problems, args.limit))
    return 1 if any(problems.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
