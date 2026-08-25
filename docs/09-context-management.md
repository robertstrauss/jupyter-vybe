# 09 — Context Management

## Source of truth: the in-memory model

The snapshot is built from `notebookPanel.content.model.toJSON()` — the live, unsaved
model — never from disk (PROMPT §5 is emphatic about this). Consequences:

- Unsaved edits are visible to the agent immediately.
- The snapshot is available with no kernel and no file on disk (an untitled notebook works).
- Cell ids come from nbformat 4.5 `id` fields, which the shared model maintains, so they are
  stable references for `propose_edit` even as cells move.

## Filtering rules

Implemented in `src/notebook/snapshot.ts`, documented in `prompts/filtering.md`, and pinned by a
test that compares the two.

| Field | Rule |
|---|---|
| `nbformat`, `nbformat_minor` | kept |
| `metadata.kernelspec`, `metadata.language_info` | kept — the agent needs the language and version |
| all other notebook metadata | dropped |
| cell `id`, `cell_type`, `source` | kept, always, in full |
| cell `execution_count` | kept — signals what has actually run |
| cell `metadata` | dropped except `tags` |
| `attachments` | dropped, replaced by `"<attachment: name>"` |
| output `stream` | kept, truncated to `context.maxOutputChars` (default 2000), middle-elided as `… <N chars elided> …` |
| output `execute_result` / `display_data`, `text/plain` | kept, same truncation |
| output `text/html`, `text/markdown`, `text/latex` | dropped if a `text/plain` alternative exists; else truncated to 500 chars |
| output `image/*`, `application/pdf`, any base64 bundle | dropped, replaced by `"<image 640x480 png>"` |
| `application/vnd.*` (plotly, vega, widgets) | dropped, replaced by `"<rich output: mimetype>"` |
| output `error` | kept — `ename`, `evalue`, and the **last 20 lines** of traceback |

Rationale for the two least obvious rules: error outputs are the highest-signal-per-token content
in a notebook and are exactly what a "fix this" prompt is about, so they are preserved
aggressively. Images are pure token cost with no signal for a text model, so they are annotated
rather than described.

The result is serialised as compact JSON (no indentation), which measures ~15% cheaper in tokens
than pretty-printed and costs nothing in model comprehension.

## Truncation under pressure

When the estimated total exceeds `context.windowTokens - context.reserveTokens`, drop in this
order until it fits, recording what was dropped so the settings pane and status bubble can say so:

1. Outputs of cells far from the prompt cell (beyond `context.cellWindow`, when set).
2. All outputs except errors.
3. Sources of markdown cells far from the prompt cell.
4. Sources of code cells far from the prompt cell, replaced by a one-line signature summary
   (`def foo(a, b): …` / `class Bar: …` / `import …`) — this preserves the namespace the agent
   needs for reuse at ~5% of the tokens.
5. Oldest conversation turns (persistent mode only), never the current request.

The prompt cell, its ±3 neighbours, and every import-bearing cell are never dropped.

**Kernel information is never dropped, at any stage.** It lives in the template outside
`{{NOTEBOOK_JSON}}`, so the ladder cannot reach it, and it is re-sent on diff-only turns. It is
two lines and it is what tells the agent which language to write and which idiom to fall back on
when there is no code left to infer style from — which is exactly the state aggressive truncation
produces ([08](08-prompt-templates.md)).

Note the interaction with style inference: truncation stage 4 replaces distant cell bodies with
signatures, and signatures still carry identifier casing — the dominant style signal. So style
inference degrades gracefully rather than failing, and only an empty or all-markdown notebook
actually falls through to the language default.

## Diff summaries for persistent sessions

PROMPT §6: "don't repeat the entire notebook, use the proper edit/difference based summary".

After each round, `last_snapshot` is retained server-side. The next turn sends a cell-level diff:

```markdown
Changes since your last turn:

ADDED   cell 7f3a1b after cell 2c9e (code)
+ import scipy.signal as sig

EDITED  cell 2c9e (code)
- window = 128
+ window = 256

REMOVED cell 91ab (markdown)

RAN     cell 2c9e -> execution_count 12, output changed:
  <stdout truncated> shape: (4096, 3)

MOVED   cell 55dd from index 9 to index 4

UNCHANGED: 23 other cells
```

Line-level `+`/`-` hunks for edits (three lines of context), whole source for additions, id only
for removals. Execution changes are reported separately from source changes because a cell that
merely *ran* is a different signal from a cell that *changed*.

The client sends a monotonically increasing `snapshotSeq`; on mismatch (server restarted, session
evicted, notebook reopened) the server replies `resync` and the client sends a full snapshot. So
the diff path can never silently desynchronise the agent's picture of the notebook — the single
most dangerous failure mode of incremental context.

A full snapshot is also forced every `VYBE_RESYNC_EVERY` turns (default 10), which bounds drift
from accumulated small errors.

## Rendering: a numbered listing, not raw JSON

The snapshot is *collected* as filtered nbformat JSON, but it is *rendered* for the agent as a
numbered listing (`assembler.render_notebook`):

```
[3] code  id=gen-a3f9   <-- the cell directly above the prompt ("the cell above")
    def normalize_x(x):
        return (x - np.mean(x)) / np.std(x)
```

Measured, not assumed. With compact JSON, a 7B asked to "edit the above cell" had to locate and
copy a uuid out of a dense one-line object, and frequently gave up and emitted a modified copy
instead. With the listing — cells numbered from 1, the cell above the prompt called out
explicitly, ids still shown so either form of address works — `propose_edit` compliance on three
edit-shaped requests went to 3/3 each on `qwen2.5-coder:7b`.

`resolve_cell_ref` accepts `"3"`, `"[3]"`, `"cell 3"`, or a raw id, and returns `""` for anything
it cannot resolve, so an unresolvable reference becomes a protocol error rather than an edit to
the wrong cell.

Presentation lives on the server, not in `snapshot.ts`: the frontend supplies data, the assembler
decides how to show it, and the rendering can be tuned without rebuilding the extension.

## Token estimation

The status meter needs a number before the request is sent, so estimation is client-side:
`ceil(chars / 3.6)` for code and `ceil(chars / 4.2)` for prose, calibrated against Qwen's
tokenizer on a corpus of sample notebooks during M3. Actual counts come back on the `usage` frame
and correct the estimate — the meter shows the estimate before a run and the true value after.

This is deliberately not a real tokenizer: shipping one costs ~2MB of WASM and several hundred ms
of startup for a bar that only needs to be right to within 10%.
