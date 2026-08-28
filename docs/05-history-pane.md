# 05 — Prompt History Pane

## Placement

A `Widget` added to the right area: `app.shell.add(historyPanel, 'right', { rank: 900 })`, which
puts its toolbar icon below the property inspector, settings, and debugger icons — matching
README's "below the settings and debugger icon". Registered with `ILayoutRestorer` so open/closed
state survives a reload. Icon: a custom `LabIcon` (a chat-bubble-over-cell glyph) so it reads as
distinct from the debugger's bug.

The pane is scoped to the **current notebook** (follows `INotebookTracker.currentChanged`) and
shows an empty state naming the trigger prefix when there is no history yet.

## Entry model

```ts
type HistoryEntry =
  | {
      kind: 'prompt';
      roundId;
      ts;
      text: string;
      agentId: string;
      tokensIn: number;
    }
  | { kind: 'prose'; roundId; ts; text: string }
  | {
      kind: 'insertion';
      roundId;
      ts;
      cellType: 'code' | 'markdown';
      source: string;
      anchorLabel: string;
    }
  | {
      kind: 'proposal';
      roundId;
      ts;
      cellId: string;
      cellLabel: string;
      before: string;
      after: string;
      outcome: 'accepted' | 'rejected' | 'stale';
    }
  | { kind: 'error'; roundId; ts; message: string };
```

Ordering is oldest at top, newest at bottom (README), auto-scrolled to the bottom on new entries
unless the user has scrolled up. Rounds are visually grouped: a sticky header per round showing
the prompt's first line, elapsed time, and a collapse chevron.

Rejected proposals are retained — that is the point of the pane. Outcome is shown as a small
badge (`accepted` green / `rejected` red / `stale` grey).

## Rendering

**Built differently from this spec, deliberately.** Insertions render as a `<pre>` and proposals
through the same differ the review UI uses, so a diff does look identical in both places. What
they are _not_ is CodeMirror instances.

The spec called for real CM6 editors, and then for virtualisation to survive them — but
virtualisation is only needed because editors are expensive. A `<pre>` costs nothing, so a
two-hundred-entry pane is fast with no viewport machinery, no measured placeholders, and no
scroll-position bugs. What is lost is syntax colour in the pane; what is gained is the whole
subsystem that existed to make the colour affordable.

If colour turns out to matter, the cheap version is a static highlighter over the text, not a
mounted editor per entry.

Reading is bounded rather than virtualised: `history.maxEntries` becomes a `limit` on the read,
and the store seeks backwards from the end of the file to answer it, so a notebook worked on for
months does not cost a full parse to show its last two hundred entries.

Each entry carries a label naming its origin: `insertion → below cell 12` or
`proposal → cell 7 (id a3f9…)`, so an entry is traceable back to a position even after the
notebook has moved on.

## Clipboard behaviour

Clicking any insertion or proposal entry copies it to **the JupyterLab cell clipboard**, so
pressing `v` in command mode pastes it as a cell below the selection (README). Implementation:
`Clipboard.getInstance().setData(JUPYTER_CELL_MIME, [cellJSON])` where `JUPYTER_CELL_MIME` is
`'application/vnd.jupyter.cells'` — the same MIME core notebook copy/paste uses, so core's
`notebook:paste-cell-below` picks it up with no extra wiring.

For a proposal, the copied cell is the **net "after" source** (README), typed to match the target
cell. A modifier-click (`Alt+click`) instead copies the raw text to the system clipboard, for
pasting into an issue or a message.

Visual confirmation: the entry flashes and the pane's status line reads `copied — press v in a
notebook to paste`.

## Persistence — the store is canonical, the notebook is not

**The prompt record lives here, not in the notebook.** A settled prompt cell is a markdown
_rendering_ of a prompt, not its storage. Deleting that cell removes the rendering and nothing
else: the prompt, the agent's prose, the insertions, and every proposal with its verdict all
remain in the store, still visible in the pane, still available to paste back.

Three things follow from making the store authoritative:

1. **The agent cannot rewrite its own history.** Its conversation context is assembled from the
   store ([08](08-prompt-templates.md)), never read back out of notebook cells. Even if a
   proposal did land on a prompt cell, the record the agent sees would be unchanged. The
   pipeline guard in [03](03-diff-ui.md) is now belt-and-braces rather than load-bearing.
2. **Prompts survive cell deletion**, cell reordering, `Cmd+Z` past the round, and closing the
   notebook.
3. **The notebook stays clean.** The store is a sidecar, not metadata, so [12](12-decisions.md)
   D4's zero-persisted-metadata invariant is untouched — a collaborator without the extension
   still opens an ordinary notebook.

### Storage

JSONL, append-only, one file per notebook, written through the server extension:

```
~/.jupyter/vybe/history/<sha256(realpath(notebook))>.jsonl
```

Append-only means a crash mid-round truncates at a record boundary and loses at most the last
entry. Each line is one `HistoryEntry` plus `roundId` and an ISO timestamp. The sidecar path is
recorded in a header record along with the notebook path it was created for, so the files are
inspectable by hand and recoverable if the hash mapping is ever lost.

**Renames** are tracked: `DocumentRegistry.IContext.pathChanged` fires on rename or move, and the
extension re-keys the file (copy to the new hash, leave a tombstone pointing forward). A notebook
renamed outside JupyterLab orphans its history; the pane says so rather than silently starting
empty, and offers to adopt an orphaned file whose header names the old path.

**Location.** `~/.jupyter/vybe/` rather than a `.vybe/` directory beside the notebook, so nothing
stray appears in the user's repo. The cost is that history does not travel with the notebook —
send someone your `.ipynb` and they get the cells, not the prompts. `history.location` offers
`adjacent` for users who want the record version-controlled alongside the work, which for a
research audit trail is a reasonable thing to want.

`history.maxEntries` bounds what the _pane renders_, not what is stored; the file keeps
everything, and the pane pages older entries in on scroll.
