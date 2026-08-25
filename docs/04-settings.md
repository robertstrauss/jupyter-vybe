# 04 — Settings

Delivered as a standard `ISettingRegistry` schema at `schema/plugin.json`, which means the pane
is reachable three ways with no custom UI work: **Settings ▸ Settings Editor ▸ Jupyter Vybe**,
the Advanced JSON editor, and the command palette (`Jupyter Vybe: Open Settings`). The extension
additionally adds a gear item to the status bubble and a launcher entry, both of which just
invoke `settingeditor:open`.

A small number of controls need richer widgets than the default schema renderer produces (agent
dropdown populated from the server, the context meter, the prefix validator). Those are
registered through `IFormRendererRegistry` with `fieldRenderer` entries for the specific
properties — the supported JupyterLab 4 path for custom settings widgets, no forked editor.

## Schema

| Key | Type | Default | Control | Notes |
|---|---|---|---|---|
| `enabled` | boolean | `true` | toggle | Master switch. Off ⇒ executor delegates everything to core immediately. |
| `agent.id` | string | `"ollama-qwen25-coder-7b"` | dropdown | Populated at runtime from `GET /jupyter-vybe/agents`. |
| `agent.host` | enum | `"server"` | dropdown | `server` \| `client`. Where the adapter runs — see [06](06-agent-integration.md). `client` requires the v1.1 sidecar; shown disabled until it is detected. |
| `agent.sidecarUrl` | string | `"http://127.0.0.1:8137"` | text | Sidecar address when `agent.host == "client"`. |
| `agent.customCommand` | string | `""` | text | argv for the `command` adapter, e.g. `claude -p --output-format stream-json`. Shown only when `agent.id == "command"`. |
| `agent.endpoint` | string | `""` | text | Base URL for the `openai-compat` adapter. |
| `agent.apiKeyEnv` | string | `"VYBE_API_KEY"` | text | **Name of an env var**, never the key itself — keys never enter settings JSON. |
| `agent.timeoutSeconds` | integer | `120` | number | Per-turn wall clock. |
| `context.persistent` | boolean | `true` | toggle | On ⇒ session keeps message history and sends notebook *diffs*; off ⇒ every prompt is single-shot with a full snapshot. |
| `context.windowTokens` | integer | `32768` | number | Model context size; drives the meter and truncation. |
| `context.reserveTokens` | integer | `2048` | number | Headroom kept for the reply. |
| `context.cellWindow` | integer | `0` | number | 0 = whole notebook. Otherwise ±N cells of full fidelity around the prompt cell; others source-only. |
| `context.maxOutputChars` | integer | `2000` | number | Per-output truncation; see [09](09-context-management.md). |
| `context.includeOutputs` | boolean | `true` | toggle | Strip all outputs when false. |
| `trigger.prefix` | string | `">"` | text + validator | Rejected if reserved or parseable as code ([02](02-prompt-cells.md)). |
| `prompts.global` | string | *(see [08](08-prompt-templates.md))* | textarea | The user-editable global prompt. |
| `prompts.showAssembled` | boolean | `false` | toggle | Debug: log the fully assembled prompt to the browser console. |
| `agentTools.kernelIntrospection` | enum | `"inspect+complete"` | dropdown | `off` \| `inspect-only` \| `inspect+complete`. See [12](12-decisions.md) D1. |
| `agentTools.maxInsertions` | integer | `8` | number | Hard cap per round. |
| `agentTools.maxProposals` | integer | `12` | number | Hard cap per round. |
| `agentTools.maxToolTurns` | integer | `6` | number | Introspection loop bound; prevents runaway. |
| `review.autoScroll` | boolean | `true` | toggle | Smooth scroll between proposals. |
| `review.acceptKey` | enum | `"yn"` | dropdown | `yn` \| `enter-backspace`. Default `Y`/`N`; `Enter` always opens a merged edit ([03](03-diff-ui.md), [12](12-decisions.md) D6). |
| `review.mergeCommentStyle` | enum | `"auto"` | dropdown | `auto` (from `language_info`) \| `hash` \| `slashes` \| `none`. Comment syntax for the `Enter` merge. |
| `history.maxEntries` | integer | `200` | number | How many entries the pane renders at once; older entries page in on scroll. Does not bound what is stored. |
| `history.persist` | boolean | **`true`** | toggle | The prompt record is durable by default — deleting a markdown prompt cell must not lose the prompt ([05](05-history-pane.md)). Off makes history session-only. |
| `history.location` | enum | `"user"` | dropdown | `user` (`~/.jupyter/vybe/history/`) \| `adjacent` (a `.vybe/` directory beside the notebook, so the record is version-controlled with the work). |
| `privacy.confirmRemoteAgents` | boolean | `true` | toggle | One confirmation per notebook before notebook contents leave the machine. |
| `advanced.logLevel` | enum | `"warn"` | dropdown | `silent` \| `warn` \| `info` \| `debug` |

## Read-only indicators in the pane

- **Context meter** — a horizontal bar: notebook snapshot / tooling+universal prompt / global
  prompt / history, stacked as segments against `context.windowTokens`, with the numeric total.
  Recomputes on notebook change and after every round. Turns amber above 75%, red above 90%,
  at which point truncation is already active and the pane says which cells were dropped.
- **Agent status** — adapter reachability (`GET /jupyter-vybe/health`), resolved model name,
  and for the Ollama adapter whether the daemon is running with a one-click "start it" that
  calls the server extension (the server runs `ollama serve` detached, exactly as `run.sh` does).

## Settings that intentionally do not exist

- Temperature / top-p / sampling. These belong to the adapter config on the server side
  (`~/.jupyter/jupyter_vybe_config.py`), not in a per-user UI — surfacing them invites
  "make it more creative", which is the opposite of this tool's thesis.
- Anything that would let the agent execute code. There is no setting, hidden or otherwise.
