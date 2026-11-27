"""Zeta 2 as a notebook agent: a turn as an edit prediction (docs/15).

Zed's Zeta 2 is Seed-Coder-8B fine-tuned to predict a programmer's next edit,
not to follow instructions. It never sees a chat message or a tool tag. Its
prompt is a fill-in-the-middle file:

    <[fim-suffix]>code after the editable region
    <[fim-prefix]><filename>diagnostics            (optional)
    *error message*: ...
    <filename>edit_history                         (unified diffs, oldest first)
    --- a/notebook.py
    +++ b/notebook.py
    ...
    <filename>notebook.py
    code before the editable region
    <<<<<<< CURRENT
    the region, with <|user_cursor|> where the user is typing
    =======
    <[fim-middle]>

and it answers with the region rewritten, then ``>>>>>>> UPDATED``. This is
Zed's "zeta2" format (V0211SeedCoder in crates/zeta_prompt).

So a notebook turn has to be phrased as something a programmer is in the
middle of doing. Measured against the demo notebook (docs/15):

* The notebook is one Python file in jupytext percent form (``# %%`` between
  cells), markdown commented out, outputs as ``# Out:`` comments.
* The request is a comment the user has just typed, shown both in the file and
  as the latest edit in the history, with the cursor on the line after it.
* **The cursor says where.** An instruction comment in the prompt cell never
  produced an edit to another cell, even with that cell inside the editable
  region. Typed as the first line of the target cell, with the cursor after
  it, it did. So the turn chooses the place: the prompt cell for new code, the
  cell above for a change, the cell that raised -- with the error in the
  diagnostics section -- for a fix. At the end of the cell instead, a change
  became an addition (a new triple() below double()); that placement is kept
  as the fallback for when the top predicts nothing.
* It predicts the *next* edit, which for "add a function" is a skeleton with
  the cursor left in the body. Predictions are chained, each applied before
  the next is asked for, until one changes nothing or leaves no cursor.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

FIM_SUFFIX = "<[fim-suffix]>"
FIM_PREFIX = "<[fim-prefix]>"
FIM_MIDDLE = "<[fim-middle]>"
FILE = "<filename>"
START = "<<<<<<< CURRENT\n"
SEPARATOR = "=======\n"
END = ">>>>>>> UPDATED"
CURSOR = "<|user_cursor|>"
NO_EDITS = "NO_EDITS"
EOS = "<[end\u2581of\u2581sentence]>"
SPECIAL = (FIM_SUFFIX, FIM_PREFIX, FIM_MIDDLE, FILE, CURSOR, START.strip(), SEPARATOR.strip(), END)

PATH = "notebook.py"
CELL = "# %%"
MAX_STEPS = 4
# Zed's budget for this format is 4096 prompt tokens, with a 350-token
# editable region. Characters, at roughly 3.5 per token, leaving room for the
# region, the history and the reply.
BEFORE_CHARS = 7000
AFTER_CHARS = 2000
OUTPUT_CHARS = 300

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_CELL_LINE = re.compile(r"Cell In\[(\d+)\], line (\d+)")
_FIX = re.compile(r"\b(fix|error|errors|bug|traceback|exception|broken|fails?|failing|crash\w*)\b", re.I)
# A request to change what is already there rather than to add something.
_EDIT = re.compile(
    r"\b(change|modify|edit|rename|update|refactor|replace|convert|round|remove|delete|"
    r"rewrite|simplify|instead|above|this cell|previous cell|make (it|this|that|the)|"
    r"move|swap|use \w+ instead)\b",
    re.I,
)


def _source(cell: dict) -> str:
    src = cell.get("source", "")
    return "".join(src) if isinstance(src, list) else str(src)


def _comment(text: str) -> str:
    return "\n".join(f"# {line}" if line.strip() else "#" for line in text.split("\n"))


def _output_comment(cell: dict) -> str:
    lines: list[str] = []
    for out in cell.get("outputs") or []:
        kind = out.get("output_type")
        if kind == "error":
            continue  # carried by the diagnostics section, where it can point at a line
        if kind == "stream":
            text = _source({"source": out.get("text", "")})
        else:
            text = _source({"source": (out.get("data") or {}).get("text/plain", "")})
        text = text.strip()
        if text:
            lines.extend(text[:OUTPUT_CHARS].split("\n")[:6])
    return "".join(f"# Out: {line}\n" for line in lines)


def render_cell(cell: dict) -> str:
    """One cell as it appears in the file, ending in a newline."""
    src = _source(cell).rstrip("\n")
    if cell.get("cell_type") == "markdown":
        return f"{CELL} [markdown]\n{_comment(src)}\n"
    if cell.get("cell_type") == "raw":
        return f"{CELL} [raw]\n{_comment(src)}\n"
    body = f"{src}\n" if src else ""
    return f"{CELL}\n{body}{_output_comment(cell)}"


def _error(cell: dict) -> dict | None:
    return next((o for o in cell.get("outputs") or [] if o.get("output_type") == "error"), None)


def _error_line(cell: dict, err: dict) -> int | None:
    """The 1-based line of this cell the traceback says raised, if it says.

    The last frame in this cell, not the first: a cell that defines a function
    and then calls it has the call as its first frame and the line that
    actually raised, inside the function, as a later one.
    """
    count = cell.get("execution_count")
    line = None
    for entry in err.get("traceback") or []:
        m = _CELL_LINE.search(_ANSI.sub("", entry))
        if m and (count is None or int(m.group(1)) == count):
            line = int(m.group(2))
    return line


@dataclass
class Plan:
    """What one turn asks Zeta, and how to read its answer back."""

    mode: str  # "insert" | "edit" | "fix"
    anchor_id: str  # the cell whose region is rewritten
    region: str  # the editable region, cursor included
    before: str
    after: str
    diagnostics: str = ""
    history: list[str] = field(default_factory=list)
    request_lines: list[str] = field(default_factory=list)  # comment lines we added
    original: str = ""  # the anchor cell's source, for an edit or a fix

    def prompt(self) -> str:
        suffix = self.after if self.after.endswith("\n") or not self.after else self.after + "\n"
        out = FIM_SUFFIX + (suffix or "\n") + FIM_PREFIX
        if self.diagnostics:
            out += self.diagnostics + "\n"
        if self.history:
            out += f"{FILE}edit_history\n" + "".join(self.history) + "\n"
        return out + f"{FILE}{PATH}\n" + self.before + START + self.region + SEPARATOR + FIM_MIDDLE


def _mode(request: str, cells: list[dict], prompt_index: int) -> tuple[str, int | None]:
    """Insert below the prompt, edit the cell above, or fix the nearest error."""
    above = [i for i in range(prompt_index - 1, -1, -1) if cells[i].get("cell_type") == "code"]
    if _FIX.search(request):
        failing = next((i for i in above if _error(cells[i])), None)
        if failing is not None:
            return "fix", failing
    if _EDIT.search(request) and above:
        return "edit", above[0]
    return "insert", None


def _trim_before(parts: list[str]) -> str:
    kept: list[str] = []
    total = 0
    for part in reversed(parts):  # nearest first
        if total + len(part) > BEFORE_CHARS:
            break
        kept.append(part)
        total += len(part)
    return "".join(reversed(kept))


def _trim_after(parts: list[str]) -> str:
    kept: list[str] = []
    total = 0
    for part in parts:
        if total + len(part) > AFTER_CHARS:
            break
        kept.append(part)
        total += len(part)
    return "".join(kept)


def _insertion_event(line_no: int, added: list[str]) -> str:
    body = "".join(f"+{line}\n" for line in added)
    return f"--- a/{PATH}\n+++ b/{PATH}\n@@ -{line_no},0 +{line_no + 1},{len(added)} @@\n{body}"


def history_since(previous: list[dict], current: list[dict], prompt_cell_id: str) -> list[str]:
    """The notebook's edits since the last turn, as Zeta's edit history.

    Zeta was trained on exactly this: the user's recent edits as unified diffs.
    Cells are rendered without outputs, so a run alone is not an edit.
    """
    if not previous:
        return []

    def text(cells: list[dict]) -> list[str]:
        out: list[str] = []
        for cell in cells:
            if cell.get("id") == prompt_cell_id:
                continue
            bare = {**cell, "outputs": []}
            out.extend(render_cell(bare).splitlines(keepends=True))
        return out

    diff = list(difflib.unified_diff(text(previous), text(current), n=1))
    if not diff:
        return []
    body = "".join(line for line in diff[2:])  # drop difflib's own ---/+++ lines
    return [f"--- a/{PATH}\n+++ b/{PATH}\n{body[-3000:]}"]


def plan_turn(
    cells: list[dict],
    prompt_cell_id: str,
    request: str,
    history: list[str] | None = None,
    placement: str = "top",
) -> Plan:
    """Lay the notebook out as Zeta's file, with the cursor where the work is.

    For a change or a fix, `placement` says where in the target cell the
    request is typed: "top" first, "bottom" as the fallback (docs/15).
    """
    prompt_index = next(
        (i for i, c in enumerate(cells) if c.get("id") == prompt_cell_id), len(cells)
    )
    mode, anchor = _mode(request, cells, prompt_index)
    comment_lines = _comment(request.strip()).split("\n")
    rendered = [render_cell(c) for c in cells]
    events = list(history or [])
    diagnostics = ""

    if mode == "insert":
        before_parts = rendered[:prompt_index]
        after_parts = rendered[prompt_index + 1 :]
        region = f"{CELL}\n" + "\n".join(comment_lines) + f"\n{CURSOR}\n"
        line_no = sum(p.count("\n") for p in before_parts)
        events.append(_insertion_event(line_no, [CELL, *comment_lines]))
        return Plan(
            mode=mode,
            anchor_id=prompt_cell_id,
            region=region,
            before=_trim_before(before_parts),
            after=_trim_after(after_parts),
            history=events[-6:],
            request_lines=comment_lines,
        )

    assert anchor is not None
    cell = cells[anchor]
    src = _source(cell).rstrip("\n")
    # The prompt cell is the instruction, now moved into the cell it is about.
    before_parts = rendered[:anchor]
    after_parts = [r for i, r in enumerate(rendered[anchor + 1 :], anchor + 1) if i != prompt_index]
    lines = src.split("\n")
    line_no = sum(p.count("\n") for p in before_parts) + 1

    if mode == "fix":
        err = _error(cell) or {}
        at = _error_line(cell, err)
        message = f"{err.get('ename', 'Error')}: {err.get('evalue', '')}".strip()
        snippet = lines[at - 1] if at is not None and 1 <= at <= len(lines) else ""
        diagnostics = f"{FILE}diagnostics\n*{message}*" + (
            f":\n```\n{snippet}\n```\n" if snippet else "\n"
        )

    if placement == "top":
        # The request typed as the cell's first line, cursor after it. Edited
        # in place where the end of the cell made it append instead: "change
        # double to triple" there became a new triple() below the old one.
        region = f"{CELL}\n" + "\n".join(comment_lines) + f"\n{CURSOR}{src}\n"
        events.append(_insertion_event(line_no, comment_lines))
    else:
        # The fallback when the top predicts nothing: "round the revenue" was
        # only ever answered from here.
        region = f"{CELL}\n{src}\n" + "\n".join(comment_lines) + f"\n{CURSOR}\n"
        events.append(_insertion_event(line_no + len(lines), comment_lines))
    return Plan(
        mode=mode,
        anchor_id=cell.get("id", ""),
        region=region,
        before=_trim_before(before_parts),
        after=_trim_after(after_parts),
        diagnostics=diagnostics,
        history=events[-6:],
        request_lines=comment_lines,
        original=src,
    )


def fim_prompt(plan: Plan) -> str:
    """New code as plain fill-in-the-middle: the base model's own task.

    No editable region and no end marker: the prefix ends at the request
    comment in a fresh cell, the suffix is the rest of the notebook, and the
    model writes what goes between. For new code this beat the editable-region
    layout on the same cases (9/10 against 8-9/10) in a third of the time
    (0.6 s against 1.9 s), because it is one call rather than a chain of
    next-edit predictions (scripts/bench-zeta-native.py, docs/15).
    """
    suffix = plan.after if plan.after.endswith("\n") or not plan.after else plan.after + "\n"
    head = f"{CELL}\n" + "\n".join(plan.request_lines) + "\n"
    return FIM_SUFFIX + (suffix or "\n") + FIM_PREFIX + f"{FILE}{PATH}\n" + plan.before + head + FIM_MIDDLE


FIM_STOP = [EOS, f"\n{CELL}", FIM_SUFFIX]


def fim_frames(plan: Plan, middle: str) -> list[dict]:
    """What fill-in-the-middle wrote, as cells below the prompt."""
    return frames(plan, middle)


def next_region(plan: Plan, output: str) -> tuple[str, bool]:
    """Zeta's reply as the next region, and whether to ask again.

    Asks again while the prediction changed something and left the cursor in
    it: a cursor left behind is the model saying the edit is not finished.
    """
    out = output.split(END)[0]
    if out.lstrip().startswith(NO_EDITS):
        return plan.region, False
    if not out.endswith("\n"):
        out += "\n"
    changed = _plain(out) != _plain(plan.region)
    return out, changed and CURSOR in out


def _plain(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace(CURSOR, "").strip().split("\n"))


def _clean(text: str, request_lines: list[str]) -> str:
    """Our scaffolding out of a region: cursor, outputs, the request comment."""
    lines = text.replace(CURSOR, "").split("\n")
    drop = {line.strip() for line in request_lines}
    kept = [
        line
        for line in lines
        if not line.lstrip().startswith("# Out:") and line.strip() not in drop
    ]
    return "\n".join(kept).strip("\n")


def _split_cells(text: str) -> list[str]:
    """A region may hold more than one cell; percent markers split them."""
    bodies: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.startswith(CELL):
            if current:
                bodies.append("\n".join(current).strip("\n"))
            current = []
            continue
        current.append(line)
    bodies.append("\n".join(current).strip("\n"))
    return [b for b in bodies if b.strip()]


def frames(plan: Plan, region: str) -> list[dict]:
    """The final region as the frames any other agent's reply becomes."""
    text = _clean(region, plan.request_lines)
    bodies = _split_cells(text)
    if plan.mode == "insert":
        return [{"op": "tool", "name": "add_cell", "args": {"type": "code", "body": b}} for b in bodies]
    if not bodies:
        return []
    first, rest = bodies[0], bodies[1:]
    out: list[dict] = []
    if first.strip() != plan.original.strip():
        out.append({"op": "tool", "name": "propose_edit", "args": {"cell": plan.anchor_id, "body": first}})
    out.extend({"op": "tool", "name": "add_cell", "args": {"type": "code", "body": b}} for b in rest)
    return out


def contains_special_tokens(cells: list[dict]) -> bool:
    """A notebook that spells a control token would be read as one."""
    return any(tok in _source(c) for c in cells for tok in SPECIAL)
