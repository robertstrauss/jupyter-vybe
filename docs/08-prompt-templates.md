# 08 — Prompt Templates

PROMPT §5 asks for **separate files** for the template initial prompt, the notebook-data
filtering rules, the tooling prompt, the universal prompt, and the user-modifiable global
prompt. All five ship in `jupyter_vybe/prompts/` and are readable and editable on disk; the
global prompt is additionally editable in settings, where the settings value overrides the file.

| File | Editable by | Purpose |
|---|---|---|
| `template.md` | advanced users (file) | The skeleton with substitution tags |
| `filtering.md` | advanced users (file) | Notebook-data filtering rules, documented **and** executable spec |
| `tooling.md` | advanced users (file) | Tool grammar and constraints |
| `universal.md` | not user-editable in UI | Role, output discipline, code standards |
| `global.md` | **user, in settings** | Style conventions; the settings value wins if non-empty |

## `template.md`

```markdown
{{UNIVERSAL_PROMPT}}

{{TOOLING_PROMPT}}

## Notebook

Kernel: {{KERNEL_NAME}} — language {{KERNEL_LANGUAGE}} {{KERNEL_VERSION}}
Write code in {{KERNEL_LANGUAGE}} unless the user asks otherwise.
{{NOTEBOOK_MODE}}   <!-- "Full notebook state:" or "Changes since your last turn:" -->

{{NOTEBOOK_JSON}}

The cell you are responding to is `{{PROMPT_CELL_ID}}`. New cells you add go directly beneath it.

## User conventions

{{GLOBAL_PROMPT}}

## Request

{{USER_PROMPT}}
```

Assembly order is fixed and deliberate: the invariant instructions come first so they occupy the
most reliably attended positions, the notebook sits in the middle where truncation is cheapest,
and the user's actual request comes **last**, immediately before generation, where instruction
following is strongest. `{{GLOBAL_PROMPT}}` sits between them so user style rules stay adjacent
to the request without displacing the safety-relevant tooling rules.

With `context.persistent` enabled, only turn 1 uses the full template. Subsequent turns send:

```markdown
Kernel: {{KERNEL_NAME}} — language {{KERNEL_LANGUAGE}} {{KERNEL_VERSION}}

Changes to the notebook since your last turn:

{{NOTEBOOK_DIFF}}

## Request

{{USER_PROMPT}}
```

Kernel information is repeated on every turn rather than relied upon from turn 1: it is two lines,
and a kernel can be switched mid-session.

## `universal.md` (draft — the non-user-editable prompt)

```markdown
You are a code-generation assistant embedded in a Jupyter notebook. You do not run code.
You cannot execute cells and you have no shell. You modify the notebook only by emitting the
tool tags described below.

Your role is to supply well-known patterns and boilerplate quickly and correctly, in small,
individually verifiable units. The user is the engineer; you are not designing their project.

Rules, in order of precedence:

1. Emit tool tags for all code. Never put code in prose.
2. Write the minimum that satisfies the request. Do not add error handling, logging,
   configuration, CLI parsing, or abstraction the user did not ask for.
3. Reuse what the notebook already has. If a module is imported, use it; do not re-import.
   If a helper exists, call it. Match the naming and idiom already present in the notebook.
4. No example usage, no demo calls, no `if __name__ == "__main__"`, unless explicitly asked.
   A minimal usage line inside a docstring is fine.
5. No summary, no restatement of the request, no follow-up questions, no offers of further
   help. Any prose you write is inserted into the user's notebook as a markdown cell, so write
   prose only when it carries information the code cannot — a caveat, an assumption you had
   to make, or a genuine ambiguity. One or two sentences.
6. Prefer editing an existing cell over adding a near-duplicate one. Prefer adding a cell over
   proposing a large rewrite of a working cell.
7. Every cell you add must be independently runnable given the cells above it. One idea per
   cell.
8. If the request is ambiguous, choose the most conventional interpretation and note the
   assumption in one sentence of prose. Do not ask and do nothing.
9. Best practice, readable, brief, efficient — in that order when they conflict.
```

## `global.md` (default value of the user-editable prompt)

Per [12](12-decisions.md) D8, the default instructs the agent to infer the **user's own** style
from the code they have already written, rather than asserting any particular convention:

```markdown
Match the style of the code already in this notebook. Infer it from what is there and follow it,
even where it departs from the language's usual conventions — if the existing functions are named
in camel case, name yours in camel case. The user's existing code is the style guide.

Infer at least: identifier casing for functions, variables, classes, and constants; comment
density and whether comments are sentences or phrases; docstring presence and format; import
grouping and whether imports are aliased; and how much work a typical cell does.

Where the notebook establishes no convention — because it is empty, or has no code yet — use the
standard idiom for the kernel's language, named above.

Comments are short partial-sentence phrases on genuinely non-obvious lines only. Routine
operations need no comment.
```

### Style precedence

The three sources of style are ordered explicitly, and the tooling prompt states the order so the
agent does not have to guess when they disagree:

| | Source | Applies when |
|---|---|---|
| 1 | **The user's global prompt**, if they have written conventions into it | always wins — it is an explicit instruction |
| 2 | **Conventions inferred from existing code in the notebook** | whenever the notebook contains code |
| 3 | **The kernel language's standard idiom** — PEP 8 for Python, tidyverse for R, and so on | only when the notebook has no code to infer from |

Level 2 beating level 3 is the substance of D8: a user who writes `camelCase` functions in Python
gets `camelCase` back, and is not lectured by the model about PEP 8. Consistency with the
surrounding code beats external correctness, because the notebook is the thing being written.

Level 3 exists so that an **empty notebook still produces idiomatic code**, which is precisely
the case where there is nothing to infer from — and it is why kernel information must reach the
agent independently of notebook content (below).

## Kernel information is always present

`{{KERNEL_NAME}}`, `{{KERNEL_LANGUAGE}}`, and `{{KERNEL_VERSION}}` come from the notebook's
`metadata.kernelspec` and `metadata.language_info`, and they carry two distinct loads:

- **Which language to write in** — indispensable when the notebook has no code at all. Without
  it, a `> load the csv and show me the columns` in a fresh R notebook gets Python back.
- **Which standard idiom to fall back on** for style level 3.

Because both matter most exactly when the notebook is empty or heavily truncated, kernel
information is **pinned**: it sits in the template outside `{{NOTEBOOK_JSON}}`, it is never
subject to the truncation ladder, and it is re-sent on every turn including diff-only turns in
persistent sessions ([09](09-context-management.md)). It survives every degradation the context
manager can apply.

When no kernel is running, the values come from the notebook's saved `kernelspec` metadata, which
is present even in a never-executed notebook. If that too is absent — a genuinely blank untitled
notebook — the agent is told the language is unknown and instructed to ask in one line of prose
rather than guessing.

## `tooling.md`

Contains the grammar from [07](07-tool-protocol.md) rendered as instructions: one **placeholder**
example per tool, the position constraint on `add_cell`, the "complete source, not a patch" rule for
`propose_edit`, the cell-id addressing scheme, the caps, and — when persistent context is on —
how to read the notebook diff format from [09](09-context-management.md). Introspection tools are
templated in or out based on `agentTools.kernelIntrospection`.

### Examples must be placeholders, not plausible answers

The first end-to-end run against `qwen2.5-coder:7b` returned the tooling prompt's own
`propose_edit` example **verbatim** — a realistic `rollingMean` function — in response to
"add a function to compute the rolling mean of x". The model did not generate code at all; it
copied the nearest thing in context that looked like an answer.

The example was realistic, and that was the flaw: at 7B, a plausible worked example in the
instructions competes with the actual request. Every example in `tooling.md` is now
unmistakably non-code (`FIRST_LINE_OF_YOUR_CELL`, `THE COMPLETE NEW SOURCE OF THAT CELL`), and
the section closes with an explicit line saying the examples are shapes, never content to emit.

Re-running the same prompt afterwards produced genuinely original code — a `np.convolve`
implementation the examples never mention. This is the single highest-leverage prompt change
found so far, and it generalises: **do not put runnable, plausible code in a tooling prompt.**

### Length buys nothing, and costs real time

`tooling.md` was 3813 characters, written to convince a 7B. Halving it to 1581 was measured, not
guessed — `scripts/bench-tools.py --tooling FILE` runs both versions through the same five cases:

| | qwen2.5-coder:7b | Gemma 4 | Gemma turn-0 latency |
|---|---|---|---|
| full, 3813 chars | 13/15 | 10/10 | 23.0 s |
| compact, 1581 chars | 14/15 | 10/10 | 17.0 s |

Compliance is unchanged on both models — the qwen difference is one flip either way, which is
the run-to-run flakiness R2 already documents. The latency difference is not noise: prefill cost
grows faster than linearly in prompt length (docs/14 N4), so the preamble is paid for on every
first turn, and superlinearly.

What survived the cut is what failure taught us to keep: the listing's indent is layout not code,
address cells by bracket number, changing existing code is an edit, examples are placeholders.

## `filtering.md`

Doubles as human documentation and as the spec `snapshot.ts` implements, with the rule table from
[09](09-context-management.md) and worked before/after examples. A test asserts the table in this
file matches the constants in `snapshot.ts`, so the documentation cannot rot.

## Where conversation history comes from

In persistent mode the agent's prior turns are replayed from the **history store**
([05](05-history-pane.md)), never re-read from notebook cells. A settled prompt cell is a
rendering; if the user deletes it, the conversation is unaffected. This is also what makes it
structurally impossible for the agent to alter its own instruction history by proposing an edit —
there is no path from a notebook cell back into the message list.

## Debugging

`prompts.showAssembled` logs the fully substituted prompt to the browser console, and
`GET /jupyter-vybe/agents?debug=assembled` returns the last assembled prompt for the session.
Both are essential when tuning against a 7B model, where prompt phrasing dominates output
quality.
