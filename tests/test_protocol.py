"""Tag-body normalisation (docs/14 N1)."""

from jupyter_vybe.protocol import EventKind, parse


def test_body_dedented_when_model_echoes_listing_indentation():
    # assemble_initial indents every cell body by four spaces; models copy that
    # indentation back into the tag. Left in, every line of the "edit" differs
    # from the original and the whole cell renders as one add and one delete
    # (docs/14 N1).
    reply = (
        '<vybe:propose_edit cell="4">\n'
        "    class A:\n"
        "        def f(self):\n"
        "\n"
        "            return 1\n"
        "</vybe:propose_edit>"
    )
    args = [e.args for e in parse(reply) if e.kind is EventKind.TOOL][0]
    assert args["body"] == "class A:\n    def f(self):\n\n        return 1"


def test_dedent_leaves_an_unindented_body_alone():
    reply = (
        '<vybe:add_cell type="code">\n'
        "def f():\n"
        "    return 1\n"
        "</vybe:add_cell>"
    )
    args = [e.args for e in parse(reply) if e.kind is EventKind.TOOL][0]
    assert args["body"] == "def f():\n    return 1"


def test_crlf_body_is_normalised():
    reply = '<vybe:add_cell type="code">\r\nx = 1\r\ny = 2\r\n</vybe:add_cell>'
    args = [e.args for e in parse(reply) if e.kind is EventKind.TOOL][0]
    assert args["body"] == "x = 1\ny = 2"


def test_a_fence_around_the_whole_body_is_unwrapped():
    reply = (
        '<vybe:add_cell type="code">\n'
        "```python\n"
        "x = 1\n"
        "```\n"
        "</vybe:add_cell>"
    )
    args = [e.args for e in parse(reply) if e.kind is EventKind.TOOL][0]
    assert args["body"] == "x = 1"


def test_a_fence_with_prose_around_it_is_unwrapped():
    """Models narrate. The tag delimits the body, so the fence is noise."""
    reply = (
        '<vybe:add_cell type="code">\n'
        "Here you go:\n"
        "```python\n"
        "def f():\n"
        "    return 1\n"
        "```\n"
        "That should do it.\n"
        "</vybe:add_cell>"
    )
    args = [e.args for e in parse(reply) if e.kind is EventKind.TOOL][0]
    assert args["body"] == "def f():\n    return 1"


def test_a_body_with_two_fences_is_left_alone():
    """A cell that genuinely contains examples: picking one would lose the other."""
    body = "```python\na = 1\n```\nand\n```python\nb = 2\n```"
    reply = f'<vybe:add_cell type="code">\n{body}\n</vybe:add_cell>'
    args = [e.args for e in parse(reply) if e.kind is EventKind.TOOL][0]
    assert args["body"] == body


def test_a_markdown_cell_keeps_its_fences():
    reply = (
        '<vybe:add_cell type="markdown">\n'
        "## Example\n"
        "```python\n"
        "x = 1\n"
        "```\n"
        "</vybe:add_cell>"
    )
    args = [e.args for e in parse(reply) if e.kind is EventKind.TOOL][0]
    assert "```python" in args["body"]
