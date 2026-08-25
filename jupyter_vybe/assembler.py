"""Assemble the full agent prompt from the five template files (docs/08)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

PROMPTS = Path(__file__).parent / "prompts"


@lru_cache(maxsize=None)
def _load(name: str) -> str:
    path = PROMPTS / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


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


def render_notebook(cells: list[dict], prompt_cell_id: str = "") -> str:
    """Render the notebook as a numbered listing rather than raw JSON.

    A small model asked to edit a cell has to name a target. Digging a uuid out
    of compact JSON is a poor task for a 7B; reading "[3]" off a listing is an
    easy one. Cells are numbered from 1 in notebook order, the cell directly
    above the prompt is called out, and ids are still shown so either form of
    address works.
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
            tag += f"  id={cid}"
        if prompt_index is not None:
            if i == prompt_index:
                tag += "   <-- this is the prompt cell you are answering"
            elif i == prompt_index - 1:
                tag += '   <-- the cell directly above the prompt ("the cell above")'
        lines.append(tag)

        count = cell.get("execution_count")
        if kind == "code" and count:
            lines.append(f"    (executed, count {count})")
        for line in source.split("\n"):
            lines.append(f"    {line}")

        for out in cell.get("outputs", []) or []:
            text = _output_text(out)
            if text:
                lines.append(f"    # output: {text}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _output_text(out: dict) -> str:
    kind = out.get("output_type")
    if kind == "error":
        return f"{out.get('ename', '')}: {out.get('evalue', '')}"
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


# One-line recap of the tool grammar, repeated on follow-up turns.
#
# Measured, not assumed: with turn 1's full tooling prompt still in the message
# history, a 3-request A/B across "no recap" / "one sentence" / "a 400-char
# recap with tag shapes" scored 3/3 tool-tag compliance in every arm on
# qwen2.5-coder:7b. Recap length simply did not matter. So this is one sentence
# plus the two tag names -- enough to re-point at the grammar if history has
# been truncated, and 57 characters instead of the 2281 of the full prompt.
#
# What actually protects against a lapse is salvage_fenced_blocks() in
# protocol.py, not prompt volume: the same model, given the same prompt, emits
# tags on one run and markdown fences on the next (docs/13 R2).
TOOL_RECAP = (
    "Reply only with <vybe:add_cell> / <vybe:propose_edit> tags, "
    "never markdown code fences."
)


def assemble_followup(*, notebook_diff: str, kernel: dict, user_prompt: str) -> str:
    """Persistent-session turns send a diff, not the whole notebook (docs/09)."""
    diff = notebook_diff.strip() or "(no changes)"
    return (
        f"{kernel_banner(kernel)}\n\n"
        f"{TOOL_RECAP}\n\n"
        f"Changes to the notebook since your last turn:\n\n{diff}\n\n"
        f"## Request\n\n{user_prompt}\n"
    )


def _substitute(template: str, values: dict[str, str]) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def missing_tags(template: str | None = None) -> list[str]:
    """Any {{TAG}} left unfilled — used by the assembler tests."""
    import re

    return re.findall(r"\{\{([A-Z_]+)\}\}", template if template is not None else _load("template.md"))
