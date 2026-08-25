"""Adapter protocol. Host-agnostic by construction (docs/06, D2).

Nothing in this package may import tornado or jupyter_server: the same code runs
inside the server extension today and inside the `jupyter-vybe-local` sidecar
in v1.1, and that only stays true if the dependency never creeps in.
"""

from __future__ import annotations

import asyncio
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

    def stream(self, messages: list[Message], cancel: asyncio.Event) -> AsyncIterator[str]: ...

    async def close(self) -> None: ...
