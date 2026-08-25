# 01 — Architecture

## Why there is a server extension

PROMPT §1 asks for a front-end/UI extension. PROMPT §5 asks the extension to drive
`claude -p '...'`, local binaries, and persistent chat processes. A browser cannot spawn a
process or hold a pipe open. The two requirements meet in a **hybrid**: a frontend extension
that owns all UI and all notebook mutation, plus a thin companion **Jupyter Server extension**
that owns nothing but agent subprocesses and HTTP fan-out.

Where that server extension *runs* is configurable, not fixed: co-located with Jupyter by
default, or — in v1.1 — as a sidecar on the user's own machine, using the identical adapter code.
See [06](06-agent-integration.md) and [12](12-decisions.md) D2; this matters whenever Jupyter is
remote and the model is not.

The server extension is not a kernel and never speaks to one. It has no handle to a kernel
connection, no `jupyter_client` import, and no code path that could produce an `execute_request`.
The "front-end extension" spirit of PROMPT §1 is preserved where it matters: **all notebook
reading and writing happens in the browser, against the in-memory model.** The server never sees
a notebook file.

See [12-decisions.md](12-decisions.md) D2 for the frontend-only alternative and its costs.

## Component map

```
┌─────────────────────────────── Browser (JupyterLab 4.6) ────────────────────────────────┐
│                                                                                          │
│  jupyter-vybe:cell-executor ──provides──▶ INotebookCellExecutor                          │
│      │  intercepts prompt cells, delegates everything else to @jupyterlab/notebook#runCell│
│      ▼                                                                                   │
│  jupyter-vybe:rounds  ── the state machine: one Round per prompt execution                │
│      │                                                                                   │
│      ├──▶ jupyter-vybe:context     builds the filtered notebook snapshot / diff summary   │
│      ├──▶ jupyter-vybe:agent       WebSocket client, streaming parse, tool loop           │
│      ├──▶ jupyter-vybe:mutations   the ONLY module allowed to touch the notebook model     │
│      ├──▶ jupyter-vybe:proposals   proposal store + diff UI + sticky bubble + navigation   │
│      ├──▶ jupyter-vybe:history     right-sidebar pane                                     │
│      ├──▶ jupyter-vybe:introspect  guarded kernel inspect/complete (allowlist of 2 msgs)   │
│      └──▶ jupyter-vybe:settings    ISettingRegistry facade, live-reloading                 │
└──────────────────────────────────────────┬───────────────────────────────────────────────┘
                                           │  /jupyter-vybe/ws  (WebSocket, one per notebook)
                                           │  /jupyter-vybe/agents, /config, /health (REST)
┌──────────────────────────────────────────▼───────────────────────────────────────────────┐
│                      jupyter_vybe — Jupyter Server extension (Python)                     │
│                                                                                           │
│  SessionManager ── one AgentSession per notebook path, LRU-evicted, idle-timeout           │
│      └── AgentSession: message history, token accounting, cancellation token               │
│              └── Adapter (one of):                                                         │
│                    OllamaAdapter          HTTP /api/chat, streaming NDJSON                 │
│                    TurboFieldfareAdapter  vendored client, CLI or OpenAI-compatible server │
│                    CommandAdapter         arbitrary argv, persistent stdio or one-shot     │
│                    OpenAICompatAdapter    arbitrary base URL + key from env                │
│  PromptAssembler ── loads the five template files, fills the tags                          │
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

## Repository layout

```
jupyter-vybe/
├── package.json                 npm: jupyter-vybe · disabledExtensions declared here
├── pyproject.toml               hatchling + hatch-jupyter-builder
├── tsconfig.json
├── src/                                        ── TypeScript frontend
│   ├── index.ts                 plugin array export
│   ├── tokens.ts                IVybeAgent, IVybeProposals, IVybeHistory, IVybeSettings
│   ├── plugins/
│   │   ├── cellExecutor.ts      provides INotebookCellExecutor
│   │   ├── rounds.ts            Round state machine, command + keybinding registration
│   │   ├── agentClient.ts       WebSocket transport, reconnect, cancellation
│   │   ├── proposals.ts         proposal store, bubble, navigation
│   │   ├── history.ts           sidebar widget
│   │   └── settings.ts
│   ├── notebook/
│   │   ├── mutations.ts         insertBelow / applyProposal / revertRound — sole mutator
│   │   ├── promptCell.ts        prefix detection, [*] state, markdown transform
│   │   ├── snapshot.ts          in-memory toJSON + output filtering
│   │   └── diffSummary.ts       cell-level change summary between snapshots
│   ├── protocol/
│   │   ├── grammar.ts           tool tag definitions, shared with Python via codegen
│   │   ├── streamParser.ts      incremental tag parser
│   │   └── types.ts
│   ├── ui/
│   │   ├── DiffOverlay.tsx      CM6 read-only editor + line decorations
│   │   ├── ReviewControls.tsx   ✓ / ✗ stack floating at the cell's right edge
│   │   ├── StatusBubble.tsx     sticky top-right counter
│   │   └── HistoryEntry.tsx
│   ├── introspect.ts            kernel inspect/complete — the only kernel-facing file
│   └── tokenEstimate.ts
├── style/                       base.css, index.css, theme variables
├── schema/plugin.json           ISettingRegistry schema (see 04)
├── jupyter_vybe/                               ── Python server extension
│   ├── __init__.py              _jupyter_server_extension_points
│   ├── handlers.py              REST + WebSocket handlers
│   ├── session.py               AgentSession, SessionManager
│   ├── assembler.py             PromptAssembler
│   ├── adapters/
│   │   ├── base.py  ollama.py  turbofieldfare.py  command.py  openai_compat.py
│   │   └── vendored/            copied from code-llm/ (see 06)
│   └── prompts/                 the five template files (see 08)
├── ui-tests/                    Galata e2e + fixture notebooks
├── tests/                       pytest for the server extension
└── docs/                        this blueprint
```

## Plugin graph

| Plugin id | provides | requires | optional |
|---|---|---|---|
| `jupyter-vybe:settings` | `IVybeSettings` | `ISettingRegistry` | `ITranslator` |
| `jupyter-vybe:agent` | `IVybeAgent` | `IVybeSettings` | — |
| `jupyter-vybe:cell-executor` | `INotebookCellExecutor` | `IVybeSettings` | `IVybeRounds` |
| `jupyter-vybe:rounds` | `IVybeRounds` | `INotebookTracker`, `IVybeAgent`, `IVybeProposals`, `IVybeSettings` | `IVybeHistory` |
| `jupyter-vybe:proposals` | `IVybeProposals` | `INotebookTracker`, `IEditorExtensionRegistry` | — |
| `jupyter-vybe:history` | `IVybeHistory` | `INotebookTracker` | `ILayoutRestorer` |

`package.json` must contain:

```json
"jupyterlab": {
  "extension": true,
  "outputDir": "jupyter_vybe/labextension",
  "disabledExtensions": ["@jupyterlab/notebook-extension:cell-executor"],
  "schemaDir": "schema"
}
```

### The bootstrap ordering hazard

`jupyter-vybe:cell-executor` is a hard dependency of
`@jupyterlab/notebook-extension:tracker`. If our plugin's `activate` throws or returns a
rejected promise, **the notebook tracker never activates and JupyterLab loses notebooks
entirely.** Therefore `cellExecutor.ts` activate must be synchronous, allocation-only, and
wrapped in a try/catch that falls back to `Object.freeze({ runCell })` — the exact core
behaviour — on any error. All settings and agent wiring is read lazily inside `runCell`, never
at activation. This is the single most important robustness rule in the codebase and is
covered by a dedicated test ([10](10-testing.md) T-BOOT).

## Data flow of one round

1. User presses `Shift+Enter` on a `>` cell. JupyterLab routes through `NotebookActions.run` →
   `Private.runCell` → `executor.runCell(options)` — **ours**.
2. We test `options.cell` against the prefix. Not a prompt cell → `return runCell(options)`
   (the imported core implementation). Done; zero behaviour change.
3. Prompt cell → we do **not** call core. We set `executionState = 'running'` (renders `[*]`),
   emit `onCellExecutionScheduled`, and hand a new `Round` to `rounds.ts`. We return the
   round's terminal promise so `run-all` waits for us in order.
4. `context` builds the snapshot; `agent` opens/reuses the notebook's WebSocket session and
   sends `{prompt, snapshot | diffSummary, settings}`.
5. Server assembles the full prompt from the five templates, calls the adapter, streams tokens
   back. Prose is forwarded verbatim; tool tags are forwarded as parsed events.
6. `mutations` applies each `add_cell` inside a single `sharedModel.transact`; `proposals`
   stages each `propose_edit`.
7. Agent finishes. Prompt cell → markdown (blockquote prompt + prose). Inserted cells become
   the selection. If proposals exist, the bubble appears and review begins.
8. Round closes when the last proposal is resolved; focus scrolls back to the (now markdown)
   prompt cell.

Cancellation (`Ctrl+C`) and reject-all unwind steps 6–8 through `revertRound`, which replays
the round's operation log backwards.
