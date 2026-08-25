# 14 — Next Steps

Captured 2026-08-25 from live use, and revised the same day as the items were
worked. N1–N3 are done; N4 is a measurement, running separately. The carried-
over list at the bottom is unchanged.

---

## N1 · The diff is not a real diff · **fixed**

**Observed.** A proposed edit rendered the entire new source in green and the
entire old source in red, including lines that had not changed.

**Two causes, both real, found in the recorded transcript and then in a test.**

1. **The model copied the listing's indentation.** `render_notebook` indents
   every cell's source by four spaces so the listing reads as a block. In
   `/tmp/vybe-transcript.jsonl` the model's `propose_edit` body came back with
   that indent still on it — every line of the "edit" differed from the
   original by four leading spaces, so every line genuinely *had* changed and
   the differ was right. Fixed in two places: `_trim_body` in `protocol.py` now
   removes an indent common to every non-blank line of a tag body, and
   `prompts/tooling.md` states outright that the listing's indent is layout,
   not code.

2. **jsdiff tokenises on the newline terminator.** A final line without a
   trailing `\n` does not compare equal to the same line with one, and the
   result degenerates from "one line changed" to one whole-blob remove plus one
   whole-blob add:

   ```
   diffLines('a = 1', 'a = 1\nb = 2')
     -> [ {removed: 'a = 1'}, {added: 'a = 1\nb = 2'} ]
   ```

   Since `_trim_body` strips the agent's trailing newline while the cell it
   replaces keeps one, every proposal hit this. `normaliseSource` in
   `src/ui/diff.ts` now terminates both sides (and normalises CRLF) before
   diffing. Note the fix is to *add* the newline, not strip it — stripping it
   from both sides looks equivalent and reintroduces the bug on any pure
   addition.

`src/__tests__/diff.spec.ts` covers both, plus the reindentation case, so a
regression shows up as a failing test rather than as a wall of red on screen.
The second cause was found *by* writing that test, which is the whole argument
for having written it earlier.

---

## N2 · Review controls too small and under-explained · **done**

Accept/reject are 52px (were 34px). The merge verdict — `Enter`, decision D6 —
now has a button of its own and a line of text naming its key; previously
nothing on screen indicated it existed.

---

## N3 · The status bubble · **done**

Bigger, and the click is now safe. The jump button is the full width of the
bubble; reject-all is small, below a divider, and labelled. Reject-all is also
undoable (`⌥⇧Z`, or the bubble, which becomes `rejected N edits — undo`):
undoing restores the proposals and re-registers the round so a later accept
still records to its operation log. Staging any new proposal discards the undo.

---

## N5 · A second prompt in the same notebook did nothing · **fixed**

**Observed.** After one prompt had run, running a second left the cell as `[ ]`
— no `[*]`, no request, nothing.

**Cause.** `Rounds.begin` refuses to start while a round is registered for the
panel, which is correct. But a round that produced proposals was never
unregistered: `_settle` handed control to `Proposals` and only `Proposals` knew
when review ended, so the entry sat in `_active` forever and every later prompt
was refused silently. A throw out of `_drive` leaked the same way.

**Fix.** `IRoundHandle` gained `endReview()`, called by `Proposals` when the
last verdict lands, and `begin`'s `finally` finishes any round that did not
reach review — including one that threw. `_finish` is idempotent. Covered by
`src/__tests__/proposals.spec.ts`.

The general shape is worth remembering: a round's lifetime is owned by two
objects, and neither one alone can see it end.

---

## N4 · Measure Gemma 4 with TurboFieldfareServer · **outstanding**

Gemma 4 is the better model on the tool bench — 5/5 with zero fence fallbacks,
against 13/15 with 2 fences for `qwen2.5-coder:7b`. The blocker is latency:
roughly 90 s per call, because `TurboFieldfareAdapter` falls back to the CLI,
which reloads the 13 GB model on every invocation.

`TurboFieldfareServer` keeps the model resident and the adapter already prefers
it when `/health` answers — that path has never been exercised.

```bash
~/.turbofieldfare/turbo-fieldfare/.build/release/TurboFieldfareServer \
  --model /Users/rrs/Desktop/thoughtcode/code-llm/scratch/gemma4.gturbo \
  --port 8080 --max-context 32768 --prompt-cache-mode single-prefix
```

Then re-run `scripts/bench-tools.py --agent turbofieldfare-gemma4` and record
wall-clock per call. **If it lands in single-digit seconds, make Gemma the
default agent** — it is the better model and the only reason it is not the
default is speed. Note `--prompt-cache-mode single-prefix`: our prompts share a
long constant prefix (universal + tooling prompt), so prefix caching should
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
- **Jest coverage for our own modules.** Now covers the protocol parser, the
  differ, and the review lifecycle. `mutations.ts` and `snapshot.ts` are still
  untested on the frontend side.

## A process note

Several fixes were reported as working while the running server predated them.
`scripts/dev.sh` now refuses to start beside a server already holding the port
and prints the commit it serves; `scripts/vybe-diagnose.sh` reports when a
transcript came from stale code. **Check the banner before concluding anything
about a fix.**
