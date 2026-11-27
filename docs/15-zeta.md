# 15 — Zeta 2: an edit predictor as a notebook agent

Zed's Zeta 2 is Seed-Coder-8B fine-tuned to predict a programmer's next edit.
It runs locally under llama.cpp (`llama-server`, port 8012) with prompt-lookup
speculative decoding. This page records what it was found to want, how a
notebook turn is mapped onto that, and how it compares with the chat models.

Code: `jupyter_vybe/zeta.py` (the turn), `jupyter_vybe/adapters/zeta.py`
(HTTP), `AgentSession._run_zeta` (wiring), `scripts/bench-agents.py` (the
benchmark), `tests/test_zeta.py`.

## What it takes and what it returns

It is a completion model with no chat template (Ollama's is `{{ .Prompt }}`).
The format is Zed's `zeta2`, `V0211SeedCoder` in
[`crates/zeta_prompt`](https://github.com/zed-industries/zed/tree/main/crates/zeta_prompt):
a suffix-prefix-middle fill-in-the-middle prompt.

```text
<[fim-suffix]>code after the editable region
<[fim-prefix]><filename>diagnostics
*NameError: name 'b' is not defined*:
```

    return sum(a) / len(b)

```
<filename>edit_history
--- a/notebook.py
+++ b/notebook.py
@@ -4,0 +5,1 @@
+# fix the error
<filename>notebook.py
code before the region
<<<<<<< CURRENT
# %%
# fix the error
<|user_cursor|>def mean(a):
    return sum(a) / len(b)
=======
<[fim-middle]>
```

It replies with the region rewritten, then `>>>>>>> UPDATED`. The reply is code
only: no prose, no fences, no tags. Zed budgets 4,096 prompt tokens and a
350-token editable region.

## Mapping a notebook turn onto it

- **The notebook is one file**, `notebook.py`, in jupytext percent form: `# %%`
  between cells, markdown commented out, outputs as `# Out:` comments.
  Errors go in the `diagnostics` section, which points at the line that
  raised. That is the innermost traceback frame in the cell.
- **New code is plain fill-in-the-middle** (see "Native use" below): the prefix
  ends at the request comment in a fresh cell, and the model writes what goes
  between. The editable-region chain is the fallback if it writes nothing.
- **The request is a comment the user has just typed.** It appears in the file
  and also as the newest event in `edit_history`, which is the channel the
  model was trained to read intent from.
- **Changes since the last turn are the edit history.** They are unified diffs of
  the notebook, with outputs left out so a run alone is not an edit. This is the
  model's own format for "what the user has been doing". Commenting old code out
  as "the previous version" would be a format it was never trained on.
- **Kernel context and tooltips are not sent.** The model cannot ask for a
  lookup, and the file is Python by construction.

## The cursor says where

Measured on the demo notebook, temperature 0:

| Request placement                                                                        | Result                                                                      |
| ---------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| "fix the error" as a comment in the prompt cell, failing cell inside the editable region | no edit (region copied back)                                                |
| the same, region widened to three cells                                                  | no edit                                                                     |
| cursor at the end of the failing line, error in diagnostics                              | fixed 2 of 4 cases (none on NameError, reshape)                             |
| request at the end of the target cell                                                    | appends: "change double to triple" became a new `triple()` below `double()` |
| **request as the first line of the target cell, cursor after it**                        | **fixed or edited in place in 6 of 6 probes**                               |
| request at the end of the target cell, for "round the revenue"                           | the only placement that answered it                                         |

An instruction never reaches across cells. The model edits where the cursor
is, so the turn chooses the place:

- new code: the prompt cell, with the request as its comment;
- a change ("change", "rename", "instead", "above", ...): the nearest code
  cell above, with the request as its first line;
- a fix ("fix", "error", "bug", ...) when a cell above has an error: that cell,
  with the error in diagnostics.

If the top of the cell predicts nothing, the turn asks again with the request
at the bottom of the cell.

## It predicts the next edit, not the whole answer

Asked for "add a function top_products(n)", the first prediction is
`def top_products(n):` with the cursor left in an empty body: the edit a
person would make next. Predictions are chained: each is applied and the
model is asked again, until one changes nothing or leaves no cursor behind.
New functions take two steps, about 2 s in all, and fixes take one.

## Speculative decoding, and whether it says where to edit

`llama-server --spec-type ngram-mod` drafts tokens from text already in the
prompt. An edit predictor mostly copies its region back, so the drafts are
accepted almost every time when it rewrites existing code. Zed's own sessions in the server log
show 96% acceptance and 165–184 tokens/s. In these probes, a multi-cell
rewrite accepted 70–75% of drafts, and new code about half. Widening the
editable region from two cells to the whole notebook (93 to 253 output tokens)
added 0.4 s.

So speculative decoding makes a large editable region cheap, and diffing the
rewrite against the original localises the edit exactly. That is what the
draft acceptance would tell you, without needing the token-level trace. But it
does not make the model _choose_ a distant cell: with the whole notebook
editable, it still only edited around the cursor. The place has to come from
the turn's layout above, not from the decoder.

The 1.7B `zeta-2.1-distill` cannot be a draft model for Zeta 2. Its LFM
tokenizer (128k) does not match Seed-Coder's (155k), and this llama.cpp has
no vocabulary translation.

## Benchmark

`scripts/bench-agents.py --run`: every agent through `AgentSession.run`, the
same notebooks and requests, the answer applied as accepting it would, the
notebook executed with a check. 8 new-function cases, 6 change/fix cases, and 6
held-out cases written after Zeta's placement was tuned. Times are from submit
to done.

| agent                        | new code | changes | held out | edited in place | median s | mean s |
| ---------------------------- | -------- | ------- | -------- | --------------- | -------- | ------ |
| zeta2                        | 7/8      | 5/6     | 6/6      | 9/10            | **0.6**  | 0.7    |
| qwen2.5-coder 7B             | 7/8      | 6/6     | 6/6      | 10/10           | 2.3      | 2.8    |
| qwen2.5-coder 7B, read ahead | 7/8      | 5/6     | 6/6      | 10/10           | 1.4      | 2.0    |
| Gemma 4 26B-A4B              | 8/8      | 5/6     | 6/6      | 10/10           | 37.0     | 37.0   |
| Gemma 4 26B-A4B, read ahead  | 8/8      | 5/6     | 6/6      | 10/10           | 3.3      | 3.4    |

The first zeta2 run used the editable-region chain for new code. It scored the
same (18/20), with a median of 1.3 s. Single runs at temperature 0 vary by
about one case between repeats, so the agents are statistically level on
accuracy here. They differ in speed.

What failed:

- zeta2 returned a list from `running_max`, and renamed `squares` to `cubes`.
  It did the second under every placement tried.
- Qwen's `top_k_indices` came out ascending, a failure noted before
  (docs/09). With read-ahead it also failed the `squares` case.
- Gemma also failed the `squares` case.

## Native use: is the layout fair to it?

The request comment is the one thing in the prompt that Zeta never saw in
training: nobody types instructions to their editor. So
`scripts/bench-zeta-native.py --run` measures it in forms that need none, on
the same cases, inserting the result where the product would:

| task | form                                                                 | tuned | held out | median s | draft acceptance |
| ---- | -------------------------------------------------------------------- | ----- | -------- | -------- | ---------------- |
| new  | product's editable-region chain (request comment, cursor)            | 7/8   | 1/2      | 1.9      | 32%              |
| new  | **started edit**: comment and signature typed, cursor in the body    | 7/8   | 2/2      | 1.1      | 34%              |
| new  | **plain FIM**: prefix ends at the request comment                    | 7/8   | 2/2      | **0.6**  | –                |
| new  | plain FIM, signature typed                                           | 6/8   | 2/2      | 0.4      | –                |
| fix  | product: request atop the cell, error in diagnostics                 | 3/3   | 2/2      | 0.7      | –                |
| fix  | native: cursor on the raising line plus diagnostics, no request text | 1/3   | 0/2      | 0.4      | 29%              |
| fix  | plain FIM of the raising line, error as a comment                    | 0/3   | 0/2      | 0.3      | –                |

- **New code:** the native forms are at least as accurate and two to three
  times faster. Plain FIM is now the product's path for new code.
- **Fixes:** the native forms fail. Shown an error with no request, Zeta
  mostly copies the line back. Through plain FIM it rewrites the bug or writes
  a comment: `y = x.reshape(3, 7)` came back as `x.reshape(3, 7)`. The
  instruction comment is what makes a fix work, so fixes and changes keep the
  editable-region layout.
- **Speculative decoding** accepts about a third of drafts when the model
  writes new code, because there is little to copy. A dash means nothing was
  drafted. It matters for rewrites: 70–96% acceptance when the region is
  mostly copied back.
