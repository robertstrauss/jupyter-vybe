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
                args["body"] = _trim_body(self._body)
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


def _trim_body(body: str) -> str:
    """Strip one leading and one trailing newline; keep all interior whitespace."""
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    if body.endswith("\r\n"):
        body = body[:-2]
    elif body.endswith("\n"):
        body = body[:-1]
    return body


def parse(text: str) -> list[Event]:
    """Convenience: parse a complete response in one shot."""
    p = StreamParser()
    out = list(p.feed(text))
    out.extend(p.close())
    return out
