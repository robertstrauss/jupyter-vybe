"""Cell-level diff summaries for follow-up turns (docs/09).

PROMPT §6: "don't repeat the entire notebook, use the proper edit/difference
based summary". Two things go on a follow-up turn instead of the whole
notebook:

* an *index* -- one line per cell, numbered, first line only -- because the
  agent still has to name a cell to edit it, and a number it cannot see is a
  number it will invent (that produced silent edits to the wrong cell);
* a *diff* of what changed since the last turn.

Execution is reported separately from source: a cell that merely ran is a
different signal from a cell that changed, and conflating them makes the agent
think code moved when only its output did.
"""

from __future__ import annotations

import difflib

CONTEXT = 3
FIRST_LINE_CHARS = 72


def _source(cell: dict) -> str:
    src = cell.get("source") or ""
    return "".join(src) if isinstance(src, list) else src


def _first_line(cell: dict) -> str:
    for line in _source(cell).split("\n"):
        if line.strip():
            return line.strip()[:FIRST_LINE_CHARS]
    return "(empty)"


def render_index(cells: list[dict], prompt_cell_id: str = "") -> str:
    """One line per cell: enough to address it, not enough to read it."""
    lines: list[str] = []
    prompt_index = next(
        (i for i, c in enumerate(cells) if c.get("id") == prompt_cell_id), None
    )
    for i, cell in enumerate(cells):
        n = i + 1
        tag = f"[{n}] {cell.get('cell_type', 'code')}"
        cid = cell.get("id", "")
        if cid:
            tag += f" id={cid}"
        tag += f": {_first_line(cell)}"
        if prompt_index is not None:
            if i == prompt_index:
                tag += "   <-- this is the prompt cell you are answering"
            elif i == prompt_index - 1:
                tag += '   <-- the cell directly above the prompt ("the cell above")'
        lines.append(tag)
    return "\n".join(lines)


def _hunks(before: str, after: str) -> str:
    """Unified +/- with context, as the spec's example shows."""
    diff = difflib.unified_diff(
        before.split("\n"),
        after.split("\n"),
        lineterm="",
        n=CONTEXT,
    )
    body = [line for line in diff if not line.startswith(("---", "+++"))]
    return "\n".join(body)


def _reordered(before: list, after: list) -> set:
    """Ids whose position relative to the other surviving cells changed.

    Deleting a cell shifts every index below it, and reporting all of those as
    moves buries the one thing that actually moved.
    """
    keep = set()
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            keep.update(before[i1:i2])
    return (set(before) | set(after)) - keep


def summarise(previous: list[dict], current: list[dict], prompt_cell_id: str = "") -> str:
    """Describe current in terms of previous, cell by cell."""
    prev_by_id = {c.get("id"): c for c in previous if c.get("id")}
    cur_by_id = {c.get("id"): c for c in current if c.get("id")}
    prev_order = [c.get("id") for c in previous if c.get("id")]
    cur_order = [c.get("id") for c in current if c.get("id")]

    common_before = [c for c in prev_order if c in cur_by_id]
    common_after = [c for c in cur_order if c in prev_by_id]
    moved = _reordered(common_before, common_after)

    out: list[str] = []
    unchanged = 0

    for index, cell in enumerate(current):
        cid = cell.get("id")
        kind = cell.get("cell_type", "code")
        if cid == prompt_cell_id:
            # The prompt cell is the request, not a change to report.
            continue
        if cid not in prev_by_id:
            after = current[index - 1].get("id") if index else None
            where = f" after cell {after}" if after else " at the top"
            out.append(f"ADDED   cell {cid}{where} ({kind})")
            out.extend(f"+ {line}" for line in _source(cell).split("\n"))
            out.append("")
            continue

        old = prev_by_id[cid]
        old_source, new_source = _source(old), _source(cell)
        changed = False
        if old_source != new_source:
            out.append(f"EDITED  cell {cid} ({kind})")
            out.append(_hunks(old_source, new_source))
            out.append("")
            changed = True

        old_count = old.get("execution_count")
        new_count = cell.get("execution_count")
        if kind == "code" and new_count and new_count != old_count:
            note = f"RAN     cell {cid} -> execution_count {new_count}"
            if (old.get("outputs") or []) != (cell.get("outputs") or []):
                note += ", output changed"
            out.append(note)
            out.append("")
            changed = True

        # A cell whose index shifted because something above it was deleted
        # has not moved; only a change in *relative* order is a move.
        if cid in moved:
            out.append(
                f"MOVED   cell {cid} from index {prev_order.index(cid) + 1} "
                f"to index {cur_order.index(cid) + 1}"
            )
            out.append("")
            changed = True

        if not changed:
            unchanged += 1

    for cid in prev_order:
        if cid not in cur_by_id and cid != prompt_cell_id:
            kind = prev_by_id[cid].get("cell_type", "code")
            out.append(f"REMOVED cell {cid} ({kind})")
            out.append("")

    if unchanged:
        out.append(f"UNCHANGED: {unchanged} other cell{'s' if unchanged != 1 else ''}")

    return "\n".join(out).strip()
