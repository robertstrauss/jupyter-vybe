"""Agent sessions: one per notebook, LRU-evicted (docs/06).

The session owns the conversation history, which is assembled from the caller's
payload — never re-read from notebook cells. That is what makes it structurally
impossible for the agent to rewrite its own instruction history (D12).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import OrderedDict
from typing import AsyncIterator

from .adapters import Message, build_adapter
from .assembler import assemble_followup, assemble_initial
from .protocol import EventKind, StreamParser, has_tool_calls, salvage_fenced_blocks

IDLE_SECONDS = 1800
MAX_SESSIONS = 8
RESYNC_EVERY = 10

BLOCKING_TOOLS = {"ipython_help", "complete"}

# Set VYBE_TRANSCRIPT to a path to record every round: the assembled prompt,
# the raw model reply, and every frame sent to the browser. This is the only
# way to tell "the model misbehaved" apart from "we mishandled a good reply".
TRANSCRIPT = os.environ.get("VYBE_TRANSCRIPT", "")


def _record(kind: str, **fields) -> None:
    if not TRANSCRIPT:
        return
    try:
        with open(TRANSCRIPT, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": kind, "ts": time.time(), **fields}) + "\n")
    except Exception:  # logging must never break a round
        pass


class AgentSession:
    def __init__(self, notebook_path: str, agent_id: str, persistent: bool, options: dict) -> None:
        self.notebook_path = notebook_path
        self.agent_id = agent_id
        self.persistent = persistent
        self.options = options
        self.adapter = build_adapter(agent_id)
        self.messages: list[Message] = []
        self.turns = 0
        self.snapshot_seq = -1
        self.touched = time.monotonic()
        self._cancel = asyncio.Event()
        self._tool_waiters: dict[str, asyncio.Future] = {}

    # -- lifecycle ---------------------------------------------------------

    def cancel(self) -> None:
        self._cancel.set()

    def reset(self) -> None:
        self.messages.clear()
        self.turns = 0
        self.snapshot_seq = -1

    def deliver_tool_result(self, call_id: str, result: str, ok: bool = True) -> None:
        fut = self._tool_waiters.pop(call_id, None)
        if fut is not None and not fut.done():
            fut.set_result((ok, result))

    # -- the turn ----------------------------------------------------------

    async def run(self, msg: dict) -> AsyncIterator[dict]:
        self.touched = time.monotonic()
        self._cancel = asyncio.Event()

        seq = msg.get("snapshotSeq", 0)
        force_full = (
            not self.persistent
            or not self.messages
            or self.turns % RESYNC_EVERY == 0
            or msg.get("snapshot") is not None and seq != self.snapshot_seq + 1
        )
        if force_full and msg.get("snapshot") is None:
            yield {"op": "resync"}
            return

        limits = msg.get("limits", {})
        if force_full:
            prompt = assemble_initial(
                notebook_json=msg["snapshot"],
                kernel=msg.get("kernelInfo", {}),
                prompt_cell_id=msg.get("promptCellId", ""),
                user_prompt=msg["prompt"],
                global_prompt=msg.get("globalPrompt", ""),
                tooling=_tooling_with_limits(limits),
            )
            if not self.persistent:
                self.messages = []
        else:
            prompt = assemble_followup(
                notebook_diff=msg.get("diff", ""),
                kernel=msg.get("kernelInfo", {}),
                user_prompt=msg["prompt"],
            )
        self.snapshot_seq = seq
        self.messages.append(Message("user", prompt))

        _record("prompt", persistent=self.persistent, turns=self.turns,
                force_full=force_full, prompt=prompt)
        yield {"op": "status", "state": "generating"}

        max_tool_turns = int(limits.get("maxToolTurns", 6))
        for _ in range(max_tool_turns + 1):
            reply, pending = "", None
            emitted_tool = False
            parser = StreamParser()

            async for chunk in self.adapter.stream(self.messages, self._cancel):
                reply += chunk
                for event in parser.feed(chunk):
                    if event.kind is EventKind.TOOL:
                        emitted_tool = True
                    out, pending = self._translate(event)
                    if out is not None:
                        yield out
                    if pending is not None:
                        break
                if pending is not None or self._cancel.is_set():
                    break

            if self._cancel.is_set():
                self.messages.append(Message("assistant", reply))
                yield {"op": "done", "stopReason": "cancelled"}
                return

            if pending is None:
                for event in parser.close():
                    if event.kind is EventKind.TOOL:
                        emitted_tool = True
                    out, _ = self._translate(event)
                    if out is not None:
                        yield out

                # R2 fallback: a reply with no tool calls but with fenced code
                # blocks is recovered rather than dumped into the notebook as
                # prose. Any model can produce a fence; not every model will
                # produce tags reliably.
                _record("reply", emitted_tool=emitted_tool, reply=reply)
                if not emitted_tool:
                    salvaged = salvage_fenced_blocks(reply)
                    _record("salvage", found=len(salvaged))
                    if salvaged:
                        yield {"op": "proseReset"}
                        for event in salvaged:
                            yield {"op": "tool", "name": event.name, "args": event.args}
                        yield {"op": "salvaged", "count": len(salvaged)}

                self.messages.append(Message("assistant", reply))
                self.turns += 1
                yield {"op": "done", "stopReason": "end"}
                return

            # A blocking lookup: pause, ask the client, resume.
            call_id, name, args = pending
            self.messages.append(Message("assistant", reply))
            yield {"op": "status", "state": "awaiting_tool"}
            yield {"op": "tool", "callId": call_id, "name": name, "args": args}
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._tool_waiters[call_id] = fut
            try:
                ok, result = await asyncio.wait_for(fut, timeout=30)
            except asyncio.TimeoutError:
                ok, result = False, "lookup timed out"
            self.messages.append(
                Message("user", f"Result of {name} ({'ok' if ok else 'failed'}):\n{result}")
            )

        self.turns += 1
        yield {"op": "done", "stopReason": "limit"}

    def _translate(self, event):
        """Map a parser event to a wire frame, flagging blocking tools."""
        if event.kind is EventKind.PROSE:
            return ({"op": "prose", "delta": event.text} if event.text else None), None
        if event.kind is EventKind.ERROR:
            return {"op": "protocolError", "message": event.text}, None
        if event.name in BLOCKING_TOOLS:
            call_id = f"c{len(self._tool_waiters)}{int(time.time() * 1000) % 100000}"
            return None, (call_id, event.name, event.args)
        return {"op": "tool", "name": event.name, "args": event.args}, None


def _tooling_with_limits(limits: dict) -> str | None:
    from .assembler import _load

    tooling = _load("tooling.md")
    if not tooling:
        return None
    return (
        tooling.replace("{{MAX_INSERTIONS}}", str(limits.get("maxInsertions", 8)))
        .replace("{{MAX_PROPOSALS}}", str(limits.get("maxProposals", 12)))
        .replace("{{MAX_TOOL_TURNS}}", str(limits.get("maxToolTurns", 6)))
    )


class SessionManager:
    def __init__(self) -> None:
        self._sessions: OrderedDict[str, AgentSession] = OrderedDict()

    def acquire(self, notebook_path: str, agent_id: str, persistent: bool, options: dict) -> AgentSession:
        self._evict()
        key = notebook_path
        session = self._sessions.get(key)
        if session is not None and session.agent_id == agent_id and session.persistent == persistent:
            self._sessions.move_to_end(key)
            session.touched = time.monotonic()
            return session
        session = AgentSession(notebook_path, agent_id, persistent, options)
        self._sessions[key] = session
        self._sessions.move_to_end(key)
        while len(self._sessions) > MAX_SESSIONS:
            _, dead = self._sessions.popitem(last=False)
            dead.cancel()
        return session

    def rename(self, old_path: str, new_path: str) -> None:
        session = self._sessions.pop(old_path, None)
        if session is not None:
            session.notebook_path = new_path
            self._sessions[new_path] = session

    def _evict(self) -> None:
        now = time.monotonic()
        for key in [k for k, s in self._sessions.items() if now - s.touched > IDLE_SECONDS]:
            self._sessions.pop(key).cancel()
