# Testing Jupyter Vybe by hand

## Start it

```bash
cd ~/Desktop/jupyter-vibe
./scripts/dev.sh
```

That starts `ollama serve` if it is not already up, writes a scratch notebook to
`~/vybe-scratch/scratch.ipynb`, and prints a URL. Ctrl+C to stop.

If you have never built it in this checkout:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]" jupyterlab==4.6.3 jupyter-builder
export PATH="$PWD/.venv/bin:$PATH"
jlpm install && jlpm build
jupyter labextension develop --overwrite .    # needs .venv/bin on PATH
```

## The five-minute tour

**1. The core loop.** The scratch notebook's second cell is a prompt:

```
> add a function that normalises x to zero mean and unit variance
```

Click it, `Shift+Enter`. Watch for three things:

- the input prompt shows `[*]` while it thinks, but the **kernel indicator stays Idle** —
  nothing was executed
- a code cell appears below with generated code, selected
- the prompt cell becomes a markdown blockquote

First run takes ~10s while Ollama loads the 4.7GB model; after that it is a few seconds.

**2. The invariant that matters.** Stop the kernel entirely (Kernel ▸ Shut Down Kernel), then run
another prompt cell. It still works. There is no kernel path.

**3. It really is a normal notebook.** Save, then:

```bash
python -m json.tool ~/vybe-scratch/scratch.ipynb | grep -A3 '"metadata"'
```

No `jupyter_vybe` key anywhere, no custom cell metadata, `execution_count` null on everything
the agent produced.

**4. Delegation off.** Settings ▸ Settings Editor ▸ Jupyter Vybe ▸ untick **Enable Jupyter Vybe**.
Now run a `>` cell: it executes as ordinary code and fails with a `SyntaxError`. That is correct —
it proves the executor is delegating to core rather than swallowing cells.

**5. Change the prefix.** Same settings page, set the prompt prefix to `>>`. Old `>` cells now run
as code; `>>` cells prompt. Try setting it to `-` or `x`: rejected, because those start valid code.

## Writing your own prompts

Anything works, but the tool is built for small, verifiable units. Good shapes:

```
> load ~/data/foo.csv into a dataframe and show the dtypes
> add type hints to the function above
> plot y against x with axis labels
```

The agent sees the whole notebook, so "the function above" resolves.

## What is not built yet

Be aware before you go looking for these:

- **Proposed edits (the diff review UI) are untested.** The code is there — staging, the
  green/red overlay, `Y`/`N`/`Enter`, the status bubble — but no run has yet produced a proposal,
  so treat the whole review flow as unverified. If a prompt does trigger one, that is the first
  real exercise of it.
- **No history pane**, and no durable prompt store. Deleting a settled prompt cell currently does
  lose the prompt, contrary to D12.
- **Persistent sessions always send a full snapshot** — the notebook diff summariser is not
  written, so long sessions burn context faster than intended.
- **No Cmd+Z round semantics.** Plain notebook undo works (every round is one ydoc transaction),
  but the round-scoped undo from docs/02 is not wired.
- **Ollama only.** TurboFieldfare, `claude -p`, and OpenAI-compatible endpoints are not built.
- **No context meter** in settings.

## Gotchas

- **Editing `jupyter_vybe/prompts/*.md` needs a server restart** — they are `lru_cache`d.
- **Editing TypeScript needs `jlpm build`**, then reload the browser. `jlpm watch` does it
  automatically.
- If JupyterLab comes up **blank**, that is a plugin registration failure. Check the browser
  console: a `Cycle detected` error means a dependency edge was added to the cell executor.
- The extension must be a symlink for edits to show up:
  `ls -la .venv/share/jupyter/labextensions/ | grep vybe` should print an arrow, not a directory.

## Automated checks

```bash
./scripts/check-invariants.sh          # the five structural gates
.venv/bin/python -m pytest tests/ -q   # 19 protocol golden tests
jlpm test                              # 39 jest tests
```
