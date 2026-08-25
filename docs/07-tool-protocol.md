# 07 — Tool Protocol

## Why a text protocol

The two backends PROMPT names cannot be relied on for native function calling: the Ollama model
card for `qwen2.5-coder` lists text only, with no tools capability, and TurboFieldfare's
OpenAI-compatible surface makes no tool guarantees. A protocol that only works on frontier
hosted models would make the default local configuration useless.

There is also a reason to prefer tagged text even where native tools *are* available: our
arguments are multi-line source code, and native tool calls carry arguments as JSON strings —
every newline escaped to `\n`, every quote and backslash escaped, indentation trapped inside a
one-line literal. Models corrupt code-inside-JSON noticeably more often than code written
literally. The tagged format carries source verbatim, with nothing to escape and nothing to
unescape.

So the protocol is tagged text, parsed incrementally from the token stream, for **every** adapter.
`supports_native_tools` remains a declared capability on the `Adapter` protocol so a native path
can be added later without restructuring, but it is not implemented in v1.0. See
[12-decisions.md](12-decisions.md) D3 for the full trade and the evidence that would reopen it.

## Grammar

XML-ish, because it survives markdown fencing, is unambiguous inside code (Python has no `<vybe:`
token), and degrades legibly if the model half-fails. Delimiters are on their own lines.

```
<vybe:add_cell type="code">
import numpy as np
</vybe:add_cell>

<vybe:add_cell type="markdown">
## Spectral analysis
</vybe:add_cell>

<vybe:propose_edit cell="a3f91c2e" note="vectorise the loop">
def rolling_mean(x, w):
    """Rolling mean via cumulative sums."""
    c = np.cumsum(np.insert(x, 0, 0))
    return (c[w:] - c[:-w]) / w
</vybe:propose_edit>

<vybe:ipython_help target="np.fft.rfft" />

<vybe:complete prefix="df.gro" />
```

Rules the parser enforces (violations are reported back to the agent as a `protocol_error` turn,
which is what makes small models recoverable rather than useless):

- Content is **verbatim cell source**. No markdown fences inside, no escaping. The first line
  after the open tag and the last line before the close tag are stripped if blank.
- `type` ∈ {`code`, `markdown`}, default `code`.
- `cell` must be an nbformat cell id present in the snapshot the agent was given.
- `propose_edit` content is the **complete new source** of the cell, not a patch. Diffing is our
  job, not the model's — this removes an entire class of malformed-patch failures.
- `add_cell` carries no position argument. Position is defined by the protocol (below the prompt
  cell, in emission order); the model cannot express anything else.
- Counts are capped by `agentTools.maxInsertions` / `maxProposals`. Excess calls are dropped and
  a `limit` stop reason is reported.

## Incremental parser

`src/protocol/streamParser.ts` (mirrored in `jupyter_vybe/protocol.py`) is a small state machine
over the token stream:

```
TEXT ──"<vybe:"──▶ TAG_NAME ──▶ ATTRS ──"/>"──▶ TEXT   (self-closing tool)
                                    └──">"──▶ BODY ──"</vybe:name>"──▶ TEXT
```

Properties that matter:

- **Streaming.** Cells appear in the notebook as the model writes them, which on a 7B model over
  a 30-line cell is the difference between "instant" and "did it hang?".
- **Partial-tag safety.** A buffer boundary mid-tag never emits a fragment as prose.
- **Prose passthrough.** Everything in `TEXT` is forwarded as `prose` deltas and ends up in the
  markdown prompt cell (PROMPT §6: "any non-tool contained output should be written out as text
  in the prompt cell").
- **Unterminated body at stream end** ⇒ the partial cell is discarded and a `truncated` stop
  reason is reported. Never apply half a cell.

A shared golden-file test suite (`tests/protocol/*.txt` → expected event JSON) runs against both
the TS and the Python implementation, so the two cannot drift.

## The agent loop

```
assemble prompt ──▶ stream ──┬─ prose ──────────────────▶ forward to client
                             ├─ add_cell / propose_edit ─▶ forward to client (fire and forget)
                             └─ ipython_help / complete ─▶ pause stream
                                                            ├─ ask client
                                                            ├─ client calls kernel inspect/complete
                                                            ├─ append result as a tool message
                                                            └─ resume (≤ maxToolTurns)
```

## Introspection tools and the kernel question

PROMPT §"Agent tools" grants the agent IPython help and completion while forbidding it from
"executing anything through the kernel or sending execution requests". Those are compatible:
`inspect_request` and `complete_request` are separate Jupyter messaging protocol messages from
`execute_request`. They do not run user code, do not create an execution entry, and do not
mutate the kernel namespace — they read it. Getting the docstring for `np.fft.rfft` or the
attributes of a live `df` requires exactly these.

All of it lives in `src/introspect.ts`, the only file in the codebase permitted to touch a
kernel connection, and it exposes precisely two functions:

```ts
export async function inspect(session, target: string): Promise<string>   // requestInspect, detail_level 0
export async function complete(session, prefix: string): Promise<string[]> // requestComplete
```

Guards, all of them enforced here rather than by prompting:

- A lint rule forbids importing `KernelConnection`/`ISessionContext` anywhere else.
- A CI grep asserts the string `requestExecute` appears nowhere in `src/`.
- `agentTools.kernelIntrospection` can disable both at runtime; when `off`, the tools are absent
  from the tooling prompt entirely so the agent never learns they exist.
- Results are truncated (2000 chars for help, 60 items for completion) before entering context.
- If no kernel is running, the tools return `"no kernel available"` — they never start one.

This is a genuine interpretation call and is escalated as decision **D1**.

## Apply pipeline

Every tool event from the server passes through validation before reaching
`mutations.ts`:

1. Round is still live and not cancelled.
2. Target cell id exists (for `propose_edit`); anchor prompt cell still exists.
2b. Target is **not** a settled prompt cell — a markdown cell whose source begins with the
   trigger-prefix blockquote. Rewriting the user's prompt history is out of bounds; rejected with
   a protocol error ([03](03-diff-ui.md)).
3. Caps not exceeded.
4. For `propose_edit`: capture current source as `before`. If the agent's `after` is identical,
   drop it silently — models frequently "edit" a cell into itself.
5. Source is a string, non-empty after trimming, and under a sanity cap (64KB).

Only then does the corresponding `mutations.ts` function run, inside the round's transaction.
