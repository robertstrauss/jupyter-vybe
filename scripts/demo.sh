#!/usr/bin/env bash
# Start JupyterLab on a fresh copy of the demo notebook (examples/demo).
#
#   ./scripts/demo.sh           # http://127.0.0.1:8899/lab/tree/demo.ipynb\?token\=vybe
#   ./scripts/demo.sh 8900      # another port
#
# Each run copies examples/demo to ~/vybe-demo again, so the demo starts
# broken in the same place every time and the copy in the repo stays clean.
# Uses the same ollama and stale-server checks as dev.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-8899}"
VENV="$PWD/.venv"
export PATH="$VENV/bin:$PATH"
DEMO="$HOME/vybe-demo"

[ -x "$VENV/bin/jupyter" ] || { echo "error: no venv. See docs/TESTING.md" >&2; exit 1; }

EXISTING="$(lsof -ti:"$PORT" 2>/dev/null || true)"
if [ -n "$EXISTING" ]; then
    echo "error: something is already listening on port $PORT (pid $EXISTING)." >&2
    echo "  kill $EXISTING && $0 $PORT" >&2
    exit 1
fi

if ! curl -s -o /dev/null --max-time 1 http://127.0.0.1:11434/; then
  echo "starting ollama serve..."
  nohup ollama serve > /tmp/ollama-serve.log 2>&1 &
  for _ in $(seq 1 60); do
    curl -s -o /dev/null --max-time 1 http://127.0.0.1:11434/ && break
    sleep 0.5
  done
fi

rm -rf "$DEMO"
cp -R examples/demo "$DEMO"

echo
echo "  http://127.0.0.1:$PORT/lab/tree/demo.ipynb?token=vybe"
echo
echo "  To use claude, codex or agy instead of the local model, pick \"runner\""
echo "  as the agent in Settings > Jupyter Vybe, then in another terminal:"
echo "    jupyter-vybe-local connect http://127.0.0.1:$PORT --token vybe --agent claude"
echo
exec jupyter lab --no-browser --port="$PORT" --ServerApp.token=vybe \
  --ServerApp.root_dir="$DEMO"
