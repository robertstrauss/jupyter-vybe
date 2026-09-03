# 12 — Executive Decisions

These are the calls that are genuinely yours, not mine: each one either reinterprets something
PROMPT.md or README.md says, trades scope against schedule, or commits the project to something
expensive to reverse. Each has a recommendation I will proceed with by default if you say
nothing, so none of them block a start.

Ordered roughly by how much rework a late reversal costs.

**Status (2026-08-25):** D2 and D3 were revised after review. D6 and D8 are resolved by your
annotations (D8's resolution was corrected — I had its emphasis backwards). D12 and D13 were
raised in review and are resolved below. All others stand at the recommendation.

|     | Decision                      | Status                                                      |
| --- | ----------------------------- | ----------------------------------------------------------- |
| D1  | Kernel introspection          | ✅ A — inspect+complete, allowlisted                        |
| D2  | Where the agent runs          | ✅ **revised** — `server` + tunnel in v1.0, sidecar v1.1    |
| D3  | Tool-call format              | ✅ **revised** — tagged text only; native path deferred     |
| D4  | Notebook metadata             | ✅ A — zero persisted metadata                              |
| D5  | Autocomplete in v1.0          | ✅ A — deferred to v1.1; rebuilt on demand in v1.2          |
| D6  | Review keybindings            | ✅ **your call** — `Y`/`N` + `Enter` opens a merged edit    |
| D7  | Diff rendering                | ✅ A — jsdiff + CM6 decorations                             |
| D8  | Default global prompt         | ✅ **your call** — infer from notebook, else language idiom |
| D9  | Name / license                | ✅ `jupyter-vybe`, BSD-3-Clause                             |
| D10 | Remote-agent privacy          | ✅ confirm on non-local adapters                            |
| D11 | Minimum JupyterLab            | ✅ `>=4.4,<5`                                               |
| D12 | Where the prompt record lives | ✅ **your call** — sidecar store, durable by default        |
| D13 | Kernel info in the payload    | ✅ **your call** — pinned, never truncated                  |

---

## D1 · Kernel introspection: does "never touch the kernel" mean _never_, or "never execute"?

**The tension.** PROMPT §"Agent tools" says the agent may use IPython help (`?`) and
autocompletion — both of which are kernel features. The same section says, in caps, that the
agent "CANNOT directly execute anything through the kernel or send execution requests to the
kernel". PROMPT §1 says "None of this extension should touch the kernel."

**The technical fact.** `inspect_request` and `complete_request` are distinct Jupyter messaging
protocol messages from `execute_request`. They read the kernel's namespace; they do not run user
code, do not advance the execution counter, and do not mutate state. Docstrings for live objects
and completions for a live DataFrame are not obtainable any other way.

| Option                                                   | Consequence                                                                                                                                                                                                                                                |
| -------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Allow `inspect` + `complete` only** _(recommended)_ | The agent can look up real signatures and real attribute names on the user's actual objects — a large quality win on a 7B model, which otherwise hallucinates APIs. Enforced by an allowlist in one file, a lint rule, and a CI grep for `requestExecute`. |
| B. Static introspection only                             | Server-side `jedi` against the notebook source. Zero kernel contact, honours the strictest reading. Cannot see runtime state, so it cannot answer "what columns does `df` have".                                                                           |
| C. No introspection                                      | Simplest, weakest.                                                                                                                                                                                                                                         |

**Recommendation: A**, with `agentTools.kernelIntrospection` in settings so you can drop to B/C
without a code change, and with the tools omitted from the tooling prompt entirely when disabled.

**Reverse cost:** low now, moderate after M4.

---

## D2 · Where does the agent run? (revised after your question)

**Your question:** does this force the local model onto the same machine as the notebook server?
What about a local model on the MacBook while Jupyter runs on a compute cluster?

**You are right, and my first draft was wrong.** Putting the adapter on the Jupyter server
silently assumes server and model are co-located. In your cluster case that assumption breaks
badly: `OllamaAdapter` hitting `127.0.0.1:11434` would reach _the cluster's_ Ollama (probably
nothing), and `CommandAdapter` would spawn `claude -p` on the cluster, with the cluster's
credentials rather than yours.

So **where the adapter runs becomes a configurable axis, not a fixed part of the architecture.**
The `Adapter` protocol is host-agnostic; only its transport changes.

| Site                                                                | How it works                                                                                                                                                                                | Covers                                                                        |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| **`server`** _(default)_                                            | Adapter runs in the Jupyter server extension, as originally designed.                                                                                                                       | Laptop-only setups: Jupyter and model on the same machine.                    |
| **`server` + reverse tunnel** _(recommended for your cluster case)_ | `ssh -R 11434:127.0.0.1:11434 cluster`. The cluster-side adapter dials `127.0.0.1:11434` and reaches **your laptop's** Ollama. Zero extension code — pure config.                           | HTTP backends: Ollama, TurboFieldfare server, any OpenAI-compatible endpoint. |
| **`client` sidecar** _(built)_                                      | `jupyter-vybe-local` on the MacBook: the **same** `jupyter_vybe.adapters` package behind a tiny local HTTP/WS server. The browser — which is already on your laptop — talks to it directly. | Everything, including `claude -p` with your local login and CLI binaries.     |
| ~~`client` browser-direct~~                                         | Browser fetches `http://127.0.0.1:11434` itself.                                                                                                                                            | **Not viable** — see below.                                                   |

**Why browser-direct is out.** Chrome's Private Network Access / Local Network Access enforcement
now gates requests from an HTTPS page to `127.0.0.1`: the request needs a preflight carrying
`Access-Control-Request-Private-Network: true` and a response carrying
`Access-Control-Allow-Private-Network: true`. Ollama does not send that header, and as of Chrome
142 there is no fallback path — it is blocked or gated behind a user permission prompt. Building
on it would produce an extension that works on one browser version and breaks on the next. The
sidecar exists precisely because it _can_ send those headers correctly.

**Why the sidecar is cheap.** It is not a second implementation. `jupyter_vybe/adapters/` has no
dependency on Tornado or on the server extension; `python -m jupyter_vybe.local` is a second
entry point around the same code, roughly 150 lines of transport. The frontend's `agentClient.ts`
already speaks one WebSocket protocol; it just points at a different URL.

**Recommendation:** ship `server` in v1.0 with the tunnel path **documented as a first-class
supported configuration** (it is one `ssh -R` flag and it solves your case today), and build the
sidecar in v1.1. `agent.host` is in the settings schema from day one so the switch is config, not
a migration.

**Outcome.** Both exist. The tunnel is documented with the three failure modes that actually
happen ([06](06-agent-integration.md)), and the sidecar turned out to be far cheaper than the
~3 days estimated — the estimate assumed writing a transport, but the constraint that kept
adapters free of Tornado (gate D2) meant there was nothing to port. What it cost instead was a
stdlib WebSocket implementation, which is ~60 lines of framing and was worth it to keep the
laptop-side install dependency-free.

**Tell me if** the cluster case is your _primary_ workflow rather than a secondary one — if it
is, the sidecar moves into v1.0 and costs about three days, and the tunnel becomes the fallback
rather than the answer.

**Reverse cost:** low. This revision is what makes it low; the original design would have been
expensive to unwind after M2.

---

## D3 · Tool-call format: native function calling, or our own tagged text? (revised)

**Your question:** why would B be "cleaner, more reliable" — for a model that supports both,
aren't A and B the same?

**Largely yes, and my phrasing was lazy.** Let me give the actual argument rather than the vibe.

Native tool calling has exactly **one** structural advantage: the serving stack can constrain
decoding to the declared schema (vLLM, Ollama's tool path, and hosted APIs all do this), so a
malformed call becomes impossible rather than merely unlikely. Everything else I implied —
"cleaner", better instruction-following — is marginal, and for a model trained on both formats it
is close to noise.

Against that, native tools have a real disadvantage **specific to this payload**: our arguments
are multi-line source code, and native tool calls carry arguments as JSON strings. That means
every newline becomes `
`, every quote and backslash gets escaped, and indentation lives inside
a single-line string literal. LLMs corrupt code-inside-JSON at a measurably higher rate than they
corrupt code written literally — it is one of the more reliable failure modes in practice. The
tagged format carries source **verbatim, with no escaping at all**:

```
<vybe:add_cell type="code">
def f(x):
    return "a \"quoted\" string"
</vybe:add_cell>
```

There is nothing for the model to escape and nothing for us to unescape.

So the honest scoring: constrained decoding is a genuine win for B; no-escaping is a genuine win
for A; and A additionally works on the two backends PROMPT names, which have no tool support at
all. That is not a close call.

**Revised recommendation: build A only.** Keep `supports_native_tools` on the `Adapter` protocol
as a declared capability so the door stays open, but do **not** implement the B path in v1.0.
Maintaining two parsers, two prompt assemblies, and two sets of golden files to gain constrained
decoding on backends that are not the default is not worth it before there is evidence of need.
The evidence would be the M4 quality bench showing tool-syntax validity below ~90% on Qwen and
above it on a native-tools backend — at which point B becomes a targeted fix rather than
speculative infrastructure.

This is a change from my first answer, which recommended building both.

**Reverse cost:** low — adding B later is additive, and the internal `ToolCall` type it would
normalise into already exists.

### Vindicated by measurement, not just by schedule

The deferral was a schedule argument at the time. It is now an evidence argument: on the
twenty-prompt bench, tagged text scores **20/20 tool choice and 8/8 edit targeting on both
models**, with zero fence fallbacks ([11](11-roadmap.md)). There is no headroom for a native
tool-calling path to recover, and it would add a second protocol to maintain, a second set of
failure modes, and a capability matrix per adapter.

The two protocol failures that did occur — a model emitting fences instead of tags, and a model
inventing a cell number — were fixed by prompt design and by `salvage_fenced_blocks`, neither of
which native tools would have helped with. `supports_native_tools` stays declared and unused.

---

## D4 · Notebook metadata: strictly zero, or a namespaced key?

**The tension.** README promises the notebook gains "no new or strange properties". But undo,
history, and re-opening a prompt all want provenance.

**The trick that makes zero-metadata viable:** a settled prompt cell becomes a markdown cell whose
source is the original prompt with its `> ` prefix intact — which is already valid markdown
blockquote syntax. It renders attractively for a collaborator without the extension, and it is
losslessly reversible back into a prompt cell. No metadata is needed for the core loop.

| Option                                                                                        | Consequence                                                                                                                                                                                                                                                                                                 |
| --------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Zero persisted metadata; all state in memory keyed by nbformat cell id** _(recommended)_ | Honours the README promise literally. History is per-session (README says it is per-session anyway). Provenance is not durable across reopening.                                                                                                                                                            |
| B. Namespaced `metadata.jupyter_vybe`                                                         | Durable provenance ("this cell was AI-generated on 2026-08-25 from this prompt") — genuinely valuable for research reproducibility. nbformat permits arbitrary metadata and other tools ignore it, so "backwards-compatible" is arguably still true. But git diffs get noisier and the promise gets softer. |

**Recommendation: A** as the default, with B available behind an off-by-default setting. If
reproducibility provenance matters to you more than diff cleanliness, say so and I will flip the
default — this is a values call about your research workflow, not a technical one.

**Reverse cost:** low (additive either way).

---

## D5 · Is advanced autocomplete in v1.0?

**The tension.** README gives it a headline sentence. PROMPT's numbered build list (items 1–6)
omits it entirely.

| Option                               | Consequence                                                                                                                                                                                                                                 |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Defer to v1.1** _(recommended)_ | v1.0 in ~6.5 weeks. Autocomplete is a separate subsystem (`ICompletionProviderManager`, FIM prompting, aggressive debounce/cancel, ghost-text rendering) with a latency budget the prompt-cell flow does not have — mixing them risks both. |
| B. Include in v1.0                   | ~8 extra days, and prompt-cell polish competes with completion latency tuning for attention.                                                                                                                                                |

**Recommendation: A.** Both backends support FIM (`README_turbofieldfare.md` documents Gemma's
tokens; Qwen2.5-Coder has its own), so nothing is being foreclosed.

**Outcome: built in v1.1**, and deferring was right for a reason the estimate did not name. The
subsystem is small — a FIM prompt, a raw completion endpoint, an
`IInlineCompletionProvider` — but almost all of its design is about _not showing stale text_,
which is the opposite of the prompt-cell flow's concern. Built together, the two would have
argued: a prompt cell may take thirty seconds and that is fine, while a completion that arrives
after the next keystroke is worse than none.

Measured against `qwen2.5-coder:7b`: 0.4–1.3 s per completion, returning real code
(`x / x.max() if x.max() != 0 else x` for `def normalise(x): return `). It is **off by default**
— it costs a model call at a keystroke cadence, and a user who wants that should say so.

**Rebuilt in v1.2, on demand.** Off-by-default was the wrong answer to the right worry. The cost
is real, so the fix is to stop guessing when to pay it: nothing is requested until the trigger
key. Once a request is deliberate, three things follow that the automatic version could not have.

The context can be expensive, so the notebook goes in whole, as an nbconvert-style script — which
replaced the FIM prompt entirely. FIM predicts a cell from itself; what a cell should say next is
mostly decided by what the cells above it defined. It also cost a token vocabulary per model
family and a raw-completion route per adapter, where an instruction turn is something every
adapter can answer.

Pressing the trigger again _extends_ the suggestion rather than replacing it, so the default of
two lines is not a ceiling — it is how much the user is asked to read before deciding.

And the ghost text is ours, so the keys are ours: `Tab` accepts, `Backspace` rejects **without
deleting a character of the user's code**, and anything else rejects by simply happening. That
last one is why this is a CodeMirror extension rather than an `IInlineCompletionProvider`: the
framework owns Tab, and the one thing users get wrong with ghost text everywhere it exists is
backspacing over it and losing what they wrote.

### One line, unless one line would not parse

The budget is a single line, and the trigger is pressed again for the next. A short certain line
beats several guessed ones, and the user decides how far the prediction goes rather than reading
a paragraph to find the sentence they wanted.

The exception is code that would not stand on its own: a line ending in a colon, or leaving a
bracket or a string unclosed. A `for` header with nothing under it is not a short answer, it is a
syntax error, and accepting it puts one in the notebook. Those run on until the code parses, or
until a hard cap of six extra lines, so a model that never closes what it opens cannot run away
with the response.

**Completeness is a property of the whole statement, not of the reply.** Measured against
`qwen2.5-coder:7b`: asked to continue `monthly = df.groupby(`, it answered `'month',` — which
looks finished read on its own and leaves a bracket open in the cell. Judged without the text the
user had already typed, the limiter stopped one line short of parsing every time.

**The join is decided by the client, because code does not begin with a newline.** The second
press means "and then what?", and the model replies with a line, not with a line break — so the
first fix for this was reported as suggestions arriving glued onto the end of the previous line.
Whether the next answer starts a line of its own is decided from whether what is already
suggested stands alone, and the same break is sent to the model, so what it continues is what the
user is looking at.

### Why the trigger is `Alt+\`

Every obvious key is taken, and taken by something that will win:

| Candidate    | Why not                                                                                  |
| ------------ | ---------------------------------------------------------------------------------------- |
| `Tab`        | The kernel's completions — which this feature _feeds on_. Taking it takes its own input. |
| `Shift+Tab`  | The kernel's docstrings, likewise fed to the model.                                      |
| `Alt+Tab`    | The window manager on Windows and most Linux desktops. Never reaches the page.           |
| `Ctrl+Tab`   | Browser tab switching off a Mac.                                                         |
| `Cmd+Tab`    | Application switching on a Mac.                                                          |
| `Ctrl+Space` | Input-source switching on macOS, and an IME toggle on many Linux setups.                 |

`Alt+\` — Option+\ on a Mac — is what Copilot uses for the same action, which makes it both the
one combination every layer leaves alone and the one a user is most likely to already know. It is
a setting regardless, and the tooltip names whatever it is set to rather than assuming.

---

## D6 · The `Enter` rebinding during review

To accept a proposal, PROMPT wants a keyboard shortcut. `Enter` in command mode normally enters
edit mode. I propose rebinding it _only_ while a round is active and the cursor is on a pending
proposal (`Enter` = accept, `Backspace` = reject).

| Option                                                       | Consequence                                                                                                                 |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| **A. `Enter` / `Backspace`, tightly scoped** _(recommended)_ | Fastest to drive; matches the "just keep pressing Enter" instinct. Scoped by CSS selector so it never fires outside review. |
| B. `Y` / `N`                                                 | Zero conflict with muscle memory, slightly slower to reach.                                                                 |

**Recommendation: A**, with `review.acceptKey` in settings offering B. Low stakes, cheap to change,
but worth a moment of your opinion since you will press it thousands of times.

**Original choice:**

Choice: Switch to Y/N if these don't have command-mode bindings already; Enter goes into edit mode, accepting new lines but including the old lines commented out. This is a third option already aligned with existing triggers for a 'something else' option allowing the user to edit the proposal. 'Enter' may still have to be intercepted; or, perhaps better, the canonical state of the cell below the rendered diff is this suggestion-accepted, old code commented version. Good for compatability so if something breaks it doesn't have invalid syntax.

### Resolution

**On your conditional — I checked, and `Y` is taken.** JupyterLab's command mode binds `Y` to
`notebook:change-cell-to-code`. `N` is free. The full set of free single letters in command mode
is `E G N Q T U W`; every other letter is bound by core.

So `Y` needs the same scoped override that `Enter` would have needed. It is a _much_ safer
override, though: "change this cell to a code cell" is meaningless while you are reviewing a diff
on it, whereas `Enter`→edit-mode is meaningful and worth preserving — which is exactly your point.

**Adopted, as a synthesis of your third option:**

| Key     | Action                                                               |
| ------- | -------------------------------------------------------------------- |
| `Y`     | accept (scoped override of `change-cell-to-code`, round-active only) |
| `N`     | reject (no core binding — no override at all)                        |
| `Enter` | **materialise the merged version and drop into edit mode**           |

`Enter` keeps its native "go edit this" meaning, and lands you in a cell whose content is the
accepted version with the replaced lines commented out beneath it — your "something else" path,
letting you hand-merge rather than take a binary verdict.

**One refinement to your stronger form.** You suggested making the commented-merged version the
_canonical_ state of the cell throughout review, for crash-safety. That does buy valid syntax at
all times, but it also means the notebook model is mutated the moment a proposal is staged: a
save mid-review persists commented-out cruft, every proposal churns the ydoc, and rejecting
becomes a restore rather than a no-op. I have made it **on-demand instead of ambient** — the
merge is materialised only when you press `Enter` on that proposal. You get the compatibility
benefit exactly where it matters (while hand-editing), without a half-reviewed notebook being
saved full of commented-out dead code.

If you would rather have it ambient after all, it is a one-line change in the proposal store —
say so and I will flip it.

**Comment syntax** is derived from the kernel's `language_info` (already in the context payload):
`#` for Python/R/Julia/shell, `//` for JS/TS/C-family, `%` for MATLAB, `<!-- -->` for markdown
cells, falling back to `#`. Recorded in [03](03-diff-ui.md).

---

## D7 · Diff rendering: `@codemirror/merge`, or hand-rolled decorations?

| Option                                                         | Consequence                                                                                                                                  |
| -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. jsdiff line diff + CM6 line decorations** _(recommended)_ | Exactly the GitHub look PROMPT asks for; full control of the ✓/✗ affordances; the same extension is reused in the history pane. ~200 lines.  |
| B. `@codemirror/merge` unified view                            | Less code, but its chrome (gutters, collapse widgets, per-chunk revert arrows) conflicts with per-cell accept/reject and is hard to restyle. |

**Recommendation: A.** Purely technical; I would not escalate it except that it is visible in
every screenshot you will ever take of this tool.

---

## D8 · The default global prompt contradicts PEP 8

PROMPT §6 specifies the default user-editable global prompt should include "Camel case function
and variable names, title case for classes." For Python that is contrary to PEP 8's
`snake_case`, and `qwen2.5-coder` will fight it.

| Option | Consequence |
|

### Measured: the instruction is enough, and a machine-written summary adds nothing

v1.1 built the obvious upgrade — measure the notebook's conventions directly (identifier casing,
docstring usage, comment density, import aliasing) and state them in the prompt, rather than
asking the model to notice. Then A/B'd it against a camelCase, docstring-free notebook,
`qwen2.5-coder:7b`, four runs an arm:

|                              | casing matched | "no docstrings" matched |
| ---------------------------- | -------------- | ----------------------- |
| instruction only             | 4/4            | 4/4                     |
| instruction + measured notes | 4/4            | 4/4                     |

Both arms at ceiling, including with eighteen cells of unrelated code between the style-bearing
code and the prompt. The measurement was **reverted rather than shipped**: it cost tokens on every
turn and bought nothing observable, and shipping it would have been exactly the "assumed, not
measured" reasoning this project keeps having to correct.

Worth noting _why_ the pessimistic case did not materialise. The scenario the notes were for is
"truncation has removed the evidence" — but the ladder's stage 4 replaces distant cell bodies
with their signatures, and signatures carry identifier casing ([09](09-context-management.md)).
The problem had already been designed out.

---|---|
| **A. Ship PROMPT's text verbatim as the default** _(recommended)_ | Faithful to the spec; the field exists precisely so the user changes it; it doubles as a demonstration that the setting works. |
| B. Ship a language-idiomatic default | Better out-of-box output, deviates from the spec. |

**Recommendation: A** — it is a _default_, editable in one place, and a user whose conventions
differ is exactly who that field is for. Say the word and it becomes B.

Choice: Tell the agent to adhere to the conventions elsewhere in the notebook. If none are set, use PEP 8 reccomendations.

### Resolution _(corrected — I had the emphasis wrong)_

My first pass read this as "infer, with the language standard as the point". You meant the
opposite emphasis: **infer the user's own style from their existing code, and follow it even where
it departs from the language's conventions.** If they write camel-case functions in Python, write
camel-case functions in Python. Do not correct them toward PEP 8.

That is a stronger and better rule, and it makes the precedence explicit:

|     | Source                                                      | Applies when                                 |
| --- | ----------------------------------------------------------- | -------------------------------------------- |
| 1   | The user's global prompt, if they wrote conventions into it | always — it is an explicit instruction       |
| 2   | **Conventions inferred from existing code in the notebook** | whenever the notebook has code               |
| 3   | The kernel language's standard idiom                        | **only** when there is no code to infer from |

Level 2 beating level 3 is the whole point: consistency with the surrounding code beats external
correctness, because the notebook is the artifact being written and a model that silently
"fixes" the user's naming is doing damage, not a favour.

Level 3 is a genuine fallback, not a default — it fires on an empty notebook, which is precisely
when there is nothing to infer from and when getting the _language_ right matters most. That is
what makes kernel information load-bearing rather than decorative; see D13.

New default text and the inference checklist (identifier casing, comment density, docstring
format, import grouping, cell granularity) are in [08](08-prompt-templates.md).

Degradation: truncation stage 4 replaces distant cell bodies with signatures, and signatures still
carry identifier casing — the dominant style signal. Style inference weakens under truncation
rather than failing.

---

## D9 · Naming and packaging identity

README says "Jupyter Vybe"; the directory is `jupyter-vibe`.

**Recommendation:** commit to **`jupyter-vybe`** — PyPI `jupyter-vybe`, npm `jupyter-vybe`, Python
module `jupyter_vybe`, plugin ids `jupyter-vybe:*`, CSS prefix `jv-`. License **BSD-3-Clause**
(the Jupyter ecosystem norm; permissive, and compatible if you ever want this upstreamed).
Initialise a git repo at M0 — there is currently none. Publish to PyPI at M7.

Tell me if you want a different name, a different license, or no public publication.

---

## D10 · Remote agents and notebook privacy

Selecting a hosted agent means the notebook's source and outputs leave the machine.

**Recommendation:** default to local (Ollama/Qwen). When a non-local adapter is selected, show a
one-time-per-notebook confirmation naming the destination, and surface the current destination
permanently in the status bubble. Controlled by `privacy.confirmRemoteAgents`, default on.

---

## D11 · Minimum JupyterLab version

`INotebookCellExecutor` landed in 4.2. Supporting anything older means monkeypatching, which
cannot be made complete (see [02](02-prompt-cells.md)).

**Recommendation:** require `jupyterlab>=4.4,<5`, develop and test against 4.6.3. Notebook 7 is a
stretch goal — same plugin system, different CSS. No JupyterLab 3 support.

---

## D12 · Where the prompt record lives _(raised in review — resolved)_

**Your point:** the prompt history needs its own storage, separate from the notebook, so that
(a) the agent cannot rewrite its own history and (b) deleting the markdown cell does not lose the
prompt.

**Adopted, and it is a better design than what I had.** A settled prompt cell is now explicitly a
_rendering_ of a round, not its storage. The record is a sidecar JSONL file, append-only, one per
notebook, written through the server extension.

What this buys, beyond the two reasons you gave:

- The agent's conversation context is assembled from the store, never re-read from notebook cells
  ([08](08-prompt-templates.md)). There is no path from a cell back into the message list, so
  "agent rewrites its own instructions" stops being a guard I have to maintain and becomes
  structurally impossible. The pipeline check from [03](03-diff-ui.md) stays as belt-and-braces.
- Prompts survive cell deletion, reordering, `Cmd+Z` past the round, and notebook close.
- `history.persist` flips to **on by default**. It was off in my draft, which was wrong given
  (b) — a durable record cannot be opt-in if losing it is a normal consequence of tidying up.

It does **not** disturb D4: a sidecar file is not notebook metadata, so the notebook a
collaborator opens is still an ordinary notebook.

**The one open sub-question is location**, and it is a genuine trade rather than an oversight:

|                                                                            | Consequence                                                                                                                                                                                                                                                                                                           |
| -------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`~/.jupyter/vybe/history/<sha256(path)>.jsonl`** _(recommended default)_ | Nothing stray appears in the user's repo. But history does not travel with the notebook — send someone the `.ipynb` and they get cells, not prompts. Renames tracked via `pathChanged`; a rename outside JupyterLab orphans the file, which the pane reports and offers to adopt rather than silently starting empty. |
| `.vybe/` beside the notebook                                               | The record is version-controlled alongside the work, which for a research audit trail — _why_ does this cell exist — is a reasonable thing to want. Costs an untracked directory in every project.                                                                                                                    |

**Recommendation:** default to the first, ship `history.location: adjacent` for the second. Given
this tool's stated audience, I would not be surprised if you want `adjacent` as _your_ default —
say so and I will flip it.

---

## D13 · Kernel information is pinned context _(raised in review — resolved)_

**Your point:** kernel information should ride along with the initial notebook payload, so the
model knows what language to write when there is no code, and which conventions to use when the
user has provided none.

**Adopted.** Both loads are real and they bite in the same situation — an empty or heavily
truncated notebook — so kernel info is now **pinned**:

- It sits in the template _outside_ `{{NOTEBOOK_JSON}}`, so the truncation ladder in
  [09](09-context-management.md) cannot reach it.
- It is re-sent on every turn, including diff-only turns in persistent sessions. It is two lines,
  and a user can switch kernels mid-session.
- It sources from `metadata.kernelspec` / `metadata.language_info`, which are present in a saved
  notebook even if no kernel has ever run — so this works with the kernel stopped, consistent
  with the rest of the design.
- If both are absent (a genuinely blank untitled notebook), the agent is told the language is
  unknown and instructed to ask in one line of prose rather than guess. Guessing Python is how
  an R user's first impression of this tool gets ruined.

This is what makes D8's level-3 fallback function at all: "use the language's standard idiom" is
meaningless unless the language is known in exactly the case where the notebook cannot tell you.
