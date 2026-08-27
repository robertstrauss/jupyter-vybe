"""The history endpoint, including the case where the record is not found."""

import json

import pytest

from jupyter_vybe.history import HistoryStore


@pytest.fixture()
def store(tmp_path):
    return HistoryStore(str(tmp_path / "nb.ipynb"), tmp_path / "history")


def test_a_round_writes_prompt_prose_and_tools(tmp_path, monkeypatch):
    """The session mirrors what it streams into the record (docs/05)."""
    import asyncio

    from jupyter_vybe.adapters import Message
    from jupyter_vybe.session import AgentSession

    class Scripted:
        async def stream(self, messages: list[Message], cancel):
            yield "Here is a helper.\n"
            yield '<vybe:add_cell type="code">\nx = 1\n</vybe:add_cell>'

    session = AgentSession.__new__(AgentSession)
    session.notebook_path = str(tmp_path / "nb.ipynb")
    session.agent_id = "scripted"
    session.persistent = True
    session.options = {}
    session.adapter = Scripted()
    session.messages = []
    session.turns = 0
    session.snapshot_seq = -1
    session.touched = 0.0
    session._cancel = asyncio.Event()
    session._tool_waiters = {}
    session._cells = []
    session._prose = ""
    session.history_enabled = True
    session.store = HistoryStore(session.notebook_path, tmp_path / "history")

    async def go():
        async for _ in session.run({
            "prompt": "add a helper",
            "snapshot": json.dumps({"cells": []}),
            "snapshotSeq": 0,
            "promptCellId": "p1",
            "kernelInfo": {"name": "python3", "language": "python"},
            "roundId": "r1",
            "limits": {},
        }):
            pass

    asyncio.run(go())

    entries = session.store.read()
    kinds = [e["kind"] for e in entries]
    assert kinds == ["prompt", "insertion", "prose"]
    assert entries[0]["text"] == "add a helper"
    assert entries[1]["source"] == "x = 1"
    assert entries[2]["text"] == "Here is a helper."
    assert all(e["roundId"] == "r1" for e in entries)


def test_history_off_writes_nothing(tmp_path):
    store = HistoryStore(str(tmp_path / "nb.ipynb"), tmp_path / "history")

    from jupyter_vybe.session import AgentSession

    session = AgentSession.__new__(AgentSession)
    session.history_enabled = False
    session.store = store
    session.notebook_path = str(tmp_path / "nb.ipynb")
    session.record({"kind": "prompt", "text": "nope"})

    assert not store.path.exists()


def test_a_read_only_store_does_not_break_the_round(tmp_path, monkeypatch):
    from jupyter_vybe.session import AgentSession

    store = HistoryStore(str(tmp_path / "nb.ipynb"), tmp_path / "history")

    def explode(_entry):
        raise OSError("read-only file system")

    monkeypatch.setattr(store, "append", explode)

    session = AgentSession.__new__(AgentSession)
    session.history_enabled = True
    session.store = store
    session.notebook_path = str(tmp_path / "nb.ipynb")

    session.record({"kind": "prompt", "text": "still answered"})  # must not raise
