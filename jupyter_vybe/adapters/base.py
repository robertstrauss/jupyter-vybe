"""Adapter protocol. Host-agnostic by construction (docs/06, D2).

Nothing in this package may import tornado or jupyter_server: the same code runs
inside the server extension and inside `jupyter-vybe-local connect` on the
user's own machine, and that only stays true if the dependency never creeps in.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import socket
import urllib.parse
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol


@dataclass
class Message:
    role: str  # "user" | "assistant" | "tool"
    content: str


@dataclass
class HealthStatus:
    ok: bool
    detail: str = ""
    model: str = ""
    host: str = ""
    hostname: str = ""  # which machine answered — makes a mis-aimed tunnel visible (R12)

    def to_json(self) -> dict:
        return {
            "ok": self.ok,
            "detail": self.detail,
            "model": self.model,
            "host": self.host,
            "hostname": self.hostname,
        }


@dataclass
class AdapterSpec:
    id: str
    label: str
    context_window: int
    local: bool = True
    requires_config: list[str] = field(default_factory=list)
    supports_native_tools: bool = False  # declared, unused in v1.0 (D3)


class Adapter(Protocol):
    spec: AdapterSpec

    async def health(self) -> HealthStatus: ...

    async def warm(self) -> None:
        """Get ready to answer, before anyone has asked anything.

        Optional, and best-effort by definition: an adapter that cannot be
        warmed does nothing here and is no worse off. For a local runtime this
        is the difference between a first prompt at three and a half seconds
        and one at half a second, which is the difference between a tool that
        feels broken on first use and one that does not.
        """

    async def prefill(self, messages: list[Message], cancel: asyncio.Event) -> str:
        """Read `messages` into the runtime's prompt cache; return its short reply.

        Optional. Ends in a user turn asking for a one-word reply, and the
        reply is returned so the caller can send it back verbatim as the next
        assistant turn: the cache only matches a continuation whose history
        repeats the model's own words exactly (session.prefill, docs/14).
        Returns "" if it did nothing. Stops promptly when `cancel` is set.
        """

    def stream(self, messages: list[Message], cancel: asyncio.Event) -> AsyncIterator[str]: ...

    async def close(self) -> None: ...


async def post_json(url: str, payload: dict, cancel: asyncio.Event, timeout: float) -> dict | None:
    """POST JSON and return the decoded reply, or None if `cancel` was set first.

    A blocking read in a thread cannot be interrupted, and urllib gives no
    handle on the socket, so this uses http.client and shuts the socket down on
    cancel. That matters for a read-ahead that turned out stale: closing the
    connection is how a local runtime is told to stop prefilling, and a
    runtime still busy with it is one the real request waits behind.
    """
    parts = urllib.parse.urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname or "127.0.0.1", parts.port or 80, timeout=timeout)

    def call() -> dict:
        conn.request(
            "POST",
            parts.path or "/",
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        body = resp.read()
        if resp.status >= 400:
            raise RuntimeError(f"HTTP {resp.status}: {body[:200]!r}")
        return json.loads(body)

    task = asyncio.ensure_future(asyncio.to_thread(call))
    # Retrieved here so a call failing after a cancel is not reported as
    # an exception nobody looked at.
    task.add_done_callback(lambda t: t.cancelled() or t.exception())
    waiter = asyncio.ensure_future(cancel.wait())
    try:
        done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return task.result()
        try:
            if conn.sock is not None:
                conn.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()
        return None
    finally:
        waiter.cancel()
