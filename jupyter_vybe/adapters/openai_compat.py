"""OpenAI-compatible chat completions: vLLM, llama.cpp, LM Studio, hosted (docs/06).

Host-agnostic like the rest of this package: urllib and asyncio only, no
tornado, no jupyter_server (D2).

The conversation is sent as turns, never folded into one message. Every server
with a prompt cache keys on the conversation continuing, so folding costs a
cache hit on every turn after the first -- measured at 24 s against 8 s on the
one local server we have (docs/14 N4).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import urllib.error
import urllib.request
from typing import AsyncIterator

from .base import Adapter, AdapterSpec, HealthStatus, Message

READ_TIMEOUT = float(os.environ.get("VYBE_OPENAI_READ_TIMEOUT", "180"))


class OpenAICompatAdapter(Adapter):
    def __init__(
        self,
        spec: AdapterSpec,
        endpoint: str,
        model: str,
        api_key_env: str = "",
    ) -> None:
        self.spec = spec
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env

    # -- helpers -----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(self.api_key_env, "") if self.api_key_env else ""
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _models(self) -> list[str]:
        req = urllib.request.Request(
            f"{self.endpoint}/v1/models", headers=self._headers(), method="GET"
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.load(resp)
        return [m.get("id", "") for m in data.get("data", [])]

    # -- health ------------------------------------------------------------

    async def health(self) -> HealthStatus:
        if self.api_key_env and not os.environ.get(self.api_key_env):
            return HealthStatus(
                False,
                f"{self.api_key_env} is not set in the server's environment",
                self.model,
                self.endpoint,
            )
        try:
            models = await asyncio.to_thread(self._models)
        except Exception as exc:  # noqa: BLE001 — reported, not raised
            return HealthStatus(False, f"{type(exc).__name__}: {exc}", self.model, self.endpoint)
        if self.model and models and self.model not in models:
            # The model id is the single most common misconfiguration, and the
            # server's own answer for it is right here.
            return HealthStatus(
                False,
                f"{self.model!r} is not served here; available: {', '.join(models[:8])}",
                self.model,
                self.endpoint,
            )
        return HealthStatus(True, "ok", self.model or (models[0] if models else ""),
                            self.endpoint, socket.gethostname())

    # -- raw completion ----------------------------------------------------

    async def complete_raw(self, prompt: str, max_tokens: int, timeout: float) -> str:
        """Fill-in-the-middle through the legacy /v1/completions route (D5).

        The chat route would wrap infilling tokens in a chat template, which
        asks the model a different question. vLLM, llama.cpp and LM Studio all
        keep the completions route for exactly this; a server that has dropped
        it answers 404, and the caller falls back to asking in words.
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "stream": False,
            "stop": ["<|endoftext|>", "<|fim_pad|>", "<|file_sep|>"],
        }

        def call() -> str:
            req = urllib.request.Request(
                f"{self.endpoint}/v1/completions",
                data=json.dumps(payload).encode(),
                headers=self._headers(),
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.load(resp)
            choices = data.get("choices") or [{}]
            return choices[0].get("text", "")

        try:
            return await asyncio.to_thread(call)
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 405):
                # No completions route here. Say so plainly rather than
                # returning an empty completion that looks like "no suggestion".
                raise NotImplementedError(
                    f"{self.endpoint} serves no /v1/completions route"
                ) from exc
            raise

    # -- streaming ---------------------------------------------------------

    async def stream(
        self, messages: list[Message], cancel: asyncio.Event
    ) -> AsyncIterator[str]:
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": int(os.environ.get("MAX_TOKENS", "1024")),
            "temperature": float(os.environ.get("TEMPERATURE", "0.2")),
            "stream": True,
        }
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        # Shared so the consumer can close it: a thread cannot be cancelled,
        # and a read between tokens blocks until the server sends something.
        handle: dict[str, object] = {}

        def pump() -> None:
            req = urllib.request.Request(
                f"{self.endpoint}/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers=self._headers(),
            )
            try:
                with urllib.request.urlopen(req, timeout=READ_TIMEOUT) as resp:
                    handle["resp"] = resp
                    for raw in resp:
                        if cancel.is_set() or stop.is_set():
                            break
                        line = raw.decode("utf-8").strip()
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choices = obj.get("choices") or [{}]
                        chunk = (choices[0].get("delta") or {}).get("content", "")
                        if chunk:
                            loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")[:400]
                loop.call_soon_threadsafe(
                    queue.put_nowait, RuntimeError(f"HTTP {exc.code}: {body}")
                )
                return
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
                    resp.close()
                except Exception:  # noqa: BLE001 — closing is best effort
                    pass
            task.cancel()

    async def close(self) -> None:
        return None
