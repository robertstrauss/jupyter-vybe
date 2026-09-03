#!/usr/bin/env python3
"""A deterministic stand-in for a model, for the end-to-end tests.

Run through the real CommandAdapter as a real subprocess, so the tests
exercise the actual transport, parser, round, and mutations -- only the model
is replaced. It branches on the prompt it is handed so one agent covers both
the insertion path and the review path.

It is a file rather than a `python3 -c` one-liner because the command setting
is split with shlex: a one-liner comes apart at the spaces, which cost an
afternoon of "the extension does nothing" before anyone looked at argv.
"""

import sys

prompt = sys.argv[-1] if len(sys.argv) > 1 else ""

if "misuse the tag once" in prompt:
    # First attempt: a propose_edit with no cell attribute, and a stray fence
    # outside the tags -- exactly the reply that put a protocol complaint in a
    # user's notebook. Second attempt (the correction carries the words below)
    # gets it right.
    import os
    import tempfile

    marker = os.path.join(tempfile.gettempdir(), "vybe-retry-marker")
    if os.path.exists(marker):
        os.remove(marker)
        print('<vybe:propose_edit cell="1" note="corrected">')
        print("value = 2")
        print("</vybe:propose_edit>")
    else:
        open(marker, "w").close()
        print("```python")
        print("<vybe:propose_edit>")
        print("value = 2")
        print("</vybe:propose_edit>")
elif "add and edit together" in prompt:
    # One round that both inserts a cell and proposes an edit, so undo has
    # more than one kind of thing to take back.
    print('<vybe:propose_edit cell="1" note="double it">')
    print("value = 2")
    print("</vybe:propose_edit>")
    print('<vybe:add_cell type="code">')
    print("helper = 'added by the round'")
    print("</vybe:add_cell>")
elif "stream a cell slowly" in prompt:
    # Writes a fenced body one line at a time, slowly enough that a test can
    # see the cell filling up before the closing tag arrives.
    import time

    print('<vybe:add_cell type="code">')
    print("```python")
    sys.stdout.flush()
    for i in range(6):
        print(f"line_{i} = {i}")
        sys.stdout.flush()
        time.sleep(0.4)
    print("```")
    print("</vybe:add_cell>")
elif "think slowly please" in prompt:
    # Streams a long tag body slowly, so the progress path is exercised: a
    # reply made only of tags is exactly the case that reported 0 tokens/sec.
    import time

    print('<vybe:add_cell type="code">')
    sys.stdout.flush()
    for i in range(12):
        print(f"value_{i} = {i} * 100  # a line of a slowly written cell")
        sys.stdout.flush()
        time.sleep(0.25)
    print("</vybe:add_cell>")
elif "aim at a cell that is not there" in prompt:
    # Names a cell number the notebook does not have: the commonest
    # recoverable protocol failure, and the one that used to produce silence.
    print('<vybe:propose_edit cell="99" note="nowhere">')
    print("x = 1")
    print("</vybe:propose_edit>")
elif "answer without tags" in prompt:
    # No tool tags at all: the server salvages cells from the fence, and the
    # user should be told that is what happened (docs/13 R2).
    print("Here you go:")
    print("```python")
    print("def salvaged():")
    print("    return 1")
    print("```")
elif "You are completing a cell" in prompt:
    # A prediction, fenced -- because that is what models do when told not to,
    # and the fence must never reach the editor. The second call sees its own
    # first answer in the script it is given, and continues from there, which
    # is how pressing the trigger again extends a suggestion.
    print("```python")
    if "read_csv('data.csv')" in prompt:
        # The second call sees its own first answer, on its own line, and
        # continues from there -- which is what pressing the trigger again
        # asks for and what the newline join exists to make true.
        print("df.head()")
    else:
        print("pd.read_csv('data.csv')")
    print("```")
elif "make the agent fail" in prompt:
    sys.stderr.write("the agent fell over\n")
    raise SystemExit(3)
elif "double the value" in prompt:
    print('<vybe:propose_edit cell="1" note="double it">')
    print("value = 2")
    print("</vybe:propose_edit>")
else:
    print("Here is the helper.")
    print('<vybe:add_cell type="code">')
    print("def double(x):")
    print("    return x * 2")
    print("</vybe:add_cell>")
