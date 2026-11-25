"""The server end of `jupyter-vybe-local connect` (docs/06, D2 revised).

The agent runs on the user's own machine -- where `claude`, `codex` and `agy`
are already logged in -- and that machine dials *out* to this server over the
authenticated `/jupyter-vybe/runner` socket. Nothing listens on the laptop, so
no web page can reach it, and the only credential involved is the Jupyter
token the user already has.

The contract across that socket is deliberately the narrowest one that works:
the server hands the runner a conversation and reads back text. It cannot name
a command, an argument, a path or an environment variable -- the runner chose
its agent when it was started, on its own machine, and nothing that arrives
over the wire can change that. A compromised or shared server can make the
user's agent *answer* something; it cannot make the user's machine *do*
anything.

Pure asyncio, like every adapter (gate D2): the Tornado handler in handlers.py
is a pipe into `HUB` and nothing more.

Wire frames, server -> runner:
  {"op": "stream", "id", "session", "caption", "messages": [{role, content}]}
  {"op": "cancel", "id"}
  {"op": "warm",   "session"}
  {"op": "close",  "session"}

runner -> server:
  {"op": "hello", "agent", "label", "hostname", "local", "contextWindow"}
  {"op": "chunk", "id", "text"}
  {"op": "end",   "id"}
  {"op": "error", "id", "message"}
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from .base import AdapterSpec, HealthStatus, Message

NOT_CONNECTED = (
    "no agent is connected from your machine. On the machine where your agent "
    "is logged in, run:  jupyter-vybe-local connect <this JupyterLab's URL> --agent claude"
)

# How often a waiting stream looks at its cancel flag. The cancel is an
# asyncio.Event the session sets from another task; polling it at this rate
# costs nothing and keeps the wait loop free of a second waiter to clean up.
_CANCEL_POLL = 0.2


class RunnerLink(Protocol):
    """What the hub needs from a connected runner: a way to talk, and to hang up."""

    async def send(self, frame: dict) -> None: ...

    def hang_up(self, reason: str) -> None: ...


@dataclass
class RunnerInfo:
    agent: str
    label: str
    hostname: str
    local: bool
    context_window: int

    @classmethod
    def from_hello(cls, hello: dict) -> "RunnerInfo":
        return cls(
            agent=str(hello.get("agent", "")),
            label=str(hello.get("label", "") or hello.get("agent", "agent")),
            hostname=str(hello.get("hostname", "")),
            local=bool(hello.get("local", False)),
            context_window=int(hello.get("contextWindow", 0) or 0),
        )


class RunnerHub:
    """At most one runner per server; a newer connection replaces the older.

    One, because the user has one laptop open on this notebook server, and a
    second connection is almost always the same laptop reconnecting after a
    network blip. Replacing rather than refusing is what makes that recover by
    itself.
    """

    def __init__(self) -> None:
        self._link: RunnerLink | None = None
        self.info: RunnerInfo | None = None
        self._streams: dict[str, asyncio.Queue] = {}

    @property
    def connected(self) -> bool:
        return self._link is not None

    def attach(self, link: RunnerLink, hello: dict) -> None:
        old = self._link
        self._link = link
        self.info = RunnerInfo.from_hello(hello)
        if old is not None and old is not link:
            # Streams in flight belonged to the old connection; they will
            # never finish, so end them now rather than leave rounds hanging.
            self._fail_all("the agent on your machine reconnected mid-reply")
            old.hang_up("replaced by a newer jupyter-vybe-local connection")

    def detach(self, link: RunnerLink) -> None:
        if self._link is not link:
            return  # an already-replaced connection closing late
        where = f" on {self.info.hostname}" if self.info and self.info.hostname else ""
        self._link = None
        self.info = None
        self._fail_all(f"the agent connection{where} closed mid-reply")

    def deliver(self, link: RunnerLink, frame: dict) -> None:
        if link is not self._link:
            return
        queue = self._streams.get(str(frame.get("id", "")))
        if queue is None:
            return  # a reply to a stream that was cancelled or already ended
        op = frame.get("op")
        if op == "chunk":
            queue.put_nowait(("chunk", str(frame.get("text", ""))))
        elif op == "end":
            queue.put_nowait(("end", ""))
        elif op == "error":
            queue.put_nowait(("error", str(frame.get("message", "the agent failed"))))

    async def send(self, frame: dict) -> None:
        if self._link is None:
            raise RuntimeError(NOT_CONNECTED)
        await self._link.send(frame)

    def open_stream(self, stream_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._streams[stream_id] = queue
        return queue

    def close_stream(self, stream_id: str) -> None:
        self._streams.pop(stream_id, None)

    def _fail_all(self, message: str) -> None:
        for queue in self._streams.values():
            queue.put_nowait(("error", message))
        self._streams.clear()


HUB = RunnerHub()


class RunnerAdapter:
    """An adapter whose model is whatever the connected runner is running."""

    def __init__(self, spec: AdapterSpec, hub: RunnerHub = HUB, notebook_path: str = "") -> None:
        self.spec = spec
        self.hub = hub
        # The runner keys its agent sessions on this, so one notebook keeps one
        # conversation on the laptop side as well (persistent CLIs hold their
        # own history; one-shot ones are sent the replayed transcript).
        self.session = uuid.uuid4().hex
        self.notebook_path = notebook_path
        # The user's own words for this round, set by the session. Shown in the
        # runner's terminal so the user can see what their agent is being
        # asked; the assembled prompt is too long to be readable there.
        self.caption = ""

    async def health(self) -> HealthStatus:
        info = self.hub.info
        if info is None:
            return HealthStatus(False, NOT_CONNECTED, "", "runner")
        return HealthStatus(
            True, f"{info.label} via jupyter-vybe-local", info.agent, "runner", info.hostname
        )

    async def warm(self) -> None:
        if self.hub.connected:
            await self.hub.send({"op": "warm", "session": self.session})

    async def stream(self, messages: list[Message], cancel: asyncio.Event) -> AsyncIterator[str]:
        if not self.hub.connected:
            raise RuntimeError(NOT_CONNECTED)
        stream_id = uuid.uuid4().hex
        queue = self.hub.open_stream(stream_id)
        try:
            await self.hub.send({
                "op": "stream",
                "id": stream_id,
                "session": self.session,
                "notebook": self.notebook_path,
                "caption": self.caption,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
            })
            while True:
                if cancel.is_set():
                    if self.hub.connected:
                        await self.hub.send({"op": "cancel", "id": stream_id})
                    return
                try:
                    kind, value = await asyncio.wait_for(queue.get(), timeout=_CANCEL_POLL)
                except asyncio.TimeoutError:
                    continue
                if kind == "chunk":
                    yield value
                elif kind == "end":
                    return
                else:
                    raise RuntimeError(value)
        finally:
            self.hub.close_stream(stream_id)

    async def close(self) -> None:
        if self.hub.connected:
            try:
                await self.hub.send({"op": "close", "session": self.session})
            except Exception:  # noqa: BLE001 -- closing is best effort
                pass
