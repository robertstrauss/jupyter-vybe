"""What a follow-up turn says changed (docs/09)."""

from jupyter_vybe.diffsummary import render_index, summarise


def cell(cid, source, kind="code", **kw):
    return {"id": cid, "cell_type": kind, "source": source, **kw}


def test_an_added_cell_carries_its_whole_source_and_its_anchor():
    before = [cell("a", "import numpy as np")]
    after = [before[0], cell("b", "def f():\n    return 1")]

    out = summarise(before, after)

    assert "ADDED   cell b after cell a (code)" in out
    assert "+ def f():" in out and "+     return 1" in out


def test_an_edit_is_a_hunk_not_the_whole_cell():
    before = [cell("a", "\n".join(f"line {i}" for i in range(20)))]
    after = [cell("a", "\n".join(f"line {i}" for i in range(20)).replace("line 10", "CHANGED"))]

    out = summarise(before, after)

    assert "-line 10" in out and "+CHANGED" in out
    assert " line 9" in out  # context around the change
    assert "line 19" not in out  # ... but not the whole cell


def test_running_a_cell_is_reported_separately_from_changing_it():
    before = [cell("a", "x = 1", execution_count=1, outputs=[])]
    after = [cell("a", "x = 1", execution_count=2, outputs=[{"text": "1"}])]

    out = summarise(before, after)

    assert "RAN     cell a -> execution_count 2, output changed" in out
    assert "EDITED" not in out, "the source did not change"


def test_the_error_a_run_produced_reaches_the_agent():
    """It used to say only "output changed", so after the first turn the agent
    could not see the traceback it was being asked to fix."""
    before = [cell("a", "df = load()", execution_count=None, outputs=[])]
    error = {"output_type": "error", "ename": "FileNotFoundError", "evalue": "data.csv"}
    after = [cell("a", "df = load()", execution_count=3, outputs=[error])]

    out = summarise(before, after)

    assert "RAN     cell a -> execution_count 3, output now:" in out
    assert "# output: FileNotFoundError: data.csv" in out


def test_printed_output_is_shown_as_the_listing_shows_it():
    stream = {"output_type": "stream", "name": "stdout", "text": "mean 0.0\nstd 1.0\n"}
    before = [cell("a", "show()", execution_count=1, outputs=[])]
    after = [cell("a", "show()", execution_count=2, outputs=[stream])]

    assert "    # output: mean 0.0 std 1.0" in summarise(before, after)


def test_an_unchanged_output_is_not_repeated():
    result = {"output_type": "execute_result", "data": {"text/plain": "42"}}
    before = [cell("a", "x", execution_count=1, outputs=[result])]
    after = [cell("a", "x", execution_count=2, outputs=[result])]

    out = summarise(before, after)

    assert "RAN     cell a -> execution_count 2" in out
    assert "# output" not in out, "the agent has already seen this output"


def test_cleared_output_is_said_rather_than_left_stale():
    result = {"output_type": "execute_result", "data": {"text/plain": "42"}}
    before = [cell("a", "x", execution_count=1, outputs=[result])]
    after = [cell("a", "x", execution_count=1, outputs=[])]

    assert "OUTPUT  cell a cleared" in summarise(before, after)


def test_an_added_cell_brings_its_output():
    result = {"output_type": "execute_result", "data": {"text/plain": "3"}}
    before = [cell("a", "import numpy as np")]
    after = [before[0], cell("b", "1 + 2", execution_count=4, outputs=[result])]

    assert "    # output: 3" in summarise(before, after)


def test_a_moved_cell_is_a_move_not_an_add_and_a_remove():
    a, b = cell("a", "first"), cell("b", "second")
    out = summarise([a, b], [b, a])
    assert "MOVED   cell b from index 2 to index 1" in out
    assert "ADDED" not in out and "REMOVED" not in out


def test_removals_and_the_unchanged_tail():
    before = [cell("a", "keep"), cell("b", "drop"), cell("c", "keep too")]
    after = [before[0], before[2]]

    out = summarise(before, after)

    assert "REMOVED cell b (code)" in out
    assert "UNCHANGED: 2 other cells" in out


def test_the_prompt_cell_is_the_request_not_a_change():
    before = [cell("a", "x = 1")]
    after = [before[0], cell("p", "#% do something")]

    out = summarise(before, after, prompt_cell_id="p")

    assert "ADDED" not in out
    assert "UNCHANGED: 1 other cell" in out


def test_the_index_is_addressable_without_being_a_transcript():
    cells = [
        cell("a", "import numpy as np\nimport pandas as pd"),
        cell("b", "def normalize(x):\n    return x / x.max()"),
        cell("p", "#% edit the above"),
    ]

    out = render_index(cells, "p")

    assert "[2] code id=b: def normalize(x):" in out
    assert "return x / x.max()" not in out
    assert 'the cell directly above the prompt ("the cell above")' in out
    assert "this is the prompt cell you are answering" in out


def test_an_empty_cell_still_gets_a_line():
    out = render_index([cell("a", "\n\n  ")], "")
    assert "[1] code id=a: (empty)" in out
