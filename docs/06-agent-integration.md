# 06 — Agent Integration

## Where adapters run

Adapters are **host-agnostic**: `jupyter_vybe/adapters/` imports no Tornado and knows nothing
about the server extension. That is what lets the same code run in two places
([12](12-decisions.md) D2):

| `agent.host` | Adapter runs in | Reached by the frontend at |
|---|---|---|
| `server` *(default)* | the Jupyter server extension | `<base_url>/jupyter-vybe/ws` |
| `client` *(v1.1)* | `jupyter-vybe-local` on the user's own machine | `http://127.0.0.1:8137/ws` |

**Co-located setups** (Jupyter and model on one laptop) use `server` and never think about this.

**Split setups** — Jupyter on a compute cluster, model on the user's laptop — have two answers.
The v1.0 answer is a reverse tunnel, which needs no extension code at all:

```bash
ssh -R 11434:127.0.0.1:11434 user@cluster    # laptop's Ollama, visible on the cluster
```

The cluster-side `OllamaAdapter` then dials `127.0.0.1:11434` as usual and reaches the laptop.
This is documented as a supported configuration, not a workaround, and the settings pane's health
probe reports which machine actually answered (via Ollama's version endpoint and a hostname
round-trip) so a misconfigured tunnel is visible rather than silently wrong.

The v1.1 answer is the **sidecar**: `python -m jupyter_vybe.local` on the laptop, a ~150-line
transport around the identical adapter package, which the browser talks to directly. It covers
what a tunnel cannot — `claude -p` and CLI binaries need to run where the user's credentials
live. It also sets the `Access-Control-Allow-Private-Network` header that browsers now require
for a page to reach localhost, which is why the browser cannot simply call Ollama itself (see
[13](13-risks.md) R12).

`agent.host` ships in the settings schema from v1.0 so enabling the sidecar is configuration
rather than migration.

## Server extension surface

`jupyter_vybe` registers via `_jupyter_server_extension_points` and mounts four routes under the
server's base URL, all authenticated by the standard `@tornado.web.authenticated` +
XSRF machinery (inherited from `ExtensionHandlerMixin` / `APIHandler`):

| Route | Method | Purpose |
|---|---|---|
| `/jupyter-vybe/agents` | GET | Discover configured adapters and their reachability. Drives the settings dropdown. |
| `/jupyter-vybe/health` | GET | Per-adapter probe (Ollama `/`, TurboFieldfare `/health`, `which` for commands). |
| `/jupyter-vybe/history` | GET/POST | Optional durable history (see [05](05-history-pane.md)). |
| `/jupyter-vybe/ws` | WebSocket | The agent channel. One connection per open notebook. |

The package ships **no kernel-facing code at all**: `jupyter_client`, `nbclient`, and
`ipykernel` are absent from its dependency list, and a CI check asserts they are never imported
transitively by `jupyter_vybe.*`. That converts invariant #1 from a promise into a build gate.

## Session model

```python
class AgentSession:
    notebook_path: str            # identity; survives kernel restarts and tab moves
    adapter: Adapter
    messages: list[Message]       # empty when context.persistent is False
    last_snapshot: NotebookSnapshot | None   # for diff summaries
    tokens_used: int
    cancel: asyncio.Event
```

`SessionManager` holds an LRU of sessions keyed by notebook path, evicting after
`VYBE_SESSION_IDLE_SECONDS` (default 1800) or when the count exceeds 8. Eviction tears down the
adapter, which for `CommandAdapter` means closing stdin and reaping the child.

Renaming a notebook re-keys the session (the frontend sends the new path on the `hello` frame).
Closing the notebook tab sends `bye`; the session survives briefly so a reload does not lose
context.

## WebSocket protocol

Client → server:

```jsonc
{"op":"hello",   "notebookPath":"…", "agentId":"…", "persistent":true}
{"op":"prompt",  "roundId":"…", "prompt":"…",
                 "snapshot":{…} | "diff":{…},          // see 09
                 "kernelInfo":{"name":"python3","language":"python","version":"3.14"},
                 "globalPrompt":"…", "limits":{"maxInsertions":8,"maxProposals":12}}
{"op":"toolResult","roundId":"…","callId":"…","ok":true,"result":"…"}   // inspect/complete answers
{"op":"cancel",  "roundId":"…"}
{"op":"reset"}                                          // clear message history
```

Server → client:

```jsonc
{"op":"status", "roundId":"…", "state":"assembling|generating|awaiting_tool"}
{"op":"prose",  "roundId":"…", "delta":"…"}             // streamed non-tool text
{"op":"tool",   "roundId":"…", "callId":"…", "name":"add_cell", "args":{…}}
{"op":"usage",  "roundId":"…", "promptTokens":1234, "completionTokens":56}
{"op":"done",   "roundId":"…", "stopReason":"end|limit|cancelled|error"}
{"op":"error",  "roundId":"…", "message":"…", "retriable":true}
```

Tool *parsing* happens on the server (one parser, in Python, shared by every adapter); tool
*execution* happens in the browser (only the browser has the notebook). `add_cell` and
`propose_edit` are fire-and-forget from the server's perspective. `ipython_help` and
`complete` block the stream: the server emits `awaiting_tool`, pauses generation, waits for a
`toolResult`, appends it to the message history, and resumes. `agentTools.maxToolTurns` bounds
that loop.

## Adapters

All implement:

```python
class Adapter(Protocol):
    id: str; label: str; context_window: int; supports_native_tools: bool
    async def health(self) -> HealthStatus: ...
    async def stream(self, messages: list[Message], cancel: asyncio.Event) -> AsyncIterator[str]: ...
    async def close(self) -> None: ...
```

### OllamaAdapter — default

`POST {host}/api/chat` with `{"model": …, "messages": […], "stream": true}`, consuming NDJSON
`message.content` deltas. Chat (not `/api/generate`) because it gives us native multi-turn
history for `context.persistent` and for the tool loop.

Auto-start behaviour is ported from `code-llm/run.sh`: if `GET {host}/` fails, spawn
`ollama serve` detached with output to a log, poll for up to 30s, then proceed. Ollama unloads
the model after ~5 min idle, so an idle session costs nothing.

Registered entries: `qwen2.5-coder:7b` (default, 32K context), `qwen2.5-coder:14b`,
`qwen2.5-coder:3b`. `supports_native_tools = False` — the Ollama model card for
qwen2.5-coder advertises text only, no tools capability, which is precisely why the tool
protocol is text-based ([07](07-tool-protocol.md), [12](12-decisions.md) D3).

### TurboFieldfareAdapter — Gemma 4 26B-A4B

Vendored from `/Users/rrs/Desktop/thoughtcode/code-llm/` into
`jupyter_vybe/adapters/vendored/`, per PROMPT §5's "copy the necessary pieces". Specifically:

| Source | Destination | Change |
|---|---|---|
| `turbofieldfare_client.py` | `vendored/turbofieldfare_client.py` | refactored from a stdout-printing `main()` into an async generator; the `resolve_cli_bin` / `resolve_model_path` probe order is kept verbatim |
| `setup_turbofieldfare.sh` | `vendored/setup_turbofieldfare.sh` | unchanged; surfaced as a "set up TurboFieldfare" button in settings |
| `README_turbofieldfare.md` | `docs/vendor/` | reference for the Gemma turn-token format |

Prefers `TurboFieldfareServer`'s OpenAI-compatible `/v1/chat/completions` when `/health` answers
(streaming SSE), falling back to `TurboFieldfareCLI --messages-file` — the same `auto` mode the
existing client implements. Gemma 4 has **no system role**: the assembled system prompt is
prepended to the first user turn, as `README_turbofieldfare.md` documents. Context window 128K
(configurable); `supports_native_tools = False`.

The vendored copy is pinned and a `make sync-vendor` target diffs it against the original so
upstream fixes are visible rather than silently divergent.

### CommandAdapter — `claude -p`, or any binary

Two modes:

- **one-shot** (default): spawn `argv + [prompt]` per turn, read stdout to EOF. History is
  replayed by re-serialising the transcript into the prompt. Robust, works with anything.
- **persistent**: keep the child alive, write a JSON line per turn to stdin, read newline
  -delimited JSON from stdout. Enables true persistent chat as PROMPT §5 requests, for tools
  that support a streaming JSON protocol (`claude -p --input-format stream-json
  --output-format stream-json`). Health-checked at session start; falls back to one-shot with a
  warning in the settings pane if the handshake fails.

`argv` is parsed with `shlex.split` and executed **without a shell** (`shell=False`), no
user-supplied string ever reaches `/bin/sh`. Environment is the server's, plus
`agent.apiKeyEnv` passthrough. The command is user-configured on their own machine, so this is
a footgun-avoidance measure, not a security boundary.

`supports_native_tools` is set from a per-command config flag. Note that in v1.0 this capability
is **declared but unused** — the native tool path is deferred ([12](12-decisions.md) D3); every
adapter goes through the tagged-text protocol.

### OpenAICompatAdapter

`POST {endpoint}/v1/chat/completions`, SSE streaming, key read from `os.environ[apiKeyEnv]`.
Covers vLLM, llama.cpp server, LM Studio, and hosted providers. `supports_native_tools = True`
by default, overridable — though unused in v1.0 ([12](12-decisions.md) D3).

## Cancellation and failure

- `cancel` sets the session's `asyncio.Event`; adapters check it between chunks and terminate
  the HTTP response or kill the child process group.
- Adapter errors become `{"op":"error", retriable}` frames. The frontend shows them in the
  status bubble and the history pane, then CANCELs the round — the notebook is never left in a
  half-applied state, because `revertRound` replays the operation log.
- A dropped WebSocket during `RUNNING` is treated as a cancel; the frontend reconnects and
  re-sends `hello` but never auto-retries the prompt (silent duplicate generation is worse than
  a visible failure).
