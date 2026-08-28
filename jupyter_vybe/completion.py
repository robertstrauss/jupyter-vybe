"""Inline completion: fill-in-the-middle against the same local model (D5, v1.1).

Deliberately a separate subsystem from the prompt-cell flow, because it has a
different latency budget: a prompt cell may take thirty seconds and that is
fine, while a completion is worthless if it arrives after the next keystroke.
Everything here is shaped by that -- short outputs, hard timeouts, a stop at
the first structural boundary, and cancellation that actually cancels.

FIM is a *raw* completion, not a chat turn: the model is given the text before
and after the cursor in its own infilling format and continues in the middle.
Wrapping that in a chat template would ask a different question and get prose
about the code instead of the code.
"""

from __future__ import annotations

import re

# Per-family infilling tokens. A model whose family is unknown gets the
# instruction fallback below, which is worse but works everywhere.
FIM_FORMATS: dict[str, dict[str, str]] = {
    "qwen": {
        "prefix": "<|fim_prefix|>",
        "suffix": "<|fim_suffix|>",
        "middle": "<|fim_middle|>",
        "stop": "<|endoftext|>",
    },
    "codellama": {
        "prefix": "<PRE> ",
        "suffix": " <SUF>",
        "middle": " <MID>",
        "stop": "<EOT>",
    },
    "starcoder": {
        "prefix": "<fim_prefix>",
        "suffix": "<fim_suffix>",
        "middle": "<fim_middle>",
        "stop": "<|endoftext|>",
    },
}

# How much context either side of the cursor. Enough to see the function being
# written and its imports; not so much that prefill dominates the budget.
PREFIX_CHARS = 2000
SUFFIX_CHARS = 800


def family_for(model: str) -> str | None:
    lowered = (model or "").lower()
    for family in FIM_FORMATS:
        if family in lowered:
            return family
    if "coder" in lowered:  # qwen2.5-coder and friends name themselves this way
        return "qwen"
    return None


def build_fim_prompt(prefix: str, suffix: str, model: str) -> tuple[str, bool]:
    """Return (prompt, is_raw). is_raw=False means it needs a chat turn."""
    prefix = prefix[-PREFIX_CHARS:]
    suffix = suffix[:SUFFIX_CHARS]
    family = family_for(model)
    if family:
        f = FIM_FORMATS[family]
        return f"{f['prefix']}{prefix}{f['suffix']}{suffix}{f['middle']}", True
    # No infilling format: ask in words, and ask narrowly. A model told to
    # "explain" will explain; told to emit only a continuation, most will.
    return (
        "Continue this code at the cursor. Reply with the continuation only: "
        "no explanation, no markdown fence, no repetition of what is already "
        "there.\n\n"
        f"BEFORE THE CURSOR:\n{prefix}\n\nAFTER THE CURSOR:\n{suffix}\n\n"
        "CONTINUATION:"
    ), False


_FENCE = re.compile(r"^\s*```[a-zA-Z0-9_+-]*\n?|\n?```\s*$")


def clean(text: str, suffix: str, multiline: bool) -> str:
    """Make a raw model continuation safe to show as ghost text.

    Three things go wrong often enough to handle here rather than in the UI:
    a fence around the answer, the model continuing past the cursor into text
    that already exists after it, and a "completion" that is only whitespace.
    """
    text = _FENCE.sub("", text)
    for token in ("<|endoftext|>", "<|fim_pad|>", "<EOT>", "<|file_sep|>"):
        text = text.split(token)[0]
    if not multiline:
        text = text.split("\n")[0]
    else:
        # Stop at a blank line followed by a new top-level statement: past that
        # the model is writing a second thing, not finishing this one.
        cut = re.search(r"\n\s*\n(?=\S)", text)
        if cut:
            text = text[: cut.start()]
    # If the model reproduced what already follows the cursor, drop the overlap
    # rather than showing the user their own code twice.
    stripped = suffix.lstrip()
    if stripped:
        head = stripped.split("\n")[0].strip()
        if head and text.rstrip().endswith(head):
            text = text.rstrip()[: -len(head)]
    return text.rstrip() if text.strip() else ""


# A line that opens a block or an unclosed bracket is one the model should be
# allowed to finish across lines; anything else, the user is mid-statement.
_OPENS_BLOCK = re.compile(r"[:{([,]\s*$")


def should_be_multiline(prefix: str, allowed: bool) -> bool:
    """Whether a completion may span lines *here*, not just in general.

    Measured against a live model: asked to finish `df['total'] = ` it returned
    the expression and then a `print(df)` on the next line -- correct code,
    wrong answer, because the user is in the middle of one statement. A
    completion may run on only when the line it continues is unfinished.
    """
    if not allowed:
        return False
    if not prefix or prefix.endswith("\n"):
        return True
    return bool(_OPENS_BLOCK.search(prefix.rsplit("\n", 1)[-1]))


def looks_useless(text: str, prefix: str) -> bool:
    """Filter completions not worth a flicker of ghost text."""
    if not text.strip():
        return True
    if len(text.strip()) < 2:
        return True
    # A completion that only repeats the line the user is already on.
    current_line = prefix.rsplit("\n", 1)[-1].strip()
    return bool(current_line) and text.strip() == current_line
