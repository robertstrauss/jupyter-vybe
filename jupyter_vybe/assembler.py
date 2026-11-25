"""Assemble the full agent prompt from the five template files (docs/08)."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

PROMPTS = Path(__file__).parent / "prompts"


@lru_cache(maxsize=None)
def _load(name: str) -> str:
    path = PROMPTS / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


def preload_prompts() -> None:
    """Read every template once, so the first prompt does not read any.

    Cheap next to loading a model -- assembly measured at 0.1ms (docs/08) --
    but it is the same idea and it costs nothing to do it at the same time.
    """
    for name in ("universal.md", "global.md", "tooling.md", "filtering.md", "template.md"):
        _load(name)


def kernel_banner(kernel: dict) -> str:
    """Pinned context — never subject to the truncation ladder (D13, docs/09)."""
    language = kernel.get("language") or "unknown"
    name = kernel.get("name") or "unknown"
    version = kernel.get("version") or ""
    if language == "unknown":
        return (
            "Kernel: unknown — this notebook declares no kernel and has no code.\n"
            "You cannot tell which language to write. Ask which language, in one short "
            "sentence of prose, and emit no cells."
        )
    return (
        f"Kernel: {name} — language {language} {version}".rstrip()
        + f"\nWrite code in {language} unless the user asks otherwise."
    )


def render_notebook(
    cells: list[dict],
    prompt_cell_id: str = "",
    brief: frozenset | set = frozenset(),
    stale: frozenset | set = frozenset(),
) -> str:
    """Render the notebook as a numbered listing rather than raw JSON.

    A small model asked to edit a cell has to name a target. Digging a uuid out
    of compact JSON is a poor task for a 7B; reading "[3]" off a listing is an
    easy one. Cells are numbered from 1 in notebook order, the cell directly
    above the prompt is called out, and ids are still shown so either form of
    address works.

    Cells whose id is in `brief` get one line instead of their source: on a
    follow-up turn, those are the cells the agent already has word for word
    from the last full listing, so their number is all it needs here.

    Cells whose id is in `stale` were edited after they last ran, so their
    outputs belong to code that is no longer there. Said outright: shown an
    old traceback under fixed code, a model "fixes" it again, into itself.
    """
    lines: list[str] = []
    prompt_index = next(
        (i for i, c in enumerate(cells) if c.get("id") == prompt_cell_id), None
    )
    for i, cell in enumerate(cells):
        n = i + 1
        kind = cell.get("cell_type", "code")
        cid = cell.get("id", "")
        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(source)

        tag = f"[{n}] {kind}"
        if cid:
            tag += f" id={cid}"
        tag += ":"
        if cid and cid in brief:
            first = next((ln.strip() for ln in source.split("\n") if ln.strip()), "(empty)")
            tag += f" {first[:72]}   (unchanged since you last saw it in full)"
        if prompt_index is not None:
            if i == prompt_index:
                tag += "   <-- this is the prompt cell you are answering"
            elif i == prompt_index - 1:
                tag += '   <-- the cell directly above the prompt ("the cell above")'
        lines.append(tag)
        if cid and cid in brief:
            continue

        count = cell.get("execution_count")
        if kind == "code" and count:
            if cid and cid in stale:
                lines.append(
                    f"    (executed, count {count}, then edited and not run since: "
                    "the output below is from the old code, not this code)"
                )
            else:
                lines.append(f"    (executed, count {count})")
        for line in source.split("\n"):
            lines.append(f"    {line}")

        for out in cell.get("outputs", []) or []:
            text = _output_text(out)
            if text:
                lines.extend(f"    # output: {part}" for part in text.split("\n"))
        # Explicit terminator: without it a header can read as belonging to the
        # block above it as easily as the block below.
        lines.append(f"[end of cell {n}]")
        lines.append("")
    return "\n".join(lines).rstrip()


EVALUE_CAP = 300
CODE_LINE_CAP = 160
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_ARROW = re.compile(r"^\s*-*>\s*\d+\s?(.*)$")
_FRAME_HEAD = re.compile(r"^(Cell In\[|File |Input In \[|<ipython-input)")


def _clip(text: str, cap: int) -> str:
    text = text.strip()
    return text if len(text) <= cap else text[: cap - 1] + "\u2026"


def _frame(entry: str) -> str:
    """One traceback frame as `where: the line that ran`, or "" if not a frame.

    IPython renders each frame as a header (`Cell In[2], line 5, in g()` or
    `File .../json/decoder.py:355, in raw_decode(...)`) followed by a few
    lines of context, the executing one marked with an arrow. A syntax error
    has no arrow; its offending line is the one after the header.
    """
    lines = [ln for ln in _ANSI.sub("", entry).split("\n") if ln.strip()]
    if len(lines) < 2 or not _FRAME_HEAD.match(lines[0].strip()):
        return ""
    head = lines[0].strip()
    if head.startswith("File "):
        # Library paths are long and say little: keep the package and file.
        path, _, rest = head[5:].partition(":")
        head = "File " + "/".join(Path(path).parts[-2:]) + ":" + rest
    code = next((m.group(1) for m in map(_ARROW.match, lines[1:]) if m), None)
    if code is None:
        code = lines[1]
    return f"{_clip(head, CODE_LINE_CAP)}: {_clip(code, CODE_LINE_CAP)}"


def _error_text(out: dict) -> str:
    """The error, then the first and last frames of its traceback.

    The first frame is where the user's cell called in; the last is where it
    actually raised. Between them is usually library plumbing, so it is
    counted rather than shown. Each frame costs one line, so a deep pandas
    traceback is four lines, not four screens.
    """
    evalue = _clip(str(out.get("evalue", "")).replace("\n", " "), EVALUE_CAP)
    lines = [f"{out.get('ename', '')}: {evalue}"]
    frames = [f for f in map(_frame, out.get("traceback") or []) if f]
    if frames:
        lines.append(f"  at {frames[0]}")
        if len(frames) > 2:
            hidden = len(frames) - 2
            lines.append(f"  ... {hidden} more frame{'s' if hidden > 1 else ''}")
        if len(frames) > 1:
            lines.append(f"  at {frames[-1]}")
    return "\n".join(lines)


def _output_text(out: dict) -> str:
    """An output as the agent sees it. Errors may span lines; nothing else does."""
    kind = out.get("output_type")
    if kind == "error":
        return _error_text(out)
    if kind == "stream":
        text = out.get("text", "")
        if isinstance(text, list):
            text = "".join(text)
        return text.strip().replace("\n", " ")[:200]
    data = out.get("data", {}) or {}
    plain = data.get("text/plain", "")
    if isinstance(plain, list):
        plain = "".join(plain)
    return str(plain).strip().replace("\n", " ")[:200]


def resolve_cell_ref(ref: str, cells: list[dict]) -> str:
    """Map whatever the model wrote in cell="..." to a real cell id.

    Accepts a 1-based index ("3", "[3]", "cell 3") or an id. Returns "" if it
    cannot be resolved, so the caller can report a protocol error rather than
    edit the wrong cell.
    """
    ref = (ref or "").strip().strip("[]").strip()
    if not ref:
        return ""
    lowered = ref.lower()
    for prefix in ("cell ", "cell#", "cell", "#"):
        if lowered.startswith(prefix):
            ref = ref[len(prefix) :].strip()
            break
    if ref.isdigit():
        idx = int(ref) - 1
        if 0 <= idx < len(cells):
            return cells[idx].get("id", "")
        return ""
    for cell in cells:
        if cell.get("id") == ref:
            return ref
    return ""


def assemble_initial(
    *,
    notebook_json: str,
    kernel: dict,
    prompt_cell_id: str,
    user_prompt: str,
    global_prompt: str = "",
    tooling: str | None = None,
    notebook_mode: str = "Full notebook state:",
) -> str:
    cells: list[dict] = []
    try:
        cells = json.loads(notebook_json).get("cells", [])
    except (json.JSONDecodeError, AttributeError, TypeError):
        cells = []
    template = _load("template.md")
    values = {
        "UNIVERSAL_PROMPT": _load("universal.md"),
        "TOOLING_PROMPT": tooling if tooling is not None else _load("tooling.md"),
        "KERNEL_BANNER": kernel_banner(kernel),
        "NOTEBOOK_MODE": notebook_mode,
        "NOTEBOOK_JSON": render_notebook(cells, prompt_cell_id) if cells else notebook_json,
        "PROMPT_CELL_ID": prompt_cell_id,
        "GLOBAL_PROMPT": global_prompt.strip() or _load("global.md"),
        "USER_PROMPT": user_prompt,
    }
    return _substitute(template, values)


# Repeated on every follow-up turn, where turn 1's full tooling prompt is still
# in the message history and still in context. So this is a reminder, not a
# re-explanation -- measured: a 3-request A/B across "no recap" / "one sentence"
# / "a 400-char recap" scored 3/3 tool-tag compliance in every arm. Recap length
# did not matter to compliance.
#
# It names the four rules whose absence produced real, silent damage anyway: a
# fence instead of a tag, an invented cell number, a duplicate instead of an
# edit, and a body indented to match the listing. What actually protects against
# a lapse is salvage_fenced_blocks() in protocol.py, not prompt volume: the same
# model, given the same prompt, emits tags on one run and fences on the next
# (docs/13 R2).
TOOL_RECAP = (
    "Reply only with <vybe:add_cell> / <vybe:propose_edit> tags, "
    "never markdown code fences.\n"
    "Address a cell by its bracket number in the listing below, e.g. cell=\"3\".\n"
    "Changing code that already exists is a propose_edit with the complete new "
    "source of that cell, not an add_cell with a modified copy.\n"
    "The listing indents each cell by four spaces for layout; write cell bodies "
    "at column zero."
)


def assemble_followup(
    *, notebook_diff: str, kernel: dict, user_prompt: str, notebook_listing: str = ""
) -> str:
    """A follow-up turn in a persistent session: what changed, what is, what to do.

    In this order, each part measured on qwen 7B (docs/09 has the table):

    1. **The diff** -- the record of what changed since the last turn.
    2. **The notebook as it is now**: every cell changed since the last full
       listing in full, with its outputs, the rest one line each. Asked to fix
       the error a run had just produced, with only the diff the agent edited
       the right cell 0 times in 10; with the current cells after the diff,
       14 in 16. Placed *before* the diff, the same cells scored 0 in 8:
       having the present is not enough, it has to be the last thing read.
    3. **The rules**, next to the request. Placed first instead, "edit the
       cell above" became a modified copy in a new cell half the time (4 in 8,
       against 16 in 16 here).

    The listing is also what makes a cell addressable. Without it the agent
    invents an index, which resolves to a real but wrong cell -- the worst
    failure this tool can produce.
    """
    diff = notebook_diff.strip() or "(no changes since your last turn)"
    parts = [kernel_banner(kernel), f"Changes since your last turn:\n\n{diff}"]
    if notebook_listing:
        parts.append(
            "The notebook as it is now. Cells changed since you last saw the whole "
            "notebook are shown in full, with their outputs; the others are "
            f"unchanged since then.\n\n{notebook_listing}"
        )
    parts.append(TOOL_RECAP)
    parts.append(f"## Request\n\n{user_prompt}")
    return "\n\n".join(parts) + "\n"


def _substitute(template: str, values: dict[str, str]) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def missing_tags(template: str | None = None) -> list[str]:
    """Any {{TAG}} left unfilled — used by the assembler tests."""
    import re

    return re.findall(r"\{\{([A-Z_]+)\}\}", template if template is not None else _load("template.md"))
