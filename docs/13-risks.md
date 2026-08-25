# 13 — Risk Register

Ordered by expected cost (probability × impact). Each risk names the concrete signal that tells
you it is happening and the mitigation already designed into the blueprint.

---

## R1 · The cell-executor override breaks notebooks entirely · **high impact, low probability**

`jupyter-vybe:cell-executor` is a hard `requires` of
`@jupyterlab/notebook-extension:tracker`. If our `activate` throws, the tracker never activates
and **JupyterLab loses notebooks completely** — not a degraded extension, a broken application.

*Signal:* notebooks fail to open at all after an install or upgrade.

*Mitigation:* `activate` is synchronous, allocation-only, and try/caught with a fallback to
`Object.freeze({ runCell })` — literally the core plugin's body. All settings and agent wiring is
read lazily inside `runCell`, never at activation. Gate **T-BOOT** forces a throw and asserts
notebooks still work. Additionally, `runCell` itself wraps its prompt-cell branch in a try/catch
that delegates to core on any error, so a bug in round handling degrades to "the cell ran as
code" rather than "the cell did nothing".

---

## R2 · A 7B model cannot follow the tool protocol reliably · **high impact, medium probability**

`qwen2.5-coder:7b` is the default and it has no native tool support. Malformed tags, code fenced
inside a tool body, invented attributes, and prose where a tag belongs are all likely.

*Signal:* the M4 quality bench shows tool-syntax validity below ~90%.

*Mitigation, in layers:* (a) the grammar is deliberately dumb — line-delimited, no escaping, no
nesting, complete-source-not-patches, no position arguments to get wrong; (b) protocol errors are
fed back to the agent as a turn, which recovers most single mistakes; (c) `tooling.md` carries one
worked example per tool; (d) the bench exists *before* prompt tuning so there is a feedback loop;
(e) `qwen2.5-coder:14b` and Gemma 4 are one dropdown away if 7B proves inadequate.

*If it still fails:* fall back to a single-tool protocol (fenced code blocks with a header
comment naming the target cell), which any model can produce. This is a real fallback, not a
theoretical one — plan for one extra day at M4 to hold it in reserve.

---

## R3 · Context window exhaustion on real research notebooks · **medium impact, high probability**

Qwen2.5-Coder's 32K window against a mature research notebook with dozens of cells and rich
outputs is genuinely tight. This will happen, not might.

*Signal:* the context meter is amber on ordinary notebooks; truncation notices appear routinely.

*Mitigation:* aggressive output filtering ([09](09-context-management.md)), the five-stage
truncation ladder that preserves signatures over bodies, diff-based updates in persistent mode,
and a visible meter so the user is never surprised. `context.cellWindow` gives an explicit escape
hatch for very large notebooks.

*Residual:* on a truly enormous notebook the agent will be working from signatures only. That is
the correct degradation, and the settings pane says so explicitly rather than silently degrading.

---

## R4 · Incremental context desynchronises the agent's picture of the notebook · **high impact, medium probability**

Persistent sessions send diffs, not snapshots. If a diff is lost, misapplied, or computed against
the wrong base, the agent proposes edits against a notebook that no longer exists — and those
proposals *look plausible*, which is the dangerous part.

*Signal:* proposals target cell ids that have moved or reference code that was deleted turns ago.

*Mitigation:* a monotonic `snapshotSeq` on every frame; a `resync` reply forcing a full snapshot
on any mismatch; a forced full snapshot every 10 turns bounding accumulated drift; and staleness
detection on the proposal itself, which blocks accepting an edit whose `before` no longer matches
the live cell. The last of these is the true backstop — even a fully desynchronised agent cannot
clobber a cell the user has touched.

---

## R5 · Undo semantics confuse users or corrupt state · **medium impact, medium probability**

Two undo layers (ydoc transactions and the round operation log) that must agree, interacting with
CodeMirror's own per-editor undo, in a notebook that may be under RTC.

*Signal:* `Cmd+Z` produces a state that is neither the pre-round nor the post-round notebook.

*Mitigation:* the round log is authoritative only while the round is active and is *sealed* the
moment the user makes any non-vybe edit, after which `Cmd+Z` is plain core behaviour. Every round
is one ydoc transaction, so even with the vybe layer bypassed entirely, native undo is atomic and
correct. Gate **T-UNDO** deep-equals `toJSON()` against the pre-round capture.

---

## R6 · The `[*]` and execution-state fiction leaks · **low impact, medium probability**

We set `executionState = 'running'` without a kernel. Execution-indicator extensions, the busy
dot, and "restart and run all" progress UI all read that state.

*Signal:* a stuck busy indicator after a cancelled round; a kernel status widget claiming activity.

*Mitigation:* `executionState` is restored to `'idle'` in a `finally` on every terminal path
including cancellation and error. `onCellExecuted` is always emitted with the correct success
flag. `executionCount` is never assigned, so the notebook's execution history is untouched.

---

## R7 · Users assume the agent ran the code · **medium impact, high probability**

The tool inserts runnable-looking cells with no outputs. A researcher glancing at the notebook may
believe results were produced.

*Signal:* user reports of "wrong numbers" that turn out to be un-run cells.

*Mitigation:* inserted cells are visibly marked as a selected group with a transient accent bar;
they carry no `execution_count`, so the `[ ]` prompt is empty — the standard Jupyter signal for
"not run". The universal prompt forbids the agent from claiming results. This is partly a
documentation problem: the README should state plainly that nothing is executed.

---

## R8 · Vendored TurboFieldfare client drifts from upstream · **low impact, medium probability**

PROMPT §5 asks us to copy pieces of `code-llm/` into this project. Copies rot.

*Signal:* a fix in `code-llm/turbofieldfare_client.py` that never reaches us.

*Mitigation:* `make sync-vendor` diffs `jupyter_vybe/adapters/vendored/` against the original path
and prints the delta; the vendored files carry a header comment naming their source and the date
copied. Low stakes — the client is ~200 lines and the resolve-path probe order is the only subtle
part.

---

## R9 · RTC / concurrent editing during a round · **medium impact, low probability**

Another collaborator edits a cell that has a pending proposal, or moves the prompt cell mid-round.

*Signal:* proposals apply to the wrong content under RTC.

*Mitigation:* insertion anchors are recomputed from the prompt cell's *id* at apply time, never
from a cached index. Proposal staleness compares `before` against live source at accept time.
Both make concurrent edits produce a visible "stale" state rather than a silent wrong write.
Full RTC correctness is explicitly out of scope ([00](00-overview.md)); graceful degradation is
what is promised.

---

## R10 · `CommandAdapter` persistent mode is fragile across agent CLIs · **low impact, high probability**

Every agent CLI has a different streaming JSON dialect, and they change between releases.

*Signal:* the persistent handshake fails for a CLI the user configured.

*Mitigation:* persistent mode is health-checked at session start and falls back to one-shot with a
visible warning in the settings pane. One-shot works with literally any executable that reads a
prompt and writes text, so the adapter is never fully broken — only less efficient.

---

## R11 · Schedule pressure from the diff UI · **medium impact, medium probability**

M5 is the longest milestone and the one most likely to expand: floating controls that track a
scrolling cell, smooth navigation, theme-correct diff colours, and a keyboard model are all
fiddly in ways that do not show up in an estimate.

*Mitigation:* M5 comes *after* a working agent (M2–M4), so if it overruns the tool is already
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

*Signal:* users on cluster deployments report the agent "not finding" their model, or a health
probe that reports success while the wrong machine is answering.

*Mitigation:* the tunnel is documented as a first-class configuration rather than a workaround,
and the health probe reports the responding machine's hostname so a tunnel pointing at the
cluster's own (empty) Ollama is visibly wrong instead of silently wrong. `agent.host` ships in
the schema from v1.0 so the v1.1 sidecar — which can send the PNA header correctly and can run
`claude -p` with the user's own credentials — is a settings change, not a migration.

*Residual:* until the sidecar ships, CLI-based agents (`claude -p`) cannot run on the laptop
while Jupyter runs remotely. A tunnel cannot carry a subprocess. This is stated plainly in the
README rather than discovered.
