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

if "double the value" in prompt:
    print('<vybe:propose_edit cell="1" note="double it">')
    print("value = 2")
    print("</vybe:propose_edit>")
else:
    print("Here is the helper.")
    print('<vybe:add_cell type="code">')
    print("def double(x):")
    print("    return x * 2")
    print("</vybe:add_cell>")
