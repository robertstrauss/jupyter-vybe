# 04 — Settings

Delivered as a standard `ISettingRegistry` schema at `schema/settings.json`. **The file name is
load-bearing**: JupyterLab keys a schema by `<package>:<basename>`, so it must match the id of the
plugin that loads it (`jupyter-vybe:settings`). It was `plugin.json` for a long time, which meant
`registry.load('jupyter-vybe:settings')` found nothing and every setting silently sat at its
default — the first Galata run is what caught it. Renaming either side without the other brings
it straight back.

The schema means the pane
is reachable three ways with no custom UI work: **Settings ▸ Settings Editor ▸ Jupyter Vybe**,
the Advanced JSON editor, and the command palette (`Jupyter Vybe: Open Settings`). The extension
additionally adds a gear item to the status bubble and a launcher entry, both of which just
invoke `settingeditor:open`.

A small number of controls need richer widgets than the default schema renderer produces. Those
are registered through `IFormRendererRegistry` with `fieldRenderer` entries for the specific
properties — the supported JupyterLab 4 path for custom settings widgets, no forked editor.
**Built** (`src/plugins/settingsRenderers.tsx`), for exactly three cases, because a custom
renderer is a maintenance cost and a schema is free:

- **`agent`** — the list comes from `GET /jupyter-vybe/agents`, and whether the chosen one is
  _reachable_ cannot be expressed in a schema at all. The probe result names the host that
  answered, since a tunnel aimed at the wrong machine otherwise looks exactly like a working one
  ([13](13-risks.md) R12).
- **`trigger.prefix`** — a prefix that parses as code silently swallows real code cells, and
  runtime is a bad time to discover that. The verdict shows as you type.
- **`agent.command` / `agent.endpoint`** — each applies to one adapter and is noise for everyone
  else, so each is labelled with the agent it belongs to.

The context meter is _not_ one of these: it lives in the status bubble, where the user already is
while a request is being built, rather than in a pane they would have to go and open.

## Schema

| Key                              | Type    | Default                              | Control          | Notes                                                                                                                                                           |
| -------------------------------- | ------- | ------------------------------------ | ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `enabled`                        | boolean | `true`                               | toggle           | Master switch. Off ⇒ executor delegates everything to core immediately.                                                                                         |
| `agent.id`                       | string  | `"ollama-qwen25-coder-7b"`           | dropdown         | Populated at runtime from `GET /jupyter-vybe/agents`.                                                                                                           |
| `agent.host`                     | enum    | `"server"`                           | dropdown         | `server` \| `client`. Where the adapter runs — see [06](06-agent-integration.md). `client` requires the v1.1 sidecar; shown disabled until it is detected.      |
| `agent.sidecarUrl`               | string  | `"http://127.0.0.1:8137"`            | text             | Sidecar address when `agent.host == "client"`.                                                                                                                  |
| `agent.command`                  | string  | `""`                                 | text             | argv for the `command` adapter, e.g. `claude -p`. Split with `shlex`, run without a shell.                                                                      |
| `agent.commandPersistent`        | boolean | `false`                              | toggle           | Keep the command alive between turns (stream-json). Falls back to one-shot if the handshake fails.                                                              |
| `agent.endpoint`                 | string  | `"http://127.0.0.1:8000"`            | text             | Base URL for the `openai-compat` adapter.                                                                                                                       |
| `agent.model`                    | string  | `""`                                 | text             | Model id as the endpoint names it in `/v1/models`.                                                                                                              |
| `agent.apiKeyEnv`                | string  | `"VYBE_API_KEY"`                     | text             | **Name of an env var**, never the key itself — keys never enter settings JSON.                                                                                  |
| `agent.timeoutSeconds`           | integer | `120`                                | number           | Per-turn wall clock.                                                                                                                                            |
| `context.persistent`             | boolean | `true`                               | toggle           | On ⇒ session keeps message history and sends notebook _diffs_; off ⇒ every prompt is single-shot with a full snapshot.                                          |
| `context.windowTokens`           | integer | `32768`                              | number           | Model context size; drives the meter and truncation.                                                                                                            |
| `context.reserveTokens`          | integer | `2048`                               | number           | Headroom kept for the reply.                                                                                                                                    |
| `context.cellWindow`             | integer | `0`                                  | number           | 0 = whole notebook. Otherwise ±N cells of full fidelity around the prompt cell; others source-only.                                                             |
| `context.maxOutputChars`         | integer | `2000`                               | number           | Per-output truncation; see [09](09-context-management.md).                                                                                                      |
| `context.includeOutputs`         | boolean | `true`                               | toggle           | Strip all outputs when false.                                                                                                                                   |
| `trigger.prefix`                 | string  | `">"`                                | text + validator | Rejected if reserved or parseable as code ([02](02-prompt-cells.md)).                                                                                           |
| `prompts.global`                 | string  | _(see [08](08-prompt-templates.md))_ | textarea         | The user-editable global prompt.                                                                                                                                |
| `prompts.showAssembled`          | boolean | `false`                              | toggle           | Debug: log the fully assembled prompt to the browser console.                                                                                                   |
| `agentTools.kernelIntrospection` | enum    | `"inspect+complete"`                 | dropdown         | `off` \| `inspect-only` \| `inspect+complete`. See [12](12-decisions.md) D1.                                                                                    |
| `agentTools.maxInsertions`       | integer | `8`                                  | number           | Hard cap per round.                                                                                                                                             |
| `agentTools.maxProposals`        | integer | `12`                                 | number           | Hard cap per round.                                                                                                                                             |
| `agentTools.maxToolTurns`        | integer | `6`                                  | number           | Introspection loop bound; prevents runaway.                                                                                                                     |
| `review.autoScroll`              | boolean | `true`                               | toggle           | Smooth scroll between proposals.                                                                                                                                |
| `review.acceptKey`               | enum    | `"yn"`                               | dropdown         | `yn` \| `enter-backspace`. Default `Y`/`N`; `Enter` always opens a merged edit ([03](03-diff-ui.md), [12](12-decisions.md) D6).                                 |
| `review.mergeCommentStyle`       | enum    | `"auto"`                             | dropdown         | `auto` (from `language_info`) \| `hash` \| `slashes` \| `none`. Comment syntax for the `Enter` merge.                                                           |
| `history.maxEntries`             | integer | `200`                                | number           | How many entries the pane renders at once; older entries page in on scroll. Does not bound what is stored.                                                      |
| `history.persist`                | boolean | **`true`**                           | toggle           | The prompt record is durable by default — deleting a markdown prompt cell must not lose the prompt ([05](05-history-pane.md)). Off makes history session-only.  |
| `history.location`               | enum    | `"user"`                             | dropdown         | `user` (`~/.jupyter/vybe/history/`) \| `adjacent` (a `.vybe/` directory beside the notebook, so the record is version-controlled with the work).                |
| `completion.enabled`             | boolean | `false`                              | toggle           | Ghost text on demand. Off by default; when on, still asks nothing until the trigger key.                                                                        |
| `completion.agentId`             | enum    | `""`                                 | dropdown         | Which agent writes suggestions. Blank uses the cell-generation agent. Suggestions are asked for far more often, so a smaller model often belongs here.          |
| `completion.triggerKey`          | string  | `"Alt-Tab"`                          | text             | CodeMirror key notation. `Alt-Tab` is taken by the window manager off a Mac, hence settable rather than fixed.                                                  |
| `completion.maxLines`            | integer | `2`                                  | number           | Lines per suggestion. Not a ceiling on the total: pressing the trigger again extends the suggestion, so this is how much you are asked to read before deciding. |
| `completion.timeoutSeconds`      | number  | `8`                                  | number           | A prediction was asked for deliberately, so it is worth waiting longer for than one that arrived uninvited.                                                     |
| `privacy.confirmRemoteAgents`    | boolean | `true`                               | toggle           | One confirmation per notebook before notebook contents leave the machine.                                                                                       |
| `advanced.logLevel`              | enum    | `"warn"`                             | dropdown         | `silent` \| `warn` \| `info` \| `debug`                                                                                                                         |

## How the pane renders

Each group is drawn by our own renderer rather than by JupyterLab's default object field
(`src/plugins/settingsRenderers.tsx`), for three things the default cannot do:

- **Irrelevant controls are greyed, with the reason beside them.** With a local model chosen,
  the command and endpoint fields are inert. Greyed rather than hidden: a control that vanishes
  leaves the user wondering whether they imagined it, and the reason it is off is the setting
  sitting a few rows above.
- **Empty boxes show an example**, not a description of one. `claude -p` in the command box is
  most of what there is to know about that field.
- **Descriptions live behind an (i)**, opening on hover and pinning on click. A panel where a
  third of the text is about agents you are not using is a panel nobody reads.

The global prompt is a textarea pre-filled with the shipped default (`GET
/jupyter-vybe/defaults`), so it can be edited rather than guessed at. Nothing is written to
settings until the user changes something: pinning today's default into their configuration as
a side effect of opening the panel would mean they never see the next one.

Only top-level properties can have renderers — `addRenderer` splits its id at the _last_ dot —
which `scripts/check-invariants.sh` enforces as T-RENDERERS after two renderers were silently
dead that way.

## Read-only indicators in the pane

- **Context meter** — **built, in the status bubble rather than the pane**: while a round is
  generating, the bubble reads `⚡ thinking… 6s` above `1.8k / 28.7k context · trimmed outputs
(3 cells)`. The estimate is the notebook portion, measured by `buildSnapshot`, against the
  budget the truncation ladder works to; what the ladder dropped is named rather than merely
  counted, so an unexpectedly small context is traceable to a cause.

  It sits there because that is where the user is looking while a request is being built, and
  because it is only actionable then — afterwards it is history, which is what the history pane
  is for. A stacked bar in the settings pane would be prettier and less useful.

- **Agent status** — adapter reachability (`GET /jupyter-vybe/health`), resolved model name,
  and for the Ollama adapter whether the daemon is running with a one-click "start it" that
  calls the server extension (the server runs `ollama serve` detached, exactly as `run.sh` does).

## Settings that intentionally do not exist

- Temperature / top-p / sampling. These belong to the adapter config on the server side
  (`~/.jupyter/jupyter_vybe_config.py`), not in a per-user UI — surfacing them invites
  "make it more creative", which is the opposite of this tool's thesis.
- Anything that would let the agent execute code. There is no setting, hidden or otherwise.
