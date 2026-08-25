# 00 — Overview, Scope, Non-Goals

## Product thesis

Language models are boilerplate compressors, not engineers. Jupyter Vybe therefore constrains
the agent to the smallest useful unit of generation — a notebook cell — and forces every change
outside that unit through human review. The user stays the engineer; the model supplies the
patterns.

Two hard invariants follow from that thesis, and every design choice in this blueprint defers
to them:

1. **The agent never executes anything.** No `execute_request`, no shell, no code paths that
   could reach one. Enforced structurally (see [02](02-prompt-cells.md)), not by prompting.
2. **The notebook stays a normal notebook.** No new cell types, no required metadata keys, no
   sidecar files. A collaborator without the extension opens the file and sees ordinary code
   and markdown cells.

## In scope for v1.0

| # | Feature | Source |
|---|---|---|
| 1 | Prompt cells: `>`-prefixed code cells routed to an agent instead of the kernel | PROMPT §1 |
| 2 | Proposed-edit diff UI with per-cell accept/reject, sticky counter, keyboard navigation | PROMPT §2 |
| 3 | Settings pane with every control named in README plus those this design requires | PROMPT §3 |
| 4 | Prompt history side pane with rendered diffs and cell-clipboard copy | PROMPT §4 |
| 5 | Agent integration: local Ollama/Qwen, TurboFieldfare/Gemma, arbitrary command, HTTP endpoint | PROMPT §5 |
| 6 | Tooling prompt, universal prompt, user-editable global prompt, context filtering | PROMPT §6 |

## Explicitly out of scope for v1.0

- **Advanced inline autocompletion.** Described in README but absent from PROMPT's build list.
  It is a different subsystem (`ICompletionProviderManager` + FIM prompting) with its own
  latency budget. Deferred to v1.1 — see [12-decisions.md](12-decisions.md) D5.
- Multi-file / repository awareness. Context is the open notebook only.
- Agent-initiated cell *deletion* or *reordering*. The tool surface is add-below and
  propose-edit; nothing else.
- Notebook 7 / nbclassic support (stretch; the plugin system is shared, the CSS is not).
- Real-time collaboration (RTC) correctness under concurrent editing. Detected and degraded
  gracefully, not solved.

## Success criteria

A build is done when, on a fresh machine:

- `pip install jupyter-vybe && jupyter lab` yields a working extension with no manual steps
  beyond selecting an agent in settings.
- Running a `>` cell with the kernel **stopped** still produces cells — proving no kernel
  dependency.
- A kernel message log (`--debug`) across a full prompt round contains zero `execute_request`
  messages originating from the extension.
- Round-tripping a notebook through the extension and reopening it in vanilla JupyterLab shows
  no unknown metadata keys and no rendering differences.
- The full loop (prompt → cells inserted → 3 proposals reviewed → back at prompt cell) is
  driveable without touching the mouse.
- `Cmd+Z` at the prompt cell after a completed round restores the pre-prompt notebook exactly,
  byte-for-byte in `toJSON()`.

## Glossary

- **Prompt cell** — a code cell whose source begins with the trigger prefix (default `>`).
- **Round** — one prompt execution and everything it produces: inserted cells, proposals, and
  the terminal state transition of the prompt cell.
- **Proposal** — a staged edit to an existing cell, not yet applied to the notebook model.
- **Insertion** — a new cell added directly below the prompt cell (or below prior insertions of
  the same round). Applied immediately, no review.
- **Adapter** — a server-side driver for one kind of agent backend.
- **Session** — a persistent agent conversation bound to one notebook path.
