#!/usr/bin/env bash
# Print what the agent was shown, what it asked to edit, and what that resolved
# to. Run after reproducing a wrong-cell edit.
set -uo pipefail
cd "$(dirname "$0")/.."
T="${VYBE_TRANSCRIPT:-/tmp/vybe-transcript.jsonl}"

[ -f "$T" ] || { echo "no transcript at $T -- start with ./scripts/dev.sh"; exit 1; }

.venv/bin/python - "$T" <<'PY'
import json, sys
records = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
res = [r for r in records if r["kind"] == "resolve"]
if not res:
    print("No propose_edit was resolved in this transcript.")
    print("If a wrong cell was edited anyway, the edit did not come from a")
    print("propose_edit tag -- it was salvaged from a fenced block and")
    print("retargeted by similarity. Check the 'salvage' records:")
    for r in records:
        if r["kind"] == "salvage":
            print("   salvage ->", r.get("kinds"), "found", r.get("found"))
    sys.exit(0)
for r in res[-3:]:
    print("=" * 70)
    print("the agent was shown these cells:")
    for line in r["listing"]:
        print("   ", line)
    print()
    print("  model asked to edit :", repr(r["requested"]))
    print("  that resolved to    :", repr(r["resolved"]))
    print("  first line of target:", repr(r["target_first_line"]))
PY
