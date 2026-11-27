# Changelog

<!-- <START NEW CHANGELOG ENTRY> -->

## 1.2.0 (unreleased)

### Follow-up turns show the notebook as it is now

- **The agent sees what a cell printed after the first turn.** Follow-ups said
  only "output changed", so a traceback never reached the agent and "fix the
  error" was a guess. Outputs now go in the diff, in the listing's form.
- **Every changed cell is shown as it now is, after the diff and right before
  the request**, so the agent no longer pieces the present together from a
  chain of diffs. Cells it already has word for word stay one line, so the
  cost follows what changed, not the notebook's size. On qwen 7B, fixing the
  cell whose error it had just been shown went from 0/10 to 14/16, and "edit
  the cell above" still lands on the right cell (16/16) (docs/09).
- **An error now shows the line that raised it.** The agent gets the message,
  then the first frame of the traceback (where the cell called in) and the
  last (where it raised), each as `location: line of code`, with the frames
  between counted. `evalue` is capped at 300 characters and kept on one line,
  and the browser keeps a deep traceback's first frames as well as its last.
- **`./scripts/demo.sh`** starts a lab on a fresh copy of `examples/demo`: a
  small stdlib-only sales notebook that fails on purpose, with ready-made
  prompt cells to fix it and carry on.

- **The notebook is read while you type the request.** Typing the prompt
  prefix sends the notebook to the model at once, and again after a pause if
  the notebook changes, so by shift-enter only the request is left to read.
  On Gemma 4 through TurboFieldfare the first cell now starts in about 4 s
  instead of 40 s; on qwen 7B through Ollama, 0.5 s instead of 7 s. For local
  models a turn is now three messages: the notebook, the model's one-word
  "Ready", then the request. That is the only shape TurboFieldfareServer's
  cache can reuse. The listing no longer repeats the prompt cell's text, which
  is the request anyway.

- **Zeta 2, Zed's edit predictor, as an agent** (`zeta2`). It runs under
  llama-server with prompt-lookup speculative decoding. It is not a chat model,
  so a turn becomes an edit prediction:
  - The notebook is one percent-format Python file.
  - New code is plain fill-in-the-middle.
  - A change or fix is the request typed atop the target cell, with the error
    in the diagnostics section.

  Its answers arrive as the same proposals as any other agent's. On the same
  20 cases it scores 18/20 at a median 0.6 s. qwen 7B scores 19/20 at 2.3 s,
  and Gemma 4 scores 19/20 at 37 s, or 3.3 s with read-ahead (docs/15).

### Fixes

- **The toolbar's stop button cancels a running prompt.** It interrupted only
  the kernel, which a prompt never uses, so the round carried on and settled
  the prompt into markdown a few seconds later.
- **An edit that changes nothing is reported, not silently dropped.** The diff
  preview used to stream in and then vanish before it could be reviewed; the
  prompt cell now says the agent proposed no change, and why.
- **Outputs left over from before an edit are marked as such.** After a fix
  was accepted but not rerun, the agent still saw the old traceback under the
  fixed code and "fixed" the cell into itself again.
- **An edit aimed at the prompt cell becomes a new cell below it.** Accepted,
  it had put the code into the prompt, which then settled into markdown.
- **Settled prompt cells no longer end with an extra newline**, nor a bare
  `>` line when the prompt was typed with a trailing Enter.

### Your agent, on your machine, without a hole in it

- **Two ways to run a command without the token are closed** (docs/13 R16).
  The 1.1 sidecar accepted a WebSocket from any web page, and the session it
  reached ran whatever command that page named. The server's agent socket
  admitted any process that reached the port, token or not. Both sockets now
  use Jupyter's own authentication, and gates T-AUTH and T-RUNNER keep it so.
- **`jupyter-vybe-local connect <url> --agent claude|codex|agy|gemini`**
  replaces the listening sidecar. It dials _out_ to the server with your token
  and listens on nothing; choose **runner** as the agent. Your CLI's own login
  is used — no API key. The server can send it text and nothing else: the agent
  and its flags are fixed when you start it, and each preset turns the agent's
  own tools off (verified per agent by planting a file and asking for it).
  `agent.host` and `agent.sidecarUrl` are gone.
- **A Claude session stays open per notebook**, so only the new turn is sent:
  ~1.6 s a round after the first, against ~3 s one-shot. Text streams as it is
  written.
- **Persistent `claude -p` works with the flags the settings describe.** It
  needs `--verbose`, which is now added; the one-shot fallback no longer
  carries the stream-json flags that made it fail too.
- **An agent that replies with nothing says why.** `agy -p` exits 0 having
  printed nothing when a tool it wanted was denied; its reason is on stderr,
  and is now the round's error rather than an empty round that looked done.
- **A long round is no longer cut off at 90 s**, and a cancel or a kernel
  lookup's result reaches the server while a round is running. The round used
  to block the socket's reads until it finished.

### Cells are written in front of you

- **A cell appears the moment its tag opens and fills in as the model writes
  it**, rather than at the end. The parser used to buffer a body until its
  closing tag, which on a local model is most of the wait spent watching
  nothing. Fences and copied-in indentation are cleaned as it streams, so
  nothing appears that has to be taken back, and the closing tag remains
  authoritative.
- **An edit streams into its diff**, not into your cell: nothing is applied
  without a verdict, so the proposal is staged when the tag opens and its diff
  grows as the body arrives. In-flight diffs are dimmed and read
  "writing an edit to cell 3…".

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
- **One piece of floating chrome, and only one**: a translucent counter at the
  top of the notebook while edits are open — how many are left, a button to
  reject the rest, and a reminder that `Cmd+Z` takes a verdict back. It exists
  for a single failure mode, walking away with edits unresolved, so it appears
  exactly while that is possible. Progress, the context meter and errors all
  moved into the prompt cell alongside everything else.

### Fixed

- **A protocol complaint could end up in your notebook in place of the code.**
  A `propose_edit` naming no cell used to write "_Protocol error: …_" into the
  settled prompt cell, losing whatever the model meant to write. The model now
  gets exactly one correction — told what was wrong and asked to send the reply
  again, with the failed attempt reverted first — and protocol errors are never
  written into the notebook. A stray ``` left outside a tag is stripped too.
- **`Thinking, 0 tokens/sec`** before the first token. There is no rate yet; it
  now says how long it has been reading the notebook instead. Measured: on a
  60-cell notebook, prompt assembly is 0.1 ms, so the wait is the model, not
  us — 3.5 s for a cold model load, ~0.6 s once warm and cached.
- **Ollama unloaded the model after five minutes idle**, so a prompt written
  after a pause paid the whole cold load again. Requests now carry
  `keep_alive` (30 minutes; `VYBE_OLLAMA_KEEP_ALIVE` to change).

- **`Thinking, 0 tokens/sec`.** The rate was counted from what the frontend
  could see, but the tag parser buffers a body until its closing tag — so a
  reply made entirely of tool tags, which is the normal case, streamed nothing
  to count until it had finished. The server reports progress from the raw
  stream now, four times a second.
- **The settings pane was blank.** A custom field renderer rendered a settings
  _group_ as a React child, which throws during render and takes every setting
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
habit every model already has — makes `qwen2.5-coder:7b` _worse_: 59/60 → 56/60
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
