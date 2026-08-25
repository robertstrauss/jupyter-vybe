"""Adapter registry (docs/06)."""

from __future__ import annotations

from .base import Adapter, AdapterSpec, HealthStatus, Message
from .ollama import OllamaAdapter
from .turbofieldfare import TurboFieldfareAdapter

ADAPTER_REGISTRY: dict[str, AdapterSpec] = {
    "ollama-qwen25-coder-7b": AdapterSpec(
        "ollama-qwen25-coder-7b", "Qwen2.5-Coder 7B (Ollama, local)", 32768
    ),
    "ollama-qwen25-coder-14b": AdapterSpec(
        "ollama-qwen25-coder-14b", "Qwen2.5-Coder 14B (Ollama, local)", 32768
    ),
    "ollama-qwen25-coder-3b": AdapterSpec(
        "ollama-qwen25-coder-3b", "Qwen2.5-Coder 3B (Ollama, local)", 32768
    ),
    "turbofieldfare-gemma4": AdapterSpec(
        "turbofieldfare-gemma4", "Gemma 4 26B-A4B (TurboFieldfare, local)", 131072
    ),
}

_OLLAMA_MODELS = {
    "ollama-qwen25-coder-7b": "qwen2.5-coder:7b",
    "ollama-qwen25-coder-14b": "qwen2.5-coder:14b",
    "ollama-qwen25-coder-3b": "qwen2.5-coder:3b",
}


def build_adapter(agent_id: str, options=None) -> Adapter:
    spec = ADAPTER_REGISTRY[agent_id]
    if agent_id in _OLLAMA_MODELS:
        return OllamaAdapter(spec, _OLLAMA_MODELS[agent_id])
    if agent_id == "turbofieldfare-gemma4":
        return TurboFieldfareAdapter(spec)
    raise KeyError(agent_id)


__all__ = [
    "ADAPTER_REGISTRY",
    "Adapter",
    "AdapterSpec",
    "HealthStatus",
    "Message",
    "TurboFieldfareAdapter",
    "build_adapter",
]
