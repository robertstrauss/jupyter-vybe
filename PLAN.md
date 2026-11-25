# Jupyter Vybe — Project Plan

A JupyterLab 4 extension that turns code cells prefixed with `>` into agent prompts, inserts
generated cells beneath them, and proposes reviewable diffs to any other cell — without the
agent ever executing anything.

This file is the index. Each document below is self-contained.

## Blueprint index

| Doc                                                            | Contents                                                      |
| -------------------------------------------------------------- | ------------------------------------------------------------- |
| [docs/00-overview.md](docs/00-overview.md)                     | Scope, non-goals, success criteria, glossary                  |
| [docs/01-architecture.md](docs/01-architecture.md)             | Component map, package layout, plugin graph, data flow        |
| [docs/02-prompt-cells.md](docs/02-prompt-cells.md)             | Execution interception, prompt-cell lifecycle, undo           |
| [docs/03-diff-ui.md](docs/03-diff-ui.md)                       | Proposal store, diff rendering, review UX, keyboard map       |
| [docs/04-settings.md](docs/04-settings.md)                     | Full settings schema, every control, validation               |
| [docs/05-history-pane.md](docs/05-history-pane.md)             | Right sidebar, entry model, clipboard behaviour               |
| [docs/06-agent-integration.md](docs/06-agent-integration.md)   | Server extension, adapters, sessions, streaming API           |
| [docs/07-tool-protocol.md](docs/07-tool-protocol.md)           | Tool grammar, parser, agent loop, apply pipeline              |
| [docs/08-prompt-templates.md](docs/08-prompt-templates.md)     | The five prompt files and how they assemble                   |
| [docs/09-context-management.md](docs/09-context-management.md) | Notebook filtering rules, diff summaries, token budget        |
| [docs/10-testing.md](docs/10-testing.md)                       | Unit, integration, Galata e2e, agent fixtures                 |
| [docs/11-roadmap.md](docs/11-roadmap.md)                       | Milestones M0–M8, deliverables, exit criteria                 |
| [docs/12-decisions.md](docs/12-decisions.md)                   | **Executive decisions needing a human**, with recommendations |
| [docs/13-risks.md](docs/13-risks.md)                           | Risk register and mitigations                                 |
| [docs/14-next-steps.md](docs/14-next-steps.md)                 | **Next steps** — outstanding bugs and polish from live use    |

## The one-paragraph version

A **frontend plugin** replaces JupyterLab's `INotebookCellExecutor` so that any code cell whose
source begins with the trigger prefix is intercepted _before_ a kernel message is ever
constructed. The prompt text plus a filtered, in-memory snapshot of the notebook is sent over a
WebSocket to a **companion Jupyter Server extension**, which owns a persistent agent session
(Ollama, TurboFieldfare, an arbitrary command like `claude -p`, or an OpenAI-compatible
endpoint). The agent replies in a tagged text protocol; the frontend parses it and applies
`add_cell` calls directly below the prompt cell and stages `propose_edit` calls as reviewable
diffs. A sticky bubble counts pending proposals; the user walks them with the keyboard,
accepting or rejecting each. When the round closes the prompt cell becomes a markdown cell
holding the original `> prompt` as a blockquote — no custom notebook metadata, fully
round-trippable for collaborators without the extension.

## Verified technical foundations

These were checked against JupyterLab source, not assumed:

- `INotebookCellExecutor` (`@jupyterlab/notebook:INotebookCellExecutor`) is a public token; the
  default provider is the plugin `@jupyterlab/notebook-extension:cell-executor`, whose entire
  body is `Object.freeze({ runCell })`. Providing our own and disabling that plugin id via
  `jupyterlab.disabledExtensions` in `package.json` is the supported override path.
- Every execution route in `NotebookActions` (`run`, `runCells`, `runAll`, `runAllAbove`,
  `runAllBelow`, `runAndAdvance`, restart-and-run-all) funnels through the private
  `Private.runCell`, which calls `executor.runCell(options)`. One executor covers all of them.
- `ICodeCellModel.executionState` is a settable `'idle' | 'running'` property; setting
  `'running'` renders the `[*]` input prompt with no kernel involvement.
- Kernel `inspect_request` / `complete_request` are distinct from `execute_request`: they do not
  run user code and do not advance the execution counter. See
  [docs/12-decisions.md](docs/12-decisions.md) D1.
- Target platform is JupyterLab 4.6.3 (current release), Node 24, Python 3.14 — all present on
  this machine. `jupyter` itself is not yet installed; M0 provisions the environment.

## What shipped, and where it lives

v1.0.0 is tagged; the sidecar and inline completion followed in 1.1. The map, for anyone
arriving at the source rather than the plan:

| Path                             | What it is                                                             |
| -------------------------------- | ---------------------------------------------------------------------- |
| `src/plugins/cellExecutor.ts`    | The interception point. One token covers every way a cell can run.     |
| `src/plugins/rounds.ts`          | A prompt execution from trigger to settled.                            |
| `src/plugins/proposals.ts`       | Staging, review, verdicts, and both undo layers.                       |
| `src/notebook/mutations.ts`      | The only module permitted to write to a notebook.                      |
| `src/notebook/snapshot.ts`       | Serialisation and the truncation ladder.                               |
| `src/plugins/inlineCompleter.ts` | Ghost text on demand: the request, and what the kernel adds to it.     |
| `src/completion/ghost.ts`        | The ghost text itself, and why Tab and Backspace are ours to bind.     |
| `jupyter_vybe/protocol.py`       | The tagged-text parser, and salvage for models that will not use it.   |
| `jupyter_vybe/assembler.py`      | Prompt assembly; the notebook as a numbered listing.                   |
| `jupyter_vybe/diffsummary.py`    | What changed since the last turn, for follow-up prompts.               |
| `jupyter_vybe/history.py`        | The prompt record: a sidecar store, not notebook metadata.             |
| `jupyter_vybe/adapters/`         | Four backends behind one interface, free of the server framework.      |
| `jupyter_vybe/local.py`          | `jupyter-vybe-local connect`: your agent CLI, dialling out to Jupyter. |
| `jupyter_vybe/adapters/relay.py` | The server end of that connection; it has no word for "run this".      |
| `jupyter_vybe/sockets.py`        | The two WebSockets that can make something run, and their auth.        |
| `jupyter_vybe/doctor.py`         | `jupyter-vybe-doctor`: every check exists because that thing happened. |
| `scripts/check-invariants.sh`    | The six rules the design is load-bearing on. Run in CI.                |
| `scripts/bench-tools.py`         | Does the model pick the right tool, and aim it at the right cell?      |
| `scripts/bench-quality.py`       | Does the code it wrote actually work?                                  |
| `scripts/bench-latency.py`       | Where the time goes, and whether prompt caching can help.              |
