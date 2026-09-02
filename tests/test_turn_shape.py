"""Which prompt form each turn gets, and why it matters (docs/09, docs/14 N4).

Turn 0 sends the whole preamble. Later turns in a persistent session send the
short form -- not to save tokens, but because a request that continues the
conversation hits the server's prompt cache: measured at 0.8 s against 48.5 s
for the same tokens sent cold. Re-assembling the full preamble every turn
appends a fresh user message whose prefix is identical but whose *content* the
server must prefill again.
"""

import asyncio
import json

from jupyter_vybe.adapters import Message
from conftest import build_session
from jupyter_vybe.session import AgentSession

KERNEL = {"name": "python3", "language": "python", "version": "3.13"}
SNAPSHOT = json.dumps({
    "cells": [
        {"id": "c1", "cell_type": "code", "source": "import numpy as np"},
        {"id": "c2", "cell_type": "code", "source": "def f(x):\n    return x"},
        {"id": "p1", "cell_type": "code", "source": "#% do a thing"},
    ]
})


class Scripted:
    """Replies with one tag, and remembers what it was asked."""

    def __init__(self):
        self.prompts: list[str] = []

    async def stream(self, messages: list[Message], cancel):
        self.prompts.append(messages[-1].content)
        yield '<vybe:add_cell type="code">\nx = 1\n</vybe:add_cell>'


def _session(adapter, persistent=True) -> AgentSession:
    return build_session(adapter, persistent=persistent)


async def _collect(agen):
    return [event async for event in agen]


def _turn(session, prompt, seq):
    async def go():
        return [e async for e in session.run({
            "prompt": prompt,
            "snapshot": SNAPSHOT,
            "snapshotSeq": seq,
            "promptCellId": "p1",
            "kernelInfo": KERNEL,
            "roundId": f"r{seq}",
            "limits": {},
        })]

    return asyncio.run(go())


def test_first_turn_carries_the_full_tooling_prompt():
    adapter = Scripted()
    session = _session(adapter)
    _turn(session, "add a function", 0)
    assert "<vybe:propose_edit cell=" in adapter.prompts[0]
    assert "## How you modify the notebook" in adapter.prompts[0]


def test_second_turn_is_short_and_still_carries_the_listing():
    adapter = Scripted()
    session = _session(adapter)
    _turn(session, "add a function", 0)
    _turn(session, "now edit it", 1)

    first, second = adapter.prompts[0], adapter.prompts[1]
    assert len(second) < len(first) / 2, "follow-up should not repeat the preamble"
    # The listing is what stops the model inventing a cell number, so it is not
    # part of what gets dropped.
    assert "[1]" in second and "[3]" in second
    assert "propose_edit" in second  # the recap still names the tools


def test_a_non_persistent_session_always_sends_the_full_prompt():
    adapter = Scripted()
    session = _session(adapter, persistent=False)
    _turn(session, "add a function", 0)
    _turn(session, "now edit it", 1)
    assert "## How you modify the notebook" in adapter.prompts[1]


def test_the_follow_up_carries_an_index_and_a_diff_not_the_notebook():
    """docs/09: the whole notebook goes once; after that, what changed."""
    adapter = Scripted()
    session = _session(adapter)
    _turn(session, "add a function", 0)
    _turn(session, "now edit it", 1)

    second = adapter.prompts[1]
    # The index is one line per cell: addressable, not a transcript.
    assert "[2] code id=c2: def f(x):" in second
    assert "    return x" not in second, "cell bodies belong in the first turn"
    assert "Changes since your last turn" in second


def test_a_changed_cell_is_reported_as_an_edit_on_the_next_turn():
    adapter = Scripted()
    session = _session(adapter)
    _turn(session, "add a function", 0)

    changed = json.loads(SNAPSHOT)
    changed["cells"][1]["source"] = "def f(x):\n    return x * 2"

    async def go():
        return [e async for e in session.run({
            "prompt": "why did that change",
            "snapshot": json.dumps(changed),
            "snapshotSeq": 1,
            "promptCellId": "p1",
            "kernelInfo": KERNEL,
            "roundId": "r1",
            "limits": {},
        })]

    asyncio.run(go())
    second = adapter.prompts[1]
    assert "EDITED  cell c2" in second
    assert "+    return x * 2" in second


def test_a_periodic_full_snapshot_bounds_drift():
    adapter = Scripted()
    session = _session(adapter)
    for i in range(11):
        _turn(session, f"request {i}", i)
    # Turn 10 resyncs: ten turns of diffs is as far as we trust the picture.
    assert "## How you modify the notebook" in adapter.prompts[10]
    assert "## How you modify the notebook" not in adapter.prompts[9]


def test_the_conversation_is_what_makes_the_short_form_safe():
    # The full tooling prompt is still in the message list on turn 2, which is
    # why the recap can be a reminder rather than a re-explanation.
    adapter = Scripted()
    session = _session(adapter)
    _turn(session, "add a function", 0)
    _turn(session, "now edit it", 1)
    assert "## How you modify the notebook" in session.messages[0].content
    assert session.messages[0].role == "user"


def test_an_out_of_order_snapshot_forces_a_full_one():
    """docs/09's sequence check, which nothing implemented until now.

    A diff is only meaningful against the snapshot it was computed from. If
    the client's counter skips -- server restarted, session evicted, notebook
    reopened -- diffing against a stale picture desynchronises the agent
    silently, which is R4 exactly. Sending everything is cheaper than being
    subtly wrong.
    """
    adapter = Scripted()
    session = _session(adapter)
    _turn(session, "first", 0)
    _turn(session, "second", 1)
    assert "## How you modify the notebook" not in adapter.prompts[1]

    # A skipped sequence number: the client is somewhere we have not seen.
    _turn(session, "third", 7)

    assert "## How you modify the notebook" in adapter.prompts[2]


def test_the_sequence_continues_normally_after_a_resync():
    adapter = Scripted()
    session = _session(adapter)
    _turn(session, "first", 0)
    _turn(session, "jumped", 5)
    # Having resynced at 5, 6 follows it and is a diff turn again.
    _turn(session, "next", 6)
    assert "## How you modify the notebook" not in adapter.prompts[2]


def test_cell_numbers_track_a_notebook_that_grew_since_turn_one():
    """The listing is renumbered every turn, and resolution uses that turn's
    listing -- so a cell added after the first prompt is addressable, and the
    numbers the model is shown are the numbers it is answered on."""
    adapter = Scripted()
    session = _session(adapter)
    _turn(session, "first", 0)

    grown = json.loads(SNAPSHOT)
    grown["cells"].insert(
        1, {"id": "added-later", "cell_type": "code", "source": "y = 2"}
    )
    asyncio.run(
        _collect(
            session.run(
                {
                    "prompt": "now edit it",
                    "snapshot": json.dumps(grown),
                    "snapshotSeq": 1,
                    "promptCellId": "p1",
                    "kernelInfo": KERNEL,
                    "roundId": "r1",
                    "limits": {},
                }
            )
        )
    )

    # The index the model sees puts the new cell at 2...
    assert "[2] code id=added-later" in adapter.prompts[1]
    # ...and that is what "2" resolves to when it answers.
    from jupyter_vybe.assembler import resolve_cell_ref

    assert resolve_cell_ref("2", session._cells) == "added-later"


def test_a_cell_is_never_addressed_by_execution_count():
    """Execution counts are reset by a kernel restart and renumbered by a
    re-run, so addressing by one would edit the wrong cell or find none."""
    from jupyter_vybe.assembler import resolve_cell_ref

    cells = [
        {"id": "first", "cell_type": "code", "source": "a", "execution_count": 7},
        {"id": "second", "cell_type": "code", "source": "b", "execution_count": 3},
    ]
    # "7" is a plausible execution count and an implausible position: it must
    # resolve to nothing rather than to the cell that happens to carry it.
    assert resolve_cell_ref("7", cells) == ""
    assert resolve_cell_ref("1", cells) == "first"
    assert resolve_cell_ref("second", cells) == "second"


def test_ollama_requests_keep_the_model_resident():
    """An idle Ollama unloads after five minutes, and the next prompt then
    pays a cold load -- seconds of nothing before the first token."""
    import asyncio
    import json as _json
    from unittest import mock

    from jupyter_vybe.adapters import build_adapter

    adapter = build_adapter("ollama-qwen25-coder-7b")
    sent = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __iter__(self):
            return iter([b'{"message": {"content": "hi"}, "done": true}'])

    def fake_urlopen(req, *a, **kw):
        sent["payload"] = _json.loads(req.data.decode())
        return FakeResponse()

    async def go():
        with mock.patch.object(adapter, "ensure_server", return_value=True):
            with mock.patch("urllib.request.urlopen", fake_urlopen):
                async for _ in adapter.stream(
                    [Message("user", "hello")], asyncio.Event()
                ):
                    pass

    asyncio.run(go())
    assert sent["payload"]["keep_alive"] == "30m"
