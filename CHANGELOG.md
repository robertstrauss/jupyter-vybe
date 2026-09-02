# Changelog

<!-- <START NEW CHANGELOG ENTRY> -->

## 1.2.0 (unreleased)

### The round reports on itself, in the cell that caused it

- **Progress and the review summary now render in the prompt cell's output
  area** rather than in a bubble in the corner. While generating you get
  `Thinking, 47 tokens/sec` and a line for each kernel lookup; when the agent
  is done, `2 edits proposed — Enter to review · N to reject all`.
- **The prompt cell stays a code cell until every verdict is in.** It used to
  convert to markdown the moment the agent stopped talking, which destroyed the
  cell you were being asked to act on.
- **Focus returns to the prompt cell** after a round, because Shift+Enter has
  already moved on by then.
- **Two unchorded keys**, `Enter` to start reviewing and `N` to reject the lot,
  replacing `⌥E` / `⌥⇧E`. Once no verdicts are outstanding, the cells the round
  inserted become the selection.

### Fixed

- **The settings pane was blank.** A custom field renderer rendered a settings
  *group* as a React child, which throws during render and takes every setting
  with it. Two other renderers were registered against a plugin id that does
  not exist and had never once fired.
- **`Cmd+Z` after an insertion did nothing.** It now removes what the round
  inserted and hands the prompt back as an editable code cell with your
  original words in it.
- **Undo deleted the wrong cell, and the post-round highlight selected the
  wrong one**, because the id of a newly inserted cell was read inside the
  transaction that created it — where every view of the notebook is still one
  mutation behind.
- **`Enter` opened the editor instead of starting a review**: our binding tied
  with core's on specificity, and core won.
- **The history pane's diffs** were set in the UI font, overflowed the pane,
  and had no syntax highlighting. They are now monospaced, scroll inside their
  own box, and are coloured by the same highlighter JupyterLab uses for code in
  markdown.
- **A settings gear** in the history pane header, since the pane is the one
  piece of vybe chrome always on screen.

### Measured, and not shipped

Letting the model wrap tag bodies in ``` fences — on the theory that it is the
habit every model already has — makes `qwen2.5-coder:7b` *worse*: 59/60 → 56/60
on tool choice and 23/24 → 20/24 on edit targeting, repeated. The parser now
tolerates a fence anyway, because models do it whether or not they are invited
to and a fence that reaches a cell is a syntax error.

## 1.1.0

- **Inline completion** (decision D5): ghost text from the same local model as
  you type, via fill-in-the-middle. Off by default — it costs a model call at a
  keystroke cadence — and switched on under Settings ▸ Jupyter Vybe ▸ Inline
  completion. On `qwen2.5-coder:7b` locally it answers in 0.4–1.3 s.

  It is built to stay out of your way: requests are debounced, a later
  keystroke supersedes an earlier request, an answer that arrives too late is
  dropped rather than shown, and a failure is silence rather than an error in
  the editor.

- **`jupyter-vybe-doctor`**: checks an installation before you blame the model
  — both halves installed and agreeing on a version, the settings schema where
  JupyterLab looks for it, the history directory writable, and which agents
  answer (naming the machine that answered). Every check exists because that
  specific thing went wrong.

- **`scripts/bench-quality.py`** runs the generated code against assertions
  rather than counting tool tags. It changes the model comparison: qwen and
  Gemma tie on protocol (20/20 tool choice each) and do not tie on code —
  19/24 against 8/8. qwen stays the default for latency, but if what you are
  writing has to be right, Gemma is the better model.

- **`make sync-vendor`**, which three documents promised and none provided:
  diffs the vendored TurboFieldfare copies against upstream so drift is visible
  rather than silent.

- **Completion works beyond Ollama**: OpenAI-compatible endpoints fill in the
  middle through `/v1/completions`.

- **Fixed:** prompt records were being written to whatever directory Jupyter
  was started from rather than `~/.jupyter/vybe/history`, because
  `Path("") or fallback` never reaches the fallback. Found by the doctor on its
  first run.

- **Fixed:** Ollama reported an agent as healthy when its model had never been
  pulled. It now says which models are actually installed.

- **`prompts.showAssembled`** now does something: the server echoes the
  assembled prompt back before generating and the browser console logs it.

- **Orphan adoption**: a notebook renamed outside JupyterLab left its prompt
  record behind. The pane already noticed; it now offers a button to adopt it.

- **Fixed:** CI ran neither the Python tests nor the invariant gates, and its
  lint step was failing. All three now run and pass.

- Under the hood: a soak test that runs 120 rounds and asserts nothing is left
  behind — the cancelled-round path leaked a thread once already.

## 1.0.0

First working version. Prompt cells, an agent behind them, and a review step
before anything touches your notebook.

### What it does

- **Prompt cells.** A code cell starting with the trigger prefix (`>` by
  default) runs against a model instead of a kernel. Every execution route in
  JupyterLab funnels through one provider token, so the keyboard, toolbar,
  menu, palette, and "run all" are covered by one interception point — and a
  prompt cell never reaches a kernel, with or without one running.
- **Generated cells** appear below the prompt as they stream. The prompt cell
  settles into markdown holding your prompt as a blockquote.
- **Proposed edits are reviewed, never applied.** A diff overlay replaces the
  cell's editor while a proposal is pending; `Y` accepts, `N` rejects, `Enter`
  opens the merged source for hand-editing with the replaced lines retained as
  comments. The notebook model is untouched until you accept.
- **Undo in two layers.** Every round is one ydoc transaction, so a plain
  `Cmd+Z` reverts it atomically. Inside a round, `Cmd+Z` takes back one verdict
  at a time; reject-all is recoverable with `⌥⇧Z`.
- **A prompt record that outlives the cells.** Prompts, replies, insertions,
  and every proposal with its verdict are kept in a sidecar JSONL file, so
  deleting a settled prompt cell loses the rendering and nothing else. Readable
  in a pane; click any entry to paste it back as a cell.
- **Agents:** Ollama (Qwen2.5-Coder 3B/7B/14B), TurboFieldfare (Gemma 4
  26B-A4B), any command that writes to stdout (`claude -p`), and any
  OpenAI-compatible endpoint. All local by default; nothing leaves the machine
  unless you point it somewhere that does.
- **Context management.** Turn one sends the notebook; later turns send an
  index and a diff. Under pressure, a truncation ladder drops outputs, then
  distant prose, then distant code bodies — keeping signatures, the prompt
  cell's neighbours, and every import.

### Things worth knowing

- **The default agent is qwen2.5-coder:7b, not the better model.** Gemma 4
  scores higher on tool use (10/10 against 13/15) but costs ~17–30 s on a first
  prompt against qwen's ~2 s. Both are selectable; the trade is documented in
  `docs/14-next-steps.md`.
- **Follow-up turns are much faster than the first** when the agent supports
  prompt caching — measured at 28 s, then 8.5 s, then 7.0 s across one session.
- **A model that will not emit tool tags is still usable**: a reply with fenced
  code blocks and no tags is salvaged into cells rather than dumped into the
  notebook as prose.
- **Nothing is written to notebook metadata.** A collaborator without the
  extension opens an ordinary notebook.

- **`jupyter-vybe-local`**, the client-side sidecar, ships in this release rather
  than waiting: run it on the machine that has the model when Jupyter is
  somewhere else, and set `agent.host` to `client`. Standard library only, so
  `pipx run --spec jupyter-vybe jupyter-vybe-local` is the whole install. It is
  what makes `claude -p` usable from a remote Jupyter, since a CLI's
  credentials live on the machine it runs on.

### Requires

JupyterLab ≥ 4.4, Python ≥ 3.9, and a model to talk to.

<!-- <END NEW CHANGELOG ENTRY> -->
