"""The prompt record: a sidecar store, not the notebook (docs/05, D12).

A settled prompt cell is a *rendering* of a round, not its storage. Deleting
that cell removes the rendering and nothing else. Two consequences the notebook
alone cannot give us:

* The agent cannot rewrite its own history. Conversation context is assembled
  from this store, never read back out of cells, so "the agent edits the cell
  holding its own instructions" stops being a guard to maintain and becomes
  structurally impossible.
* Prompts survive cell deletion, reordering, undo past the round, and close.

Append-only JSONL, one file per notebook. A crash mid-write truncates at a
record boundary and loses at most the last entry; readers skip anything that
does not parse rather than failing the whole file.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

SCHEMA = 1
DEFAULT_ROOT = Path(os.environ.get("JUPYTER_VYBE_HISTORY", "")) or (
    Path.home() / ".jupyter" / "vybe" / "history"
)

# Entry kinds the pane knows how to render (docs/05). Anything else is kept in
# the file -- it is append-only and may have been written by a newer version --
# but is not surfaced.
KINDS = {"prompt", "prose", "insertion", "proposal", "error"}


def key_for(notebook_path: str) -> str:
    """Stable file key for a notebook path.

    realpath first: a notebook reached through a symlink is the same notebook.
    A path that does not resolve (unsaved, or on a server whose cwd differs)
    still hashes, it just hashes as itself.
    """
    try:
        resolved = os.path.realpath(notebook_path)
    except OSError:
        resolved = notebook_path
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()


@dataclass
class HistoryStore:
    """One notebook's record. Cheap to construct; the file opens per append.

    `location` is D12's open sub-question, left to the user:

    * ``user`` keeps records under ~/.jupyter/vybe/history, so nothing stray
      appears in a repository -- at the cost of history not travelling with the
      notebook.
    * ``adjacent`` writes ``.vybe/<name>.ipynb.jsonl`` beside the notebook, so
      the record is version-controlled alongside the work. For a research audit
      trail -- *why* does this cell exist -- that is a reasonable thing to want.
    """

    notebook_path: str
    root: Path = DEFAULT_ROOT
    location: str = "user"

    @property
    def path(self) -> Path:
        if self.location == "adjacent":
            notebook = Path(self.notebook_path)
            # Named after the notebook, not hashed: the point of this mode is
            # that a human reading the repository can tell what it belongs to.
            return notebook.parent / ".vybe" / f"{notebook.name}.jsonl"
        return Path(self.root) / f"{key_for(self.notebook_path)}.jsonl"

    def append(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Append one entry, stamping ts. Returns what was written."""
        record = dict(entry)
        record.setdefault("ts", time.time())
        self._ensure_header()
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def read(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Entries oldest first, header excluded. Bad lines are skipped.

        With a limit, only the tail of the file is read. The pane asks for the
        last couple of hundred entries; a notebook worked on for months should
        not cost a full parse of its whole history to answer that.
        """
        if limit:
            return [
                e for e in self._tail(limit) if e.get("kind") not in ("header", None)
            ][-limit:]
        return [e for e in self._iter() if e.get("kind") != "header"]

    def _tail(self, limit: int) -> list[dict[str, Any]]:
        """The last `limit` parseable entries, read backwards from the end."""
        if not self.path.exists():
            return []
        # Entries are a few hundred bytes; 4 KB a piece is generous and keeps
        # the common case to a single seek.
        block = 4096
        with self.path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            data = b""
            while size > 0 and data.count(b"\n") <= limit:
                step = min(block, size)
                size -= step
                fh.seek(size)
                data = fh.read(step) + data
                block *= 2
        lines = data.split(b"\n")
        if size > 0:
            # The first line is probably cut in half by where we started.
            lines = lines[1:]
        out: list[dict[str, Any]] = []
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return out

    def header(self) -> dict[str, Any] | None:
        for entry in self._iter():
            if entry.get("kind") == "header":
                return entry
        return None

    def rekey(self, new_path: str) -> "HistoryStore":
        """Follow a rename: copy the record forward, leave a tombstone behind.

        A tombstone rather than a delete, because a rename that JupyterLab did
        not see -- `mv` in a terminal -- would otherwise orphan the file with
        nothing pointing at where it went (docs/05).
        """
        target = HistoryStore(new_path, self.root, self.location)
        if not self.path.exists() or target.path == self.path:
            return target
        target.path.parent.mkdir(parents=True, exist_ok=True)
        target.path.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
        target.append({"kind": "renamed", "from": self.notebook_path, "to": new_path})
        self.append({"kind": "tombstone", "movedTo": new_path})
        return target

    # -- internals ---------------------------------------------------------

    def _iter(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # A torn final line from a crash. Skipping it is the whole
                    # reason the format is one JSON object per line.
                    continue

    def _ensure_header(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size:
            return
        # The header names the notebook this file was created for, so a store
        # whose hash mapping is lost is still recoverable by hand.
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "kind": "header",
                        "schema": SCHEMA,
                        "notebookPath": self.notebook_path,
                        "ts": time.time(),
                    }
                )
                + "\n"
            )


def find_orphan(notebook_name: str, root: Path = DEFAULT_ROOT) -> Path | None:
    """A store whose header names a notebook of this basename.

    Renaming outside JupyterLab orphans a file. Rather than silently starting
    empty, the pane offers to adopt one whose header names the old path.
    """
    root = Path(root)
    if not root.exists():
        return None
    for store_path in sorted(root.glob("*.jsonl")):
        try:
            with store_path.open(encoding="utf-8") as fh:
                first = fh.readline()
            header = json.loads(first)
        except (OSError, json.JSONDecodeError):
            continue
        if header.get("kind") != "header":
            continue
        if Path(header.get("notebookPath", "")).name != notebook_name:
            continue
        if key_for(header.get("notebookPath", "")) == store_path.stem:
            # Still keyed to a path that exists as itself; not an orphan.
            if Path(header["notebookPath"]).exists():
                continue
        return store_path
    return None
