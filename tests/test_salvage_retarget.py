"""Salvage must be able to produce an edit, not only a new cell.

A fenced code block carries no target, so plain salvage turns "edit the cell
above" into a near-duplicate copy -- the most annoying way for the fallback to
be wrong, and the behaviour that was actually reported. retarget_salvage
converts a block that clearly rewrites an existing cell into a propose_edit.
"""

from jupyter_vybe.protocol import retarget_salvage, salvage_fenced_blocks

CELLS = [
    {"id": "setup", "cell_type": "code", "source": "import numpy as np\nx = np.arange(20)"},
    {
        "id": "gen1",
        "cell_type": "code",
        "source": "def normalize_x(x):\n    return (x - np.mean(x)) / np.std(x)",
    },
    {"id": "md1", "cell_type": "markdown", "source": "> a previous prompt"},
]


def _kinds(reply, cells=CELLS, **kw):
    return [e.name for e in retarget_salvage(salvage_fenced_blocks(reply), cells, **kw)]


def _fence(code):
    return f"```python\n{code}\n```"


def test_small_rewrite_of_same_cell_becomes_an_edit():
    reply = _fence("def normalize_x(x):\n    s = np.std(x)\n    return (x - np.mean(x)) / s")
    assert _kinds(reply) == ["propose_edit"]


def test_large_rewrite_of_same_function_still_becomes_an_edit():
    """Raw similarity drops to ~0.29 here; the shared name is the signal."""
    reply = _fence(
        "def normalize_x(x, axis=None, ddof=0):\n"
        "    mu = np.mean(x, axis=axis)\n"
        "    sd = np.std(x, axis=axis, ddof=ddof)\n"
        "    return (x - mu) / np.where(sd == 0, 1, sd)"
    )
    assert _kinds(reply) == ["propose_edit"]


def test_edit_targets_the_right_cell():
    reply = _fence("def normalize_x(x):\n    return x")
    events = retarget_salvage(salvage_fenced_blocks(reply), CELLS)
    assert events[0].args["cell"] == "gen1"
    assert "recovered from a plain code block" in events[0].args["note"]


def test_unrelated_code_stays_a_new_cell():
    assert _kinds(_fence("import pandas as pd\ndf = pd.DataFrame()")) == ["add_cell"]


def test_brand_new_function_stays_a_new_cell():
    assert _kinds(_fence("def running_max(a):\n    return np.maximum.accumulate(a)")) == [
        "add_cell"
    ]


def test_differently_named_function_stays_a_new_cell():
    """Same body, new name: "add a z-score function" must not overwrite."""
    assert _kinds(_fence("def zscore(x):\n    return (x - np.mean(x)) / np.std(x)")) == [
        "add_cell"
    ]


def test_markdown_cells_are_never_edit_targets():
    assert _kinds("```markdown\n## Notes\n```") == ["add_cell"]


def test_prompt_cell_is_excluded_from_targeting():
    reply = _fence("def normalize_x(x):\n    return x")
    assert _kinds(reply, exclude_ids={"gen1"}) == ["add_cell"]


def test_identical_source_is_not_proposed_as_an_edit():
    reply = _fence("def normalize_x(x):\n    return (x - np.mean(x)) / np.std(x)")
    assert _kinds(reply) == ["add_cell"]


def test_two_blocks_do_not_both_claim_the_same_cell():
    reply = (
        _fence("def normalize_x(x):\n    return x + 1")
        + "\n"
        + _fence("def normalize_x(x):\n    return x + 2")
    )
    assert _kinds(reply).count("propose_edit") == 1
