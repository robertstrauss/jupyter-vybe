# 13 — Risk Register

Ordered by expected cost (probability × impact). Each risk names the concrete signal that tells
you it is happening and the mitigation already designed into the blueprint.

---

## R1 · The cell-executor override breaks notebooks entirely · **REALISED IN TESTING**

_This one happened._ Declaring `IVybeRounds` as an optional dependency of the executor created
`rounds -> tracker -> cell-executor -> rounds`; Lumino rejected the registration and JupyterLab
rendered a blank page with no notebooks and nothing shown to the user. Fixed by late binding
through `src/roundRegistry.ts` ([01](01-architecture.md)). The lesson generalises: an optional
dependency is still an edge, and a cycle here is fatal to the whole application, not to us.

The mitigations below remain in force for the _other_ way this fails — a throw during activate.

`jupyter-vybe:cell-executor` is a hard `requires` of
`@jupyterlab/notebook-extension:tracker`. If our `activate` throws, the tracker never activates
and **JupyterLab loses notebooks completely** — not a degraded extension, a broken application.

_Signal:_ notebooks fail to open at all after an install or upgrade.

_Mitigation:_ `activate` is synchronous, allocation-only, and try/caught with a fallback to
`Object.freeze({ runCell })` — literally the core plugin's body. All settings and agent wiring is
read lazily inside `runCell`, never at activation. Gate **T-BOOT** forces a throw and asserts
notebooks still work. Additionally, `runCell` itself wraps its prompt-cell branch in a try/catch
that delegates to core on any error, so a bug in round handling degrades to "the cell ran as
code" rather than "the cell did nothing".

---

## R2 · A 7B model cannot follow the tool protocol reliably · **REALISED — confirmed flaky**

_Measured._ Given a byte-identical prompt, `qwen2.5-coder:7b` emits correct `<vybe:add_cell>`
tags on one run and a plain markdown code fence on the next. Three consecutive session runs
produced fences; three more produced tags. It is not a prompt-structure bug and it is not
fixable by prompt volume — an A/B across no recap / one sentence / a 400-char recap scored 3/3
compliance in _every_ arm, including no recap at all.

**Salvage alone is not enough, though.** A fenced block carries no target, so
recovering it can only ever produce an `add_cell` — which turns "edit the cell
above" into a near-duplicate copy. That was reported in use and is the most
irritating way for the fallback to be wrong. `retarget_salvage()` now converts
a recovered block into a `propose_edit` when it clearly rewrites an existing
cell: a shared top-level `def`/`class` name is the strong signal (a large
rewrite of the same function scores only ~0.29 on raw text similarity, so a
similarity floor alone misses exactly the case worth catching), and without a
shared name it takes near-identity, 0.92, to retarget. A wrong guess costs one
keystroke, because the result is a reviewable diff rather than a silent write.

The mitigation that matters is therefore **salvage, not instruction**:
`salvage_fenced_blocks()` recovers a fenced reply as `add_cell` calls, and the session emits a
`proseReset` so the code does not also land in the settled markdown cell. Any model can produce
a fence, so this closes the gap for models that will not produce tags reliably.

Gemma 4 via TurboFieldfare produced correct tags on first contact, with tighter output than
Qwen. It is a candidate default if it proves steadier over a larger sample.

The layered mitigations below still apply.

`qwen2.5-coder:7b` is the default and it has no native tool support. Malformed tags, code fenced
inside a tool body, invented attributes, and prose where a tag belongs are all likely.

_Signal:_ the M4 quality bench shows tool-syntax validity below ~90%.

_Mitigation, in layers:_ (a) the grammar is deliberately dumb — line-delimited, no escaping, no
nesting, complete-source-not-patches, no position arguments to get wrong; (b) protocol errors are
fed back to the agent as a turn, which recovers most single mistakes; (c) `tooling.md` carries one
worked example per tool; (d) the bench exists _before_ prompt tuning so there is a feedback loop;
(e) `qwen2.5-coder:14b` and Gemma 4 are one dropdown away if 7B proves inadequate.

_If it still fails:_ fall back to a single-tool protocol (fenced code blocks with a header
comment naming the target cell), which any model can produce. This is a real fallback, not a
theoretical one — plan for one extra day at M4 to hold it in reserve.

---

## R3 · Context window exhaustion on real research notebooks · **medium impact, high probability**

Qwen2.5-Coder's 32K window against a mature research notebook with dozens of cells and rich
outputs is genuinely tight. This will happen, not might.

_Signal:_ the context meter is amber on ordinary notebooks; truncation notices appear routinely.

_Mitigation:_ aggressive output filtering ([09](09-context-management.md)), the five-stage
truncation ladder that preserves signatures over bodies, diff-based updates in persistent mode,
and a visible meter so the user is never surprised. `context.cellWindow` gives an explicit escape
hatch for very large notebooks.

_Residual:_ on a truly enormous notebook the agent will be working from signatures only. That is
the correct degradation, and the settings pane says so explicitly rather than silently degrading.

---

## R4 · Incremental context desynchronises the agent's picture of the notebook · **high impact, medium probability**

Persistent sessions send diffs, not snapshots. If a diff is lost, misapplied, or computed against
the wrong base, the agent proposes edits against a notebook that no longer exists — and those
proposals _look plausible_, which is the dangerous part.

_Signal:_ proposals target cell ids that have moved or reference code that was deleted turns ago.

_Mitigation:_ a monotonic `snapshotSeq` on every frame; a `resync` reply forcing a full snapshot
on any mismatch; a forced full snapshot every 10 turns bounding accumulated drift; and staleness
detection on the proposal itself, which blocks accepting an edit whose `before` no longer matches
the live cell. The last of these is the true backstop — even a fully desynchronised agent cannot
clobber a cell the user has touched.

---

## R5 · Undo semantics confuse users or corrupt state · **medium impact, medium probability**

Two undo layers (ydoc transactions and the round operation log) that must agree, interacting with
CodeMirror's own per-editor undo, in a notebook that may be under RTC.

_Signal:_ `Cmd+Z` produces a state that is neither the pre-round nor the post-round notebook.

_Mitigation:_ the round log is authoritative only while the round is active and is _sealed_ the
moment the user makes any non-vybe edit, after which `Cmd+Z` is plain core behaviour. Every round
is one ydoc transaction, so even with the vybe layer bypassed entirely, native undo is atomic and
correct. Gate **T-UNDO** deep-equals `toJSON()` against the pre-round capture.

---

## R6 · The `[*]` and execution-state fiction leaks · **low impact, medium probability**

We set `executionState = 'running'` without a kernel. Execution-indicator extensions, the busy
dot, and "restart and run all" progress UI all read that state.

_Signal:_ a stuck busy indicator after a cancelled round; a kernel status widget claiming activity.

_Mitigation:_ `executionState` is restored to `'idle'` in a `finally` on every terminal path
including cancellation and error. `onCellExecuted` is always emitted with the correct success
flag. `executionCount` is never assigned, so the notebook's execution history is untouched.

---

## R7 · Users assume the agent ran the code · **medium impact, high probability**

The tool inserts runnable-looking cells with no outputs. A researcher glancing at the notebook may
believe results were produced.

_Signal:_ user reports of "wrong numbers" that turn out to be un-run cells.

_Mitigation:_ inserted cells are visibly marked as a selected group with a transient accent bar;
they carry no `execution_count`, so the `[ ]` prompt is empty — the standard Jupyter signal for
"not run". The universal prompt forbids the agent from claiming results. This is partly a
documentation problem: the README should state plainly that nothing is executed.

---

## R8 · Vendored TurboFieldfare client drifts from upstream · **low impact, medium probability**

PROMPT §5 asks us to copy pieces of `code-llm/` into this project. Copies rot.

_Signal:_ a fix in `code-llm/turbofieldfare_client.py` that never reaches us.

_Mitigation:_ `make sync-vendor` diffs `jupyter_vybe/adapters/vendored/` against the original path
and prints the delta; the vendored files carry a header comment naming their source and the date
copied. Low stakes — the client is ~200 lines and the resolve-path probe order is the only subtle
part.

---

## R9 · RTC / concurrent editing during a round · **medium impact, low probability**

Another collaborator edits a cell that has a pending proposal, or moves the prompt cell mid-round.

_Signal:_ proposals apply to the wrong content under RTC.

_Mitigation:_ insertion anchors are recomputed from the prompt cell's _id_ at apply time, never
from a cached index. Proposal staleness compares `before` against live source at accept time.
Both make concurrent edits produce a visible "stale" state rather than a silent wrong write.
Full RTC correctness is explicitly out of scope ([00](00-overview.md)); graceful degradation is
what is promised.

---

## R10 · `CommandAdapter` persistent mode is fragile across agent CLIs · **low impact, high probability**

Every agent CLI has a different streaming JSON dialect, and they change between releases.

_Signal:_ the persistent handshake fails for a CLI the user configured.

_Mitigation:_ persistent mode is health-checked at session start and falls back to one-shot with a
visible warning in the settings pane. One-shot works with literally any executable that reads a
prompt and writes text, so the adapter is never fully broken — only less efficient.

---

## R11 · Schedule pressure from the diff UI · **medium impact, medium probability**

M5 is the longest milestone and the one most likely to expand: floating controls that track a
scrolling cell, smooth navigation, theme-correct diff colours, and a keyboard model are all
fiddly in ways that do not show up in an estimate.

_Mitigation:_ M5 comes _after_ a working agent (M2–M4), so if it overruns the tool is already
useful for insertions-only work. The proposal path can ship in a reduced form (accept/reject
buttons without the sticky bubble, no smooth scroll) and be polished in M8 without blocking
anything downstream.

---

## R12 · Split laptop/cluster setups have no zero-config path · **medium impact, medium probability**

When Jupyter runs on a compute cluster and the model runs on the user's laptop, neither of the
obvious answers is free. Browser-direct to `127.0.0.1` is blocked: Chrome's Private Network
Access enforcement requires the target to answer a preflight with
`Access-Control-Allow-Private-Network: true`, which Ollama does not send, and as of Chrome 142
there is no fallback. The reverse tunnel works but is a manual step the user must know to take.

_Signal:_ users on cluster deployments report the agent "not finding" their model, or a health
probe that reports success while the wrong machine is answering.

_Mitigation:_ the tunnel is documented as a first-class configuration rather than a workaround,
and the health probe reports the responding machine's hostname so a tunnel pointing at the
cluster's own (empty) Ollama is visibly wrong instead of silently wrong. `agent.host` ships in
the schema from v1.0 so the v1.1 sidecar — which can send the PNA header correctly and can run
`claude -p` with the user's own credentials — is a settings change, not a migration.

_Residual (1.2):_ the 1.1 sidecar closed this and opened R16; the dial-out runner closes both.
CLI agents run on the laptop with their own login, and nothing there listens. The remaining cost
is one install on the laptop, which any design that uses the user's subscriptions has to pay.

---

## R16 · Anyone who could reach a socket could run a command · **REALISED — fixed in 1.2**

Found by a status audit on 2026-11-16, both by doing it rather than reading it:

1. **The 1.1 sidecar accepted any web page.** `jupyter-vybe-local` listened on
   `127.0.0.1:8137` and echoed back whatever `Origin` it was sent unless started with
   `--allow-origin`. WebSockets are not covered by CORS, so a page on any site, open in the
   same browser, could connect, say `hello` with the `command` agent and a command of its
   choosing, and have it run on the laptop. A connection claiming `https://evil.example`
   created a file.
2. **The server's agent socket ignored the token.** `AgentSocket` overrode
   `get_current_user` to return a user for every request and stubbed out
   `check_xsrf_cookie`. Cross-origin connections were still refused by Tornado, but any process
   that could reach the port — another user on a shared login node — could open it without the
   token and run a command through the `command` agent. docs/06 said the route was
   authenticated.

_Fixed:_

- The agent socket and the new runner socket use Jupyter's own WebSocket authentication
  (`sockets.AuthenticatedSocket`, the terminal socket's pattern). Tests open both without a
  token, with a wrong one and from a foreign origin, and expect 403. **T-AUTH** fails on any
  handler that overrides `get_current_user`, `check_xsrf_cookie` or `check_origin`.
- The listening sidecar is gone. Its replacement, `jupyter-vybe-local connect`, **dials out**
  to the server with the token and listens on nothing, so no web page can reach it
  ([06](06-agent-integration.md)).
- The runner cannot be told what to run. Its agent is fixed on its own command line; server
  frames carry text and ids only. **T-RUNNER** fails if `local.py` reads a command-shaped field
  from a frame, and a test sends one naming a command, argv, cwd and env and checks nothing
  of it is used.
- The agent CLIs' own tools are switched off per preset, because a notebook can hold text
  written to talk an agent into using them. That this matters was measured, not assumed:
  headless `agy -p` read a planted file on request. The table in docs/06 records what was
  verified for each.

_Found on the way:_ the round was awaited inside `on_message`, so Tornado read no further frame
until it ended — no cancel, no kernel-lookup result, and, once the socket gained Jupyter's
keep-alive, no pong, which closed any round longer than 90 s. Rounds now run as tasks; a test
cancels one mid-flight.

_Residual:_ with the token, Jupyter skips the origin check by design (the token is the secret).
Anyone holding the token can already run code through a kernel, so the agent socket gives them
nothing new. `--command` and `gemini` are not contained by us; docs/06 says so.

---

## R15 · A protocol complaint shown in place of the user's code · **REALISED — fixed**

Reported from live use. `qwen2.5-coder:7b` emitted a `<vybe:propose_edit>` with no `cell`
attribute at all, wrapped in a stray ``` fence, and the settled prompt cell ended up reading:

````
```python

_Protocol error: propose_edit named cell '', which is not a cell in this notebook._
````

Three failures at once. The model's actual content was lost; a message about _our_ wire format
was written into the user's notebook, where it is both meaningless and permanent; and the stray
fence delimiter was left stranded beside it.

_Fixed, in that order of severity:_

- **The model gets one correction.** A `propose_edit` that names no resolvable cell no longer
  ends the round: the model is told exactly what was wrong and asked to send the reply again.
  Once, and only once — a model that insists is a model the user is waiting on for nothing.
  Anything the failed attempt produced is reverted first, so a retry cannot duplicate the
  well-formed half of a bad reply.
- **Protocol errors never reach the notebook.** They are shown while the round is live and kept
  in the prompt record; they are not written into the settled cell. Agent errors still are — a
  round that died because the adapter fell over has to say so in place.
- **Stray fence delimiters are stripped** from settled prose, so a misplaced ``` does not survive
  as the whole visible content of a cell.

_Not the cause, though it looked like one:_ cell numbering. The listing is renumbered from the
snapshot every turn and resolved against that same snapshot, so a cell added mid-session is
addressable and the numbers the model sees are the numbers it is answered on. Execution counts
are never used to address anything — they appear only as descriptive text — precisely because a
kernel restart renumbers them. Both are now covered by tests.

---

## R14 · A round that fails silently · **REALISED — fixed**

`protocolError` frames — the server's answer when the model names a cell that does not exist —
were sent by the session and **not handled by the frontend at all**. The round produced no cell,
no message, and no clue: exactly the "nothing happened" the extension is meant never to do, and
the same shape as the settings schema that silently did not load.

_Fixed:_ protocol errors and agent errors both surface twice over — in the status bubble while it
is happening, and in the settled markdown cell where the prompt ends up, so it survives dismissal.
Two Galata tests cover it: a model aimed at a cell that does not exist, and an agent that exits
non-zero.

_The general lesson, which this project keeps relearning:_ the dangerous failures here are not
crashes. They are paths that quietly do nothing — a stale server, a schema keyed to the wrong
name, a keybinding whose selector matches nothing, a frame nobody handles.

---

## R13 · A silent edit to the wrong cell · **REALISED — fixed**

The worst failure this tool can produce, because unlike a bad suggestion it is
not obviously wrong: the diff UI renders correctly, it just points at the wrong
cell.

_Cause._ Follow-up turns in a persistent session carried only a kernel banner, a
one-line tool recap, and an empty diff — **no cell listing at all**. Asked to
"edit the cell above", the agent had no numbers to work from, invented one, and
`resolve_cell_ref` faithfully mapped that invented number onto a real cell.

Two things made it worse. The follow-up prompt also omitted the guidance that
changing existing code is an edit rather than an addition, so it disagreed with
the prompt `scripts/bench-tools.py` measures — the bench scored a prompt that
users only ever saw on turn 1. And `resolve_cell_ref` accepting any in-range
integer meant a fabricated index looked exactly like a real one.

_Fix._ Every turn now sends the full prompt, listing included, until diff
summaries exist to carry the same guidance. Out-of-range indices are refused
rather than clamped. Verified 4/4 on the failing case.

_Residual._ An in-range but wrong index is still indistinguishable from a
correct one. The real protection is that the result is a reviewable diff:
the header of the review UI names the target cell, so a mis-aimed edit is
visible before it is accepted, not after.
