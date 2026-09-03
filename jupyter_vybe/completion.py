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


# ---- predicting the rest of a cell (v1.2) --------------------------------
#
# The fill-in-the-middle path above predicts a cell from itself. That is the
# wrong context for a notebook: what a cell should say next is mostly decided
# by what the cells above it defined. Everything below sends the notebook as
# a script and asks, in words, for the next line or two -- which every adapter
# can answer, with no per-model token vocabulary and no raw-completion route.

PREDICT_INSTRUCTIONS = """\
You are completing a cell in a Jupyter notebook. The notebook so far is below,
written as a Python script: each cell begins with a `# In[n]:` marker, and
markdown cells are commented out.

Continue cell {cell} from exactly where it stops. Reply with code only -- no
explanation, no fences, and do not repeat any of the code that is already
there.

Write at most {max_lines} line(s), and prefer to stop at one. The user asks
again for the next line, so a single certain line is worth more than several
guessed ones, and stopping part-way through something is expected and fine.

The one exception: if that line opens a block -- it ends in a colon, or leaves
a bracket or a string unclosed -- write on until the block has a body and the
code would parse. A `for` header with nothing under it is a syntax error, not
a short answer.
"""


def build_predict_prompt(script: str, cell: int, max_lines: int = 1, hints: str = "") -> str:
    """The whole request, as one instruction turn."""
    parts = [
        PREDICT_INSTRUCTIONS.format(cell=cell, max_lines=max_lines),
        "```python\n" + script + "\n```",
    ]
    if hints.strip():
        # What the kernel knows about the names in play. Live truth about the
        # session beats the model's guess at what an object offers, and it is
        # the one thing an offline model cannot infer from the notebook.
        parts.append("From the running kernel:\n" + hints.strip())
    return "\n\n".join(parts)


def strip_fences(text: str) -> str:
    """Remove code fences wherever the model put them.

    Asked for code with no fences, models produce fences anyway, often only
    the opening one. Stripping every fence line is safe here because the reply
    is code by construction: a lone ``` is never part of the answer.
    """
    lines = [line for line in text.split("\n") if not _FENCE_LINE.match(line)]
    return "\n".join(lines)


_FENCE_LINE = re.compile(r"^\s*```[A-Za-z0-9_+-]*\s*$")


def limit_lines(text: str, max_lines: int, prefix: str = "") -> str:
    """Hold the model to the length it was asked for.

    Told "at most two lines", models write four. The instruction is worth
    having anyway -- it shapes what they aim for -- but the ceiling is enforced
    here, because a suggestion longer than advertised is one the user has to
    read all of before deciding.

    The ceiling bends in exactly one place: code that would not parse. A line
    that opens a block, or leaves a bracket or a string unclosed, is not a
    short answer -- it is a broken one, and accepting it puts a syntax error in
    the user's notebook. Those keep going until the code stands on its own, or
    until the hard cap below, which exists so a model that never closes what it
    opens cannot run away with the response.

    `prefix` is what the user has already typed, and it is not optional in
    practice. Completeness is a property of the whole statement, not of the
    reply: asked to continue `monthly = df.groupby(`, a model answering
    `'month',` has written something that looks finished on its own and leaves
    a bracket open in the cell. Judged without the prefix, that stops one line
    short of parsing, every time.
    """
    lines = text.split("\n")
    kept: list[str] = []
    for line in lines:
        kept.append(line)
        if len(kept) < max_lines:
            continue
        if not _incomplete(prefix + "\n".join(kept)):
            break
        if len(kept) >= max_lines + BLOCK_BODY_CAP:
            break
    return "\n".join(kept)


# How far past the line budget a block body may run. Six lines is enough for
# any reasonable loop or function body; past that the model has lost the
# thread, and a runaway suggestion is worse than a truncated one.
BLOCK_BODY_CAP = 6


def _incomplete(text: str) -> bool:
    """Whether stopping here would leave code that does not parse.

    Deliberately a scanner rather than `compile()`: the text is a fragment of a
    cell, so almost all of it fails to compile for reasons that have nothing to
    do with whether the model should keep writing. What matters is narrower --
    an unclosed bracket, an unterminated string, or a block header with no body
    -- and those can be read off the characters.

    Triple quotes are checked before single ones, or a docstring's opening
    delimiter scans as an empty string followed by one more quote, and every
    docstring in the notebook reads as unterminated.
    """
    i = 0
    depth = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "#":
            # A comment runs to the end of the line and may say anything.
            newline = text.find("\n", i)
            i = n if newline == -1 else newline
            continue
        if ch in QUOTES:
            triple = text[i : i + 3]
            if triple in TRIPLES:
                close = text.find(triple, i + 3)
                if close == -1:
                    return True
                i = close + 3
                continue
            after = _end_of_short_string(text, i)
            if after is None:
                return True
            i = after
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        i += 1
    if depth > 0:
        return True
    last = next((line for line in reversed(text.split("\n")) if line.strip()), "")
    return last.rstrip().endswith((":", "\\"))


QUOTES = ("'", '"')
TRIPLES = ("'''", '"""')


def _end_of_short_string(text: str, start: int) -> "int | None":
    """Index just past a normal quoted string, or None if it never closes.

    A newline ends the search: an unclosed single-quoted string is a broken
    line, and treating the rest of the cell as string content would hide every
    bracket in it from the scan.
    """
    quote = text[start]
    i = start + 1
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            return i + 1
        if ch == "\n":
            return None
        i += 1
    return None


def _without_overlap(text: str, already_typed: str) -> str:
    """Drop any restatement of what the user has already written.

    Models routinely reply with the whole line they were asked to continue:
    given `df = `, they answer `df = pd.read_csv(...)`. Shown as ghost text
    that duplicates the user's own code in front of them, which reads as the
    editor having lost track of the cursor.

    Done by overlap rather than by stripping a stripped prefix, so the
    whitespace lands right: the user's trailing space is theirs, and the
    suggestion must not begin with a second one.
    """
    stripped = text.lstrip()
    lead = text[: len(text) - len(stripped)]
    tail = already_typed.rsplit("\n", 1)[-1]
    # Longest suffix of what they typed that the model repeated back.
    for size in range(len(tail), 0, -1):
        if stripped.startswith(tail[-size:]):
            return stripped[size:]
    return lead + stripped


def clean_prediction(text: str, already_typed: str, max_lines: int) -> str:
    """Everything between what the model said and what is safe to show."""
    text = strip_fences(text)
    for token in ("<|endoftext|>", "<|fim_pad|>", "<EOT>", "<|file_sep|>"):
        text = text.split(token)[0]

    text = _without_overlap(text, already_typed)

    text = limit_lines(text, max_lines, already_typed)
    return text if text.strip() else ""
