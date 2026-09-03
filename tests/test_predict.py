"""Predicting the rest of a cell.

The fill-in-the-middle path predicted a cell from itself, with a different
token vocabulary per model family. This one sends the notebook as a script and
asks in words, which every adapter can answer. What is left to get right is
what comes back: models fence code they were told not to fence, restate the
line they were given, and write four lines when asked for two.
"""

import pytest

from jupyter_vybe.completion import (
    build_predict_prompt,
    clean_prediction,
    limit_lines,
    strip_fences,
)


def test_the_prompt_names_the_cell_and_the_limit():
    prompt = build_predict_prompt("# In[1]:\ndf = ", cell=1, max_lines=2)

    assert "cell 1" in prompt
    assert "at most 2 line(s)" in prompt
    # Being allowed to stop part-way is the instruction that makes a two-line
    # limit produce useful answers instead of cramped ones.
    assert "expected and fine" in prompt


def test_kernel_hints_are_included_only_when_there_are_any():
    assert "running kernel" not in build_predict_prompt("x", 1, 2)
    assert "running kernel" in build_predict_prompt("x", 1, 2, hints="df.mean")
    # Whitespace is not a hint.
    assert "running kernel" not in build_predict_prompt("x", 1, 2, hints="  \n ")


@pytest.mark.parametrize(
    "raw",
    [
        "```python\npd.read_csv('x')\n```",
        "```\npd.read_csv('x')\n```",
        # The opening fence alone, which is what actually happens mid-stream.
        "```python\npd.read_csv('x')",
    ],
)
def test_fences_never_reach_the_editor(raw):
    assert strip_fences(raw).strip() == "pd.read_csv('x')"


def test_the_model_restating_the_line_is_not_shown_twice():
    # Asked to continue `df = `, models routinely reply with the whole line.
    # Shown as ghost text that reads as the editor losing the cursor.
    assert clean_prediction("df = pd.read_csv('x')", "df = ", 2) == "pd.read_csv('x')"


def test_the_line_limit_is_enforced_not_merely_requested():
    assert limit_lines("one\ntwo\nthree\nfour", 2) == "one\ntwo"
    # Under the limit is left exactly as it is, trailing structure and all.
    assert limit_lines("one\ntwo", 4) == "one\ntwo"


def test_an_answer_of_only_whitespace_is_no_answer():
    assert clean_prediction("   \n  \n", "df = ", 2) == ""
    assert clean_prediction("```\n```", "df = ", 2) == ""


def test_end_tokens_are_cut_rather_than_shown():
    assert clean_prediction("pd.read_csv('x')<|endoftext|>", "df = ", 2) == (
        "pd.read_csv('x')"
    )


def test_the_users_own_spacing_survives_the_overlap_trim():
    """Their trailing space is theirs; the suggestion must not add a second."""
    assert clean_prediction("df = pd.read_csv('x')", "df = ", 2) == "pd.read_csv('x')"
    # And with no space typed, the suggestion supplies its own.
    assert clean_prediction("df =pd.read_csv('x')", "df =", 2) == "pd.read_csv('x')"


def test_a_continuation_that_repeats_nothing_is_left_alone():
    assert clean_prediction("pd.read_csv('x')", "df = ", 2) == "pd.read_csv('x')"
