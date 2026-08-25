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
prompts = [r for r in records if r["kind"] == "prompt"]
res = [r for r in records if r["kind"] == "resolve"]

# Is the server that wrote this even running the current code? A stale server
# is the single most likely explanation for a fix that "did not work", so check
# it before drawing any conclusion from what is missing.
if prompts:
    listing = "[end of cell 1]" in prompts[-1]["prompt"]
    raw_json = '"nbformat"' in prompts[-1]["prompt"]
    print(f"prompt format: {'numbered listing' if listing else 'RAW JSON'}")
    if raw_json and not listing:
        print()
        print("  !! This server predates the numbered-listing change.")
        print("  !! It is running old code, so recent fixes are NOT in effect.")
        print("  !! Stop it and start a fresh one:  ./scripts/dev.sh")
        print()
    print()

if not res:
    if not records:
        print("Transcript is empty -- no rounds recorded.")
    elif not any(r["kind"] == "salvage" for r in records):
        print("No propose_edit was resolved, and no salvage ran either.")
        print("Most likely this server predates the resolution logging, so the")
        print("evidence simply was not recorded. Restart and reproduce again.")
    else:
        print("No propose_edit was resolved, but salvage did run -- so the edit")
        print("came from a fenced block retargeted by similarity, not from a tag:")
        for r in records:
            if r["kind"] == "salvage":
                print("   salvage ->", r.get("kinds"), "found", r.get("found"))
    for r in records:
        if r["kind"] == "reply" and "propose_edit" in r.get("reply", ""):
            import re as _re
            m = _re.search(r'cell="([^"]*)"', r["reply"])
            if m:
                print()
                print("  A propose_edit tag WAS emitted, asking for cell:", repr(m.group(1)))
                print("  It was not logged as resolved, confirming stale server code.")
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
