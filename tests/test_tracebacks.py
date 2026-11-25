"""Errors reach the agent with the line that raised, not just the message.

The fixture is real IPython output (ANSI colour and all), captured from a
kernel: a JSONDecodeError four frames deep into the stdlib, a missing module,
a ZeroDivisionError two calls down, a SyntaxError, and a KeyError raised
directly.
"""

import json
from pathlib import Path

from jupyter_vybe.assembler import EVALUE_CAP, _output_text, render_notebook
from jupyter_vybe.diffsummary import summarise

FIXTURE = Path(__file__).parent / "fixtures" / "ipython_tracebacks.jsonl"
TRACEBACKS = [json.loads(line) for line in FIXTURE.read_text().splitlines() if line]


def error(tb, ename="E", evalue="v"):
    return {"output_type": "error", "ename": ename, "evalue": evalue, "traceback": tb}


def test_first_and_last_frames_with_the_middle_counted():
    text = _output_text(error(TRACEBACKS[0], "JSONDecodeError", "Expecting property name"))
    assert text.split("\n") == [
        "JSONDecodeError: Expecting property name",
        "  at Cell In[1], line 4: load('{bad')",
        "  ... 3 more frames",
        "  at File json/decoder.py:361, in JSONDecoder.raw_decode(self, s, idx): "
        "obj, end = self.scan_once(s, idx)",
    ]


def test_a_cell_frame_alone_shows_its_line_once():
    text = _output_text(error(TRACEBACKS[1]))
    assert text.split("\n")[1:] == ["  at Cell In[2], line 1: import pandas as pd"]


def test_the_frame_that_raised_inside_the_cell():
    lines = _output_text(error(TRACEBACKS[2])).split("\n")
    assert lines[1] == "  at Cell In[3], line 7: g()"
    assert lines[-1] == "  at Cell In[3], line 2, in f(x): return 1/x"


def test_a_syntax_error_shows_the_offending_line():
    assert "  at Cell In[4], line 1: x = (1," in _output_text(error(TRACEBACKS[3]))


def test_evalue_is_capped_and_kept_on_one_line():
    first = _output_text(error([], "KeyError", "a\nb" + "x" * 1000)).split("\n")
    assert len(first) == 1
    assert first[0].startswith("KeyError: a b")
    assert len(first[0]) <= len("KeyError: ") + EVALUE_CAP


def test_no_traceback_is_just_the_message():
    assert _output_text(error(None, "ValueError", "bad")) == "ValueError: bad"


def test_every_traceback_line_is_an_output_line_in_the_listing_and_the_diff():
    cell = {
        "id": "a",
        "cell_type": "code",
        "source": "load('{bad')",
        "execution_count": 1,
        "outputs": [error(TRACEBACKS[0], "JSONDecodeError", "x")],
    }
    listing = render_notebook([cell])
    assert "    # output:   at Cell In[1], line 4: load('{bad')" in listing
    before = [dict(cell, execution_count=None, outputs=[])]
    assert "    # output:   ... 3 more frames" in summarise(before, [cell])
