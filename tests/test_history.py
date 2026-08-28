"""The prompt record outlives the cell that rendered it (docs/05, D12)."""

import json
from pathlib import Path

import pytest

from jupyter_vybe.history import HistoryStore, find_orphan, key_for


@pytest.fixture()
def store(tmp_path):
    return HistoryStore(str(tmp_path / "work.ipynb"), tmp_path / "history")


def test_appends_and_reads_back_in_order(store):
    store.append({"kind": "prompt", "roundId": "r1", "text": "first"})
    store.append({"kind": "prose", "roundId": "r1", "text": "second"})
    kinds = [e["kind"] for e in store.read()]
    assert kinds == ["prompt", "prose"]
    assert all("ts" in e for e in store.read())


def test_header_names_the_notebook_so_a_lost_mapping_is_recoverable(store):
    store.append({"kind": "prompt", "roundId": "r1", "text": "x"})
    header = store.header()
    assert header["notebookPath"].endswith("work.ipynb")
    assert header["schema"] == 1
    # The header is not an entry: the pane renders entries only.
    assert all(e["kind"] != "header" for e in store.read())


def test_a_torn_final_line_costs_one_entry_not_the_file(store):
    store.append({"kind": "prompt", "roundId": "r1", "text": "kept"})
    with store.path.open("a", encoding="utf-8") as fh:
        fh.write('{"kind": "prose", "text": "tru')  # killed mid-write
    assert [e["text"] for e in store.read()] == ["kept"]


def test_limit_returns_the_most_recent(store):
    for i in range(5):
        store.append({"kind": "prompt", "roundId": f"r{i}", "text": str(i)})
    assert [e["text"] for e in store.read(limit=2)] == ["3", "4"]


def test_key_follows_symlinks_so_one_notebook_has_one_record(tmp_path):
    real = tmp_path / "real.ipynb"
    real.write_text("{}")
    link = tmp_path / "link.ipynb"
    link.symlink_to(real)
    assert key_for(str(link)) == key_for(str(real))


def test_rename_carries_the_record_forward_and_leaves_a_pointer(tmp_path):
    root = tmp_path / "history"
    old = HistoryStore(str(tmp_path / "old.ipynb"), root)
    old.append({"kind": "prompt", "roundId": "r1", "text": "before the rename"})

    new = old.rekey(str(tmp_path / "new.ipynb"))

    assert [e["text"] for e in new.read() if e["kind"] == "prompt"] == [
        "before the rename"
    ]
    assert any(e["kind"] == "renamed" for e in new.read())
    # The old file stays, pointing forward: a rename we did not see is the
    # case this exists for.
    assert any(e.get("movedTo", "").endswith("new.ipynb") for e in old.read())


def test_an_orphan_is_found_by_name_when_the_path_is_gone(tmp_path):
    root = tmp_path / "history"
    moved = tmp_path / "gone.ipynb"
    store = HistoryStore(str(moved), root)
    store.append({"kind": "prompt", "roundId": "r1", "text": "still here"})

    found = find_orphan("gone.ipynb", root)

    assert found == store.path
    assert json.loads(found.read_text().splitlines()[0])["kind"] == "header"


def test_a_notebook_that_still_exists_is_not_an_orphan(tmp_path):
    root = tmp_path / "history"
    present = tmp_path / "present.ipynb"
    present.write_text("{}")
    HistoryStore(str(present), root).append({"kind": "prompt", "text": "x"})
    assert find_orphan("present.ipynb", root) is None


def test_adjacent_location_writes_beside_the_notebook(tmp_path):
    """D12's second option: the record version-controlled with the work."""
    notebook = tmp_path / "project" / "analysis.ipynb"
    notebook.parent.mkdir()
    notebook.write_text("{}")
    store = HistoryStore(str(notebook), tmp_path / "unused", location="adjacent")

    store.append({"kind": "prompt", "text": "why does this cell exist"})

    assert store.path == notebook.parent / ".vybe" / "analysis.ipynb.jsonl"
    assert store.path.exists()
    # Named after the notebook, not hashed: a human reading the repo can tell
    # what it belongs to.
    assert "analysis" in store.path.name
    assert [e["text"] for e in store.read()] == ["why does this cell exist"]


def test_adjacent_rename_follows_the_notebook(tmp_path):
    old = tmp_path / "old.ipynb"
    old.write_text("{}")
    store = HistoryStore(str(old), tmp_path / "unused", location="adjacent")
    store.append({"kind": "prompt", "text": "kept"})

    new = HistoryStore(str(tmp_path / "new.ipynb"), tmp_path / "unused", "adjacent")
    moved = store.rekey(str(tmp_path / "new.ipynb"))

    assert moved.path == new.path
    assert [e["text"] for e in moved.read() if e["kind"] == "prompt"] == ["kept"]


def test_a_limited_read_does_not_parse_the_whole_file(store, monkeypatch):
    """The pane asks for the last N entries; a long-lived notebook should not
    cost a full parse to answer that."""
    for i in range(500):
        store.append({"kind": "prompt", "roundId": f"r{i}", "text": f"prompt {i}"})

    parsed = []
    real_iter = store._iter

    def spy():
        parsed.append(1)
        yield from real_iter()

    monkeypatch.setattr(store, "_iter", spy)

    entries = store.read(limit=5)

    assert [e["text"] for e in entries] == [f"prompt {i}" for i in range(495, 500)]
    assert not parsed, "a limited read went through the whole file"


def test_the_tail_read_survives_a_torn_last_line(store):
    for i in range(20):
        store.append({"kind": "prompt", "roundId": f"r{i}", "text": str(i)})
    with store.path.open("a", encoding="utf-8") as fh:
        fh.write('{"kind": "prompt", "text": "tru')

    assert [e["text"] for e in store.read(limit=3)] == ["17", "18", "19"]


def test_the_tail_never_returns_the_header_as_an_entry(store):
    store.append({"kind": "prompt", "roundId": "r0", "text": "only one"})
    entries = store.read(limit=50)
    assert [e["kind"] for e in entries] == ["prompt"]


def test_limited_and_unlimited_reads_agree(store):
    for i in range(30):
        store.append({"kind": "prompt", "roundId": f"r{i}", "text": str(i)})
    assert store.read(limit=10) == store.read()[-10:]


def test_the_default_root_is_under_the_home_directory(monkeypatch):
    """`Path("") or fallback` never reaches the fallback: Path("") is ".".

    That spelling put every prompt record in whatever directory Jupyter was
    started from, which is both surprising and, in a repository, untracked
    clutter the design explicitly avoids (D12).
    """
    from jupyter_vybe.history import _default_root

    monkeypatch.delenv("JUPYTER_VYBE_HISTORY", raising=False)
    root = _default_root()
    assert root == Path.home() / ".jupyter" / "vybe" / "history"
    assert root != Path(".")


def test_the_env_override_is_honoured_and_expanded(monkeypatch):
    from jupyter_vybe.history import _default_root

    monkeypatch.setenv("JUPYTER_VYBE_HISTORY", "~/somewhere/else")
    assert _default_root() == Path.home() / "somewhere" / "else"

    monkeypatch.setenv("JUPYTER_VYBE_HISTORY", "   ")
    assert _default_root() == Path.home() / ".jupyter" / "vybe" / "history"
