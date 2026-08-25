# 10 — Testing

The hard part of testing this project is that the interesting behaviour is *absence*: no kernel
message, no metadata key, no leftover mutation. Those get dedicated, named gates rather than
being folded into feature tests.

## Invariant gates (CI-blocking)

| Id | Gate | How |
|---|---|---|
| **T-KERNEL-1** | `requestExecute` appears nowhere in `src/` | CI grep |
| **T-KERNEL-2** | `jupyter_vybe` never imports `jupyter_client`, `ipykernel`, `nbclient` | import-graph check in pytest |
| **T-KERNEL-3** | A full prompt round with `--debug` logging emits zero `execute_request` messages | Galata e2e capturing the kernel WS |
| **T-KERNEL-4** | A prompt round completes with **no kernel started at all** | Galata: open notebook, no-kernel session, run `>` cell |
| **T-HIST** | Deleting a settled prompt cell loses nothing: the round is still in the store, still in the pane, still in the agent's context on the next turn | Galata + a store assertion |
| **T-KERNELINFO** | Kernel info survives the full truncation ladder; an empty R notebook gets R code | jest on the assembler, plus one bench prompt |
| **T-META** | Round-trip a notebook through a full round; `toJSON()` contains no key outside nbformat 4.5 | jest + a JSON-schema check against nbformat |
| **T-BOOT** | If `jupyter-vybe:cell-executor` activate throws, notebooks still open and execute normally | jest: force a throw, assert core fallback |
| **T-UNDO** | `Cmd+Z` at the prompt cell after a settled round restores the pre-round `toJSON()` exactly | Galata, deep-equal |
| **T-MUTATE** | No module other than `mutations.ts` imports notebook-model write APIs | custom ESLint rule |

## Unit — TypeScript (jest, via `jlpm test`)

- `promptCell.ts`: prefix detection matrix (leading whitespace, blank lines, multi-char prefixes,
  `>` inside code, prefix in a markdown cell, empty cell, prefix-only cell).
- Prefix validator: every reserved prefix rejected; a table of ~40 candidate prefixes with
  expected verdicts.
- `streamParser.ts`: golden files shared with Python (see below), plus adversarial inputs —
  tag split across every possible chunk boundary, unterminated body, nested `<vybe:` inside a
  body, tags inside markdown fences, attribute quoting variants.
- `diffSummary.ts`: add/edit/remove/move/run permutations; id reuse; simultaneous move+edit.
- `snapshot.ts`: one fixture notebook per filtering rule row.
- Diff/hunk computation: empty before, empty after, identical, whitespace-only, 1000-line cell.

## Unit — Python (pytest)

- `protocol.py` against the same golden files as the TS parser. A test asserts both
  implementations produce byte-identical event JSON for every fixture — this is what keeps the
  two from drifting.
- `assembler.py`: tag substitution, missing-tag detection, persistent vs. single-shot shape,
  introspection tools templated in/out.
- Adapters against local fakes: a fake Ollama NDJSON server, a fake SSE server, a fake
  persistent-stdio child. Covers mid-stream disconnect, malformed JSON line, immediate EOF,
  process that never exits (timeout), process that exits nonzero.
- `SessionManager`: eviction, idle timeout, rename re-keying, `snapshotSeq` resync.

## Integration — frontend against a scripted agent

A `MockAdapter` replays a scripted token stream from a fixture file, so the whole frontend
pipeline is testable deterministically with no model. Fixture scenarios:

`only-prose`, `single-cell`, `eight-cells`, `cells-plus-3-proposals`, `proposal-on-missing-cell`,
`over-cap`, `truncated-mid-tag`, `malformed-attrs`, `edit-to-identical-source`,
`introspection-then-cells`, `immediate-error`, `slow-stream` (for cancellation).

## E2E — Galata

The scaffold ships Galata; these are the flows that must pass on 4.6.3:

1. Type `> plot a sine wave`, `Shift+Enter` → 2 cells appear below, selected as a group; prompt
   cell is markdown containing the `>` blockquote.
2. Same, with 3 proposals → bubble reads 3; `Alt+E` jumps to the first; `Enter` accepts and
   advances; count decrements; after the last, focus lands back on the prompt cell.
3. `Alt+Shift+E` reject-all → notebook is byte-identical to pre-round, prompt cell is a code
   cell again with the original text.
4. `Ctrl+C` mid-stream → same restoration; server session shows the round cancelled.
5. Run-all over a notebook mixing prompt cells and code cells → code cells execute in order,
   prompt cells do not, execution counts are contiguous across the code cells only.
6. Disable the master switch → `>` cells run as ordinary code (and fail with a `SyntaxError`,
   proving delegation is real).
7. Change the prefix to `>>`, verify old `>` cells now execute as code and `>>` cells prompt.
8. History pane: open, see entries, click a proposal, press `v` in the notebook, correct cell
   pastes.
9. With a round active, enter edit mode on a cell carrying a pending proposal and type `y n` —
   the characters are inserted, no proposal resolves, the bubble count is unchanged. `Esc` then
   `Y` resolves it. (Guards the one binding that could make the tool unusable.)
10. Proposal on a rendered markdown cell → cell unrenders to show the diff, re-renders on accept;
   on reject it re-renders with the original source.
11. Agent proposes an edit to a settled prompt cell → rejected by the pipeline, protocol error
   returned, notebook unchanged.
12. Two notebooks open, prompt in each → sessions are independent; snapshot of one never appears
   in the other's assembled prompt.

### Regression: example regurgitation

`bench/no-example-copying`: run "add a function to compute the rolling mean of x" against a
notebook containing only `import numpy as np`, and assert the reply contains none of the literal
identifiers used in `tooling.md`'s examples. This caught a real failure where the 7B model
returned the tooling prompt's own example verbatim ([08](08-prompt-templates.md)); it is cheap
and it guards a class of bug that is invisible in unit tests.

## Manual / model-quality checks

Automated tests cannot tell whether the prompts are any good. A scored bench of 20 realistic
research prompts (load a CSV and describe it; fit a sklearn model; refactor this loop; add
type hints; make this plot publication-ready; …) is run against `qwen2.5-coder:7b`,
`qwen2.5-coder:14b`, and Gemma 4 at each milestone that touches
[08](08-prompt-templates.md), scored on: tool-syntax validity, cells runnable as emitted,
unrequested extras, and prose discipline. Results tracked in `docs/bench/RESULTS.md`. This is
the only reliable feedback loop on prompt engineering, and it needs to exist before the prompts
are tuned, not after.
