"""TurboFieldfare adapter — Gemma 4 26B-A4B on Apple Silicon (docs/06).

Prefers TurboFieldfareServer's OpenAI-compatible /v1/chat/completions when it
answers, and falls back to the TurboFieldfareCLI binary otherwise. That is the
same `auto` behaviour the vendored client implements.

Gemma 4 has no system role: the assembled prompt goes in the first user turn,
as README_turbofieldfare.md documents.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import AsyncIterator

from .base import Adapter, AdapterSpec, HealthStatus, Message

DEFAULT_HOST = os.environ.get("TURBOFIELDFARE_HOST", "http://127.0.0.1:8080")
# Between-token read timeout. Generous: a cold prefill on a long prompt can
# take a minute before the first token appears.
READ_TIMEOUT = float(os.environ.get("TURBOFIELDFARE_READ_TIMEOUT", "180"))
# Only used if /v1/models cannot be read; the server's own default id.
_FALLBACK_MODEL_ID = "gemma-4-26b-a4b-it"

# Probe order copied from the vendored client's resolve_cli_bin / resolve_model_path.
_CLI_CANDIDATES = [
    os.environ.get("TURBOFIELDFARE_BIN", ""),
    os.path.expanduser("~/.turbofieldfare/turbo-fieldfare/.build/release/TurboFieldfareCLI"),
    "TurboFieldfareCLI",
]
_MODEL_CANDIDATES = [
    os.environ.get("TURBOFIELDFARE_MODEL", ""),
    "/Users/rrs/Desktop/thoughtcode/code-llm/scratch/gemma4.gturbo",
    os.path.expanduser("~/.turbofieldfare/turbo-fieldfare/scratch/gemma4.gturbo"),
]


def _as_turns(messages: list[Message]) -> list[dict]:
    """Keep the conversation as turns rather than folding it into one.

    Gemma 4 has no *system* role, so a system message is merged into the first
    user turn -- but user and assistant turns are kept as turns, and that is
    what makes the server's prompt cache usable at all: a hit requires the new
    request to continue the cached conversation, and a single re-folded user
    message is a different conversation every time. Measured: 0.8 s for a
    continuation against 48.5 s for the same tokens sent as one blob
    (docs/14 N4).
    """
    wire: list[dict] = []
    pending_system = ""
    for message in messages:
        if message.role == "system":
            pending_system = f"{pending_system}\n\n{message.content}".strip()
            continue
        content = message.content
        if pending_system and message.role == "user":
            content = f"{pending_system}\n\n{content}"
            pending_system = ""
        wire.append({"role": message.role, "content": content})
    if pending_system:
        wire.append({"role": "user", "content": pending_system})
    return wire


def _resolve(candidates: list[str], must_be_exec: bool) -> str | None:
    for c in candidates:
        if not c:
            continue
        p = Path(c)
        if must_be_exec:
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
        elif p.exists():
            return str(p)
    return None


class TurboFieldfareAdapter(Adapter):
    def __init__(self, spec: AdapterSpec, host: str = DEFAULT_HOST) -> None:
        self.spec = spec
        self.host = host.rstrip("/")
        self.cli = _resolve(_CLI_CANDIDATES, must_be_exec=True)
        # A filesystem path: what the CLI wants. The server wants its own model
        # id instead, which is a different thing entirely (see _server_model).
        self.model = _resolve(_MODEL_CANDIDATES, must_be_exec=False)
        self._server_model_id: str | None = None

    # -- health ------------------------------------------------------------

    def _server_up(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.host}/health", method="GET")
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _server_model(self) -> str:
        """The id the server answers to, read from /v1/models.

        Sending the model *directory* here returns 404: the server matches on
        its own id ("gemma-4-26b-a4b-it"), not on a path. This is why the
        server path was dead on arrival despite /health answering (docs/14 N4).
        """
        if self._server_model_id:
            return self._server_model_id
        try:
            req = urllib.request.Request(f"{self.host}/v1/models", method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.load(resp)
            self._server_model_id = data["data"][0]["id"]
        except Exception:
            self._server_model_id = _FALLBACK_MODEL_ID
        return self._server_model_id

    async def health(self) -> HealthStatus:
        if await asyncio.to_thread(self._server_up):
            return HealthStatus(True, "server", "gemma4", self.host, socket.gethostname())
        if self.cli and self.model:
            return HealthStatus(True, f"cli: {self.cli}", "gemma4", self.cli, socket.gethostname())
        missing = []
        if not self.cli:
            missing.append("TurboFieldfareCLI binary")
        if not self.model:
            missing.append("gemma4.gturbo model directory")
        return HealthStatus(
            False,
            f"not found: {', '.join(missing)} — run adapters/vendored/setup_turbofieldfare.sh",
            "gemma4",
            self.host,
        )

    # -- streaming ---------------------------------------------------------

    async def stream(self, messages: list[Message], cancel: asyncio.Event) -> AsyncIterator[str]:
        wire = _as_turns(messages)
        if await asyncio.to_thread(self._server_up):
            async for chunk in self._stream_server(wire, cancel):
                yield chunk
            return
        if not (self.cli and self.model):
            status = await self.health()
            raise RuntimeError(status.detail)
        async for chunk in self._stream_cli(wire, cancel):
            yield chunk

    async def _stream_server(
        self, wire: list[dict], cancel: asyncio.Event
    ) -> AsyncIterator[str]:
        payload = {
            "model": self._server_model(),
            "messages": wire,
            "max_tokens": int(os.environ.get("MAX_TOKENS", "1024")),
            "temperature": float(os.environ.get("TEMPERATURE", "0.2")),
            "stream": True,
        }
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        # Never signal the caller's cancel event: setting it on normal
        # completion made every turn look cancelled to the session, which
        # returned before the post-stream salvage pass could run.
        stop = asyncio.Event()

        # The read blocks between tokens, and a thread cannot be cancelled. So
        # the response object is shared with the consumer, which closes it to
        # unblock the read. Without this a cancelled round leaves a thread
        # parked on a socket, the process unable to exit, and the request open
        # on the server -- one such request logged 1015 s of "generating" for a
        # turn the client had finished with in 25 s.
        handle: dict[str, object] = {}

        def pump() -> None:
            req = urllib.request.Request(
                f"{self.host}/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=READ_TIMEOUT) as resp:
                    handle["resp"] = resp
                    for raw in resp:
                        if cancel.is_set() or stop.is_set():
                            break
                        line = raw.decode("utf-8").strip()
                        if not line or line.startswith(":") or not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        chunk = obj["choices"][0].get("delta", {}).get("content", "")
                        if chunk:
                            loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as exc:  # noqa: BLE001 — forwarded to the caller
                loop.call_soon_threadsafe(queue.put_nowait, exc)
                return
            loop.call_soon_threadsafe(queue.put_nowait, None)

        task = asyncio.create_task(asyncio.to_thread(pump))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            stop.set()
            resp = handle.get("resp")
            if resp is not None:
                try:
                    resp.close()  # unblocks the thread parked in read()
                except Exception:  # noqa: BLE001 — closing is best effort
                    pass
            task.cancel()

    async def _stream_cli(
        self, wire: list[dict], cancel: asyncio.Event
    ) -> AsyncIterator[str]:
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as tf:
            json.dump(wire, tf)
            messages_file = tf.name
        cmd = [
            self.cli,
            "--model",
            self.model,
            "--max-new",
            os.environ.get("MAX_TOKENS", "1024"),
            "--temperature",
            os.environ.get("TEMPERATURE", "0.2"),
            "--messages-file",
            messages_file,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            assert proc.stdout is not None
            while True:
                if cancel.is_set():
                    proc.kill()
                    return
                block = await proc.stdout.read(256)
                if not block:
                    break
                yield block.decode("utf-8", errors="replace")
            await proc.wait()
            if proc.returncode not in (0, None):
                err = (await proc.stderr.read()).decode("utf-8", errors="replace")
                raise RuntimeError(f"TurboFieldfareCLI failed: {err[:400]}")
        finally:
            if os.path.exists(messages_file):
                os.remove(messages_file)

    async def close(self) -> None:
        return None
