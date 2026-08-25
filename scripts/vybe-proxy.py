#!/usr/bin/env python3
"""Logging proxy between Jupyter Vybe and an HTTP model backend.

Sits in front of Ollama (or any OpenAI-compatible server), forwards requests
verbatim, and records exactly what was sent and exactly what came back. Use it
when you need to tell "the model misbehaved" apart from "we mishandled a good
reply" -- the two look identical from the notebook.

    # terminal 1
    python scripts/vybe-proxy.py

    # terminal 2 -- point Vybe at the proxy instead of Ollama
    CODELLM_HOST=http://127.0.0.1:11435 ./scripts/dev.sh

    # terminal 3 -- watch exchanges as they happen
    python scripts/vybe-proxy.py --show

Streaming is preserved: chunks are relayed as they arrive, not buffered, so
the notebook behaves exactly as it does without the proxy.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from tornado import httpclient, httpserver, ioloop, web

DEFAULT_LOG = "/tmp/vybe-proxy.jsonl"


class ProxyHandler(web.RequestHandler):
    SUPPORTED_METHODS = ("GET", "POST", "PUT", "DELETE", "HEAD")

    def initialize(self, upstream: str, log_path: str) -> None:
        self.upstream = upstream.rstrip("/")
        self.log_path = log_path

    def _log(self, record: dict) -> None:
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    async def _forward(self) -> None:
        url = f"{self.upstream}{self.request.uri}"
        body = self.request.body.decode("utf-8", "replace") if self.request.body else ""
        started = time.time()
        chunks: list[str] = []

        def on_chunk(chunk: bytes) -> None:
            text = chunk.decode("utf-8", "replace")
            chunks.append(text)
            self.write(chunk)
            self.flush()

        client = httpclient.AsyncHTTPClient()
        try:
            await client.fetch(
                httpclient.HTTPRequest(
                    url,
                    method=self.request.method,
                    body=self.request.body if self.request.body else None,
                    headers=self.request.headers,
                    streaming_callback=on_chunk,
                    request_timeout=600,
                    allow_nonstandard_methods=True,
                )
            )
            error = None
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised as 502
            error = f"{type(exc).__name__}: {exc}"

        raw = "".join(chunks)
        record = {
            "ts": started,
            "elapsed": round(time.time() - started, 2),
            "method": self.request.method,
            "path": self.request.path,
            "request": _parse(body),
            "response_text": _assemble(raw, self.request.path),
            "response_raw_bytes": len(raw),
            "error": error,
        }
        # /api/version and other chatter is noise; only record generations.
        if self.request.path.rstrip("/").endswith(("chat", "generate", "completions")):
            self._log(record)
        if error:
            self.set_status(502)
            self.finish(json.dumps({"error": error}))

    get = post = put = delete = head = _forward


def _parse(body: str):
    try:
        return json.loads(body)
    except Exception:
        return body


def _assemble(raw: str, path: str) -> str:
    """Reassemble a streamed response into the text the model actually produced."""
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("data: "):
            line = line[6:]
            if line == "[DONE]":
                continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        # Ollama /api/chat, Ollama /api/generate, OpenAI-compatible
        out.append(obj.get("message", {}).get("content", ""))
        out.append(obj.get("response", ""))
        for choice in obj.get("choices", []) or []:
            out.append(choice.get("delta", {}).get("content", ""))
            out.append(choice.get("text", ""))
    return "".join(o for o in out if o)


def show(log_path: str, last: int, full: bool) -> int:
    p = Path(log_path)
    if not p.exists():
        print(f"no log at {log_path} -- is the proxy running, and is Vybe pointed at it?")
        return 1
    records = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    if not records:
        print("log is empty -- no generation requests have gone through the proxy yet.")
        return 1
    for rec in records[-last:]:
        req = rec["request"]
        msgs = req.get("messages") if isinstance(req, dict) else None
        print("=" * 78)
        print(f"{rec['method']} {rec['path']}   {rec['elapsed']}s"
              + (f"   ERROR {rec['error']}" if rec["error"] else ""))
        if isinstance(req, dict):
            print(f"model: {req.get('model')}   stream={req.get('stream')}")
        print("-" * 78)
        print(">>> SENT TO MODEL")
        if msgs:
            for m in msgs:
                content = m.get("content", "")
                if not full and len(content) > 1600:
                    head, tail = content[:1100], content[-500:]
                    content = f"{head}\n\n[... {len(m['content']) - 1600} chars elided, --full to see ...]\n\n{tail}"
                print(f"  [{m.get('role')}]")
                for line in content.split("\n"):
                    print("   ", line)
        else:
            print("   ", str(req)[:2000])
        print("-" * 78)
        print("<<< RETURNED BY MODEL")
        text = rec["response_text"] or "(nothing decoded)"
        for line in text.split("\n"):
            print("   ", line)
        print("-" * 78)
        print("verdict:",
              "TOOL TAGS" if "<vybe:" in text
              else "MARKDOWN FENCE" if "```" in text
              else "PLAIN PROSE")
    print("=" * 78)
    print(f"{len(records)} exchange(s) in {log_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--upstream", default="http://127.0.0.1:11434")
    ap.add_argument("--port", type=int, default=11435)
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--show", action="store_true", help="print recorded exchanges and exit")
    ap.add_argument("--last", type=int, default=1, help="with --show, how many to print")
    ap.add_argument("--full", action="store_true", help="with --show, do not elide the prompt")
    args = ap.parse_args()

    if args.show:
        return show(args.log, args.last, args.full)

    Path(args.log).write_text("")
    app = web.Application([
        (r".*", ProxyHandler, {"upstream": args.upstream, "log_path": args.log}),
    ])
    httpserver.HTTPServer(app).listen(args.port)
    print(f"vybe proxy  {args.upstream}  ->  http://127.0.0.1:{args.port}")
    print(f"logging to  {args.log}")
    print()
    print(f"  CODELLM_HOST=http://127.0.0.1:{args.port} ./scripts/dev.sh")
    print(f"  python {sys.argv[0]} --show")
    print()
    ioloop.IOLoop.current().start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
