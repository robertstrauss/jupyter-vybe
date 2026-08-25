"""A follow-up turn must show the notebook listing.

Regression: follow-up turns carried only a kernel banner, a tool recap, and an
empty diff -- no cell numbers at all. Asked to "edit the cell above", the agent
invented an index, and resolve_cell_ref mapped that invented number onto a real
cell. A silent edit to the wrong cell is the worst failure this tool can
produce, so the listing goes on every turn.
"""

from jupyter_vybe.assembler import assemble_followup, render_notebook, resolve_cell_ref

KERNEL = {"name": "python3", "language": "python", "version": "3.13"}
CELLS = [
    {"id": "setup", "cell_type": "code", "source": "import numpy as np", "execution_count": 1},
    {"id": "gen1", "cell_type": "code", "source": "def normalize_x(x):\n    return x"},
    {"id": "p2", "cell_type": "code", "source": "> edit the above cell"},
]


def test_followup_includes_the_cell_listing():
    out = assemble_followup(
        notebook_diff="",
        kernel=KERNEL,
        user_prompt="edit the above cell",
        notebook_listing=render_notebook(CELLS, "p2"),
    )
    assert "[1]" in out and "[2]" in out and "[3]" in out


def test_listing_marks_the_cell_above_the_prompt():
    listing = render_notebook(CELLS, "p2")
    above = [l for l in listing.splitlines() if "the cell above" in l]
    assert len(above) == 1
    assert above[0].startswith("[2]")


def test_listing_marks_the_prompt_cell():
    listing = render_notebook(CELLS, "p2")
    marked = [l for l in listing.splitlines() if "prompt cell you are answering" in l]
    assert len(marked) == 1
    assert marked[0].startswith("[3]")


def test_each_cell_is_explicitly_terminated():
    """Without a terminator a header can read as belonging to the block above."""
    listing = render_notebook(CELLS, "p2")
    for n in (1, 2, 3):
        assert f"[end of cell {n}]" in listing


def test_headers_end_with_a_colon_before_any_annotation():
    listing = render_notebook(CELLS, "p2")
    for line in listing.splitlines():
        if line.startswith("[") and not line.startswith("[end of"):
            head = line.split("<--")[0].rstrip()
            assert head.endswith(":"), line


def test_cell_content_is_indented_under_its_own_header():
    listing = render_notebook(CELLS, "p2").splitlines()
    i = next(i for i, l in enumerate(listing) if l.startswith("[2]"))
    assert listing[i + 1] == "    def normalize_x(x):"


def test_index_resolves_to_the_cell_with_that_number():
    assert resolve_cell_ref("2", CELLS) == "gen1"
    assert resolve_cell_ref("1", CELLS) == "setup"
    assert resolve_cell_ref("3", CELLS) == "p2"


def test_out_of_range_index_is_refused_rather_than_clamped():
    assert resolve_cell_ref("0", CELLS) == ""
    assert resolve_cell_ref("4", CELLS) == ""
    assert resolve_cell_ref("-1", CELLS) == ""
