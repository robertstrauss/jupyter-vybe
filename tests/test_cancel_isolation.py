"""Adapters must not mutate the caller's cancel event.

Regression: OllamaAdapter.stream ended with `finally: cancel.set()`, which
fires on normal completion as well as on early exit. The session then saw a
set cancel event, reported stopReason "cancelled", and returned *before* the
post-stream salvage pass. Tool tags still worked, because those are emitted
mid-stream; fenced replies silently landed in the notebook as prose.
"""

import asyncio

import pytest

from jupyter_vybe.adapters.base import AdapterSpec, Message
from jupyter_vybe.session import AgentSession

FENCED = "Here you go:\n\n```python\ndef f(x):\n    return x * 2\n```\n\nDone."


class ScriptedAdapter:
    spec = AdapterSpec("scripted", "Scripted", 32768)

    def __init__(self, text: str) -> None:
        self.text = text
        self.saw_cancel_set_at_end = None

    async def health(self):  # pragma: no cover - unused
        return None

    async def stream(self, messages, cancel):
        try:
            for i in range(0, len(self.text), 5):
                yield self.text[i : i + 5]
        finally:
            self.saw_cancel_set_at_end = cancel.is_set()

    async def close(self):
        return None


def _session(adapter) -> AgentSession:
    s = AgentSession.__new__(AgentSession)
    s.notebook_path = "/n.ipynb"
    s.agent_id = "scripted"
    s.persistent = True
    s.options = {}
    s.adapter = adapter
    s.messages = []
    s.turns = 0
    s.snapshot_seq = -1
    s.touched = 0.0
    s._cancel = asyncio.Event()
    s._tool_waiters = {}
    return s


async def _run(session):
    msg = {
        "prompt": "add a doubling function",
        "snapshot": "{}",
        "snapshotSeq": 0,
        "kernelInfo": {"name": "python3", "language": "python", "version": "3.13"},
        "promptCellId": "p1",
        "globalPrompt": "",
        "limits": {},
    }
    return [f async for f in session.run(msg)]


@pytest.mark.asyncio
async def test_normal_completion_is_not_reported_as_cancelled():
    session = _session(ScriptedAdapter(FENCED))
    frames = await _run(session)
    done = [f for f in frames if f["op"] == "done"]
    assert done and done[0]["stopReason"] == "end"


@pytest.mark.asyncio
async def test_fenced_reply_is_salvaged_into_a_cell():
    session = _session(ScriptedAdapter(FENCED))
    frames = await _run(session)
    tools = [f for f in frames if f["op"] == "tool"]
    assert len(tools) == 1
    assert tools[0]["name"] == "add_cell"
    assert tools[0]["args"]["body"] == "def f(x):\n    return x * 2"


@pytest.mark.asyncio
async def test_salvage_emits_prose_reset_so_code_is_not_duplicated():
    session = _session(ScriptedAdapter(FENCED))
    frames = await _run(session)
    ops = [f["op"] for f in frames]
    assert "proseReset" in ops
    assert ops.index("proseReset") < ops.index("tool")


@pytest.mark.asyncio
async def test_session_cancel_event_stays_clear_after_a_normal_turn():
    adapter = ScriptedAdapter(FENCED)
    session = _session(adapter)
    await _run(session)
    assert not session._cancel.is_set()
    assert adapter.saw_cancel_set_at_end is False


@pytest.mark.asyncio
async def test_tagged_reply_needs_no_salvage():
    tagged = '<vybe:add_cell type="code">\nz = 1\n</vybe:add_cell>'
    session = _session(ScriptedAdapter(tagged))
    frames = await _run(session)
    assert not [f for f in frames if f["op"] == "salvaged"]
    assert [f for f in frames if f["op"] == "tool"]
