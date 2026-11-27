#!/usr/bin/env python3
"""Every agent on level ground: same notebooks, same requests, same checks.

bench-quality.py calls a chat adapter directly, so it cannot measure an agent
that is not a chat model (Zeta 2) and does not measure what the product does
around the model (read-ahead, retargeting, salvage). This drives
``AgentSession.run`` -- the product's own entry point -- for every agent, then
applies what came back to the notebook exactly as accepting it would: an
``add_cell`` goes below the prompt, a ``propose_edit`` replaces its cell. The
resulting notebook is run top to bottom with a check appended.

Two kinds of case:

* **new**: an empty prompt below a setup cell; write a named function.
* **change**: a cell that is wrong (it raised, and its traceback is in the
  snapshot) or that should be changed; the request is to fix or change it.
  Passing needs the notebook to work afterwards, by whichever route; the
  report also counts how often the agent edited the cell in place.

Times are from submit to done. For agents that read ahead (qwen, Gemma) both
are reported: cold, and after the read-ahead a ``>`` starts has finished.

It **executes code a language model wrote**, in a subprocess with a timeout,
and only with --run. Read the cases first.

    .venv/bin/python scripts/bench-agents.py --run
    .venv/bin/python scripts/bench-agents.py --run --agents zeta2,ollama-qwen25-coder-7b
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jupyter_vybe.session import AgentSession  # noqa: E402

KERNEL = {"name": "python3", "language": "python", "version": "3.13"}
SETUP = "import numpy as np\nx = np.arange(20)"
LIMITS = {"maxInsertions": 4, "maxProposals": 4, "maxToolTurns": 2}

NEW = [
    ("write a function called running_max(a) returning the running maximum of an array",
     "assert running_max(np.array([1, 3, 2, 5, 4])).tolist() == [1, 3, 3, 5, 5]"),
    ("write a function called clip_range(a, lo, hi) that clips an array between lo and hi",
     "assert clip_range(np.array([-1, 2, 9]), 0, 5).tolist() == [0, 2, 5]"),
    ("write a function called zscore(a) returning the z-scores of an array",
     "r = zscore(np.array([1.0, 2.0, 3.0]))\nassert abs(r.mean()) < 1e-9 and abs(r.std() - 1) < 1e-9"),
    ("write a function called chunks(a, n) splitting an array into n equal chunks",
     "assert [c.tolist() for c in chunks(np.arange(6), 3)] == [[0, 1], [2, 3], [4, 5]]"),
    ("write a function called is_increasing(a) returning True if an array is monotonically increasing",
     "assert is_increasing(np.array([1, 2, 3])) and not is_increasing(np.array([3, 1, 2]))"),
    ("write a function called moving_average(a, w) computing the moving average with window w",
     "assert np.allclose(moving_average(np.array([1.0, 2, 3, 4]), 2), [1.5, 2.5, 3.5])"),
    ("write a function called top_k_indices(a, k) returning the indices of the k largest values, largest first",
     "assert list(top_k_indices(np.array([5, 1, 9, 3]), 2)) == [2, 0]"),
    ("write a function called rescale(a) that rescales an array to the range 0 to 1",
     "assert np.allclose(rescale(np.array([2.0, 4.0, 6.0])), [0.0, 0.5, 1.0])"),
]


def _tb(count: int, frames: list[tuple[int, str]], ename: str, evalue: str) -> dict:
    """An error output shaped like IPython's: one entry per frame."""
    entries = ["\x1b[31m" + "-" * 75 + "\x1b[39m"]
    for line, code in frames:
        entries.append(f"Cell \x1b[32mIn[{count}]\x1b[39m, line {line}\n----> {line} {code}")
    entries.append(f"{ename}: {evalue}")
    return {"output_type": "error", "ename": ename, "evalue": evalue, "traceback": entries}


# (request, the cell to change, its error output or None, check)
CHANGE = [
    ("fix the error",
     "def mean(a):\n    return sum(a) / len(b)\n\nm = mean([1, 2, 3])",
     _tb(2, [(4, "m = mean([1, 2, 3])"), (2, "return sum(a) / len(b)")], "NameError", "name 'b' is not defined"),
     "assert m == 2"),
    ("fix the error so that y has 4 rows",
     "y = x.reshape(3, 7)",
     _tb(2, [(1, "y = x.reshape(3, 7)")], "ValueError", "cannot reshape array of size 20 into shape (3,7)"),
     "assert y.shape == (4, 5)"),
    ("fix the error",
     'label = "n=" + len(x)',
     _tb(2, [(1, 'label = "n=" + len(x)')], "TypeError", 'can only concatenate str (not "int") to str'),
     'assert label == "n=20"'),
    ("change double so it returns three times a instead",
     "def double(a):\n    return a * 2",
     None,
     "assert double(np.array([1, 2])).tolist() == [3, 6]"),
    ("change squares in the cell above to hold cubes instead",
     "squares = x ** 2",
     None,
     "assert squares[2] == 8"),
    ("make h default to 1 in area",
     "def area(w, h):\n    return w * h",
     None,
     "assert area(3) == 3 and area(2, 5) == 10"),
]


# Held out: written after Zeta's placement was tuned on the cases above, and
# not looked at while tuning. Its scores on these are the honest ones.
HELD_OUT_NEW = [
    ("write a function called count_above(a, t) returning how many values are above t",
     "assert count_above(np.array([1, 5, 7, 2]), 4) == 2"),
    ("write a function called second_largest(a) returning the second largest value",
     "assert second_largest(np.array([3, 9, 4])) == 4"),
]
HELD_OUT_CHANGE = [
    ("fix the error so first_five holds the first five values",
     "first_five = x[[0, 1, 2, 3, 4, 25]]",
     _tb(2, [(1, "first_five = x[[0, 1, 2, 3, 4, 25]]")], "IndexError", "index 25 is out of bounds for axis 0 with size 20"),
     "assert first_five.tolist() == [0, 1, 2, 3, 4]"),
    ("fix the error",
     "m = x.average()",
     _tb(2, [(1, "m = x.average()")], "AttributeError", "'numpy.ndarray' object has no attribute 'average'"),
     "assert m == 9.5"),
    ("change greet to say Hi instead of Hello",
     'def greet(name):\n    return "Hello " + name',
     None,
     'assert greet("Bo") == "Hi Bo"'),
    ("change evens to keep only the values divisible by 4",
     "evens = x[x % 2 == 0]",
     None,
     "assert evens.tolist() == [0, 4, 8, 12, 16]"),
]


def notebook(target: str | None = None, error: dict | None = None) -> list[dict]:
    cells = [{"id": "setup", "cell_type": "code", "source": SETUP, "execution_count": 1, "outputs": []}]
    if target is not None:
        outputs = [error] if error else []
        cells.append({"id": "target", "cell_type": "code", "source": target,
                      "execution_count": 2, "outputs": outputs})
    cells.append({"id": "prompt", "cell_type": "code", "source": "> ", "outputs": []})
    return cells


def frame_for(cells: list[dict], request: str | None) -> dict:
    snap = [dict(c) for c in cells]
    snap[-1] = {**snap[-1], "source": "> " + (request or "")}
    msg = {"snapshot": json.dumps({"cells": snap}), "snapshotSeq": 0, "promptCellId": "prompt",
           "kernelInfo": KERNEL, "globalPrompt": "", "limits": LIMITS}
    if request is not None:
        msg.update(prompt=request, roundId="r")
    return msg


def apply(cells: list[dict], frames: list[dict]) -> tuple[list[str], bool]:
    """The notebook's code after accepting everything, and whether the target was edited."""
    sources = {c["id"]: c["source"] for c in cells}
    inserted: list[str] = []
    edited = False
    for f in frames:
        if f.get("op") != "tool":
            continue
        args = f.get("args", {})
        if f["name"] == "add_cell":
            inserted.append(args.get("body", ""))
        elif f["name"] == "propose_edit" and args.get("cell") in sources:
            sources[args["cell"]] = args.get("body", "")
            edited = edited or args["cell"] == "target"
    code: list[str] = []
    for c in cells:
        if c["id"] == "prompt":
            code.extend(inserted)
        elif c["cell_type"] == "code":
            code.append(sources[c["id"]])
    return code, edited


def execute(code: list[str], check: str, timeout: float) -> tuple[bool, str]:
    program = "\n\n".join(code + [check, "print('PASS')"]) + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(program)
        path = fh.name
    try:
        proc = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "timed out"
    finally:
        Path(path).unlink(missing_ok=True)
    if proc.returncode == 0 and "PASS" in proc.stdout:
        return True, ""
    tail = (proc.stderr or proc.stdout).strip().splitlines()
    return False, tail[-1][:100] if tail else f"exit {proc.returncode}"


async def one(agent: str, cells: list[dict], request: str, read_ahead: bool) -> tuple[list[dict], float]:
    session = AgentSession(f"bench-{agent}-{time.monotonic_ns()}.ipynb", agent, True, {})
    session.history_enabled = False
    if read_ahead:
        session.prefill(frame_for(cells, None))
        if session._prefill is not None:
            await session._prefill.task
    started = time.monotonic()
    frames = [f async for f in session.run(frame_for(cells, request))]
    return frames, time.monotonic() - started


async def bench(agent: str, read_ahead: bool, execute_code: bool, timeout: float) -> dict:
    results = {"new": [], "change": [], "held": [], "edited": 0, "times": []}
    cases = [("new", r, notebook(), c) for r, c in NEW]
    cases += [("change", r, notebook(t, e), c) for r, t, e, c in CHANGE]
    cases += [("held", r, notebook(), c) for r, c in HELD_OUT_NEW]
    cases += [("held", r, notebook(t, e), c) for r, t, e, c in HELD_OUT_CHANGE]
    for kind, request, cells, check in cases:
        frames, elapsed = await one(agent, cells, request, read_ahead)
        code, edited = apply(cells, frames)
        errors = [f.get("message", "") for f in frames if f.get("op") == "error"]
        ok, why = execute(code, check, timeout) if execute_code else (False, "not run")
        results[kind].append(ok)
        results["edited"] += edited
        results["times"].append(elapsed)
        label = request[:58]
        print(f"  {kind:6} {'pass' if ok else 'FAIL'} {elapsed:6.1f}s  {label}"
              + (f"  [{why}]" if not ok and why else "")
              + (f"  error: {errors[0][:80]}" if errors else "")
              + ("  (edited in place)" if edited else ""), flush=True)
    return results


async def main_async(agents: list[str], execute_code: bool, timeout: float, read_ahead: str) -> None:
    rows = []
    for agent in agents:
        modes = [False]
        if read_ahead == "both" and agent != "zeta2":
            modes = [False, True]
        for ra in modes:
            print(f"\n== {agent}{' (read ahead)' if ra else ''}", flush=True)
            r = await bench(agent, ra, execute_code, timeout)
            rows.append((agent + (" +read-ahead" if ra else ""), r))
    print("\n| agent | new code | changes | held out | edited in place | median s | mean s |")
    print("|---|---|---|---|---|---|---|")
    n_change = len(CHANGE) + len(HELD_OUT_CHANGE)
    for name, r in rows:
        t = r["times"]
        print(f"| {name} | {sum(r['new'])}/{len(r['new'])} | {sum(r['change'])}/{len(r['change'])} "
              f"| {sum(r['held'])}/{len(r['held'])} | {r['edited']}/{n_change} "
              f"| {statistics.median(t):.1f} | {statistics.mean(t):.1f} |")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", default="zeta2,ollama-qwen25-coder-7b,turbofieldfare-gemma4")
    ap.add_argument("--run", action="store_true", help="execute the generated code (read the cases first)")
    ap.add_argument("--timeout", type=float, default=15)
    ap.add_argument("--read-ahead", choices=["both", "cold"], default="both")
    args = ap.parse_args()
    asyncio.run(main_async(args.agents.split(","), args.run, args.timeout, args.read_ahead))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
