# Jupyter Vybe — Project Plan

A JupyterLab 4 extension that turns code cells prefixed with `>` into agent prompts, inserts
generated cells beneath them, and proposes reviewable diffs to any other cell — without the
agent ever executing anything.

This file is the index. Each document below is self-contained.

## Blueprint index

| Doc | Contents |
|---|---|
| [docs/00-overview.md](docs/00-overview.md) | Scope, non-goals, success criteria, glossary |
| [docs/01-architecture.md](docs/01-architecture.md) | Component map, package layout, plugin graph, data flow |
| [docs/02-prompt-cells.md](docs/02-prompt-cells.md) | Execution interception, prompt-cell lifecycle, undo |
| [docs/03-diff-ui.md](docs/03-diff-ui.md) | Proposal store, diff rendering, review UX, keyboard map |
| [docs/04-settings.md](docs/04-settings.md) | Full settings schema, every control, validation |
| [docs/05-history-pane.md](docs/05-history-pane.md) | Right sidebar, entry model, clipboard behaviour |
| [docs/06-agent-integration.md](docs/06-agent-integration.md) | Server extension, adapters, sessions, streaming API |
| [docs/07-tool-protocol.md](docs/07-tool-protocol.md) | Tool grammar, parser, agent loop, apply pipeline |
| [docs/08-prompt-templates.md](docs/08-prompt-templates.md) | The five prompt files and how they assemble |
| [docs/09-context-management.md](docs/09-context-management.md) | Notebook filtering rules, diff summaries, token budget |
| [docs/10-testing.md](docs/10-testing.md) | Unit, integration, Galata e2e, agent fixtures |
| [docs/11-roadmap.md](docs/11-roadmap.md) | Milestones M0–M8, deliverables, exit criteria |
| [docs/12-decisions.md](docs/12-decisions.md) | **Executive decisions needing a human**, with recommendations |
| [docs/13-risks.md](docs/13-risks.md) | Risk register and mitigations |

## The one-paragraph version

A **frontend plugin** replaces JupyterLab's `INotebookCellExecutor` so that any code cell whose
source begins with the trigger prefix is intercepted *before* a kernel message is ever
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
