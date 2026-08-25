"""Follow-up turns must still tell the agent how to emit tool calls.

Regression: turn 2+ of a persistent session assembled a prompt with no tool
grammar in it at all, so the model fell back to markdown code fences and its
output landed in the notebook as prose instead of as cells.
"""

from jupyter_vybe.assembler import assemble_followup, assemble_initial
from jupyter_vybe.protocol import has_tool_calls, parse, salvage_fenced_blocks

KERNEL = {"name": "python3", "language": "python", "version": "3.13"}


def test_followup_carries_tool_grammar():
    out = assemble_followup(notebook_diff="", kernel=KERNEL, user_prompt="add a function")
    assert "<vybe:add_cell" in out
    assert "<vybe:propose_edit" in out
    assert "never markdown code fences" in out


def test_followup_states_kernel_language():
    out = assemble_followup(notebook_diff="", kernel=KERNEL, user_prompt="x")
    assert "python" in out


def test_empty_diff_is_labelled_not_blank():
    out = assemble_followup(notebook_diff="   ", kernel=KERNEL, user_prompt="x")
    assert "(no changes)" in out


def test_initial_carries_tool_grammar():
    out = assemble_initial(
        notebook_json="{}", kernel=KERNEL, prompt_cell_id="c1", user_prompt="x"
    )
    assert "<vybe:add_cell" in out


def test_salvage_recovers_fenced_code():
    reply = "Sure!\n\n```python\ndef f(x):\n    return x\n```\n\nHope that helps."
    assert not has_tool_calls(parse(reply))
    events = salvage_fenced_blocks(reply)
    assert len(events) == 1
    assert events[0].name == "add_cell"
    assert events[0].args["type"] == "code"
    assert events[0].args["body"] == "def f(x):\n    return x"


def test_salvage_marks_markdown_fences():
    events = salvage_fenced_blocks("```markdown\n## Heading\n```")
    assert events[0].args["type"] == "markdown"


def test_salvage_ignores_replies_with_no_fences():
    assert salvage_fenced_blocks("just some prose, no code at all") == []


def test_salvage_skips_empty_fences():
    assert salvage_fenced_blocks("```python\n\n```") == []


def test_tooling_examples_are_not_runnable_code():
    """Realistic examples get copied verbatim by small models (docs/08)."""
    from jupyter_vybe.assembler import _load

    tooling = _load("tooling.md")
    assert "def rollingMean" not in tooling
    assert "np.cumsum" not in tooling
    assert "placeholders" in tooling
