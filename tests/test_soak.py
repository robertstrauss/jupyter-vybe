"""Many rounds in a row leave nothing behind.

The bug this guards against actually happened: an abandoned stream parked a
thread on a socket, and because a thread cannot be cancelled the process could
not exit and the request stayed open server-side. One leak per round is
invisible in a test that runs one round.
"""

import asyncio
import gc
import json
import threading

import pytest

from jupyter_vybe.adapters import Message
from jupyter_vybe.session import AgentSession, SessionManager

ROUNDS = 120
SNAPSHOT = json.dumps({
    "cells": [
        {"id": "c1", "cell_type": "code", "source": "import numpy as np"},
        {"id": "p1", "cell_type": "code", "source": "#% do a thing"},
    ]
})


class Scripted:
    """Answers instantly, and counts how many times it was asked."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, messages: list[Message], cancel: asyncio.Event):
        self.calls += 1
        yield '<vybe:add_cell type="code">\nx = 1\n</vybe:add_cell>'


def _session(adapter, tmp_path) -> AgentSession:
    s = AgentSession.__new__(AgentSession)
    s.notebook_path = str(tmp_path / "soak.ipynb")
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
    s._cells = []
    s._prose = ""
    s.history_enabled = False
    s.store = None
    return s


async def _round(session, index: int) -> list[dict]:
    return [
        event
        async for event in session.run({
            "prompt": f"request {index}",
            "snapshot": SNAPSHOT,
            "snapshotSeq": index,
            "promptCellId": "p1",
            "kernelInfo": {"name": "python3", "language": "python"},
            "roundId": f"r{index}",
            "limits": {},
        })
    ]


def test_many_rounds_leak_no_threads(tmp_path):
    adapter = Scripted()
    session = _session(adapter, tmp_path)
    gc.collect()
    before = threading.active_count()

    async def go():
        for i in range(ROUNDS):
            await _round(session, i)

    asyncio.run(go())
    gc.collect()

    assert adapter.calls == ROUNDS
    assert threading.active_count() == before


def test_tool_waiters_do_not_accumulate(tmp_path):
    """Each blocking lookup registers a future; none may outlive its round."""
    session = _session(Scripted(), tmp_path)

    async def go():
        for i in range(ROUNDS):
            await _round(session, i)

    asyncio.run(go())
    assert session._tool_waiters == {}


def test_the_conversation_grows_but_stays_proportionate(tmp_path):
    """Persistent history is the point; unbounded growth per round is not."""
    session = _session(Scripted(), tmp_path)

    async def go():
        for i in range(20):
            await _round(session, i)

    asyncio.run(go())

    # One user message and one assistant message per round, and no more: a
    # duplicate append per turn would double memory and prompt size together.
    assert len(session.messages) == 40
    assert session.turns == 20
    # And later turns are the short form, so the transcript does not grow by a
    # full notebook each time (docs/09).
    assert len(session.messages[-2].content) < len(session.messages[0].content)


def test_the_session_manager_reuses_rather_than_accumulates(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jupyter_vybe.session.build_adapter", lambda *a, **k: Scripted()
    )
    manager = SessionManager()
    first = manager.acquire(
        notebook_path=str(tmp_path / "a.ipynb"),
        agent_id="scripted",
        persistent=True,
        options={},
    )
    for _ in range(50):
        again = manager.acquire(
            notebook_path=str(tmp_path / "a.ipynb"),
            agent_id="scripted",
            persistent=True,
            options={},
        )
    assert again is first, "one notebook, one session"


@pytest.mark.parametrize("cancel_at", [0, 1, 5])
def test_cancelling_repeatedly_leaves_nothing_running(tmp_path, cancel_at):
    """A cancelled round is the path that leaked a thread once already."""
    session = _session(Scripted(), tmp_path)
    gc.collect()
    before = threading.active_count()

    async def go():
        for i in range(30):
            if i % (cancel_at + 1) == 0:
                session.cancel()
            await _round(session, i)
            session._cancel = asyncio.Event()

    asyncio.run(go())
    gc.collect()
    assert threading.active_count() == before
