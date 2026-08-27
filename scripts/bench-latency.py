#!/usr/bin/env python3
"""Where the 24 s per call goes, and whether any of it is recoverable.

The tool bench (bench-tools.py) answers "does the model use its tools". This
answers "why is it slow", which is a different question with a different shape:
prompt size against wall clock, and whether the server's prompt cache can be
made to hit at all (docs/14 N4).

    .venv/bin/python scripts/bench-latency.py sweep
    .venv/bin/python scripts/bench-latency.py continuation
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

sys.path.insert(0, ".")

DEFAULT_HOST = "http://127.0.0.1:8080"
# One line of filler is close enough to one token per word for a slope; the
# absolute token count comes from the server log, not from this estimate.
FILLER = "x = compute_value(index, offset)  # step\n"


def model_id(host: str) -> str:
    with urllib.request.urlopen(f"{host}/v1/models", timeout=5) as resp:
        return json.load(resp)["data"][0]["id"]


def call(host: str, model: str, messages: list[dict], max_tokens: int) -> tuple[str, float]:
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": False,
    }
    req = urllib.request.Request(
        f"{host}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=600) as resp:
        body = json.load(resp)
    elapsed = time.monotonic() - started
    return body["choices"][0]["message"]["content"], elapsed


def sweep(host: str, model: str) -> None:
    """Latency against prompt size, with the reply pinned short.

    If the line is steep, the fixed preamble is the cost and trimming it pays.
    If it is flat with a high intercept, trimming buys nothing.
    """
    print(f"{'lines':>7} {'approx chars':>13} {'seconds':>9}")
    for lines in (1, 40, 120, 260, 500):
        prompt = FILLER * lines + "\nReply with the single word: ok"
        _, secs = call(host, model, [{"role": "user", "content": prompt}], 8)
        print(f"{lines:>7} {len(prompt):>13} {secs:>9.1f}")


def continuation(host: str, model: str) -> None:
    """Can the prompt cache hit at all, and what is a hit worth?

    single-prefix caches the previous prompt *and* its completion, so a hit
    needs the next request to continue that conversation. Two independent
    requests sharing a preamble always miss -- which is exactly how the
    extension sends its rounds today.
    """
    preamble = FILLER * 260
    first = [{"role": "user", "content": preamble + "\nReply with the single word: one"}]
    reply, secs1 = call(host, model, first, 8)
    print(f"  turn 1 (cold)                    {secs1:>6.1f}s")

    second = first + [
        {"role": "assistant", "content": reply},
        {"role": "user", "content": "Reply with the single word: two"},
    ]
    _, secs2 = call(host, model, second, 8)
    print(f"  turn 2 as a continuation         {secs2:>6.1f}s")

    independent = [
        {"role": "user", "content": preamble + "\nReply with the single word: three"}
    ]
    _, secs3 = call(host, model, independent, 8)
    print(f"  turn 3 independent, same preamble {secs3:>5.1f}s")
    print(
        "\n  A continuation that is much faster than turn 1, next to an "
        "independent\n  call that is not, is the cache working exactly as "
        "documented and not\n  for the shape of request we send."
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["sweep", "continuation"])
    ap.add_argument("--host", default=DEFAULT_HOST)
    args = ap.parse_args()
    host = args.host.rstrip("/")
    model = model_id(host)
    print(f"host {host}  model {model}\n")
    (sweep if args.mode == "sweep" else continuation)(host, model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
