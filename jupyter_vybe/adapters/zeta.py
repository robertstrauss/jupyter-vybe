"""Zeta 2 through llama-server (docs/15).

Zed's edit-prediction model, served by llama.cpp with prompt-lookup
speculative decoding (``--spec-type ngram-mod``). That suits an edit predictor
exactly: most of what it writes is the editable region copied back, and a
draft taken from the prompt is accepted almost every time there -- measured at
96% on a rewrite, against about half on new code.

Unlike every other adapter this one is not a chat model and does not speak the
tagged-text protocol. The session lays the turn out (jupyter_vybe/zeta.py) and
calls ``complete`` directly; ``stream`` exists only to satisfy the protocol.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import urllib.request
from typing import AsyncIterator

from .base import Adapter, AdapterSpec, HealthStatus, Message, post_json

DEFAULT_HOST = os.environ.get("ZETA_HOST", "http://127.0.0.1:8012")
STOP = ">>>>>>> UPDATED"


class ZetaAdapter(Adapter):
    # Read by the session: drive this with zeta.plan_turn, not with messages.
    edit_predict = True

    def __init__(self, spec: AdapterSpec, host: str = DEFAULT_HOST) -> None:
        self.spec = spec
        self.host = host.rstrip("/")

    def _get(self, path: str) -> dict:
        with urllib.request.urlopen(f"{self.host}{path}", timeout=2) as resp:
            return json.load(resp)

    async def health(self) -> HealthStatus:
        try:
            await asyncio.to_thread(self._get, "/health")
        except Exception as exc:  # noqa: BLE001 -- reported, not raised
            return HealthStatus(
                False,
                f"llama-server is not answering at {self.host} ({exc}). Start it with "
                "llama-server -m <zeta-2 gguf> --port 8012 --spec-type ngram-mod (docs/15)",
                "zeta2",
                self.host,
            )
        return HealthStatus(True, "llama-server", "zeta2", self.host, socket.gethostname())

    async def warm(self) -> None:
        return None  # llama-server holds the model for as long as it runs

    async def complete(self, prompt: str, cancel: asyncio.Event, n_predict: int = 768) -> tuple[str, dict]:
        """One prediction: the rewritten region, and llama-server's timings."""
        reply = await post_json(
            f"{self.host}/completion",
            {
                "prompt": prompt,
                "n_predict": n_predict,
                "temperature": 0,
                "stop": [STOP],
                # The file and history are the same from one chained step to
                # the next; only the region at the end changes.
                "cache_prompt": True,
            },
            cancel,
            timeout=120,
        )
        if reply is None:
            return "", {}
        return reply.get("content", ""), reply.get("timings", {})

    async def fill(self, prompt: str, stop: list[str], cancel: asyncio.Event, n_predict: int = 512) -> tuple[str, dict]:
        """Plain fill-in-the-middle: no editable region, no end marker."""
        reply = await post_json(
            f"{self.host}/completion",
            {"prompt": prompt, "n_predict": n_predict, "temperature": 0, "stop": stop,
             "cache_prompt": True},
            cancel,
            timeout=120,
        )
        if reply is None:
            return "", {}
        return reply.get("content", ""), reply.get("timings", {})

    async def stream(self, messages: list[Message], cancel: asyncio.Event) -> AsyncIterator[str]:
        raise RuntimeError("Zeta predicts edits; the session drives it through complete()")
        yield ""  # pragma: no cover -- makes this an async generator

    async def close(self) -> None:
        return None
