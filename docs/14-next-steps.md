# 14 — Next Steps

Captured 2026-08-25 from live use. Ordered by whether the thing is *wrong* or
merely *unpolished* — the diff bug first, because it undermines trust in the
review UI, which is the feature the whole design rests on.

---

## N1 · The diff is not a real diff · **bug, highest priority**

**Observed.** A proposed edit renders the entire new source in green and the
entire old source in red, including lines that did not change. A real unified
diff should show unchanged lines as plain context and colour only the lines
that actually differ.

**Where.** `src/ui/diff.ts` → `diffLines()`, rendered by
`src/ui/DiffOverlay.ts`. The `coalesce()` step already assumes a mix of
`context` / `add` / `del` lines and trims around the changed ones, so if every
line arrives marked added or removed, coalescing has nothing to do and the
whole cell renders as one block.

**Likely causes, in order of suspicion:**

1. **Trailing-newline mismatch.** The agent's body is trimmed of its trailing
   newline (`_trim_body` in `protocol.py`), while the existing cell source may
   keep one. jsdiff then sees the final line as changed, and depending on how
   the parts split, can degenerate into one big remove + one big add.
2. **Whitespace or indentation differences** throughout — the model rewrites
   the cell with different indentation, so genuinely every line differs. This
   would be correct behaviour on wrong input, and the fix belongs in the
   prompt, not the differ.
3. **Line-ending mismatch** (`\r\n` vs `\n`) between the notebook source and
   the model's reply, which makes every line differ invisibly.

**Do first:** dump `before` and `after` with `JSON.stringify` for a proposal
that renders wrongly and compare them character by character. That separates
cause 2 (real difference, differ is right) from causes 1 and 3 (artefact).

**Then:** normalise both sides before diffing — strip a single trailing
newline from each, normalise CRLF to LF — and add a jest test with a
before/after pair differing by one line in the middle, asserting exactly one
`add`, one `del`, and the rest `context`. There is currently no such test,
which is why this shipped.

---

## N2 · Review controls are too small and under-explained · **UX**

- **Accept/reject buttons are too small.** Currently 34×34px in
  `style/base.css` (`.jv-review-button`). Make them substantially larger —
  they are the primary interaction of the whole tool.
- **Nothing says `Enter` modifies.** The buttons label `Y` and `N`, but the
  third verdict — `Enter` to open the merged version and hand-edit it
  (decision D6) — is invisible. Add a line beneath the stack, e.g.
  `Enter to modify`, styled like the existing `.jv-review-key` labels.

---

## N3 · The status bubble is too small and its click is dangerous · **UX, partly a bug**

- **Make it bigger.** It is the only indicator that anything needs review.
- **Add a shortcut to jump to the first edit and begin reviewing**, shown on
  the bubble itself rather than only in the keymap.
- **Clicking must jump to the first edit, never reject.** Right now the bubble
  has two stacked buttons (`src/ui/DiffOverlay.ts` → `StatusBubble`): the main
  one jumps, the small one below it rejects all. At the current size they are
  easy to confuse, so a click intended as "take me there" can discard every
  proposed edit. Jump must be the large, obvious, default target; reject-all
  must be visually separate and hard to hit by accident — ideally with an undo
  path, since it currently discards an entire round's review in one click.

---

## N4 · Measure Gemma 4 with TurboFieldfareServer · **performance**

Gemma 4 is the better model on the tool bench — 5/5 with zero fence fallbacks,
against 13/15 with 2 fences for `qwen2.5-coder:7b`. The blocker is latency:
roughly 90 s per call, because `TurboFieldfareAdapter` falls back to the CLI,
which reloads the 13 GB model on every invocation.

`TurboFieldfareServer` keeps the model resident and the adapter already
prefers it when `/health` answers — that path has never been exercised.

```bash
~/.turbofieldfare/turbo-fieldfare/.build/release/TurboFieldfareServer \
  --model /Users/rrs/Desktop/thoughtcode/code-llm/scratch/gemma4.gturbo \
  --port 8080 --max-context 32768 --prompt-cache-mode single-prefix
```

Then re-run `scripts/bench-tools.py --agent turbofieldfare-gemma4` and record
wall-clock per call. **If it lands in single-digit seconds, make Gemma the
default agent** — it is the better model and the only reason it is not the
default is speed. Note `--prompt-cache-mode single-prefix`: our prompts share
a long constant prefix (universal + tooling prompt), so prefix caching should
help substantially.

---

## Carried over — still unbuilt

From [11-roadmap.md](11-roadmap.md), unchanged by the above:

- **Durable prompt history store (D12).** Deleting a settled prompt cell still
  loses the prompt, contrary to the decision.
- **History pane.** Not started.
- **`diffSummary.ts`.** Every turn currently sends a full snapshot; the
  follow-up prompt path is disabled until this exists, because the short
  follow-up form lacked the cell listing and caused edits to the wrong cell
  ([13-risks.md](13-risks.md) R13).
- **The truncation ladder** in `snapshot.ts` — only output filtering is built.
- **Round-scoped `Cmd+Z`.** `OperationLog` and `revertRound` exist; the command
  is not wired.
- **Remaining adapters** — `claude -p` and OpenAI-compatible.
- **Galata e2e**, settings custom renderers, context meter.
- **Jest coverage for our own modules.** Only the protocol parser is tested on
  the frontend side; the diff bug in N1 is a direct consequence.

## A process note

Several fixes today were reported as working while the running server predated
them. `scripts/dev.sh` now refuses to start beside a server already holding the
port and prints the commit it serves; `scripts/vybe-diagnose.sh` reports when a
transcript came from stale code. **Check the banner before concluding anything
about a fix.**
