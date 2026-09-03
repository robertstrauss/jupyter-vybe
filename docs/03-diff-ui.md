# 03 — Proposal & Diff Review UI

## Proposal model

```ts
interface Proposal {
  id: string; // uuid
  roundId: string;
  cellId: string; // nbformat 4.5 cell id — stable across reorders
  before: string; // cell source captured when the proposal was staged
  after: string; // agent's replacement source
  hunks: Hunk[]; // computed line diff
  rationale?: string; // one line from the agent, shown under the ✓/✗ stack
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
.jv-diff-add {
  background: var(--jp-diff-added-color, rgba(46, 160, 67, 0.15));
}
.jv-diff-del {
  background: var(--jp-diff-deleted-color, rgba(248, 81, 73, 0.15));
}
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
                                       ┌──────────┐
   ┌──────────────────────────────┐    │ ✓ Accept │   Y
   │  - old line                  │    ├──────────┤
   │  + new line                  │    │ ✗ Reject │   N
   └──────────────────────────────┘    ├──────────┤
                                       │ ✎ Modify │   ⏎
                                       └──────────┘
```

- Buttons are `position: sticky; top: …` inside an absolutely positioned right-edge container so
  they stay visible while a long cell scrolls.
- They are large: the primary interaction of the extension, and far too small to hit comfortably
  at first (docs/14 N2).
- **Each says what it does, in a word.** A stack of ✓, ✗ and ✎ asks the reader to guess which one
  is the merge. The key stays a caption underneath, where it reads as a shortcut rather than as
  part of what the button says — and the separate line of hint text that used to sit below the
  stack is gone, because it repeated the caption two lines above it.
- **The merge verdict exists under both key schemes.** With `Y`/`N` it is `Enter`, which is free;
  when `Enter` is accept it is `Space`, which means nothing in command mode and sits under the
  hand. Losing a whole verdict by preferring one keyboard scheme would have made the two quietly
  unequal.
- Only the _active_ proposal shows the stack; others show a thin left border marker so the user
  can see how many remain in view.

## The prompt cell's output area

**Where a round reports on itself.** Progress and the review summary render into the prompt
cell's output area — as a widget attached to the cell's DOM, never as real outputs, so the
notebook gains nothing (D4) and a collaborator without the extension opens an ordinary file.

While generating:

```
Thinking, 47 tokens/sec
Used IPython tools to look up numpy.mean
```

The rate is characters-received over seconds, converted with the estimator from
[09](09-context-management.md) — an estimate of an estimate, and enough to answer "is it
working".

**The count comes from the server, on the raw stream.** The frontend cannot measure this: the
tag parser buffers a body until its closing tag, so a reply made entirely of tool tags — the
normal case — emits nothing to count until it has finished. The first version dutifully reported
`0 tokens/sec` for the whole round. The server now sends a `progress` frame four times a second
with the cumulative character count. The lookup lines matter more than they look: an introspection call is a silent pause
in generation, and from outside a pause is indistinguishable from a hang.

Once the agent has answered and edits are waiting:

```
2 edits proposed
Enter to review · N to reject all
```

**The prompt cell stays a code cell until every verdict is in.** Converting it to markdown at
settle time — which is what it used to do — destroys the cell the user is being asked to act on
and moves the count somewhere they are not looking.

**Focus returns to the prompt cell**, in command mode, because Shift+Enter has already moved on
by the time a round settles. Without that the summary appears on a cell nobody is looking at and
the keys that resolve it do nothing.

When no edits were proposed, or once the last verdict is in, the prompt becomes its markdown
record and the cells the round inserted become the selection.

## The review session

A review is a session, not a decoration. While edits are pending the notebook
carries `jv-review-session`: every cell that is not under review — the prompt
excepted, since it is the round — dims and stops answering. Clicks on one are
caught in the capture phase and refused, and the notebook's own cell-to-cell
navigation keys are declined.

This exists for one failure mode: clicking away into another cell, forgetting
the diffs, and leaving a round open. That is how a proposal gets applied days
later, or never.

Two things it must not be. It must not be a trap — `Escape` rejects
everything remaining and closes it, from anywhere in the notebook, which is
why that binding is scoped to the whole notebook rather than to a cell. And
it must not refuse in silence: a click that does nothing is
indistinguishable from a broken page, so the counter's border flashes red.

The exit is graceful and total. The class, both listeners, the dimming, the
diffs, and the counter all go together the moment the last verdict lands.

### The merge is a session too

Accepting a proposal removes it, and the merge verdict accepts — so the cell
the user was just invited to hand-edit stopped being a proposal at the moment
they were asked to edit it, and the review session promptly dimmed it and
refused their clicks. The merge is therefore a state the session knows about,
not the absence of one.

While it is open, everything except the cell being edited recedes — the other
diffs included, because stepping from a half-written merge into a diff would
leave the merge behind unconfirmed. `Escape` confirms and walks on to the next
edit; clicking away means the same thing, because a click outside a merge is
someone finishing, not someone making a mistake. There is nothing to apply
either way: the merged text has been in the cell since the verdict, and every
keystroke since is the user's own.

`jv-merge-session` and `jv-review-session` are mutually exclusive, which is
what stops `Escape` meaning "confirm this merge" and "reject everything" at
the same time.

**Guard the input, never chase the focus.** The first implementation watched
`activeCellChanged` and hauled the selection back to a diff. It fought
JupyterLab for focus and lost in ways that took a bisect to see: running the
last cell makes JupyterLab add a cell below and select it, which arrives
_after_ the round has put the cursor on the prompt — so the guard dragged the
user onto a diff, and the `Enter` meant to start the review arrived as the
merge verdict instead. Refusing keys and clicks up front decides nothing
about where the user is; it only declines to move them.

## The review counter

The only floating chrome, and the session's only control surface. It exists
for the failure mode above, so it appears exactly while that is possible and
vanishes the moment the last verdict lands.

```
   ┌──────────────────────────────────────────────────────┐
   │             3 edits left to review                   │
   │  [ Next Enter ]  [ Reject All esc ]  [ Undo Merge ⌘Z ]│
   └──────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────┐
   │              Manual proposal review                  │
   │  [ Confirm Merge and Continue esc ]  [ Undo Merge ⌘Z ]│
   └──────────────────────────────────────────────────────┘
```

Two rows — what is happening, and what to do about it — because they read
better stacked, and a card tall enough to hold both stops looking like a pill.
Every action is a button naming the key that does the same thing, set as a
`<kbd>` in the code font on a grey field so it reads as a key rather than as
more of the label. That matters most for `esc`, the only one that is a word
and would otherwise disappear into the sentence beside it.

The border breathes between 30% and 60% brand colour over six seconds: slow
enough to register as alive rather than as urgent, because the point is that
an open review is still open, not that it needs attention this second.

Translucent, because it is a reminder rather than a control panel, and set
below the notebook's toolbar rather than across it — floating over the
toolbar made it read as part of JupyterLab instead of part of the round. It
carries every way out: jump to the next edit, reject what is left, or take a
verdict back with `Cmd+Z`. Each names the key that does the same thing, so
the mouse teaches the keyboard.

Everything else a round has to say — progress, the context meter, errors, the
proposal count itself — belongs to the prompt cell's output area, next to the
prompt that caused it. There is no second place to look.

## Navigation and keyboard map

All bindings are registered against the selector `.jp-Notebook.jv-round-active` so they never
shadow core shortcuts outside a round.

**Two rules about bindings, both learned by watching a key do nothing.**

_Focus the cell, not the notebook._ Lumino resolves a keybinding against the focused element, so
a binding selecting `.jp-Cell.jv-…` is inert while focus sits on `.jp-Notebook`. Every place that
moves the user — settling into review, jumping to the first edit, advancing after a verdict —
focuses the cell it moved them to.

_Beat core on specificity, deliberately._ Core binds `Enter` to
`.jp-Notebook.jp-mod-commandMode .jp-Cell:focus`. Our prompt-cell binding computed to exactly the
same specificity, and on a tie core won — so `Enter` opened the editor instead of starting the
review. Adding `:focus` to ours breaks the tie and states the intended condition besides.

**The class goes on `panel.content.node`, not `panel.node`.** Those selectors need both classes on
one element, and `.jp-Notebook` is the content node; while the marker sat on the panel node the
selectors matched nothing and `Y` / `N` / `Enter` did nothing at all. Only the buttons worked,
which is exactly why it went unnoticed for so long — the first end-to-end run of the review flow
is what found it.

Review also _begins_ on the keyboard, but not by moving anyone: `beginReview` leaves the user on
the prompt cell, which shows the count, and `Enter` starts the walk. Settling a round leaves an
editor focused otherwise, so the first `Y` went into a cell as the letter y.

_Never enter edit mode without saying which cell._ The merge verdict accepts the proposal first,
and accepting advances the review — so `mode = 'edit'` landed the cursor on whatever the advance
had selected, usually the prompt. The merge sets the active cell back to the merged one before
entering edit mode, and puts the cursor at the end of it. Undo has the mirror of this problem:
it restores the prompt cell for review, which focuses the prompt, so taking a verdict back has to
focus the restored diff afterwards or the next `Y` lands nowhere.

| Keys                                  | Command                                                               | Context                                  |
| ------------------------------------- | --------------------------------------------------------------------- | ---------------------------------------- |
| `Shift+Enter` on `>` cell             | (core run, intercepted)                                               | always                                   |
| `Cmd+Shift+Enter` on `>` markdown     | `jupyter-vybe:reopen-prompt`                                          | always                                   |
| `Enter`                               | `jupyter-vybe:goto-first-proposal`                                    | on the prompt cell, edits pending        |
| `N`                                   | `jupyter-vybe:reject-all`                                             | on the prompt cell, edits pending        |
| `Escape`                              | `jupyter-vybe:reject-all` — the way out of a session                  | anywhere in a review session             |
| `Y`                                   | `jupyter-vybe:accept-proposal`                                        | command mode, on a proposal              |
| `N`                                   | `jupyter-vybe:reject-proposal`                                        | command mode, on a proposal              |
| `Enter` (`Space` in the other scheme) | `jupyter-vybe:edit-proposal` — materialise the merge, enter edit mode | command mode, on a proposal              |
| `J` / `K`, `↓` / `↑`                  | refused, with a flash — a session does not let you wander off         | command mode, review session             |
| `Cmd+Z`                               | `jupyter-vybe:undo` (layered, see [02](02-prompt-cells.md))           | round active                             |
| `Ctrl+C`                              | `jupyter-vybe:cancel-round`                                           | round active                             |
| `Alt+Shift+Z`                         | `jupyter-vybe:undo-reject-all`                                        | command mode (the round is already over) |

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

| Command                        | Keys    | Selector                                                                     |
| ------------------------------ | ------- | ---------------------------------------------------------------------------- |
| `jupyter-vybe:accept-proposal` | `Y`     | `.jp-Notebook.jp-mod-commandMode.jv-round-active .jp-Cell.jv-proposal:focus` |
| `jupyter-vybe:reject-proposal` | `N`     | `.jp-Notebook.jp-mod-commandMode.jv-round-active .jp-Cell.jv-proposal:focus` |
| `jupyter-vybe:edit-proposal`   | `Enter` | `.jp-Notebook.jp-mod-commandMode.jv-round-active .jp-Cell.jv-proposal:focus` |

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
  so the diff is shown against its _source_, which is what is being edited. Its prior rendered
  state is recorded on the proposal.
- **Accept** → source updated, then the prior rendered state restored (so a cell that was rendered
  before the proposal is rendered again after it).
- **Reject** → rendered state restored, model never touched.
- **`Enter` merge** → comment syntax is `<!-- -->`, which has a pleasant side effect: the replaced
  lines are invisible in the rendered output, so a half-merged markdown cell still _reads_
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

## Ghost text, and what the kernel contributes to it

Not strictly review UI, but the same rule governs both: the extension owns the keys only where it
has earned them.

`Tab` and `Shift+Tab` stay the kernel's. That is not politeness — it is self-interest. Both are
fed to the model: an attribute completion sends `complete_request` (what `Tab` returns) and
`inspect_request` on the object (what `Shift+Tab` shows), so the suggestion knows what the object
actually offers in _this_ session rather than what the model guesses from the notebook. Taking
either key would be taking the feature's own input. Both honour
`agentTools.kernelIntrospection`, the same switch that governs the agent's lookups: a user who
said not to talk to their kernel meant all of us.

`Tab` is bound, but only ever consumes the key when a suggestion is on screen; with none it
declines and the key carries on to JupyterLab.

The tooltip above the cursor is the whole progress report. Before the first character it reads
`predicting…`, then the measured rate — a local model takes a beat, and in that beat a trigger
key is indistinguishable from a key that did nothing. Once there is text it becomes the key map,
naming the configured trigger rather than assuming what it is.
