"""Fill-in-the-middle completion (D5, v1.1).

The unit under test is the prompt shape and the cleaning, because that is
where FIM goes wrong: a chat-wrapped FIM prompt returns prose about the code,
and an uncleaned continuation shows the user their own code back.
"""

from jupyter_vybe.completion import (
    build_fim_prompt,
    clean,
    family_for,
    looks_useless,
)


def test_qwen_gets_its_own_infilling_tokens():
    prompt, raw = build_fim_prompt("def add(a, b):\n    return ", "\n\nprint(add(1, 2))", "qwen2.5-coder:7b")
    assert raw is True
    assert prompt.startswith("<|fim_prefix|>def add")
    assert "<|fim_suffix|>\n\nprint(add(1, 2))<|fim_middle|>" in prompt
    # The middle marker is last: everything after it is what the model writes.
    assert prompt.endswith("<|fim_middle|>")


def test_a_model_without_a_known_format_is_asked_in_words():
    prompt, raw = build_fim_prompt("x = ", "\n", "gemma-4-26b-a4b-it")
    assert raw is False
    assert "continuation only" in prompt
    assert "BEFORE THE CURSOR" in prompt and "AFTER THE CURSOR" in prompt


def test_family_detection_covers_the_names_models_actually_have():
    assert family_for("qwen2.5-coder:7b") == "qwen"
    assert family_for("deepseek-coder:6.7b") == "qwen"  # same infilling tokens
    assert family_for("codellama:13b") == "codellama"
    assert family_for("starcoder2:3b") == "starcoder"
    assert family_for("gemma-4-26b-a4b-it") is None


def test_context_is_bounded_on_both_sides():
    prompt, _ = build_fim_prompt("x" * 9000, "y" * 9000, "qwen2.5-coder:7b")
    # Prefill cost grows faster than linearly, so an unbounded window would
    # make completion slower than typing (docs/14 N4).
    assert len(prompt) < 4000


def test_a_fenced_answer_is_unwrapped():
    assert clean("```python\na + b\n```", "", True) == "a + b"


def test_single_line_mode_stops_at_the_first_newline():
    assert clean("a + b\nprint(a)", "", False) == "a + b"


def test_multiline_stops_before_the_model_starts_a_second_thing():
    text = "    return a + b\n\ndef unrelated():\n    pass"
    assert clean(text, "", True) == "    return a + b"


def test_code_that_already_follows_the_cursor_is_not_repeated():
    # The model happily writes past the cursor into text that is already
    # there; showing it would duplicate the user's own line.
    assert clean("a + b\nprint(add(1, 2))", "\nprint(add(1, 2))", True) == "a + b"


def test_end_markers_are_trimmed():
    assert clean("a + b<|endoftext|>more", "", True) == "a + b"


def test_nothing_useful_is_reported_as_nothing():
    assert looks_useless("", "x = ")
    assert looks_useless("   \n  ", "x = ")
    assert looks_useless(")", "x = (")  # a single character is a flicker
    assert not looks_useless("np.mean(x)", "y = ")


def test_a_completion_that_only_echoes_the_current_line_is_useless():
    assert looks_useless("total = 0", "def f():\n    total = 0")


def test_multiline_is_allowed_only_where_the_line_is_unfinished():
    from jupyter_vybe.completion import should_be_multiline

    # Mid-statement: one line is the answer.
    assert not should_be_multiline("df['total'] = ", True)
    # After a colon, or inside an open bracket, or on a fresh line: run on.
    assert should_be_multiline("def f():\n    if x:", True)
    assert should_be_multiline("values = [", True)
    assert should_be_multiline("def f():\n", True)
    assert should_be_multiline("", True)
    # And never when the user has asked for single-line suggestions.
    assert not should_be_multiline("def f():", False)
