#!/usr/bin/env python3
"""Does the generated code actually work? (M8's open question.)

bench-tools.py answers "did the model pick the right tool and aim it at the
right cell". That is the part that is cheap to automate, and it says nothing
about whether the code inside the tag runs, or is correct.

This runs the generated cell against a check. It therefore **executes code a
language model wrote**, which is exactly what the extension itself refuses to
do, so:

* it is opt-in behind --run, and prints the code and exits without it;
* each cell runs in a subprocess with a timeout, so a hang or a crash costs
  one case rather than the run;
* the prompts are fixed and local, and the model is one you chose.

It is still your machine running generated code. Read the cases before you
pass --run.

    .venv/bin/python scripts/bench-quality.py                    # dry run
    .venv/bin/python scripts/bench-quality.py --run --runs 2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, ".")

import jupyter_vybe.assembler as assembler
from jupyter_vybe.adapters import Message, build_adapter
from jupyter_vybe.assembler import assemble_initial
from jupyter_vybe.protocol import parse, salvage_fenced_blocks
from jupyter_vybe.session import _tooling_with_limits

KERNEL = {"name": "python3", "language": "python", "version": "3.13"}
SETUP = "import numpy as np\nx = np.arange(20)\n"

SNAP = json.dumps({
    "nbformat": 4, "nbformat_minor": 5,
    "metadata": {"kernelspec": {"name": "python3"}, "language_info": {"name": "python"}},
    "cells": [
        {"id": "setup001", "cell_type": "code", "source": SETUP.strip(), "execution_count": 1},
        {"id": "prompt01", "cell_type": "code", "source": "> ..."},
    ],
})

# (request, check). The request names the function so the check can call it --
# a bench that has to guess the name measures the guess, not the code.
CASES = [
    (
        "write a function called running_max(a) returning the running maximum of an array",
        "assert running_max(np.array([1, 3, 2, 5, 4])).tolist() == [1, 3, 3, 5, 5]",
    ),
    (
        "write a function called clip_range(a, lo, hi) that clips an array between lo and hi",
        "assert clip_range(np.array([-1, 2, 9]), 0, 5).tolist() == [0, 2, 5]",
    ),
    (
        "write a function called zscore(a) returning the z-scores of an array",
        "r = zscore(np.array([1.0, 2.0, 3.0]))\n"
        "assert abs(r.mean()) < 1e-9 and abs(r.std() - 1) < 1e-9",
    ),
    (
        "write a function called chunks(a, n) splitting an array into n equal chunks",
        "assert [c.tolist() for c in chunks(np.arange(6), 3)] == [[0, 1], [2, 3], [4, 5]]",
    ),
    (
        "write a function called is_increasing(a) returning True if an array is monotonically increasing",
        "assert is_increasing(np.array([1, 2, 3])) and not is_increasing(np.array([3, 1, 2]))",
    ),
    (
        "write a function called moving_average(a, w) computing the moving average with window w",
        "assert np.allclose(moving_average(np.array([1.0, 2, 3, 4]), 2), [1.5, 2.5, 3.5])",
    ),
    (
        "write a function called top_k_indices(a, k) returning the indices of the k largest values, largest first",
        "assert list(top_k_indices(np.array([5, 1, 9, 3]), 2)) == [2, 0]",
    ),
    (
        "write a function called rescale(a) that rescales an array to the range 0 to 1",
        "assert np.allclose(rescale(np.array([2.0, 4.0, 6.0])), [0.0, 0.5, 1.0])",
    ),
]


def run_case(cell: str, check: str, timeout: float) -> tuple[bool, str]:
    """Execute one generated cell plus its check, isolated and bounded."""
    program = f"{SETUP}\n{cell}\n\n{check}\nprint('PASS')\n"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(program)
        path = fh.name
    try:
        proc = subprocess.run(
            [sys.executable, path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "timed out"
    finally:
        Path(path).unlink(missing_ok=True)
    if proc.returncode == 0 and "PASS" in proc.stdout:
        return True, ""
    tail = (proc.stderr or proc.stdout).strip().splitlines()
    return False, tail[-1][:120] if tail else f"exit {proc.returncode}"


async def generate(adapter, request: str) -> tuple[str, float]:
    prompt = assemble_initial(
        notebook_json=SNAP, kernel=KERNEL, prompt_cell_id="prompt01",
        user_prompt=request, tooling=_tooling_with_limits({}),
    )
    started = time.monotonic()
    out = ""
    async for chunk in adapter.stream([Message("user", prompt)], asyncio.Event()):
        out += chunk
    elapsed = time.monotonic() - started
    events = list(parse(out))
    for event in events:
        if event.kind.value == "tool" and event.name == "add_cell":
            return event.args.get("body", ""), elapsed
    # No tag. The extension does not give up here either -- a reply with
    # fenced code is salvaged into cells (docs/13 R2) -- so a bench that
    # stopped here would measure the protocol rather than the product.
    for event in salvage_fenced_blocks(out):
        if event.name == "add_cell":
            return event.args.get("body", ""), elapsed
    return "", elapsed


async def main_async(agent: str, runs: int, execute: bool, timeout: float) -> int:
    adapter = build_adapter(agent)
    print(f"agent: {agent}   executing: {execute}\n")
    passed = total = 0
    for request, check in CASES:
        for attempt in range(runs):
            cell, elapsed = await generate(adapter, request)
            label = request.split("called ")[1].split("(")[0]
            if not cell:
                print(f"  {label:18} no cell emitted            [{elapsed:.1f}s]")
                total += 1
                continue
            if not execute:
                print(f"  {label:18} generated {len(cell):4d} chars   [{elapsed:.1f}s]")
                if attempt == 0:
                    print("      " + "\n      ".join(cell.splitlines()[:6]))
                total += 1
                continue
            ok, why = run_case(cell, check, timeout)
            passed += ok
            total += 1
            mark = "pass" if ok else "FAIL"
            print(f"  {label:18} {mark}  [{elapsed:.1f}s]" + (f"  {why}" if why else ""))
    print(f"\n  correct {passed}/{total}" if execute else f"\n  generated {total} cells")
    if not execute:
        print("  (dry run: pass --run to execute the generated code and check it)")
    return 0 if not execute or passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="ollama-qwen25-coder-7b")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument(
        "--run",
        action="store_true",
        help="execute the generated code. Read the cases first: this runs what a model wrote.",
    )
    ap.add_argument("--timeout", type=float, default=15)
    ap.add_argument(
        "--universal",
        help="alternative universal prompt file, for A/B'ing a prompt change",
    )
    args = ap.parse_args()
    if args.universal:
        replacement = Path(args.universal).read_text(encoding="utf-8")
        original = assembler._load

        def patched(name: str) -> str:
            return replacement if name == "universal.md" else original(name)

        assembler._load = patched
        print(f"universal prompt: {args.universal}")
    return asyncio.run(main_async(args.agent, args.runs, args.run, args.timeout))


if __name__ == "__main__":
    raise SystemExit(main())
