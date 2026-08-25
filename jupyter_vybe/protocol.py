"""Tagged-text tool protocol — incremental parser (Python side).

The canonical wire format for every adapter (docs/07). Mirrored by
src/protocol/streamParser.ts; both are pinned to the same golden files in
tests/protocol/, and a test asserts they emit byte-identical event JSON.

Grammar::

    <vybe:add_cell type="code">
    <body, verbatim source>
    </vybe:add_cell>

    <vybe:propose_edit cell="a3f91c2e" note="why">
    <body, complete new source>
    </vybe:propose_edit>

    <vybe:ipython_help target="np.fft.rfft" />
    <vybe:complete prefix="df.gro" />

Bodies are verbatim: no escaping, no nesting, no fences.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator, Literal

TAG_OPEN = "<vybe:"

BODY_TOOLS = {"add_cell", "propose_edit"}
VOID_TOOLS = {"ipython_help", "complete"}
KNOWN_TOOLS = BODY_TOOLS | VOID_TOOLS

_ATTR_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"')
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class EventKind(str, Enum):
    PROSE = "prose"
    TOOL = "tool"
    ERROR = "protocol_error"


@dataclass
class Event:
    kind: EventKind
    text: str = ""
    name: str = ""
    args: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict:
        if self.kind is EventKind.PROSE:
            return {"kind": "prose", "text": self.text}
        if self.kind is EventKind.TOOL:
            return {"kind": "tool", "name": self.name, "args": self.args}
        return {"kind": "protocol_error", "text": self.text}


class _State(Enum):
    TEXT = "text"
    TAG = "tag"
    BODY = "body"


class StreamParser:
    """Feed tokens in, get events out. Safe across arbitrary chunk boundaries."""

    def __init__(self) -> None:
        self._buf = ""
        self._state = _State.TEXT
        self._tool = ""
        self._args: dict[str, str] = {}
        self._body = ""
        self._closed = False

    def feed(self, chunk: str) -> Iterator[Event]:
        self._buf += chunk
        yield from self._drain(final=False)

    def close(self) -> Iterator[Event]:
        """Flush at end of stream. An unterminated body is discarded, never applied."""
        if self._closed:
            return
        self._closed = True
        yield from self._drain(final=True)
        if self._state is _State.TEXT:
            if self._buf:
                yield Event(EventKind.PROSE, text=self._buf)
                self._buf = ""
        elif self._state is _State.TAG:
            yield Event(EventKind.ERROR, text=f"stream ended inside a tag: {self._buf[:80]!r}")
        elif self._state is _State.BODY:
            yield Event(
                EventKind.ERROR,
                text=f"stream ended inside <vybe:{self._tool}> body; partial cell discarded",
            )
        self._buf = ""

    # -- internals ---------------------------------------------------------

    def _drain(self, final: bool) -> Iterator[Event]:
        while True:
            if self._state is _State.TEXT:
                idx = self._buf.find(TAG_OPEN)
                if idx == -1:
                    # Hold back a possible partial "<vybe:" straddling the boundary.
                    keep = 0 if final else _partial_suffix(self._buf, TAG_OPEN)
                    emit, self._buf = self._buf[: len(self._buf) - keep], self._buf[len(self._buf) - keep :]
                    if emit:
                        yield Event(EventKind.PROSE, text=emit)
                    return
                if idx:
                    yield Event(EventKind.PROSE, text=self._buf[:idx])
                self._buf = self._buf[idx:]
                self._state = _State.TAG

            elif self._state is _State.TAG:
                end = self._buf.find(">")
                if end == -1:
                    return
                raw = self._buf[: end + 1]
                self._buf = self._buf[end + 1 :]
                yield from self._open_tag(raw)

            else:  # BODY
                close = f"</{TAG_OPEN[1:]}{self._tool}>"
                idx = self._buf.find(close)
                if idx == -1:
                    keep = 0 if final else _partial_suffix(self._buf, close)
                    self._body += self._buf[: len(self._buf) - keep]
                    self._buf = self._buf[len(self._buf) - keep :]
                    return
                self._body += self._buf[:idx]
                self._buf = self._buf[idx + len(close) :]
                args = dict(self._args)
                args["body"] = strip_body_fence(
                    _trim_body(self._body), args.get("type", "code")
                )
                yield Event(EventKind.TOOL, name=self._tool, args=args)
                self._tool, self._args, self._body = "", {}, ""
                self._state = _State.TEXT

    def _open_tag(self, raw: str) -> Iterator[Event]:
        inner = raw[len(TAG_OPEN) : -1].rstrip()
        void = inner.endswith("/")
        if void:
            inner = inner[:-1].rstrip()
        m = _NAME_RE.match(inner)
        if not m:
            self._state = _State.TEXT
            yield Event(EventKind.ERROR, text=f"malformed tool tag: {raw!r}")
            return
        name = m.group(0)
        args = {k: v for k, v in _ATTR_RE.findall(inner[m.end() :])}
        if name not in KNOWN_TOOLS:
            self._state = _State.TEXT
            yield Event(
                EventKind.ERROR,
                text=f"unknown tool {name!r}; known tools: {', '.join(sorted(KNOWN_TOOLS))}",
            )
            return
        if void:
            if name not in VOID_TOOLS:
                self._state = _State.TEXT
                yield Event(EventKind.ERROR, text=f"<vybe:{name}> requires a body, got a void tag")
                return
            self._state = _State.TEXT
            yield Event(EventKind.TOOL, name=name, args=args)
            return
        if name not in BODY_TOOLS:
            self._state = _State.TEXT
            yield Event(EventKind.ERROR, text=f"<vybe:{name}> is a void tool; use <vybe:{name} ... />")
            return
        self._tool, self._args, self._body = name, args, ""
        self._state = _State.BODY


def _partial_suffix(buf: str, token: str) -> int:
    """Length of the longest suffix of buf that is a proper prefix of token."""
    for n in range(min(len(token) - 1, len(buf)), 0, -1):
        if buf[-n:] == token[:n]:
            return n
    return 0


_BODY_FENCE_RE = re.compile(r"\A[ \t]*```[A-Za-z0-9_+-]*[ \t]*\n(.*?)\n?[ \t]*```[ \t]*\Z", re.S)


def strip_body_fence(body: str, cell_type: str) -> str:
    """Unwrap a markdown fence a model wrapped around a *code* body.

    The tag already delimits the body, so a fence wrapping the whole of a code
    cell is the model double-wrapping, and leaving it in produces a cell whose
    first line is ```python -- a syntax error.

    Markdown cells are left alone: a markdown cell whose content is a fenced
    example is legitimate, and unwrapping it would destroy the example
    (tests/protocol/body-containing-code-fence.txt).
    """
    if cell_type == "markdown":
        return body
    m = _BODY_FENCE_RE.match(body)
    return m.group(1) if m else body


def _trim_body(body: str) -> str:
    """Normalise line endings, strip one leading/trailing newline, dedent."""
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    if body.startswith("\n"):
        body = body[1:]
    if body.endswith("\n"):
        body = body[:-1]
    return _dedent(body)


def _dedent(body: str) -> str:
    """Remove indentation the model copied out of the notebook listing.

    assemble_initial renders every cell's source indented by four spaces so the
    listing reads as a block. Models echo that indentation back inside the tag
    body, which produces an "edit" where every single line differs from the
    original by four leading spaces -- the whole cell then renders as one solid
    add and one solid delete instead of a diff (docs/14 N1).

    Only a uniform indent shared by every non-blank line is removed, so the
    interior shape of the code is untouched. A body whose first line already
    sits at column zero is left exactly as it is, which is the common case.
    """
    lines = body.split("\n")
    indents = [
        len(line) - len(line.lstrip(" \t")) for line in lines if line.strip()
    ]
    if not indents:
        return body
    common = min(indents)
    if common == 0:
        return body
    return "\n".join(line[common:] if line.strip() else line for line in lines)


def parse(text: str) -> list[Event]:
    """Convenience: parse a complete response in one shot."""
    p = StreamParser()
    out = list(p.feed(text))
    out.extend(p.close())
    return out


# --- salvage ------------------------------------------------------------
# Risk R2 in docs/13: a model that will not emit tool tags reliably still has
# to be usable. Any model can produce a fenced code block, so a reply that
# carries no tool calls but does carry fences is recovered as add_cell calls
# rather than dumped into the notebook as prose.

_FENCE_RE = re.compile(
    r"^[ \t]*```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\n(.*?)(?:^[ \t]*```[ \t]*$|\Z)",
    re.S | re.M,
)

MARKDOWN_LANGS = {"markdown", "md"}


def salvage_fenced_blocks(text: str) -> list[Event]:
    """Recover add_cell calls from a reply that used markdown fences instead.

    Returns [] if there is nothing to recover, so the caller can tell the
    difference between "salvaged" and "the model genuinely said nothing".
    """
    events: list[Event] = []
    for lang, body in _FENCE_RE.findall(text):
        source = body.rstrip("\n")
        if not source.strip():
            continue
        cell_type = "markdown" if lang.lower() in MARKDOWN_LANGS else "code"
        events.append(
            Event(EventKind.TOOL, name="add_cell", args={"type": cell_type, "body": source})
        )
    return events


def has_tool_calls(events: list[Event]) -> bool:
    return any(e.kind is EventKind.TOOL for e in events)


# Salvage can only ever produce add_cell, because a fenced block carries no
# target. That turns "edit the cell above" into a near-duplicate copy, which is
# the most annoying way for the fallback to be wrong. So when a recovered block
# closely resembles an existing code cell, retarget it as an edit of that cell.
# Textual similarity alone is a poor signal: two unrelated cells that both
# start with imports score surprisingly high. So the strong signal is
# redefining the same top-level name, and raw similarity has to clear a much
# higher bar on its own.
EDIT_SIMILARITY = 0.92  # no shared name: only a near-identical rewrite counts as an edit
# A shared top-level name needs no text floor at all: a substantial rewrite of
# the same function scores as low as 0.29 on raw similarity, which is exactly
# the case worth catching. A wrong guess here costs one keystroke, since the
# result is a reviewable diff rather than a silent write.
EDIT_SIMILARITY_NAMED = 0.0
_DEF_RE = re.compile(r"^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+([A-Za-z_][A-Za-z0-9_]*)", re.M)


def _defined_names(source: str) -> set[str]:
    return set(_DEF_RE.findall(source))


def retarget_salvage(
    events: list[Event], cells: list[dict], exclude_ids: set[str] | None = None
) -> list[Event]:
    """Convert salvaged add_cell events into propose_edit where they clearly
    rewrite an existing cell.

    `cells` is the cell list from the snapshot the agent was given.
    """
    from difflib import SequenceMatcher

    exclude = exclude_ids or set()
    out: list[Event] = []
    claimed: set[str] = set()
    for event in events:
        body = event.args.get("body", "")
        best_id, best_ratio = None, 0.0
        for cell in cells:
            cid = cell.get("id")
            if (
                not cid
                or cid in exclude
                or cid in claimed
                or cell.get("cell_type") != "code"
            ):
                continue
            source = cell.get("source") or ""
            if isinstance(source, list):
                source = "".join(source)
            if not source.strip():
                continue
            shared = _defined_names(source) & _defined_names(body)
            ratio = SequenceMatcher(None, source, body).ratio()
            floor = EDIT_SIMILARITY_NAMED if shared else EDIT_SIMILARITY
            if ratio < floor or ratio >= 0.999:
                continue
            # Redefining the same name is the real signal; rank those first.
            score = ratio + (1.0 if shared else 0.0)
            if score > best_ratio:
                best_id, best_ratio = cid, min(ratio, 0.99)
        if best_id:
            claimed.add(best_id)
            out.append(
                Event(
                    EventKind.TOOL,
                    name="propose_edit",
                    args={
                        "cell": best_id,
                        "note": f"recovered from a plain code block ({int(best_ratio * 100)}% similar)",
                        "body": body,
                    },
                )
            )
        else:
            out.append(event)
    return out
