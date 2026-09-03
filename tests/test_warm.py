"""Being ready before the first prompt, not because of it.

Warming is best-effort by construction: it exists to save three seconds, and
nothing about it is worth failing an idle notebook over. These pin that down,
because "swallow every exception" is the kind of code that rots into
swallowing the exception that mattered.
"""

import asyncio

import pytest

from jupyter_vybe.assembler import _load, preload_prompts
from jupyter_vybe.session import AgentSession


def test_preloading_fills_the_template_cache():
    _load.cache_clear()
    assert _load.cache_info().currsize == 0

    preload_prompts()

    # The first prompt of a session now reads no files at all.
    assert _load.cache_info().currsize >= 5


def _session(tmp_path):
    return AgentSession(str(tmp_path / "n.ipynb"), "command", True, {"command": "true"})


def test_warming_an_adapter_with_nothing_to_warm_is_fine(tmp_path):
    session = _session(tmp_path)
    # Adapters inherit the protocol's do-nothing default, so every one of them
    # can be warmed and only the ones with weights to load actually do.
    asyncio.run(session.warm())  # must not raise

    # And it still did the half it could do.
    assert _load.cache_info().currsize >= 5


def test_a_failing_warm_up_is_not_the_users_problem(tmp_path):
    session = _session(tmp_path)

    async def explode() -> None:
        raise RuntimeError("ollama is not running")

    session.adapter.warm = explode  # type: ignore[attr-defined]

    # Nobody has asked for anything yet. The real request is where an
    # unreachable agent gets reported, with a message about the request.
    asyncio.run(session.warm())


def test_the_ollama_warm_up_asks_for_no_tokens(monkeypatch):
    """It loads the weights; it must not generate, or it is just a prompt."""
    from jupyter_vybe.adapters import ollama

    sent: list[dict] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        import json

        sent.append(json.loads(req.data.decode()))
        return FakeResponse()

    adapter = ollama.OllamaAdapter.__new__(ollama.OllamaAdapter)
    adapter.model = "qwen2.5-coder:7b"
    adapter.host = "http://127.0.0.1:11434"
    adapter._server_up = True
    monkeypatch.setattr(ollama.urllib.request, "urlopen", fake_urlopen)

    asyncio.run(adapter.warm())

    assert len(sent) == 1
    assert sent[0]["prompt"] == ""
    assert sent[0]["model"] == "qwen2.5-coder:7b"
    # Held in memory afterwards, or the next prompt pays the load again.
    assert sent[0]["keep_alive"] == ollama.KEEP_ALIVE


@pytest.mark.parametrize("attr", ["health", "stream", "close"])
def test_warm_did_not_displace_the_rest_of_the_protocol(attr):
    from jupyter_vybe.adapters.base import Adapter

    assert hasattr(Adapter, attr)
