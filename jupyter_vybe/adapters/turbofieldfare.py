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
        self.model = _resolve(_MODEL_CANDIDATES, must_be_exec=False)

    # -- health ------------------------------------------------------------

    def _server_up(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.host}/health", method="GET")
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                return resp.status == 200
        except Exception:
            return False

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
        # Gemma 4 has no system role; fold the transcript into one user turn.
        prompt = "\n\n".join(m.content for m in messages)
        if await asyncio.to_thread(self._server_up):
            async for chunk in self._stream_server(prompt, cancel):
                yield chunk
            return
        if not (self.cli and self.model):
            status = await self.health()
            raise RuntimeError(status.detail)
        async for chunk in self._stream_cli(prompt, cancel):
            yield chunk

    async def _stream_server(self, prompt: str, cancel: asyncio.Event) -> AsyncIterator[str]:
        payload = {
            "model": self.model or "gemma4",
            "messages": [{"role": "user", "content": prompt}],
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

        def pump() -> None:
            req = urllib.request.Request(
                f"{self.host}/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req) as resp:
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
            task.cancel()

    async def _stream_cli(self, prompt: str, cancel: asyncio.Event) -> AsyncIterator[str]:
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as tf:
            json.dump([{"role": "user", "content": prompt}], tf)
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
