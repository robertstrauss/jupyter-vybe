#!/usr/bin/env python3
"""Zeta 2 in its own use cases, against the same cases as bench-agents.py.

bench-agents.py drives Zeta the way the product does: the request typed as a
comment, the cursor where the work is, predictions chained. That is Zed's own
prompt format, but the *request* is the one thing Zeta never saw in training --
it predicts what a programmer does next, and nobody types an instruction to
their editor. So this measures it in forms that need no instruction at all,
the way Zed uses it, and inserts the answer where we choose:

  product       the product's layout (jupyter_vybe/zeta.py), for comparison
  started       next-edit prediction from a started edit: the request comment
                and the function's signature already typed (and in the edit
                history), cursor in the empty body; chained like Zed's
                accept-and-predict-again
  fim           plain Seed-Coder fill-in-the-middle, no editable region: the
                prefix ends at the request comment, the model writes the cell
  fim+sig       the same, with the signature typed
  fix-native    a fix with no request text: cursor on the line that raised and
                the error in the diagnostics section, as Zed would send it
  fim-fix       fill-in-the-middle of the line that raised, the error as a
                comment above it

Every prediction goes through llama-server's prompt-lookup speculative
decoding (--spec-type ngram-mod); the draft acceptance is reported per form.
Executes model-written code, in a subprocess with a timeout, only with --run.

    .venv/bin/python scripts/bench-zeta-native.py --run
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jupyter_vybe import zeta  # noqa: E402
from jupyter_vybe.adapters import build_adapter  # noqa: E402

_spec = importlib.util.spec_from_file_location("bench_agents", ROOT / "scripts" / "bench-agents.py")
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)

EOS = "<[end▁of▁sentence]>"
SIG = re.compile(r"called (\w+)\(([^)]*)\)")


class Counter:
    def __init__(self):
        self.drafted = 0
        self.accepted = 0
        self.calls = 0

    def add(self, timings: dict) -> None:
        self.calls += 1
        self.drafted += int(timings.get("draft_n") or 0)
        self.accepted += int(timings.get("draft_n_accepted") or 0)


async def complete_raw(adapter, prompt: str, stop: list[str], counter: Counter, n: int = 400) -> str:
    """A plain completion, for the FIM forms (no editable region, no end marker)."""
    reply = await zeta_post(adapter, {"prompt": prompt, "n_predict": n, "temperature": 0,
                                      "stop": stop, "cache_prompt": True})
    counter.add(reply.get("timings", {}))
    return reply.get("content", "")


async def zeta_post(adapter, body: dict) -> dict:
    from jupyter_vybe.adapters.base import post_json
    return await post_json(f"{adapter.host}/completion", body, asyncio.Event(), timeout=120) or {}


async def chain(adapter, plan: zeta.Plan, counter: Counter) -> str:
    region = plan.region
    prompt = plan.prompt()
    for _ in range(zeta.MAX_STEPS):
        out, timings = await adapter.complete(prompt, asyncio.Event())
        counter.add(timings)
        region, again = zeta.next_region(plan, out)
        if not again:
            break
        plan.region = region
        prompt = plan.prompt()
    return region


def file_parts(cells: list[dict]) -> tuple[str, str]:
    """Everything above the prompt cell, and below it, as the file."""
    idx = next(i for i, c in enumerate(cells) if c["id"] == "prompt")
    before = "".join(zeta.render_cell(c) for c in cells[:idx])
    after = "".join(zeta.render_cell(c) for c in cells[idx + 1:])
    return before, after


def cells_from(body: str) -> list[dict]:
    return [{"op": "tool", "name": "add_cell", "args": {"type": "code", "body": b}}
            for b in zeta._split_cells(body)]


async def new_case(form: str, adapter, cells: list[dict], request: str, counter: Counter) -> list[dict]:
    m = SIG.search(request)
    signature = f"def {m.group(1)}({m.group(2)}):" if m else ""
    comment = f"# {request}"
    if form == "product":
        plan = zeta.plan_turn(cells, "prompt", request)
        return zeta.frames(plan, await chain(adapter, plan, counter))
    before, after = file_parts(cells)
    if form == "started":
        line_no = before.count("\n")
        plan = zeta.Plan(
            mode="insert", anchor_id="prompt",
            region=f"{zeta.CELL}\n{comment}\n{signature}\n    {zeta.CURSOR}\n",
            before=before, after=after,
            history=[zeta._insertion_event(line_no, [zeta.CELL, comment, signature])],
            request_lines=[comment],
        )
        return zeta.frames(plan, await chain(adapter, plan, counter))
    # Plain FIM, Seed-Coder's SPM order: suffix, prefix, middle.
    head = f"{zeta.CELL}\n{comment}\n" + (f"{signature}\n" if form == "fim+sig" else "")
    prompt = (zeta.FIM_SUFFIX + (after or "\n") + zeta.FIM_PREFIX + f"{zeta.FILE}{zeta.PATH}\n"
              + before + head + zeta.FIM_MIDDLE)
    middle = await complete_raw(adapter, prompt, [EOS, "\n# %%", zeta.FIM_SUFFIX], counter)
    body = head + middle
    return cells_from("\n".join(l for l in body.split("\n") if l.strip() != comment))


async def fix_case(form: str, adapter, cells: list[dict], request: str, counter: Counter) -> list[dict]:
    if form == "product":
        plan = zeta.plan_turn(cells, "prompt", request)
        region = await chain(adapter, plan, counter)
        produced = zeta.frames(plan, region)
        if not produced:
            plan = zeta.plan_turn(cells, "prompt", request, placement="bottom")
            produced = zeta.frames(plan, await chain(adapter, plan, counter))
        return produced
    target = next(c for c in cells if c["id"] == "target")
    err = zeta._error(target)
    lines = target["source"].split("\n")
    at = (zeta._error_line(target, err) or 1) - 1
    message = f"{err['ename']}: {err['evalue']}"
    idx = cells.index(target)
    before = "".join(zeta.render_cell(c) for c in cells[:idx])
    after = "".join(zeta.render_cell(c) for c in cells[idx + 1:] if c["id"] != "prompt")
    if form == "fix-native":
        marked = list(lines)
        marked[at] += zeta.CURSOR
        plan = zeta.Plan(
            mode="fix", anchor_id="target", region=f"{zeta.CELL}\n" + "\n".join(marked) + "\n",
            before=before, after=after,
            diagnostics=f"{zeta.FILE}diagnostics\n*{message}*:\n```\n{lines[at]}\n```\n",
            original=target["source"],
        )
        return zeta.frames(plan, await chain(adapter, plan, counter))
    # fim-fix: regenerate the line that raised, the error as a comment above it.
    indent = lines[at][: len(lines[at]) - len(lines[at].lstrip())]
    head = "\n".join(lines[:at]) + ("\n" if at else "") + f"{indent}# {message}\n"
    tail = "\n".join(lines[at + 1:])
    prompt = (zeta.FIM_SUFFIX + (tail + "\n" if tail else "") + after + zeta.FIM_PREFIX
              + f"{zeta.FILE}{zeta.PATH}\n" + before + f"{zeta.CELL}\n" + head + zeta.FIM_MIDDLE)
    middle = await complete_raw(adapter, prompt, [EOS, "\n# %%", zeta.FIM_SUFFIX], counter, n=120)
    # The first line it wrote, not the first line: a reply may open with a newline.
    new_line = next((l for l in middle.split("\n") if l.strip()), lines[at])
    if not new_line.startswith(indent):
        new_line = indent + new_line.lstrip()
    body = "\n".join(lines[:at] + [new_line] + lines[at + 1:])
    return [{"op": "tool", "name": "propose_edit", "args": {"cell": "target", "body": body}}]


NEW_FORMS = ("product", "started", "fim", "fim+sig")
FIX_FORMS = ("product", "fix-native", "fim-fix")


async def main_async(execute_code: bool, timeout: float) -> None:
    adapter = build_adapter("zeta2")
    new_cases = [(r, c, False) for r, c in B.NEW] + [(r, c, True) for r, c in B.HELD_OUT_NEW]
    fix_cases = [(r, t, e, c, False) for r, t, e, c in B.CHANGE if e] + \
                [(r, t, e, c, True) for r, t, e, c in B.HELD_OUT_CHANGE if e]
    rows = []
    for form in NEW_FORMS:
        counter, passed, held, times = Counter(), 0, 0, []
        print(f"\n== new code: {form}", flush=True)
        for request, check, is_held in new_cases:
            cells = B.notebook()
            t = time.monotonic()
            produced = await new_case(form, adapter, cells, request, counter)
            times.append(time.monotonic() - t)
            code, _ = B.apply(cells, produced)
            ok, why = B.execute(code, check, timeout) if execute_code else (False, "not run")
            passed += ok and not is_held
            held += ok and is_held
            print(f"  {'pass' if ok else 'FAIL'} {times[-1]:5.1f}s {'(held) ' if is_held else ''}{request[24:70]}"
                  + (f"  [{why}]" if not ok else ""), flush=True)
        rows.append(("new", form, passed, len(B.NEW), held, len(B.HELD_OUT_NEW), times, counter))
    for form in FIX_FORMS:
        counter, passed, held, times = Counter(), 0, 0, []
        print(f"\n== fixes: {form}", flush=True)
        for request, target, error, check, is_held in fix_cases:
            cells = B.notebook(target, error)
            t = time.monotonic()
            produced = await fix_case(form, adapter, cells, request, counter)
            times.append(time.monotonic() - t)
            code, _ = B.apply(cells, produced)
            ok, why = B.execute(code, check, timeout) if execute_code else (False, "not run")
            passed += ok and not is_held
            held += ok and is_held
            print(f"  {'pass' if ok else 'FAIL'} {times[-1]:5.1f}s {'(held) ' if is_held else ''}{request}  [{target.splitlines()[-1][:40]}]"
                  + (f"  [{why}]" if not ok else ""), flush=True)
        n_tuned = sum(1 for c in fix_cases if not c[4])
        rows.append(("fix", form, passed, n_tuned, held, len(fix_cases) - n_tuned, times, counter))
    if not rows:
        return
    print("\n| task | form | tuned cases | held out | median s | draft acceptance |")
    print("|---|---|---|---|---|---|")
    for task, form, p, n, h, nh, times, c in rows:
        acc = f"{c.accepted}/{c.drafted} ({100 * c.accepted / c.drafted:.0f}%)" if c.drafted else "-"
        print(f"| {task} | {form} | {p}/{n} | {h}/{nh} | {statistics.median(times):.1f} | {acc} |")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="execute the generated code (read the cases first)")
    ap.add_argument("--timeout", type=float, default=15)
    ap.add_argument("--forms", help="comma-separated subset of the forms")
    args = ap.parse_args()
    if args.forms:
        global NEW_FORMS, FIX_FORMS
        chosen = set(args.forms.split(","))
        NEW_FORMS = tuple(f for f in NEW_FORMS if f in chosen)
        FIX_FORMS = tuple(f for f in FIX_FORMS if f in chosen)
    asyncio.run(main_async(args.run, args.timeout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
