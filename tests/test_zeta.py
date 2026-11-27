"""Zeta 2 as a notebook agent: the turn as an edit prediction (docs/15)."""

import asyncio
import json

from conftest import build_session
from jupyter_vybe import zeta

TRACEBACK = [
    "\x1b[31m-----\x1b[39m",
    'Cell \x1b[32mIn[1]\x1b[39m, line 3\n      1 import csv\n----> 3 with open("sales.csv") as f:',
    "FileNotFoundError: [Errno 2] No such file or directory: 'sales.csv'",
]
LOAD = 'import csv\n\nwith open("sales.csv") as f:\n    rows = list(csv.DictReader(f))\nlen(rows)'


def notebook(load_error=True):
    load = {"id": "load", "cell_type": "code", "source": LOAD, "execution_count": 1, "outputs": []}
    if load_error:
        load["outputs"] = [{
            "output_type": "error",
            "ename": "FileNotFoundError",
            "evalue": "[Errno 2] No such file or directory: 'sales.csv'",
            "traceback": TRACEBACK,
        }]
    else:
        load["outputs"] = [{"output_type": "execute_result", "data": {"text/plain": "1000"}}]
    return [
        {"id": "intro", "cell_type": "markdown", "source": "# Demo\nThe file is sales_2024.csv."},
        load,
        {"id": "p", "cell_type": "code", "source": "> ..."},
    ]


def test_the_notebook_is_one_percent_format_file():
    cells = notebook(load_error=False)
    assert zeta.render_cell(cells[0]) == "# %% [markdown]\n# # Demo\n# The file is sales_2024.csv.\n"
    assert zeta.render_cell(cells[1]).endswith("len(rows)\n# Out: 1000\n")


def test_new_code_goes_below_the_prompt_with_the_request_as_a_comment():
    plan = zeta.plan_turn(notebook(load_error=False), "p", "add a function top(n)")
    assert plan.mode == "insert" and plan.anchor_id == "p"
    assert plan.region == f"# %%\n# add a function top(n)\n{zeta.CURSOR}\n"
    # The request is also the user's latest edit, which is what Zeta reads.
    assert "+# add a function top(n)" in plan.history[-1]
    prompt = plan.prompt()
    assert prompt.startswith(zeta.FIM_SUFFIX)
    assert prompt.endswith(zeta.SEPARATOR + zeta.FIM_MIDDLE)
    assert "len(rows)" in prompt.split(zeta.START)[0], "the cells above are the file before the region"


def test_a_fix_types_the_request_atop_the_cell_that_raised():
    plan = zeta.plan_turn(notebook(), "p", "fix the error")
    assert plan.mode == "fix" and plan.anchor_id == "load"
    assert plan.region == f"# %%\n# fix the error\n{zeta.CURSOR}{LOAD}\n"
    # The error, pointed at the line that raised (the traceback's line 3).
    assert plan.diagnostics.startswith(f"{zeta.FILE}diagnostics\n*FileNotFoundError:")
    assert 'with open("sales.csv") as f:' in plan.diagnostics
    assert "> ..." not in plan.prompt(), "the prompt cell is the instruction, not code"


def test_the_raising_line_is_the_innermost_frame_in_the_cell():
    cells = notebook()
    cells[1]["outputs"][0]["traceback"] = [
        "Cell In[1], line 5\n----> 5 len(rows)",
        "Cell In[1], line 3, in f\n----> 3 with open(...)",
        "FileNotFoundError: ...",
    ]
    assert 'with open("sales.csv") as f:' in zeta.plan_turn(cells, "p", "fix it").diagnostics


def test_a_change_is_typed_atop_the_cell_above_or_failing_that_below_it():
    request = "change it to read sales_2024.csv instead"
    top = zeta.plan_turn(notebook(load_error=False), "p", request)
    assert top.mode == "edit" and top.anchor_id == "load"
    assert top.region == f"# %%\n# {request}\n{zeta.CURSOR}{LOAD}\n"
    bottom = zeta.plan_turn(notebook(load_error=False), "p", request, placement="bottom")
    assert bottom.region.endswith(f"len(rows)\n# {request}\n{zeta.CURSOR}\n")


def test_fix_without_an_error_is_new_code():
    assert zeta.plan_turn(notebook(load_error=False), "p", "write a bug report helper").mode == "insert"


def test_frames_drop_the_scaffolding():
    plan = zeta.plan_turn(notebook(load_error=False), "p", "add a function top(n)")
    region = f"# %%\n# add a function top(n)\ndef top(n):\n    return n{zeta.CURSOR}\n# Out: 3\n"
    assert zeta.frames(plan, region) == [
        {"op": "tool", "name": "add_cell", "args": {"type": "code", "body": "def top(n):\n    return n"}}
    ]


def test_a_region_with_two_cells_becomes_two_cells():
    plan = zeta.plan_turn(notebook(load_error=False), "p", "add f and call it")
    region = "# %%\n# add f and call it\ndef f():\n    return 1\n# %%\nf()\n"
    bodies = [f["args"]["body"] for f in zeta.frames(plan, region)]
    assert bodies == ["def f():\n    return 1", "f()"]


def test_an_edit_is_a_proposal_for_the_anchor_cell():
    plan = zeta.plan_turn(notebook(), "p", "fix the error")
    region = "# %%\n# fix the error\n" + LOAD.replace("sales.csv", "sales_2024.csv") + "\n"
    assert zeta.frames(plan, region) == [{
        "op": "tool",
        "name": "propose_edit",
        "args": {"cell": "load", "body": LOAD.replace("sales.csv", "sales_2024.csv")},
    }]
    assert zeta.frames(plan, "# %%\n" + LOAD + "\n") == [], "no change, no proposal"


def test_chaining_stops_when_the_cursor_is_gone():
    plan = zeta.plan_turn(notebook(load_error=False), "p", "add top(n)")
    skeleton = f"# %%\n# add top(n)\ndef top(n):\n    {zeta.CURSOR}\n"
    region, again = zeta.next_region(plan, skeleton + zeta.END)
    assert again and region == skeleton
    plan.region = region
    done = "# %%\n# add top(n)\ndef top(n):\n    return n\n"
    region, again = zeta.next_region(plan, done)
    assert not again and region == done
    assert zeta.next_region(plan, "NO_EDITS\n")[1] is False


def test_history_since_ignores_a_run_but_not_an_edit():
    before = notebook(load_error=True)
    ran = notebook(load_error=False)
    assert zeta.history_since(before, ran, "p") == []
    edited = notebook(load_error=False)
    edited[1] = {**edited[1], "source": LOAD.replace("sales.csv", "sales_2024.csv")}
    (event,) = zeta.history_since(before, edited, "p")
    assert '-with open("sales.csv") as f:' in event and '+with open("sales_2024.csv") as f:' in event


class Predictor:
    edit_predict = True

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    async def complete(self, prompt, cancel, n_predict=768):
        self.prompts.append(prompt)
        return self.replies.pop(0), {}


def run(session, cells, request):
    msg = {"prompt": request, "snapshot": json.dumps({"cells": cells}), "snapshotSeq": 0,
           "promptCellId": "p", "roundId": "r"}

    async def go():
        return [f async for f in session.run(msg)]

    return asyncio.run(go())


def test_the_session_chains_predictions_into_one_cell():
    adapter = Predictor([
        f"# %%\n# add top(n)\ndef top(n):\n    {zeta.CURSOR}\n",
        "# %%\n# add top(n)\ndef top(n):\n    return sorted(rows)[:n]\n",
    ])
    frames = run(build_session(adapter), notebook(load_error=False), "add top(n)")
    assert len(adapter.prompts) == 2
    tools = [f for f in frames if f["op"] == "tool"]
    assert tools == [{"op": "tool", "name": "add_cell",
                      "args": {"type": "code", "body": "def top(n):\n    return sorted(rows)[:n]"}}]
    assert frames[-1] == {"op": "done", "stopReason": "end"}


def test_the_session_says_so_when_nothing_changes():
    adapter = Predictor(["NO_EDITS\n", "NO_EDITS\n"])
    frames = run(build_session(adapter), notebook(), "fix the error")
    assert len(adapter.prompts) == 2, "the top, then the bottom of the cell"
    assert not [f for f in frames if f["op"] == "tool"]
    assert any(f["op"] == "prose" and "no change" in f["delta"] for f in frames)


def test_a_change_falls_back_to_the_bottom_of_the_cell():
    fixed = LOAD.replace("sales.csv", "sales_2024.csv")
    adapter = Predictor(["NO_EDITS\n", f"# %%\n{fixed}\n# fix the error\n"])
    frames = run(build_session(adapter), notebook(), "fix the error")
    assert f"{LOAD}\n# fix the error\n{zeta.CURSOR}" in adapter.prompts[1]
    assert [f["args"]["body"] for f in frames if f["op"] == "tool"] == [fixed]


def test_control_tokens_in_the_notebook_are_refused():
    cells = notebook()
    cells[0]["source"] = "see <|user_cursor|> here"
    frames = run(build_session(Predictor([])), cells, "fix the error")
    assert frames[0]["op"] == "error"


class Filler(Predictor):
    def __init__(self, fills, replies=()):
        super().__init__(replies)
        self.fills = list(fills)
        self.fill_prompts = []

    async def fill(self, prompt, stop, cancel, n_predict=512):
        self.fill_prompts.append(prompt)
        return self.fills.pop(0), {}


def test_new_code_is_one_fill_in_the_middle_call():
    adapter = Filler(["def top(n):\n    return sorted(rows)[:n]\n"])
    frames = run(build_session(adapter), notebook(load_error=False), "add top(n)")
    (prompt,) = adapter.fill_prompts
    assert prompt.endswith("# %%\n# add top(n)\n" + zeta.FIM_MIDDLE)
    assert zeta.START not in prompt, "plain FIM: no editable region"
    assert adapter.prompts == [], "no next-edit chain needed"
    assert [f["args"]["body"] for f in frames if f["op"] == "tool"] == ["def top(n):\n    return sorted(rows)[:n]"]


def test_an_empty_fill_falls_back_to_the_editable_region():
    adapter = Filler([""], ["# %%\n# add top(n)\ndef top(n):\n    return n\n"])
    frames = run(build_session(adapter), notebook(load_error=False), "add top(n)")
    assert len(adapter.prompts) == 1
    assert [f["args"]["body"] for f in frames if f["op"] == "tool"] == ["def top(n):\n    return n"]


def test_a_fix_does_not_use_fill_in_the_middle():
    fixed = LOAD.replace("sales.csv", "sales_2024.csv")
    adapter = Filler([], [f"# %%\n# fix the error\n{fixed}\n"])
    run(build_session(adapter), notebook(), "fix the error")
    assert adapter.fill_prompts == [] and len(adapter.prompts) == 1
