#!/usr/bin/env python3
"""Run scripts/check_catalog.py --verify-integrity and gate on its result.

Byte checks (size, hash, unreadable file) are fatal: they have no exclusions.

The checker exits 1 for any problem, including the 14 intentional exclusions
(removed deliberately in b955068) that show up as orphan files. Those are
listed in .github/known-exclusions.txt and are warning-only here. Any other
problem, any orphan not in that list, or exit code 2 fails the run.
"""
import re
import subprocess
import sys

res = subprocess.run([sys.executable, "scripts/check_catalog.py", "--verify-integrity",
                     "--limit", "100000"],
                     capture_output=True, text=True)
sys.stdout.write(res.stdout)
sys.stderr.write(res.stderr)
if res.returncode == 0:
    sys.exit(0)
if res.returncode != 1:
    print("FAIL: checker exit code", res.returncode)
    sys.exit(res.returncode)

REQUIRED = ["missing file", "case mismatch", "filename field mismatch",
            "file outside games/", "no file field", "duplicate id",
            "duplicate slug", "duplicate file", "orphan file (not in catalog)",
            "stats.totalGames mismatch",
            "size mismatch", "hash mismatch", "unreadable file"]
ORPHAN = "orphan file (not in catalog)"


def die(msg, code=2):
    # Fail CLOSED: any format uncertainty is a hard failure.
    print("FAIL (gate, fail-closed):", msg)
    sys.exit(code)


known = {l.strip() for l in open(".github/known-exclusions.txt") if l.strip()}
counts = {}
for m in re.finditer(r"^  (.+?)\s+(\d+)$", res.stdout, re.M):
    if m.group(1).strip() in counts:
        die("duplicate count key in output: " + m.group(1).strip())
    counts[m.group(1).strip()] = int(m.group(2))
missing = [k for k in REQUIRED if k not in counts]
if missing:
    die("required summary count keys missing from checker output: %s" % missing)
unknown = sorted(set(counts) - set(REQUIRED))
if unknown:
    die("unrecognized summary count keys: %s" % unknown)
tot = re.findall(r"^Problems found:\s+(\d+)\s*$", res.stdout, re.M)
if len(tot) != 1:
    die("'Problems found: N' line missing or repeated")
if int(tot[0]) != sum(counts.values()):
    die("Problems found %s != sum of counts %s" % (tot[0], sum(counts.values())))
sections = re.findall(r"^ORPHAN FILE \(NOT IN CATALOG\) \((\d+)\)\s*$", res.stdout, re.M)
orphans, in_orphans = set(), False
for line in res.stdout.splitlines():
    if line.startswith("ORPHAN FILE"):
        in_orphans = True
    elif line and not line.startswith("  "):
        in_orphans = False
    elif in_orphans and line.startswith("  - "):
        orphans.add(line[4:].strip())
reported = counts[ORPHAN]
if reported and len(sections) != 1:
    die("orphan count %d but orphan section missing/repeated" % reported)
if not reported and sections:
    die("orphan section present but orphan count is zero")
if sections and int(sections[0]) != reported:
    die("orphan section header count != summary count")
if len(orphans) != reported:
    die("parsed %d orphans but checker reported %d" % (len(orphans), reported), 1)
bad = {k: v for k, v in counts.items() if v and k != ORPHAN}
new_orphans = orphans - known
if bad or new_orphans:
    print("FAIL: new integrity problems:", bad, sorted(new_orphans))
    sys.exit(1)
print("OK: only the {} known intentional exclusions remain (warning-only).".format(len(orphans)))
