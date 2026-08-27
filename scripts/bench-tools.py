#!/usr/bin/env python3
"""Measure tool-tag compliance across models and prompt shapes.

Answers the only two questions that matter for the protocol: does the model
emit tags at all, and when asked to change existing code does it emit an edit
rather than a duplicate?

    .venv/bin/python scripts/bench-tools.py --agent ollama-qwen25-coder-7b --runs 4
    .venv/bin/python scripts/bench-tools.py --agent turbofieldfare-gemma4 --runs 2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

sys.path.insert(0, ".")

from jupyter_vybe.adapters import Message, build_adapter
from jupyter_vybe.assembler import assemble_initial, resolve_cell_ref
from jupyter_vybe.protocol import parse
from jupyter_vybe.session import _tooling_with_limits

KERNEL = {"name": "python3", "language": "python", "version": "3.13"}

# A notebook with the shapes that actually trip models up: an import cell, a
# markdown heading, a function to edit, a cell that failed, and the prompt cell
# last. Cell numbers below refer to this listing.
SNAP = json.dumps({
    "nbformat": 4, "nbformat_minor": 5,
    "metadata": {"kernelspec": {"name": "python3"}, "language_info": {"name": "python"}},
    "cells": [
        {"id": "setup001", "cell_type": "code",
         "source": "import numpy as np\nx = np.arange(20)", "execution_count": 1},
        {"id": "head0001", "cell_type": "markdown",
         "source": "## Preprocessing"},
        {"id": "prompt01", "cell_type": "markdown",
         "source": "> add a function that normalises x"},
        {"id": "gen-a3f9", "cell_type": "code",
         "source": "def normalize_x(x):\n    return (x - np.mean(x)) / np.std(x)",
         "execution_count": 2},
        {"id": "broke001", "cell_type": "code",
         "source": "total = np.sum(x, axes=0)", "execution_count": 3,
         "outputs": [{"output_type": "error", "ename": "TypeError",
                      "evalue": "sum() got an unexpected keyword argument 'axes'"}]},
        {"id": "prompt02", "cell_type": "code", "source": "> ..."},
    ],
})

# (request, expected tool). Twenty cases, weighted the way real use is: mostly
# "write me something", with the edit cases that failure taught us to watch --
# "the cell above", a named function, a numbered cell, and a fix for an error
# that is visible in the outputs rather than stated in the prompt (M8).
CELLS = json.loads(SNAP)["cells"]

CASES = [
    # -- additions ------------------------------------------------------
    ("add a function that returns the running maximum of x", "add_cell"),
    ("write a function to clip x between 0 and 5", "add_cell"),
    ("add a function computing the median absolute deviation of x", "add_cell"),
    ("write a helper that splits x into n chunks of equal length", "add_cell"),
    ("add a function to compute a moving average of x with window w", "add_cell"),
    ("give me a function that returns the indices of the k largest values", "add_cell"),
    ("write a function that rescales x to the range 0 to 1", "add_cell"),
    ("add a function that returns True if x is monotonically increasing", "add_cell"),
    ("write a short function to compute the z-scores of an arbitrary array", "add_cell"),
    ("add a markdown cell summarising what this notebook does so far", "add_cell"),
    ("write a function that pads x to a multiple of 8 with zeros", "add_cell"),
    ("add a function returning the first difference of x", "add_cell"),
    # -- edits, by every form of address a user actually uses -------------
    # The third element is the cell the edit must land on. A proposal aimed at
    # the wrong cell renders as a perfectly good diff, which is what made
    # wrong-cell edits so hard to notice; the bench checks the target because
    # a human reading the output will not.
    ("edit the cell above to use a keyword argument", "propose_edit", "broke001"),
    ("change normalize_x so it accepts an axis argument", "propose_edit", "gen-a3f9"),
    ("add a docstring to the normalise function", "propose_edit", "gen-a3f9"),
    ("fix the error in cell 5", "propose_edit", "broke001"),
    ("cell 4 should return float32, not float64", "propose_edit", "gen-a3f9"),
    ("rename normalize_x to standardise, and update it", "propose_edit", "gen-a3f9"),
    ("make normalize_x handle a list as well as an array", "propose_edit", "gen-a3f9"),
    ("the Preprocessing heading should say Preprocessing and QA", "propose_edit", "head0001"),
]


async def run(agent: str, runs: int, tooling_file: str | None = None) -> int:
    adapter = build_adapter(agent)
    tooling = None
    if tooling_file:
        from pathlib import Path

        raw = Path(tooling_file).read_text(encoding="utf-8")
        tooling = _tooling_with_limits({}, raw)
        print(f"tooling: {tooling_file} ({len(tooling or '')} chars)")
    tally = {"tagged": 0, "fenced": 0, "correct_tool": 0, "total": 0,
             "targeted": 0, "target_cases": 0}
    timings: list[float] = []
    targets: list[tuple] = []
    print(f"agent: {agent}\n")
    for case in CASES:
        request, expected = case[0], case[1]
        expected_cell = case[2] if len(case) > 2 else None
        got = []
        for _ in range(runs):
            prompt = assemble_initial(
                notebook_json=SNAP, kernel=KERNEL, prompt_cell_id="prompt02",
                user_prompt=request,
                tooling=tooling if tooling is not None else _tooling_with_limits({}),
            )
            out = ""
            started = time.monotonic()
            async for chunk in adapter.stream([Message("user", prompt)], asyncio.Event()):
                out += chunk
            timings.append(time.monotonic() - started)
            events = [e for e in parse(out) if e.kind.value == "tool"]
            tools = [e.name for e in events]
            if expected_cell:
                tally["target_cases"] += 1
                edit = next((e for e in events if e.name == "propose_edit"), None)
                resolved = resolve_cell_ref(edit.args.get("cell", ""), CELLS) if edit else ""
                if resolved == expected_cell:
                    tally["targeted"] += 1
                else:
                    targets.append((request, edit.args.get("cell") if edit else None, resolved))
            tally["total"] += 1
            if tools:
                tally["tagged"] += 1
            elif "```" in out:
                tally["fenced"] += 1
            if expected in tools:
                tally["correct_tool"] += 1
            got.append(tools[0] if tools else ("fence" if "```" in out else "prose"))
        ok = sum(g == expected for g in got)
        secs = " ".join(f"{t:.1f}s" for t in timings[-runs:])
        print(f"  want {expected:13} {ok}/{runs}  got {got}  [{secs}]")
    t = tally["total"]
    print(f"\n  tags emitted   {tally['tagged']}/{t}")
    print(f"  fell back to fence {tally['fenced']}/{t}")
    print(f"  correct tool   {tally['correct_tool']}/{t}")
    if tally["target_cases"]:
        print(f"  correct cell   {tally['targeted']}/{tally['target_cases']}")
        for request, asked, resolved in targets:
            print(f"    MISS: {request!r} -> cell={asked!r} ({resolved or 'unresolved'})")
    if timings:
        # First call absorbs model load and cold prompt cache; the rest is the
        # latency a user actually feels, which is what decides the default
        # agent (docs/14 N4).
        rest = timings[1:] or timings
        print(f"  first call     {timings[0]:.1f}s")
        print(
            f"  after that     {min(rest):.1f}s min / "
            f"{sum(rest) / len(rest):.1f}s mean / {max(rest):.1f}s max"
        )
    return 0 if tally["correct_tool"] == t else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="ollama-qwen25-coder-7b")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument(
        "--tooling",
        help="alternative tooling prompt file, for measuring what its length buys",
    )
    args = ap.parse_args()
    return asyncio.run(run(args.agent, args.runs, args.tooling))


if __name__ == "__main__":
    raise SystemExit(main())
