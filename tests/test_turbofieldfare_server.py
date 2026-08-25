"""The TurboFieldfareServer path, which /health answering does not prove.

The adapter prefers the server whenever /health answers, but it was sending
the model *directory* as the API model field and the server 404s on an id it
does not know -- so every call fell over as soon as the server was actually
running (docs/14 N4).
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from jupyter_vybe.adapters import Message, build_adapter

MODEL_ID = "gemma-4-26b-a4b-it"
seen: dict = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep pytest output clean
        pass

    def do_GET(self):
        if self.path == "/health":
            body = b'{"status":"ok"}'
        elif self.path == "/v1/models":
            body = json.dumps(
                {"data": [{"id": MODEL_ID, "object": "model"}], "object": "list"}
            ).encode()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        payload = json.loads(
            self.rfile.read(int(self.headers["Content-Length"])).decode()
        )
        seen["payload"] = payload
        if self.path != "/v1/chat/completions" or payload.get("model") != MODEL_ID:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for piece in ["<vybe:add_cell", ' type="code">\nx = 1\n', "</vybe:add_cell>"]:
            chunk = {"choices": [{"delta": {"content": piece}}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


@pytest.fixture()
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def _collect(adapter) -> str:
    async def go():
        out = ""
        async for chunk in adapter.stream([Message("user", "hi")], asyncio.Event()):
            out += chunk
        return out

    return asyncio.run(go())


def test_streams_from_the_server_using_its_own_model_id(server, monkeypatch):
    seen.clear()
    monkeypatch.setenv("TURBOFIELDFARE_HOST", server)
    adapter = build_adapter("turbofieldfare-gemma4")
    adapter.host = server

    out = _collect(adapter)

    assert seen["payload"]["model"] == MODEL_ID
    assert "/" not in seen["payload"]["model"], "sent a path, not the server's id"
    assert out == '<vybe:add_cell type="code">\nx = 1\n</vybe:add_cell>'


def test_health_reports_the_server_when_it_answers(server):
    adapter = build_adapter("turbofieldfare-gemma4")
    adapter.host = server
    status = asyncio.run(adapter.health())
    assert status.ok
    assert status.detail == "server"
