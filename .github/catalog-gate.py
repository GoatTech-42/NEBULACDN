#!/usr/bin/env python3
"""Run scripts/check_catalog.py unchanged and gate on its result.

The checker exits 1 for any problem, including the 14 intentional exclusions
(removed deliberately in b955068) that show up as orphan files. Those are
listed in .github/known-exclusions.txt and are warning-only here. Any other
problem, any orphan not in that list, or exit code 2 fails the run.
"""
import re
import subprocess
import sys

res = subprocess.run([sys.executable, "scripts/check_catalog.py", "--limit", "100000"],
                     capture_output=True, text=True)
sys.stdout.write(res.stdout)
sys.stderr.write(res.stderr)
if res.returncode == 0:
    sys.exit(0)
if res.returncode != 1:
    print("FAIL: checker exit code", res.returncode)
    sys.exit(res.returncode)

known = {l.strip() for l in open(".github/known-exclusions.txt") if l.strip()}
counts = {m.group(1).strip(): int(m.group(2))
          for m in re.finditer(r"^  (.+?)\s+(\d+)$", res.stdout, re.M)}
bad = {k: v for k, v in counts.items()
       if v and k != "orphan file (not in catalog)"}
orphans, in_orphans = set(), False
for line in res.stdout.splitlines():
    if line.startswith("ORPHAN FILE"):
        in_orphans = True
    elif line and not line.startswith("  "):
        in_orphans = False
    elif in_orphans and line.startswith("  - "):
        orphans.add(line[4:].strip())
new_orphans = orphans - known
reported = counts.get("orphan file (not in catalog)")
if reported is not None and reported != len(orphans):
    print("FAIL: parsed {} orphans but checker reported {} (output format drift?)".format(len(orphans), reported))
    sys.exit(1)
if bad or new_orphans or not counts:
    print("FAIL: new integrity problems:", bad, sorted(new_orphans))
    sys.exit(1)
print("OK: only the {} known intentional exclusions remain (warning-only).".format(len(orphans)))
