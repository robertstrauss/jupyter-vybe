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
from .diffsummary import render_index, summarise
from .history import HistoryStore
from .assembler import (
    assemble_followup,
    assemble_initial,
    render_notebook,
    resolve_cell_ref,
)
from .protocol import (
    EventKind,
    StreamParser,
    has_tool_calls,
    retarget_salvage,
    salvage_fenced_blocks,
)

IDLE_SECONDS = 1800
MAX_SESSIONS = 8
RESYNC_EVERY = 10
# How often to tell the client how much has arrived. Four times a second is
# enough for a counter a human reads, and cheap enough not to matter.
PROGRESS_INTERVAL = 0.25

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
        # Adapter configuration rides in the hello options: which command to
        # run, which endpoint, which environment variable holds the key.
        self.adapter = build_adapter(agent_id, options)
        self.messages: list[Message] = []
        self.turns = 0
        self.snapshot_seq = -1
        self.touched = time.monotonic()
        self._cancel = asyncio.Event()
        self._tool_waiters: dict[str, asyncio.Future] = {}
        self._cells: list[dict] = []  # last snapshot, for resolving cell references
        # The prompt record is a sidecar, not the notebook (D12, docs/05). On
        # by default: a durable record cannot be opt-in when losing it is the
        # normal consequence of tidying up a cell.
        self.history_enabled = bool(options.get("historyPersist", True))
        # "user" (default) or "adjacent" -- see HistoryStore (D12).
        self.history_location = str(options.get("historyLocation", "user"))
        # prompts.showAssembled: echo the assembled prompt to the client, which
        # logs it. "What did the model actually see" is the question that keeps
        # needing an answer, and reconstructing it by hand is error-prone.
        self.echo_prompt = bool(options.get("echoPrompt", False))
        self._prose = ""
        self.store = HistoryStore(notebook_path, location=self.history_location)

    # -- lifecycle ---------------------------------------------------------

    def cancel(self) -> None:
        self._cancel.set()

    def record(self, entry: dict) -> None:
        """Append to the prompt record. Never fails a round (docs/05)."""
        if not self.history_enabled:
            return
        try:
            self.store.notebook_path = self.notebook_path
            self.store.append(entry)
        except OSError as exc:
            # A read-only home directory loses the record, not the answer.
            _record("history_error", error=str(exc))

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
        # Turn 0 sends everything; later turns in a persistent session send the
        # short form. This was pinned to always-full while the follow-up prompt
        # lacked a cell listing, which made the model invent cell numbers and
        # edit the wrong cell. The listing now goes on every turn and TOOL_RECAP
        # carries the rules whose absence caused real damage (docs/09).
        #
        # It is also the single largest latency win available. Measured against
        # TurboFieldfareServer: a request that continues the conversation reuses
        # the cached prefix and prefills only what is new -- 0.8 s against 48 s
        # for the same tokens sent cold (docs/14 N4). Re-sending the full
        # preamble each turn threw that away.
        first_turn = not self.persistent or not self.messages
        # A full snapshot every RESYNC_EVERY turns bounds drift from
        # accumulated small errors in the diff path (docs/09).
        periodic = self.turns > 0 and self.turns % RESYNC_EVERY == 0
        # The sequence check docs/09 promised and nothing implemented: a diff
        # is only meaningful against the snapshot it was computed from. If the
        # client's counter skipped -- server restarted, session evicted,
        # notebook reopened -- the picture here is stale, and diffing against
        # it would desynchronise the agent silently, which is R4 exactly.
        # Sending everything is cheaper than being subtly wrong.
        out_of_order = not first_turn and seq != self.snapshot_seq + 1
        force_full = first_turn or periodic or out_of_order or msg.get("forceFull", False)
        if out_of_order:
            _record("resync", expected=self.snapshot_seq + 1, got=seq)
        if force_full and msg.get("snapshot") is None:
            yield {"op": "resync"}
            return

        limits = msg.get("limits", {})
        snapshot_cells: list[dict] = []
        # _translate needs the cell list to resolve index references.
        raw_snapshot = msg.get("snapshot")
        if raw_snapshot:
            try:
                snapshot_cells = json.loads(raw_snapshot).get("cells", [])
            except (json.JSONDecodeError, AttributeError):
                snapshot_cells = []
        previous_cells = self._cells
        if snapshot_cells:
            self._cells = snapshot_cells
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
            # An index rather than the whole notebook: the agent still has to
            # name a cell, and a number it cannot see is a number it invents
            # (docs/09). The bodies it needs are in the conversation already.
            prompt = assemble_followup(
                notebook_diff=summarise(
                    previous_cells, snapshot_cells, msg.get("promptCellId", "")
                ),
                kernel=msg.get("kernelInfo", {}),
                user_prompt=msg["prompt"],
                notebook_listing=render_index(
                    snapshot_cells, msg.get("promptCellId", "")
                ),
            )
        self.snapshot_seq = seq
        self.messages.append(Message("user", prompt))

        _record("prompt", persistent=self.persistent, turns=self.turns,
                force_full=force_full, prompt=prompt)
        round_id = msg.get("roundId", "")
        self.record({
            "kind": "prompt",
            "roundId": round_id,
            "text": msg["prompt"],
            "agentId": self.agent_id,
            "promptChars": len(prompt),
        })
        if self.echo_prompt:
            yield {"op": "assembledPrompt", "prompt": prompt}
        yield {"op": "status", "state": "generating"}

        max_tool_turns = int(limits.get("maxToolTurns", 6))
        for _ in range(max_tool_turns + 1):
            reply, pending = "", None
            emitted_tool = False
            parser = StreamParser()

            # Progress is reported from here, on the raw stream, because
            # nothing downstream can see it: the parser buffers a tag body
            # until its closing tag, so a reply made entirely of tool tags --
            # the normal case -- emits no frames at all until it is finished.
            # The client was dutifully reporting 0 tokens/sec for the whole
            # round.
            last_progress = 0.0
            async for chunk in self.adapter.stream(self.messages, self._cancel):
                reply += chunk
                now = time.monotonic()
                if now - last_progress >= PROGRESS_INTERVAL:
                    last_progress = now
                    yield {"op": "progress", "chars": len(reply)}
                for event in parser.feed(chunk):
                    if event.kind is EventKind.TOOL:
                        emitted_tool = True
                    out, pending = self._translate(event)
                    if out is not None:
                        self._record_frame(round_id, out)
                        yield out
                    if pending is not None:
                        break
                if pending is not None or self._cancel.is_set():
                    break

            if self._cancel.is_set():
                self.messages.append(Message("assistant", reply))
                self._flush_prose(round_id)
                yield {"op": "done", "stopReason": "cancelled"}
                return

            if pending is None:
                for event in parser.close():
                    if event.kind is EventKind.TOOL:
                        emitted_tool = True
                    out, _ = self._translate(event)
                    if out is not None:
                        self._record_frame(round_id, out)
                        yield out

                # R2 fallback: a reply with no tool calls but with fenced code
                # blocks is recovered rather than dumped into the notebook as
                # prose. Any model can produce a fence; not every model will
                # produce tags reliably.
                _record("reply", emitted_tool=emitted_tool, reply=reply)
                if not emitted_tool:
                    salvaged = salvage_fenced_blocks(reply)
                    # A fenced block carries no target, so salvage alone turns
                    # "edit the cell above" into a near-duplicate copy. Retarget
                    # a block that clearly rewrites an existing cell.
                    salvaged = retarget_salvage(
                        salvaged, snapshot_cells, exclude_ids={msg.get("promptCellId", "")}
                    )
                    _record(
                        "salvage",
                        found=len(salvaged),
                        kinds=[e.name for e in salvaged],
                    )
                    if salvaged:
                        yield {"op": "proseReset"}
                        for event in salvaged:
                            frame = {"op": "tool", "name": event.name, "args": event.args}
                            self._record_frame(round_id, frame)
                            yield frame
                        yield {"op": "salvaged", "count": len(salvaged)}

                self.messages.append(Message("assistant", reply))
                self.turns += 1
                self._flush_prose(round_id)
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
        self._flush_prose(round_id)
        yield {"op": "done", "stopReason": "limit"}

    # -- the prompt record -------------------------------------------------

    def _record_frame(self, round_id: str, frame: dict) -> None:
        """Mirror an outgoing frame into the store (docs/05 entry model)."""
        op = frame.get("op")
        if op == "prose":
            self._prose += frame.get("delta", "")
            return
        if op == "proseReset":
            # The prose was salvaged code, not commentary; it never belonged
            # in the record either.
            self._prose = ""
            return
        if op != "tool":
            return
        name = frame.get("name")
        args = frame.get("args") or {}
        if name == "add_cell":
            self.record({
                "kind": "insertion",
                "roundId": round_id,
                "cellType": args.get("type", "code"),
                "source": args.get("body", ""),
            })
        elif name == "propose_edit":
            cell_id = args.get("cell", "")
            self.record({
                "kind": "proposal",
                "roundId": round_id,
                "cellId": cell_id,
                "cellLabel": self._first_line(cell_id),
                "before": self._source_of(cell_id),
                "after": args.get("body", ""),
                "note": args.get("note", ""),
                # The verdict is the user's and arrives later, by POST from the
                # frontend once they accept or reject (docs/05).
                "outcome": "pending",
            })

    def _flush_prose(self, round_id: str) -> None:
        text = self._prose.strip()
        self._prose = ""
        if text:
            self.record({"kind": "prose", "roundId": round_id, "text": text})

    def _source_of(self, cell_id: str) -> str:
        cell = next((c for c in self._cells if c.get("id") == cell_id), None)
        if not cell:
            return ""
        src = cell.get("source") or ""
        return "".join(src) if isinstance(src, list) else src

    def _first_line(self, cell_id: str) -> str:
        return self._source_of(cell_id).split("\n")[0][:70]

    def _translate(self, event):
        """Map a parser event to a wire frame, flagging blocking tools."""
        if event.kind is EventKind.PROSE:
            return ({"op": "prose", "delta": event.text} if event.text else None), None
        if event.kind is EventKind.ERROR:
            return {"op": "protocolError", "message": event.text}, None
        if event.name in BLOCKING_TOOLS:
            call_id = f"c{len(self._tool_waiters)}{int(time.time() * 1000) % 100000}"
            return None, (call_id, event.name, event.args)
        args = dict(event.args)
        if event.name == "propose_edit":
            ref = args.get("cell", "")
            resolved = resolve_cell_ref(ref, self._cells)
            # Record exactly what the model asked for, what it resolved to, and
            # what is actually in that cell. A mis-aimed edit renders as a
            # perfectly good diff, so this is the only way to see it went wrong.
            target = next((c for c in self._cells if c.get("id") == resolved), None)
            preview = ""
            if target:
                src = target.get("source") or ""
                if isinstance(src, list):
                    src = "".join(src)
                preview = src.split("\n")[0][:70]
            _record(
                "resolve",
                requested=ref,
                resolved=resolved,
                target_first_line=preview,
                cell_count=len(self._cells),
                listing=[
                    f"[{i + 1}] {c.get('cell_type')} {c.get('id')}"
                    for i, c in enumerate(self._cells)
                ],
            )
            if not resolved:
                return {
                    "op": "protocolError",
                    "message": (
                        f"propose_edit named cell {ref!r}, which is not a cell in this "
                        "notebook. Use the number in brackets from the listing."
                    ),
                }, None
            args["cell"] = resolved
        return {"op": "tool", "name": event.name, "args": args}, None


def _tooling_with_limits(limits: dict, source: str | None = None) -> str | None:
    """Fill the limit placeholders. `source` overrides the packaged prompt,
    which is how scripts/bench-tools.py measures what prompt length buys."""
    from .assembler import _load

    tooling = source if source is not None else _load("tooling.md")
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
