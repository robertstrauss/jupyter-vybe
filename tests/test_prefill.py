"""Reading the notebook ahead of the request (session.prefill).

The client sends a `warm` frame with a snapshot when a cell becomes a prompt.
The session reads that turn's context into the model's cache, and the real
turn sends: the context, the model's own reply to it, then the request. That
shape is what TurboFieldfareServer's single-prefix cache can reuse -- measured
at 1,190 of 1,220 prompt tokens, 2.6 s against 30 s cold.
"""

import asyncio
import json

from conftest import build_session
from jupyter_vybe.adapters import Message
from jupyter_vybe.assembler import READY_ACK, READY_INSTRUCTION, split_request

KERNEL = {"name": "python3", "language": "python", "version": "3.13"}


def snapshot(prompt_source="> ", extra=""):
    cells = [
        {"id": "c1", "cell_type": "code", "source": "import numpy as np"},
        {"id": "c2", "cell_type": "code", "source": "def f(x):\n    return x" + extra},
        {"id": "p1", "cell_type": "code", "source": prompt_source},
    ]
    return json.dumps({"cells": cells})


def frame(seq=0, prompt_source="> ", extra="", **more):
    return {
        "snapshot": snapshot(prompt_source, extra),
        "snapshotSeq": seq,
        "promptCellId": "p1",
        "kernelInfo": KERNEL,
        "limits": {},
        **more,
    }


class Reader:
    """Can read ahead; records what it was asked and when."""

    def __init__(self, ack="Ready", gate: asyncio.Event | None = None):
        self.ack = ack
        self.gate = gate
        self.prefills: list[list[Message]] = []
        self.cancels: list[asyncio.Event] = []
        self.streams: list[list[Message]] = []

    async def prefill(self, messages, cancel):
        self.prefills.append(list(messages))
        self.cancels.append(cancel)
        if self.gate is not None:
            waiter = asyncio.ensure_future(cancel.wait())
            gate = asyncio.ensure_future(self.gate.wait())
            await asyncio.wait({waiter, gate}, return_when=asyncio.FIRST_COMPLETED)
            waiter.cancel()
            gate.cancel()
            if cancel.is_set():
                return ""
        return self.ack

    async def stream(self, messages, cancel):
        self.streams.append(list(messages))
        yield '<vybe:add_cell type="code">\nx = 1\n</vybe:add_cell>'


async def run(session, msg):
    return [e async for e in session.run(msg)]


def test_the_turn_is_context_then_the_models_reply_then_the_request():
    async def go():
        adapter = Reader(ack="Ready.")
        session = build_session(adapter)
        session.prefill(frame())
        await run(session, frame(prompt_source="> add a function g", prompt="add a function g", roundId="r0"))
        return adapter, session

    adapter, session = asyncio.run(go())
    assert len(adapter.prefills) == 1, "read once, ahead; not again at submit"
    sent = adapter.streams[0]
    assert [m.role for m in sent] == ["user", "assistant", "user"]
    assert sent[0].content == adapter.prefills[0][-1].content
    assert sent[0].content.endswith(READY_INSTRUCTION)
    # Sent back verbatim: the server matches the history on the exact words.
    assert sent[1].content == "Ready."
    assert sent[2].content.endswith("## Request\n\nadd a function g")
    assert session.messages[-1].role == "assistant"


def test_the_request_text_does_not_change_the_context():
    """Typing the request must not make the read-ahead stale."""

    async def go():
        adapter = Reader()
        session = build_session(adapter)
        session.prefill(frame(prompt_source=">"))
        session.prefill(frame(prompt_source="> add"))  # deduplicated
        await run(session, frame(prompt_source="> add a function g", prompt="add a function g"))
        return adapter

    adapter = asyncio.run(go())
    assert len(adapter.prefills) == 1
    assert "add a function" not in adapter.streams[0][0].content


def test_a_notebook_change_after_the_read_ahead_reads_again():
    async def go():
        gate = asyncio.Event()
        adapter = Reader(gate=gate)
        session = build_session(adapter)
        session.prefill(frame())
        await asyncio.sleep(0)
        runner = asyncio.ensure_future(
            run(session, frame(extra="\n# edited", prompt="p", prompt_source="> p"))
        )
        await asyncio.sleep(0.01)
        gate.set()
        await runner
        return adapter

    adapter = asyncio.run(go())
    assert len(adapter.prefills) == 2
    assert adapter.cancels[0].is_set(), "the stale read-ahead is stopped, not left to finish"
    assert "# edited" in adapter.streams[0][0].content


def test_without_a_read_ahead_the_turn_reads_first():
    async def go():
        adapter = Reader()
        session = build_session(adapter)
        await run(session, frame(prompt="p", prompt_source="> p"))
        return adapter

    adapter = asyncio.run(go())
    assert len(adapter.prefills) == 1
    assert [m.role for m in adapter.streams[0]] == ["user", "assistant", "user"]


def test_a_failed_read_ahead_falls_back_to_a_fixed_reply():
    async def go():
        adapter = Reader(ack="")
        session = build_session(adapter)
        await run(session, frame(prompt="p", prompt_source="> p"))
        return adapter

    adapter = asyncio.run(go())
    assert adapter.streams[0][1].content == READY_ACK


def test_the_follow_up_read_ahead_continues_the_conversation():
    async def go():
        adapter = Reader()
        session = build_session(adapter)
        await run(session, frame(seq=0, prompt="p", prompt_source="> p"))
        session.prefill(frame(seq=1, extra="\n# more"))
        await asyncio.sleep(0)
        return adapter, session

    adapter, session = asyncio.run(go())
    second = adapter.prefills[1]
    # The whole first turn, the reply included, then one new user message:
    # the only continuation the server's cache reuses.
    assert second[:-1] == session.messages
    assert second[-1].role == "user"
    assert "EDITED" in second[-1].content


def test_cancelling_while_reading_commits_nothing():
    async def go():
        adapter = Reader(gate=asyncio.Event())
        session = build_session(adapter)
        runner = asyncio.ensure_future(run(session, frame(prompt="p", prompt_source="> p")))
        await asyncio.sleep(0.01)
        session.cancel()
        events = await runner
        return session, events

    session, events = asyncio.run(go())
    assert events[-1] == {"op": "done", "stopReason": "cancelled"}
    assert session.messages == []
    assert session.snapshot_seq == -1


def test_no_read_ahead_while_a_round_runs():
    adapter = Reader()
    session = build_session(adapter, _running=True)
    asyncio.run(asyncio.sleep(0))
    session.prefill(frame())
    assert adapter.prefills == [] and session._prefill is None


def test_an_adapter_that_cannot_read_ahead_gets_one_message():
    class Plain:
        def __init__(self):
            self.streams = []

        async def stream(self, messages, cancel):
            self.streams.append(list(messages))
            yield "ok"

    adapter = Plain()
    session = build_session(adapter)
    session.prefill(frame())  # ignored
    asyncio.run(run(session, frame(prompt="p", prompt_source="> p")))
    assert len(adapter.streams[0]) == 1
    assert READY_INSTRUCTION not in adapter.streams[0][0].content
    assert adapter.streams[0][0].content.rstrip().endswith("## Request\n\np")


def test_split_request_takes_the_last_heading():
    context, request = split_request("conventions: ## Request is a heading\n\n## Request\n\ndo it\n")
    assert context == "conventions: ## Request is a heading"
    assert request == "## Request\n\ndo it"
