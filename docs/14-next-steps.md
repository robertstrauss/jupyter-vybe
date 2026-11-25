# 14 — Next Steps

Captured 2026-08-25 from live use, revised 2026-08-26 as the items were worked.
N1, N2, N3 and N5 are done; N4 is resolved, with qwen keeping the default for a
reason that is now measured rather than assumed. What is left is listed at the
bottom.

---

## N1 · The diff is not a real diff · **fixed**

**Observed.** A proposed edit rendered the entire new source in green and the
entire old source in red, including lines that had not changed.

**Two causes, both real, found in the recorded transcript and then in a test.**

1. **The model copied the listing's indentation.** `render_notebook` indents
   every cell's source by four spaces so the listing reads as a block. In
   `/tmp/vybe-transcript.jsonl` the model's `propose_edit` body came back with
   that indent still on it — every line of the "edit" differed from the
   original by four leading spaces, so every line genuinely _had_ changed and
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
   diffing. Note the fix is to _add_ the newline, not strip it — stripping it
   from both sides looks equivalent and reintroduces the bug on any pure
   addition.

`src/__tests__/diff.spec.ts` covers both, plus the reindentation case, so a
regression shows up as a failing test rather than as a wall of red on screen.
The second cause was found _by_ writing that test, which is the whole argument
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

## N4 · Gemma 4 through TurboFieldfareServer · **resolved; qwen still the default**

The server path had never run, and did not work when it did: the adapter sent
the model _directory_ as the API `model` field, where the server matches on its
own id (`gemma-4-26b-a4b-it`) and 404s otherwise. `/health` answering had been
taken as proof the path worked.

Three fixes came out of measuring it, each revealed by the one before:

1. **The model id.** Read from `/v1/models` now, with a stub-server test.
2. **The conversation was folded into one message.** True that Gemma 4 has no
   _system_ role — but user and assistant turns are turns, and the server's
   prompt cache only hits when a request continues the cached conversation. A
   re-folded blob is a new conversation every time and never hit.
3. **An abandoned stream parked a thread on the socket.** A thread cannot be
   cancelled, so a cancelled round left the request open server-side; one turn
   the client finished with in 25 s was logged as 1015 s of generating. The
   response is closed from the consumer side now.

With those, and follow-up turns re-enabled, three turns of one session:

| turn | prompt | cached | wall clock |
| ---- | ------ | ------ | ---------- |
| 0    | 1822   | 0      | 28.0 s     |
| 1    | 2171   | 1862   | 8.5 s      |
| 2    | 2562   | 2251   | 7.0 s      |

**Steady state clears the single-digit bar; a first turn does not.** Gemma is
the better model — 10/10 correct tool with zero fence fallbacks, repeatedly,
against qwen's 13/15 — but a first prompt costs ~17-30 s against qwen's ~2 s,
so **qwen stays the default**. That is now a real trade rather than a foregone
conclusion, and a user who wants the better model can pick it in settings.

### Where the wait actually goes (2026-09-02)

Measured per turn through a real `AgentSession` against `qwen2.5-coder:7b`,
splitting time-to-first-token from generation:

| turn          | to first token | generating | rate once started |
| ------------- | -------------- | ---------- | ----------------- |
| 0, cold model | 3.47 s         | 0.87 s     | ~51 tok/s         |
| 0, warm model | 0.14 s         | 0.78 s     | ~40 tok/s         |
| 1             | 0.59 s         | 0.95 s     | ~34 tok/s         |
| 2             | 0.62 s         | 0.76 s     | ~36 tok/s         |

**Almost none of it is ours.** Prompt assembly is 0.1 ms on a 60-cell
notebook, measured. The wait is the model reading the prompt, and the two
things that make it long are both addressable:

- **Cold model load** — 3.5 s, and it recurs, because Ollama unloads an idle
  model after five minutes. Requests now carry `keep_alive` (30 minutes,
  `VYBE_OLLAMA_KEEP_ALIVE` to change), which is the difference between "slow
  every time I come back to it" and "slow once".
- **First-turn prompt size** — 24k characters for 60 cells, against 2.9k on
  follow-ups, which is why turn 2 reaches its first token in a fifth of the
  time. The diff summaries already do this work; there is little left to cut
  without losing the listing the model needs to address a cell.

The display no longer reports `0 tokens/sec` during that gap. There is no rate
yet, and showing zero reads as broken rather than busy.

### What the prompt-size curve says

`scripts/bench-latency.py sweep`, one uninterrupted run:

| prompt tokens | 34  | 580 | 1700 | 3660 | 7020  |
| ------------- | --- | --- | ---- | ---- | ----- |
| seconds       | 6.1 | 6.7 | 17.8 | 55.7 | 129.9 |

Prefill grows faster than linearly. That is why `tooling.md` is now half its
old size (docs/08) and why the truncation ladder is a latency feature as much
as a budget one (docs/09).

**A caution about these numbers.** Machine throughput drifts by up to 2× across
runs under sustained load: the same 1298-token prompt averaged 17.0 s in one
run and 29.9 s in another an hour later. Comparisons _within_ a single
uninterrupted run are sound; comparisons across runs are not. An earlier A/B of
mine was worthless for a dumber reason — I ran both arms at once and they
contended for the GPU. Run arms sequentially, in one session, and say so.

---

## Carried over — now built

Everything on [11-roadmap.md](11-roadmap.md) through M6 is in: the durable
prompt store and its pane (D12, both locations), diff summaries, the truncation
ladder, round-scoped `Cmd+Z`, the `command` and OpenAI-compatible adapters,
rename tracking, the context meter, the custom settings renderers, and Galata
cover for the no-kernel prompt path. `mutations.ts` — the module with the most
power to damage a notebook — finally has tests.

What is left is not roadmap work but judgement calls that want a user:

- **Is Gemma worth its first turn?** The answer changed once the bench started
  running the code rather than counting tags.

  |                  | tool choice | edit target | code that _works_ | per call |
  | ---------------- | ----------- | ----------- | ----------------- | -------- |
  | qwen2.5-coder:7b | 20/20       | 8/8         | **19/24**         | 1.7 s    |
  | Gemma 4 26B-A4B  | 20/20       | 8/8         | **8/8**           | 25 s     |

  Tied on protocol, not on code. qwen fails `top_k_indices` consistently —
  `np.argsort(a)[-k:]` under a docstring promising largest-first — and
  occasionally emits no tag at all (mostly recovered by salvage). Gemma has not
  missed yet.

  So the trade is now explicit: **qwen is fifteen times faster, Gemma writes
  better code.** The default stays qwen because a first prompt at 25 s reads as
  broken, but a user doing something that has to be right should switch, and
  the settings pane should probably say so. Note what this cost to learn: a
  bench that only counted tool choice called them equal.

- **How aggressively should the ladder trim?** `context.windowTokens` minus a
  4096-token reserve is a guess that has never been wrong in testing because
  testing never used a 200-cell notebook.
- **The client-side sidecar (D2 v1.1)** — built, then replaced in 1.2 by the dial-out
  runner after it proved reachable from any web page (13 R16). The SSH reverse tunnel still
  covers HTTP backends with nothing installed.
- **Native tool calling (D3).** Deliberately deferred, and the tagged-text
  protocol plus salvage has held up well enough that there is no pressure.

## A process note

Several fixes were reported as working while the running server predated them.
`scripts/dev.sh` now refuses to start beside a server already holding the port
and prints the commit it serves; `scripts/vybe-diagnose.sh` reports when a
transcript came from stale code. **Check the banner before concluding anything
about a fix.**
