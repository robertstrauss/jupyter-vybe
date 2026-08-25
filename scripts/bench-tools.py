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

sys.path.insert(0, ".")

from jupyter_vybe.adapters import Message, build_adapter
from jupyter_vybe.assembler import assemble_initial
from jupyter_vybe.protocol import parse
from jupyter_vybe.session import _tooling_with_limits

KERNEL = {"name": "python3", "language": "python", "version": "3.13"}
SNAP = json.dumps({
    "nbformat": 4, "nbformat_minor": 5,
    "metadata": {"kernelspec": {"name": "python3"}, "language_info": {"name": "python"}},
    "cells": [
        {"id": "setup001", "cell_type": "code",
         "source": "import numpy as np\nx = np.arange(20)", "execution_count": 1},
        {"id": "prompt01", "cell_type": "markdown",
         "source": "> add a function that normalises x"},
        {"id": "gen-a3f9", "cell_type": "code",
         "source": "def normalize_x(x):\n    return (x - np.mean(x)) / np.std(x)"},
        {"id": "prompt02", "cell_type": "code", "source": "> ..."},
    ],
})

# (request, expected tool)
CASES = [
    ("add a function that returns the running maximum of x", "add_cell"),
    ("write a function to clip x between 0 and 5", "add_cell"),
    ("edit the above cell to handle a zero standard deviation", "propose_edit"),
    ("change normalize_x so it accepts an axis argument", "propose_edit"),
    ("add a docstring to the normalise function", "propose_edit"),
]


async def run(agent: str, runs: int) -> int:
    adapter = build_adapter(agent)
    tally = {"tagged": 0, "fenced": 0, "correct_tool": 0, "total": 0}
    print(f"agent: {agent}\n")
    for request, expected in CASES:
        got = []
        for _ in range(runs):
            prompt = assemble_initial(
                notebook_json=SNAP, kernel=KERNEL, prompt_cell_id="prompt02",
                user_prompt=request, tooling=_tooling_with_limits({}),
            )
            out = ""
            async for chunk in adapter.stream([Message("user", prompt)], asyncio.Event()):
                out += chunk
            tools = [e.name for e in parse(out) if e.kind.value == "tool"]
            tally["total"] += 1
            if tools:
                tally["tagged"] += 1
            elif "```" in out:
                tally["fenced"] += 1
            if expected in tools:
                tally["correct_tool"] += 1
            got.append(tools[0] if tools else ("fence" if "```" in out else "prose"))
        ok = sum(g == expected for g in got)
        print(f"  want {expected:13} {ok}/{runs}  got {got}")
    t = tally["total"]
    print(f"\n  tags emitted   {tally['tagged']}/{t}")
    print(f"  fell back to fence {tally['fenced']}/{t}")
    print(f"  correct tool   {tally['correct_tool']}/{t}")
    return 0 if tally["correct_tool"] == t else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="ollama-qwen25-coder-7b")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()
    return asyncio.run(run(args.agent, args.runs))


if __name__ == "__main__":
    raise SystemExit(main())
