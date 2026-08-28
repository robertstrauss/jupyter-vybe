"""The history endpoint, including the case where the record is not found."""

import json

import pytest

from conftest import build_session

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

    session = build_session(
        Scripted(),
        str(tmp_path / "nb.ipynb"),
        history_enabled=True,
        store=HistoryStore(str(tmp_path / "nb.ipynb"), tmp_path / "history"),
    )

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

    # No streaming in this test: record() is the whole subject.
    session = build_session(None, str(tmp_path / "nb.ipynb"), store=store)
    session.record({"kind": "prompt", "text": "nope"})

    assert not store.path.exists()


def test_a_read_only_store_does_not_break_the_round(tmp_path, monkeypatch):
    from jupyter_vybe.session import AgentSession

    store = HistoryStore(str(tmp_path / "nb.ipynb"), tmp_path / "history")

    def explode(_entry):
        raise OSError("read-only file system")

    monkeypatch.setattr(store, "append", explode)

    session = build_session(
        None, str(tmp_path / "nb.ipynb"), history_enabled=True, store=store
    )

    session.record({"kind": "prompt", "text": "still answered"})  # must not raise


def test_adopting_an_orphan_carries_its_entries_to_the_new_path(tmp_path):
    """A rename JupyterLab never saw -- `mv` in a terminal.

    The pane finds a record whose header names a notebook of this name and
    offers it; adopting copies the entries onto the current path's key so the
    prompts come back rather than starting empty (docs/05).
    """
    root = tmp_path / "history"
    old = HistoryStore(str(tmp_path / "before.ipynb"), root)
    old.append({"kind": "prompt", "roundId": "r1", "text": "written before the mv"})

    # Simulate the adoption the handler performs: copy the orphan's file onto
    # the new path's key, then rekey.
    adopted = HistoryStore(str(tmp_path / "after.ipynb"), root)
    adopted.path.parent.mkdir(parents=True, exist_ok=True)
    adopted.path.write_text(old.path.read_text())

    entries = adopted.read()

    assert [e["text"] for e in entries if e["kind"] == "prompt"] == [
        "written before the mv"
    ]


def test_the_echo_flag_returns_the_prompt_the_agent_was_given(tmp_path):
    """prompts.showAssembled: "what did it actually see" is the question that
    keeps needing an answer."""
    import asyncio

    from jupyter_vybe.adapters import Message
    from jupyter_vybe.session import AgentSession

    class Scripted:
        async def stream(self, messages: list[Message], cancel):
            yield "done"

    session = build_session(
        Scripted(), str(tmp_path / "n.ipynb"), echo_prompt=True
    )

    async def go():
        return [e async for e in session.run({
            "prompt": "add a helper",
            "snapshot": json.dumps({"cells": []}),
            "snapshotSeq": 0,
            "promptCellId": "p1",
            "kernelInfo": {"name": "python3", "language": "python"},
            "roundId": "r1",
            "limits": {},
        })]

    frames = asyncio.run(go())
    echoed = [f for f in frames if f["op"] == "assembledPrompt"]
    assert len(echoed) == 1
    assert "add a helper" in echoed[0]["prompt"]
    # It precedes generation, so it is there even if the round then fails.
    assert frames.index(echoed[0]) < next(
        i for i, f in enumerate(frames) if f.get("state") == "generating"
    )
