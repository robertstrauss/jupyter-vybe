# 03 — Proposal & Diff Review UI

## Proposal model

```ts
interface Proposal {
  id: string;            // uuid
  roundId: string;
  cellId: string;        // nbformat 4.5 cell id — stable across reorders
  before: string;        // cell source captured when the proposal was staged
  after: string;         // agent's replacement source
  hunks: Hunk[];         // computed line diff
  rationale?: string;    // one line from the agent, shown under the ✓/✗ stack
  state: 'pending' | 'accepted' | 'rejected' | 'stale';
}
```

A proposal goes **stale** if the user edits the target cell after staging (`before` no longer
matches the live source). Stale proposals are shown greyed with a "cell changed — re-diff?"
affordance; accepting one is blocked. This is cheap insurance against the agent silently
clobbering work the user did while the model was thinking.

The store lives in `jupyter-vybe:proposals` and is keyed by notebook panel id, so switching
tabs preserves in-flight reviews.

## Diff computation

Line-level diff via `diff` (jsdiff) `diffLines`, then coalesced into hunks with three lines of
context. We do **not** use `@codemirror/merge`'s unified view: its chrome (gutter markers,
collapse widgets, revert arrows) fights the GitHub-style full-line green/red backgrounds the
PROMPT asks for, and its accept/revert model is per-chunk rather than per-cell. Hand-rolled
decorations over a read-only CM6 editor give exact control. See
[12-decisions.md](12-decisions.md) D7.

Rendering: a `StateField<DecorationSet>` producing `Decoration.line({class})` for
`jv-diff-add` / `jv-diff-del`, plus optional `Decoration.mark` for intra-line word diffs
(`diffWordsWithSpace`) at reduced opacity. Colors come from JupyterLab CSS variables so both
themes work:

```css
.jv-diff-add { background: var(--jp-diff-added-color, rgba(46,160,67,.15)); }
.jv-diff-del { background: var(--jp-diff-deleted-color, rgba(248,81,73,.15)); }
```

## Where the diff appears

While a proposal is pending, the target cell's normal editor is **hidden** (not replaced in the
model — the model is untouched until accept) and a `DiffOverlay` widget is inserted into the
cell's input area. The overlay is a read-only CM6 editor showing the unified before/after with
decorations, using the same language mode as the cell so highlighting matches.

Nothing about the notebook model changes while proposals are pending. Rejecting is therefore
free: dispose the overlay, unhide the editor.

## Per-cell review controls

Floating over the cell's right edge, vertically stacked, per PROMPT §2:

```
                                          ┌──────┐
   ┌──────────────────────────────────┐   │  ✓   │   Y
   │  - old line                      │   ├──────┤
   │  + new line                      │   │  ✗   │   N
   └──────────────────────────────────┘   ├──────┤
                                          │  ✎   │   ⏎
                                          └──────┘
                                         ⏎ to modify
```

- Buttons are `position: sticky; top: …` inside an absolutely positioned right-edge container so
  they stay visible while a long cell scrolls.
- Accept and reject are 52px: they are the primary interaction of the extension and were far too
  small to hit comfortably at first (docs/14 N2).
- Under each button, small text names its shortcut (`Y` / `N` / `⏎`).
- The third verdict — merge and hand-edit — has a button of its own, smaller than the other two,
  plus a line of text naming its key. It existed from the start but nothing on screen said so.
- Only the *active* proposal shows the stack; others show a thin left border marker so the user
  can see how many remain in view.

## Status bubble

Sticky, top-right of the notebook panel (`position: sticky; top: 8px; float: right; z-index`
above the cell toolbar but below dialogs):

```
┌───────────────────────────────┐
│ ⚡ 3 edits proposed            │
│ click or ⌥E to review         │
├───────────────────────────────┤
│ reject all  ⌥⇧E               │
└───────────────────────────────┘
```

- Click, or the shortcut, jumps to the first unresolved proposal. The jump button is the entire
  top of the bubble; reject-all sits below a divider, small and clearly labelled. They were once
  adjacent and similarly sized, so a click meaning "show me" could discard the round (docs/14 N3).
- **Reject-all is undoable.** One click discards a whole round's review, so the rejected batch is
  kept and the bubble turns into `rejected 3 edits — undo ⌥⇧Z` instead of vanishing. Undoing
  restores the proposals *and* re-registers the round, so accepting afterwards still records to
  the round's operation log. Staging any new proposal discards the undo.
- The count decrements on each resolution, with a brief number-change animation.
- Also renders round status during `RUNNING` (`⚡ thinking…` + elapsed time + a cancel affordance),
  so there is one place to look for "is something happening".
- Disappears on SETTLE.

## Navigation and keyboard map

All bindings are registered against the selector `.jp-Notebook.jv-round-active` so they never
shadow core shortcuts outside a round.

**The class goes on `panel.content.node`, not `panel.node`.** Those selectors need both classes on
one element, and `.jp-Notebook` is the content node; while the marker sat on the panel node the
selectors matched nothing and `Y` / `N` / `Enter` did nothing at all. Only the buttons worked,
which is exactly why it went unnoticed for so long — the first end-to-end run of the review flow
is what found it.

Review also *begins* on the keyboard: `beginReview` selects the first proposal, puts the notebook
in command mode, and takes focus. Settling a round leaves an editor focused otherwise, so the
first `Y` went into a cell as the letter y.

| Keys | Command | Context |
|---|---|---|
| `Shift+Enter` on `>` cell | (core run, intercepted) | always |
| `Cmd+Shift+Enter` on `>` markdown | `jupyter-vybe:reopen-prompt` | always |
| `Alt+E` | `jupyter-vybe:goto-first-proposal` | round active |
| `Alt+Shift+E` | `jupyter-vybe:reject-all` | round active |
| `Y` | `jupyter-vybe:accept-proposal` | command mode, on a proposal |
| `N` | `jupyter-vybe:reject-proposal` | command mode, on a proposal |
| `Enter` | `jupyter-vybe:edit-proposal` — materialise the merge, enter edit mode | command mode, on a proposal |
| `J` / `K`, `↓` / `↑` | next / previous proposal | command mode, round active |
| `Cmd+Z` | `jupyter-vybe:undo` (layered, see [02](02-prompt-cells.md)) | round active |
| `Ctrl+C` | `jupyter-vybe:cancel-round` | round active |
| `Alt+Shift+Z` | `jupyter-vybe:undo-reject-all` | command mode (the round is already over) |

### Why `Y` / `N` and not `Enter` / `Backspace`

`Enter` in command mode means "go edit this", which is a meaning worth keeping during review —
so it is kept, and given the third verdict below. Accept/reject take `Y`/`N` instead.

`N` has no core binding. `Y` does — `notebook:change-cell-to-code` — so it is a scoped override.
Changing a cell's type while reviewing a diff on it is meaningless, so nothing useful is shadowed.
(For the record, the only single letters core leaves free in command mode are `E G N Q T U W`.)

### These bindings are inert in edit mode

`Y` and `N` are letters. If they fired while the user was typing, the tool would be unusable — so
the scoping has to be exactly right, and JupyterLab's own mechanism gives it to us for free.
Core's binding is:

```
notebook:change-cell-to-code   Y   .jp-Notebook.jp-mod-commandMode:not(.jp-mod-readWrite) :focus
```

The notebook node carries `.jp-mod-commandMode` or `.jp-mod-editMode`, and in edit mode focus is
inside the CodeMirror editor rather than on the cell. So a command-mode-scoped binding **cannot**
fire while typing: `Y` in edit mode inserts the character `y`, exactly as it does today.

Ours mirror core's selectors with `.jv-round-active` added, which makes them strictly more
specific and therefore winners during a round, and non-matching outside one:

| Command | Keys | Selector |
|---|---|---|
| `jupyter-vybe:accept-proposal` | `Y` | `.jp-Notebook.jp-mod-commandMode.jv-round-active .jp-Cell.jv-proposal:focus` |
| `jupyter-vybe:reject-proposal` | `N` | `.jp-Notebook.jp-mod-commandMode.jv-round-active .jp-Cell.jv-proposal:focus` |
| `jupyter-vybe:edit-proposal` | `Enter` | `.jp-Notebook.jp-mod-commandMode.jv-round-active .jp-Cell.jv-proposal:focus` |

Three conditions must all hold for any of them to fire: command mode, an active round, and the
focused cell actually carrying a pending proposal. Outside that intersection every key keeps its
normal meaning — including on a cell in the same notebook that has no proposal on it.

The round trip is therefore: `Y`/`N` to verdict → `Enter` to hand-merge → type freely (`y` is
just `y`) → `Esc` back to command mode, where `Y`/`N` are live again for the next proposal.
`Esc` is core's binding and is untouched.

A test asserts that with a round active, `Y` in edit mode inserts a character and resolves
nothing ([10](10-testing.md), added to the Galata flows).

### The third verdict: `Enter` opens a merged edit

Binary accept/reject is the wrong shape when a proposal is 80% right. Pressing `Enter` on a
pending proposal **materialises the merge into the cell and drops into edit mode**: the accepted
lines live, the lines they replace retained directly beneath as comments.

```python
def rollingMean(x, w):
    c = np.cumsum(np.insert(x, 0, 0))
    return (c[w:] - c[:-w]) / w
    # -- replaced --
    # total = 0
    # for i in range(len(x) - w):
    #     total += ...
```

Two properties earn this its place. The cell is **valid syntax at every moment**, so an
interrupted or half-finished review never leaves a notebook that will not parse. And the user can
hand-merge — take three lines of the suggestion and none of the rest — which the binary path
cannot express.

The merge is materialised **on demand, not ambiently**. While a proposal merely sits pending, the
notebook model is untouched and the diff lives in a read-only overlay; only `Enter` writes to the
model. The alternative — keeping every pending proposal materialised as commented-merge from the
moment it is staged — buys valid syntax even across a crash, but a save mid-review would then
persist commented-out dead code, every staged proposal would churn the ydoc, and reject would
become a restore rather than a no-op. On-demand gets the benefit where it actually matters.

Leaving edit mode after an `Enter` merge resolves the proposal as `accepted` (the user's edited
text is now simply the cell's content); the commented block is left exactly as the user left it,
on the theory that deleting their comments for them is worse than leaving tidying to them.

**Comment syntax** comes from the kernel's `language_info.name`, already carried in the context
payload: `#` for Python, R, Julia, and shell; `//` for JavaScript, TypeScript, and the C family;
`%` for MATLAB; `<!-- -->` for markdown cells; `#` as the fallback for unknown kernels.

Resolving a proposal auto-advances: smooth-scroll (`behavior: 'smooth'`, ~180ms, respecting
`prefers-reduced-motion`) to the next unresolved proposal, activating it. After the last one,
scroll back to the prompt cell and restore command-mode focus there.

## Proposals on markdown cells

The agent may propose edits to markdown cells as well as code cells, and markdown cells have a
rendered state that code cells do not. The handling:

- **While pending**, the target markdown cell is temporarily unrendered (`cell.rendered = false`)
  so the diff is shown against its *source*, which is what is being edited. Its prior rendered
  state is recorded on the proposal.
- **Accept** → source updated, then the prior rendered state restored (so a cell that was rendered
  before the proposal is rendered again after it).
- **Reject** → rendered state restored, model never touched.
- **`Enter` merge** → comment syntax is `<!-- -->`, which has a pleasant side effect: the replaced
  lines are invisible in the rendered output, so a half-merged markdown cell still *reads*
  correctly even before the user tidies it.
- Syntax highlighting in the overlay uses the markdown mode rather than the kernel language.

### One target is off limits: settled prompt cells

A settled prompt cell is a markdown cell whose source is the user's original prompt as a
blockquote ([02](02-prompt-cells.md)). The agent must not propose edits to those — it would be
rewriting the user's own prompt history, and under persistent context it would be rewriting the
record of its own past instructions.

The apply pipeline rejects any `propose_edit` whose target is a markdown cell beginning with the
trigger prefix blockquote, with a protocol error fed back to the agent
([07](07-tool-protocol.md)). This is enforced in the pipeline rather than merely discouraged in
the tooling prompt, because it is exactly the sort of rule a small model will violate while
"tidying up" the notebook.

## Inserted-cell presentation

Insertions need no review, but per PROMPT the whole group must be selected after the round:
`notebook.activeCellIndex = firstInserted; notebook.extendContiguousSelectionTo(lastInserted)`.
They also carry a transient `.jv-inserted` class that fades a subtle left accent bar over ~2s,
so the eye lands on what is new without leaving permanent decoration.
