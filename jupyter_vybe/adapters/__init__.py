"""Adapter registry (docs/06)."""

from __future__ import annotations

from .base import Adapter, AdapterSpec, HealthStatus, Message
from .command import CommandAdapter
from .ollama import OllamaAdapter
from .openai_compat import OpenAICompatAdapter
from .relay import HUB, RunnerAdapter
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
    "command": AdapterSpec(
        "command",
        "A command (claude -p, or any binary)",
        200000,
        requires_config=["agent.command"],
    ),
    "runner": AdapterSpec(
        "runner",
        "The agent on your own machine (jupyter-vybe-local connect)",
        200000,
        # Where the *model* runs is the runner's to say -- claude, codex and
        # agy are cloud models, an Ollama runner is not. Updated from the
        # runner's hello in AgentsHandler; False until then is the safe answer.
        local=False,
    ),
    "openai-compat": AdapterSpec(
        "openai-compat",
        "OpenAI-compatible endpoint (vLLM, llama.cpp, LM Studio, hosted)",
        32768,
        local=False,
        requires_config=["agent.endpoint", "agent.model"],
        # Declared, unused in v1.0: every adapter goes through the tagged-text
        # protocol (D3).
        supports_native_tools=True,
    ),
}

_OLLAMA_MODELS = {
    "ollama-qwen25-coder-7b": "qwen2.5-coder:7b",
    "ollama-qwen25-coder-14b": "qwen2.5-coder:14b",
    "ollama-qwen25-coder-3b": "qwen2.5-coder:3b",
}


def build_adapter(agent_id: str, options=None) -> Adapter:
    """Options come from the settings pane, as a plain dict of strings."""
    spec = ADAPTER_REGISTRY[agent_id]
    opts = _as_dict(options)
    if agent_id in _OLLAMA_MODELS:
        return OllamaAdapter(spec, _OLLAMA_MODELS[agent_id])
    if agent_id == "turbofieldfare-gemma4":
        return TurboFieldfareAdapter(spec)
    if agent_id == "command":
        return CommandAdapter(
            spec,
            opts.get("command", ""),
            persistent=str(opts.get("persistent", "")).lower() in ("1", "true", "yes"),
            api_key_env=opts.get("apiKeyEnv", ""),
        )
    if agent_id == "runner":
        return RunnerAdapter(spec, HUB, notebook_path=str(opts.get("notebookPath", "")))
    if agent_id == "openai-compat":
        return OpenAICompatAdapter(
            spec,
            opts.get("endpoint", "http://127.0.0.1:8000"),
            opts.get("model", ""),
            api_key_env=opts.get("apiKeyEnv", ""),
        )
    raise KeyError(agent_id)


def _as_dict(options) -> dict:
    """Accepts a dict, or the tornado get_query_arguments callable."""
    if options is None:
        return {}
    if isinstance(options, dict):
        return {k: v for k, v in options.items()}
    return {}


__all__ = [
    "ADAPTER_REGISTRY",
    "Adapter",
    "AdapterSpec",
    "HealthStatus",
    "Message",
    "CommandAdapter",
    "OpenAICompatAdapter",
    "TurboFieldfareAdapter",
    "build_adapter",
]
