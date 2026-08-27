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
    s = AgentSession.__new__(AgentSession)
    s.notebook_path = "/n.ipynb"
    s.agent_id = "scripted"
    s.persistent = persistent
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
