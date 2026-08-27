"""The OpenAI-compatible adapter against a stub server (docs/06)."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from jupyter_vybe.adapters import Message
from jupyter_vybe.adapters.base import AdapterSpec
from jupyter_vybe.adapters.openai_compat import OpenAICompatAdapter

SPEC = AdapterSpec("openai-compat", "openai-compat", 32768, local=False)
seen: dict = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "served-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen["payload"] = payload
        seen["auth"] = self.headers.get("Authorization")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for piece in ["<vybe:add_cell>", "x = 1", "</vybe:add_cell>"]:
            chunk = {"choices": [{"delta": {"content": piece}}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


@pytest.fixture()
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def _collect(adapter, messages):
    async def go():
        out = ""
        async for chunk in adapter.stream(messages, asyncio.Event()):
            out += chunk
        return out

    return asyncio.run(go())


def test_streams_sse_deltas(server):
    adapter = OpenAICompatAdapter(SPEC, server, "served-model")
    out = _collect(adapter, [Message("user", "hi")])
    assert out == "<vybe:add_cell>x = 1</vybe:add_cell>"


def test_the_conversation_is_sent_as_turns_not_one_blob(server):
    """Folding costs a prompt-cache hit on every turn after the first."""
    adapter = OpenAICompatAdapter(SPEC, server, "served-model")
    _collect(
        adapter,
        [
            Message("user", "first"),
            Message("assistant", "reply"),
            Message("user", "second"),
        ],
    )
    roles = [m["role"] for m in seen["payload"]["messages"]]
    assert roles == ["user", "assistant", "user"]


def test_health_reports_which_models_are_actually_served(server):
    adapter = OpenAICompatAdapter(SPEC, server, "a-model-that-is-not-there")
    status = asyncio.run(adapter.health())
    assert not status.ok
    assert "served-model" in status.detail


def test_health_names_a_missing_key_before_making_a_request():
    adapter = OpenAICompatAdapter(
        SPEC, "http://127.0.0.1:9", "m", api_key_env="VYBE_NOT_SET_XYZ"
    )
    status = asyncio.run(adapter.health())
    assert not status.ok
    assert "VYBE_NOT_SET_XYZ" in status.detail


def test_the_key_is_sent_as_a_bearer_token(server, monkeypatch):
    monkeypatch.setenv("VYBE_TEST_KEY", "sk-test")
    adapter = OpenAICompatAdapter(SPEC, server, "served-model", api_key_env="VYBE_TEST_KEY")
    _collect(adapter, [Message("user", "hi")])
    assert seen["auth"] == "Bearer sk-test"
