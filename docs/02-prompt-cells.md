# 02 — Prompt Cells: Interception, Lifecycle, Undo

## The interception point

JupyterLab ≥ 4.2 exposes `INotebookCellExecutor`, a token whose default provider is:

```ts
// @jupyterlab/notebook-extension/src/cellexecutor.ts — verbatim
export const cellExecutor: JupyterFrontEndPlugin<INotebookCellExecutor> = {
  id: '@jupyterlab/notebook-extension:cell-executor',
  provides: INotebookCellExecutor,
  activate: (): INotebookCellExecutor => Object.freeze({ runCell })
};
```

Every run path in `NotebookActions` — `run`, `runAndAdvance`, `runAndInsert`, `runCells`,
`runAll`, `runAllAbove`, `runAllBelow`, and the restart-and-run-all command — reaches
`Private.runCell`, which ends in `executor.runCell(options)`. Replacing the executor therefore
covers keyboard, toolbar, menu, palette, and programmatic execution in one place. There is no
second door.

This is why we do **not** monkeypatch. `NotebookActions` is a TypeScript namespace: its exported
functions call each other through local bindings, so reassigning `NotebookActions.runCells`
would not intercept `run()`. The executor token is the only complete, supported hook.

```ts
// src/plugins/cellExecutor.ts  (shape, not final)
import { INotebookCellExecutor, runCell as coreRunCell } from '@jupyterlab/notebook';

const plugin: JupyterFrontEndPlugin<INotebookCellExecutor> = {
  id: 'jupyter-vybe:cell-executor',
  provides: INotebookCellExecutor,
  autoStart: true,
  activate: (app, settings?: IVybeSettings, rounds?: IVybeRounds) => {
    // NOTHING may throw here — see 01, bootstrap ordering hazard.
    return Object.freeze({
      runCell: async (options) => {
        try {
          if (!isPromptCell(options.cell, settings?.prefix ?? '>')) {
            return coreRunCell(options);        // untouched core behaviour
          }
          return await rounds!.begin(options);  // never reaches a kernel
        } catch (err) {
          console.error('[jupyter-vybe] falling back to core executor', err);
          return coreRunCell(options);
        }
      }
    });
  }
};
```

### Detection rule

A cell is a prompt cell iff **all** hold:

- `cell.model.type === 'code'`
- the source's first non-whitespace-at-start character (leading blank lines allowed, leading
  spaces *not*) equals the configured prefix
- the extension master switch is on

The prefix is compared against the raw first character exactly — no trimming — so a line of
Python that happens to contain `>` later is unaffected. `>>` (a doctest paste) is still a prompt
cell by this rule; that is intentional and matches "any natural language or example code may
follow".

### Prefix validation

Settings must reject any prefix that could be valid code or is already claimed:

- reserved: `!` (shell), `?` / `??` (IPython help), `%` / `%%` (magics), `/` (some kernels)
- rejected if the single-character prefix, followed by `x = 1`, parses as valid Python — this
  catches `-`, `+`, `~`, `(`, `[`, `*`, and identifier characters
- allowed by construction: `>`, `>>`, `@@` (not a valid decorator on its own line? — validated
  by the same parse test), and arbitrary multi-character strings that fail the parse test

The parse test runs in the frontend using a tiny hand-written check (not the kernel) plus a
static denylist. Prefix changes apply to newly executed cells only; existing markdown
blockquotes keep the prefix they were written with.

## Prompt-cell lifecycle

```
  code cell "> …"
        │  Shift+Enter
        ▼
  ┌───────────────┐  executionState='running'  → input prompt shows [*]
  │   RUNNING     │  cell editor becomes read-only, .jv-prompt-running class
  └───────┬───────┘  Ctrl+C → CANCELLED
          │ agent stream completes
          ▼
  ┌───────────────┐  insertions applied & selected
  │   APPLYING    │  proposals staged
  └───────┬───────┘
          │
          ├── proposals == 0 ──▶ SETTLED
          └── proposals  > 0 ──▶ REVIEWING ──(all resolved / reject-all)──▶ SETTLED
                                     │
                                     └── Ctrl+C ──▶ CANCELLED
  SETTLED:   prompt cell → markdown cell, selection = inserted cells, focus returns to prompt
  CANCELLED: every mutation of the round reverted, cell restored as an *unexecuted* code cell
             with the original prompt text, cursor placed in it, edit mode
```

`executionCount` is **never** assigned. `onCellExecutionScheduled` and `onCellExecuted` are
still emitted so that execution-indicator extensions and the busy dot behave sanely, but since
`executionCount` stays `null` the cell contributes nothing to the notebook's execution history —
satisfying PROMPT §1.

## The markdown transformation

On SETTLE, the prompt cell is replaced by a markdown cell whose source is:

```markdown
> the user's original prompt text, verbatim, including the > prefix
> continuation lines are prefixed too

<agent prose, if any, verbatim>
```

Two properties make this the right encoding:

- It renders as a blockquote — visually distinct, obviously a prompt, no CSS required.
- It is **losslessly reversible**: stripping `> ` from the leading blockquote recovers the exact
  prompt. No metadata key is needed, satisfying the README's backwards-compatibility promise.
  See [12-decisions.md](12-decisions.md) D4.

**This cell is a rendering, not the record.** The prompt, the prose, and every insertion and
proposal are written to the history store ([05](05-history-pane.md)) as the round settles. The
markdown cell exists so the notebook reads sensibly on its own; deleting it loses the rendering
and nothing else, and the round remains in the pane, still pasteable, still part of the agent's
conversation context. The blockquote round-trip is a convenience for `reopen-prompt`, not the
storage mechanism ([12](12-decisions.md) D12).

The reverse operation is a command, `jupyter-vybe:reopen-prompt`, bound to `Cmd+Shift+Enter` on
a markdown cell that starts with the prefix blockquote. It converts back to a code cell with the
prompt restored — available forever, not just inside the undo window.

Cell type change goes through `NotebookActions.changeCellType`, never through hand-editing the
model, so cell ids, ydoc history, and RTC stay coherent.

## Undo

Two layers, deliberately:

**Layer 1 — native.** Every mutation of a round is wrapped in one
`notebook.model.sharedModel.transact(() => { … })`. The ydoc undo manager therefore sees the
whole round as a single unit, so a plain `Cmd+Z` from anywhere reverts it atomically. This costs
nothing and keeps vanilla undo honest even for users who never learn the vybe shortcuts.

**Layer 2 — round-scoped.** While a round is in `REVIEWING`, `jupyter-vybe:undo` takes over
`Cmd+Z` (a keybinding with a `.jp-Notebook.jv-round-active` selector, more specific than the
core binding, registered later — Lumino resolves to ours). Semantics, per README:

| Where the cursor is | `Cmd+Z` does |
|---|---|
| On a just-accepted proposal | un-accepts it, returns to it, re-stages it as pending |
| On a pending proposal | steps back to the previously resolved proposal and un-resolves it |
| Back at the prompt cell, round settled | reverts the entire round: removes inserted cells, discards all proposals, restores the code prompt cell |

Layer 2 is `jupyter-vybe:undo`, bound to `Accel Z` with the `.jv-round-active` selector so it
wins over core's binding only while a round is live. It takes one verdict back per press, most
recent first: an accepted proposal is restored to its previous source (recorded as an edit of its
own, so a later revert still unwinds cleanly) and re-staged as pending, with the cell showing its
diff again. A stale proposal is skipped -- the user's own edit is what settled it. When there is
no verdict left to take back, the press reverts the round itself.

Layer 2 is implemented as an explicit operation log on the `Round` object, not by reading ydoc
history. When the round leaves `REVIEWING` and the user makes any non-vybe edit, the log is
sealed and `Cmd+Z` reverts to core behaviour — no surprise time-travel days later.

`Ctrl+C` during `RUNNING` or `REVIEWING` is `jupyter-vybe:cancel-round`: aborts the agent stream
server-side, replays the operation log backwards, restores the code prompt cell in edit mode.

## Mutation discipline

`src/notebook/mutations.ts` is the only module permitted to write to a notebook model, and it
exposes exactly four functions:

```ts
insertCellsBelow(panel, anchorId, cells: {type, source}[], round): string[]  // returns new ids
applyProposal(panel, cellId, newSource, round): void
revertRound(panel, round): void
convertPromptToMarkdown(panel, cellId, promptText, prose, round): void
```

All four use `NotebookActions` / `sharedModel.transact` / `sharedModel.updateSource`. Direct
`model.cells` manipulation is banned and enforced by a lint rule
(`no-restricted-imports` + a custom ESLint rule scoped to that file). This is what keeps the
agent structurally unable to corrupt a notebook: the tool protocol's only reachable effects are
these four functions.

### Insertion position constraint

`add_cell` may only target the position directly below the prompt cell, or below a cell already
inserted by the same round. The anchor is recomputed from the *current* index of the prompt
cell's id at apply time, so concurrent user edits above cannot misplace insertions. A tool call
naming any other position is rejected by the parser with a protocol error fed back to the agent
(see [07](07-tool-protocol.md)). The cap on insertions per round is a setting, default 8.
