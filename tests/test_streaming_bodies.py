"""Cell bodies arriving live, before the closing tag (docs/07).

The point is that a user watching a slow model sees the cell being written
instead of a blank pause. So the contract is: emit as early as is safe, never
emit something that has to be taken back, and let the closing TOOL event be
authoritative.
"""

from jupyter_vybe.protocol import EventKind, StreamParser, parse


def run(chunks: list[str], stream: bool = True):
    parser = StreamParser(stream_bodies=stream)
    events = []
    for chunk in chunks:
        events.extend(parser.feed(chunk))
    events.extend(parser.close())
    return events


def deltas(events) -> str:
    return "".join(e.text for e in events if e.kind is EventKind.TOOL_DELTA)


def final(events) -> str:
    return next(e.args["body"] for e in events if e.kind is EventKind.TOOL)


def test_the_open_tag_is_announced_before_any_body():
    events = run(['<vybe:add_cell type="code">\n', 'x = 1\n', '</vybe:add_cell>'])
    assert events[0].kind is EventKind.TOOL_START
    assert events[0].name == "add_cell"
    # Every attribute is known at the open tag, so the consumer can already
    # place the cell it is about to fill.
    assert events[0].args["type"] == "code"


def test_a_body_arrives_in_pieces_and_ends_up_whole():
    events = run(
        ['<vybe:add_cell type="code">\n', 'def f():\n', '    return 1\n', '</vybe:add_cell>']
    )
    assert deltas(events) == "def f():\n    return 1\n"
    assert final(events) == "def f():\n    return 1"


def test_a_fence_is_stripped_as_it_streams_not_afterwards():
    events = run(
        [
            '<vybe:add_cell type="code">\n',
            "```python\n",
            "x = 1\n",
            "```\n",
            "</vybe:add_cell>",
        ]
    )
    # Neither delimiter ever reaches the consumer, so the cell never flickers
    # with a stray ```python in it.
    assert "```" not in deltas(events)
    assert deltas(events) == "x = 1\n"
    assert final(events) == "x = 1"


def test_a_closing_fence_split_across_chunks_is_never_emitted():
    events = run(
        ['<vybe:add_cell type="code">\n', "```\n", "x = 1\n", "``", "`\n", "</vybe:add_cell>"]
    )
    assert "`" not in deltas(events)


def test_indentation_the_model_copied_is_removed_live():
    events = run(
        [
            '<vybe:propose_edit cell="3">\n',
            "    class A:\n",
            "        def f(self):\n",
            "</vybe:propose_edit>",
        ]
    )
    assert deltas(events) == "class A:\n    def f(self):\n"


def test_the_final_event_is_authoritative():
    """The stream is a guess in progress; the close is the answer."""
    events = run(
        ['<vybe:add_cell type="code">\n', "a = 1\n", "b = 2", "</vybe:add_cell>"]
    )
    assert final(events) == "a = 1\nb = 2"


def test_an_unterminated_body_streams_but_never_becomes_a_tool():
    events = run(['<vybe:add_cell type="code">\n', "half a cel"])
    assert deltas(events) == "half a cel"
    assert not [e for e in events if e.kind is EventKind.TOOL]
    # And the consumer is told, so it can discard what it showed.
    assert any(e.kind is EventKind.ERROR for e in events)


def test_streaming_is_off_by_default():
    """`parse` describes the outcome of a reply, not its timing -- which is
    what keeps the golden fixtures meaningful for both parsers."""
    events = parse('<vybe:add_cell type="code">\nx = 1\n</vybe:add_cell>')
    assert [e.kind for e in events] == [EventKind.TOOL]


def test_prose_around_a_tag_is_unaffected_by_streaming():
    events = run(['before ', '<vybe:add_cell type="code">\n', "x = 1\n", "</vybe:add_cell>", " after"])
    prose = "".join(e.text for e in events if e.kind is EventKind.PROSE)
    assert prose == "before  after"


class Misuser:
    """Names no cell, always. Stands in for a model that will not be told."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, messages, cancel):
        self.calls += 1
        yield '<vybe:propose_edit>\nvalue = 2\n</vybe:propose_edit>'


class MisusesOnce:
    """Gets it wrong once, then takes the correction."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, messages, cancel):
        self.calls += 1
        if self.calls == 1:
            yield '<vybe:propose_edit>\nvalue = 2\n</vybe:propose_edit>'
        else:
            yield '<vybe:propose_edit cell="1">\nvalue = 2\n</vybe:propose_edit>'


def _round(adapter, tmp_path):
    import asyncio
    import json

    from conftest import build_session

    session = build_session(adapter, str(tmp_path / "n.ipynb"))
    snapshot = json.dumps(
        {"cells": [{"id": "c1", "cell_type": "code", "source": "value = 1"}]}
    )

    async def go():
        return [
            event
            async for event in session.run(
                {
                    "prompt": "double it",
                    "snapshot": snapshot,
                    "snapshotSeq": 0,
                    "promptCellId": "p1",
                    "kernelInfo": {"name": "python3", "language": "python"},
                    "roundId": "r1",
                    "limits": {},
                }
            )
        ]

    return session, asyncio.run(go())


def test_a_misused_tag_is_corrected_once_and_then_works(tmp_path):
    adapter = MisusesOnce()
    _, frames = _round(adapter, tmp_path)

    assert adapter.calls == 2, "the model should have been asked again"
    # The user never hears about the first attempt.
    assert not [f for f in frames if f["op"] == "protocolError"]
    tools = [f for f in frames if f["op"] == "tool"]
    assert tools and tools[0]["args"]["cell"] == "c1"


def test_the_client_is_told_to_discard_the_failed_attempt(tmp_path):
    """A retry replaces the whole reply, so whatever the first one produced
    has to go -- otherwise its well-formed half would be duplicated."""
    _, frames = _round(MisusesOnce(), tmp_path)
    assert [f["op"] for f in frames].count("retrying") == 1


def test_a_model_that_insists_is_not_retried_forever(tmp_path):
    adapter = Misuser()
    _, frames = _round(adapter, tmp_path)

    # Once, and only once: the user is waiting.
    assert adapter.calls == 2
    errors = [f for f in frames if f["op"] == "protocolError"]
    assert len(errors) == 1
    assert "not a cell in this notebook" in errors[0]["message"]
