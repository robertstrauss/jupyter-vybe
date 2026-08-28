"""Ollama adapter — the default backend (docs/06).

Uses /api/chat rather than /api/generate: it gives native multi-turn history for
persistent sessions and for the tool loop. Auto-start of `ollama serve` is ported
from code-llm/run.sh.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import urllib.error
import urllib.request
from typing import AsyncIterator

from .base import Adapter, AdapterSpec, HealthStatus, Message

DEFAULT_HOST = os.environ.get("CODELLM_HOST", "http://127.0.0.1:11434")


class OllamaAdapter(Adapter):
    def __init__(self, spec: AdapterSpec, model: str, host: str = DEFAULT_HOST) -> None:
        self.spec = spec
        self.model = model
        self.host = host.rstrip("/")

    async def health(self) -> HealthStatus:
        try:
            await asyncio.to_thread(self._probe)
        except Exception as exc:
            return HealthStatus(False, f"unreachable at {self.host}: {exc}", self.model, self.host)
        return HealthStatus(True, "", self.model, self.host, socket.gethostname())

    def _probe(self) -> None:
        with urllib.request.urlopen(f"{self.host}/api/version", timeout=2):
            pass

    async def ensure_server(self) -> bool:
        """Start `ollama serve` if it is not already up. Returns True if reachable."""
        try:
            await asyncio.to_thread(self._probe)
            return True
        except Exception:
            pass
        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError:
            return False
        for _ in range(60):
            await asyncio.sleep(0.5)
            try:
                await asyncio.to_thread(self._probe)
                return True
            except Exception:
                continue
        return False

    async def complete_raw(self, prompt: str, max_tokens: int, timeout: float) -> str:
        """A raw, non-chat completion, for fill-in-the-middle (docs/12 D5).

        /api/generate with raw=True skips the chat template, which matters:
        FIM tokens wrapped in a chat turn ask the model a different question
        and get prose about the code instead of the code.
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "raw": True,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": max_tokens,
                # A completion the user has already typed past is worthless, so
                # the model is stopped early rather than left to finish.
                "stop": ["<|endoftext|>", "<|fim_pad|>", "<|file_sep|>"],
            },
        }

        def call() -> str:
            req = urllib.request.Request(
                f"{self.host}/api/generate",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp).get("response", "")

        return await asyncio.to_thread(call)

    async def stream(self, messages: list[Message], cancel: asyncio.Event) -> AsyncIterator[str]:
        if not await self.ensure_server():
            raise RuntimeError(
                "ollama is not running and could not be started (is it installed?)"
            )
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": {"temperature": 0.2},
        }
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        # Never signal the caller's cancel event: setting it on normal
        # completion made every turn look cancelled to the session, which
        # returned before the post-stream salvage pass could run.
        stop = asyncio.Event()

        def pump() -> None:
            req = urllib.request.Request(
                f"{self.host}/api/chat",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req) as resp:
                    for raw in resp:
                        if cancel.is_set() or stop.is_set():
                            break
                        line = raw.strip()
                        if not line:
                            continue
                        obj = json.loads(line)
                        if "error" in obj:
                            loop.call_soon_threadsafe(queue.put_nowait, RuntimeError(obj["error"]))
                            return
                        chunk = obj.get("message", {}).get("content", "")
                        if chunk:
                            loop.call_soon_threadsafe(queue.put_nowait, chunk)
                        if obj.get("done"):
                            break
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

    async def close(self) -> None:
        return None
