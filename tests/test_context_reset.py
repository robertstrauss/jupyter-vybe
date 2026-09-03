"""Erasing a notebook's conversation, and keeping notebooks out of each other's.

The pane's trash-can sends `reset`; these are the two properties the button is
promising. Neither is obvious from the code: sessions are keyed by path in one
place and the reset is three fields in another.
"""

from jupyter_vybe.session import AgentSession, SessionManager


def _session(tmp_path, name="n.ipynb"):
    return AgentSession(str(tmp_path / name), "command", True, {"command": "true"})


def test_reset_sends_the_whole_notebook_again(tmp_path):
    session = _session(tmp_path)
    session.messages.append(("user", "an earlier prompt"))
    session.turns = 3
    session.snapshot_seq = 7

    session.reset()

    # Turn zero again: no history to continue, and no snapshot to diff
    # against, so the next prompt describes the notebook in full.
    assert session.messages == []
    assert session.turns == 0
    assert session.snapshot_seq == -1


def test_each_notebook_gets_its_own_conversation(tmp_path):
    registry = SessionManager()
    opts = {"command": "true"}

    one = registry.acquire(str(tmp_path / "a.ipynb"), "command", True, opts)
    two = registry.acquire(str(tmp_path / "b.ipynb"), "command", True, opts)

    assert one is not two

    one.messages.append(("user", "about notebook a"))
    one.reset()

    # Erasing one notebook's context leaves the other's alone -- the point of
    # keying by path, and the failure the user would notice first if it were
    # ever keyed by anything else.
    assert registry.acquire(str(tmp_path / "b.ipynb"), "command", True, opts) is two
    assert two.messages == []
    assert registry.acquire(str(tmp_path / "a.ipynb"), "command", True, opts) is one
