#!/usr/bin/env bash
# Start a dev JupyterLab with jupyter-vybe loaded, plus a scratch notebook.
#
#   ./scripts/dev.sh            # start on :8899
#   ./scripts/dev.sh 8900       # start on another port
#
# Rebuild after changing TypeScript:   jlpm build
# Restart this script after changing jupyter_vybe/prompts/*.md (they are cached).
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-8899}"
VENV="$PWD/.venv"
export PATH="$VENV/bin:$PATH"

[ -x "$VENV/bin/jupyter" ] || { echo "error: no venv. See docs/TESTING.md" >&2; exit 1; }

# A server already on this port keeps serving its OWN code. Python imports
# modules once at startup, so an editable install does NOT pick up edits: an
# old server plus a fresh browser reload silently tests stale code, which has
# already cost hours of misdiagnosis. Refuse to start alongside one.
EXISTING="$(lsof -ti:"$PORT" 2>/dev/null || true)"
if [ -n "$EXISTING" ]; then
    echo "error: something is already listening on port $PORT (pid $EXISTING)." >&2
    echo "       It is serving its own copy of the code, not your working tree." >&2
    echo >&2
    echo "  kill $EXISTING && $0 $PORT" >&2
    exit 1
fi

# The agent needs ollama; start it if it is not already up (as code-llm/run.sh does).
if ! curl -s -o /dev/null --max-time 1 http://127.0.0.1:11434/; then
  echo "starting ollama serve..."
  nohup ollama serve > /tmp/ollama-serve.log 2>&1 &
  for _ in $(seq 1 60); do
    curl -s -o /dev/null --max-time 1 http://127.0.0.1:11434/ && break
    sleep 0.5
  done
fi
ollama list | grep -q 'qwen2.5-coder:7b' || echo "warning: qwen2.5-coder:7b not pulled (ollama pull qwen2.5-coder:7b)"

export VYBE_TRANSCRIPT="${VYBE_TRANSCRIPT:-/tmp/vybe-transcript.jsonl}"
rm -f "$VYBE_TRANSCRIPT"
echo "transcript: $VYBE_TRANSCRIPT"

mkdir -p ~/vybe-scratch
cat > ~/vybe-scratch/scratch.ipynb <<'NB'
{"cells":[{"cell_type":"code","execution_count":null,"id":"setup001","metadata":{},"outputs":[],"source":"import numpy as np\nx = np.arange(20)"},{"cell_type":"code","execution_count":null,"id":"prompt01","metadata":{},"outputs":[],"source":"> add a function that normalises x to zero mean and unit variance"}],"metadata":{"kernelspec":{"display_name":"Python 3","language":"python","name":"python3"},"language_info":{"name":"python","version":"3.13"}},"nbformat":4,"nbformat_minor":5}
NB

REV="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
DIRTY=""
git diff --quiet 2>/dev/null || DIRTY=" +uncommitted changes"
echo
echo "  serving commit $REV$DIRTY"
echo "  http://127.0.0.1:$PORT/lab/tree/scratch.ipynb?token=vybe"
echo
exec jupyter lab --no-browser --port="$PORT" --ServerApp.token=vybe \
  --ServerApp.root_dir="$HOME/vybe-scratch"
