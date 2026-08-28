"""The client-side sidecar (D2 v1.1): transport only, same session underneath.

Every test drives it the way a browser would -- a real socket, a real RFC 6455
handshake, real frames -- because the whole point of the sidecar is that a
browser can talk to it where it cannot talk to Ollama.
"""

import asyncio
import base64
import json
import os
import socket
import struct
import threading
import time

import pytest

from jupyter_vybe.adapters import Message
from jupyter_vybe.local import Frame, Sidecar, _accept_key


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    """A blocking websocket client, small enough to be obviously correct."""

    def __init__(self, port: int, origin: str = "https://cluster.example.org"):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                "GET /ws HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                f"Origin: {origin}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        self.buffer = b""
        header = self._read_until(b"\r\n\r\n")
        assert b"101 Switching Protocols" in header, header
        assert _accept_key(key).encode() in header
        self.handshake = header

    def _read_until(self, sentinel: bytes) -> bytes:
        while sentinel not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            self.buffer += chunk
        head, _, rest = self.buffer.partition(sentinel)
        self.buffer = rest
        return head + sentinel

    def _recv_exact(self, n: int) -> bytes:
        while len(self.buffer) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("closed")
            self.buffer += chunk
        out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out

    def send(self, payload: dict) -> None:
        data = json.dumps(payload).encode()
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        header = bytearray([0x81])
        if len(data) < 126:
            header.append(0x80 | len(data))
        else:
            header.append(0x80 | 126)
            header += struct.pack(">H", len(data))
        self.sock.sendall(bytes(header) + mask + masked)

    def recv(self) -> dict:
        head = self._recv_exact(2)
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8))[0]
        return json.loads(self._recv_exact(length).decode())

    def recv_until(self, op: str, limit: int = 40) -> list[dict]:
        seen = []
        for _ in range(limit):
            frame = self.recv()
            seen.append(frame)
            if frame.get("op") == op:
                return seen
        raise AssertionError(f"never saw {op!r}; got {[f.get('op') for f in seen]}")

    def close(self) -> None:
        self.sock.close()


class Scripted:
    """Stands in for a model, without the registry knowing."""

    async def stream(self, messages: list[Message], cancel):
        yield '<vybe:add_cell type="code">\nx = 1\n</vybe:add_cell>'


def _run_sidecar(server: Sidecar):
    """Start a sidecar on its own loop and return (loop, shutdown).

    Teardown cancels the serving task and closes the loop rather than just
    stopping it: a coroutine left suspended is collected later and surfaces as
    an unraisable-exception warning in whichever test happens to call gc.
    """
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    holder: dict = {}

    async def serve():
        started = await asyncio.start_server(server.handle, server.host, server.port)
        holder["server"] = started
        ready.set()
        try:
            await started.serve_forever()
        except asyncio.CancelledError:
            started.close()
            await started.wait_closed()
            raise

    def run():
        asyncio.set_event_loop(loop)
        holder["task"] = loop.create_task(serve())
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    assert ready.wait(timeout=5)

    def shutdown():
        task = holder.get("task")
        if task is not None:
            loop.call_soon_threadsafe(task.cancel)
        loop.call_soon_threadsafe(loop.stop)
        for _ in range(50):
            if not loop.is_running():
                break
            time.sleep(0.02)
        loop.close()

    return shutdown


@pytest.fixture()
def sidecar(monkeypatch):
    port = free_port()
    shutdown = _run_sidecar(Sidecar("127.0.0.1", port))
    yield port
    shutdown()


def http_get(port: int, path: str, origin: str = "https://cluster.example.org"):
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(
        f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nOrigin: {origin}\r\n\r\n".encode()
    )
    data = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    sock.close()
    head, _, body = data.partition(b"\r\n\r\n")
    return head.decode("latin-1"), body.decode("utf-8")


def test_health_answers_with_the_hostname_that_answered(sidecar):
    head, body = http_get(sidecar, "/health")
    assert "200 OK" in head
    payload = json.loads(body)
    assert payload["ok"] is True
    # Which machine answered is the whole question in a split setup (R12).
    assert payload["hostname"] == socket.gethostname()


def test_every_response_carries_the_private_network_header(sidecar):
    """Without this Chrome blocks an HTTPS page from reaching 127.0.0.1.

    It is the entire reason the sidecar exists rather than the browser calling
    Ollama itself (docs/12 D2, docs/13 R12).
    """
    head, _ = http_get(sidecar, "/health")
    assert "Access-Control-Allow-Private-Network: true" in head
    assert "Access-Control-Allow-Origin: https://cluster.example.org" in head


def test_the_preflight_is_answered(sidecar):
    sock = socket.create_connection(("127.0.0.1", sidecar), timeout=5)
    sock.sendall(
        b"OPTIONS /ws HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        b"Origin: https://cluster.example.org\r\n"
        b"Access-Control-Request-Private-Network: true\r\n\r\n"
    )
    data = sock.recv(4096).decode("latin-1")
    sock.close()
    assert "204" in data
    assert "Access-Control-Allow-Private-Network: true" in data


def test_agents_lists_the_same_registry_the_server_extension_serves(sidecar):
    _, body = http_get(sidecar, "/agents")
    ids = {a["id"] for a in json.loads(body)["agents"]}
    assert {"ollama-qwen25-coder-7b", "command", "openai-compat"} <= ids


def test_a_round_streams_over_the_websocket(sidecar, monkeypatch):
    monkeypatch.setattr(
        "jupyter_vybe.session.build_adapter", lambda *a, **k: Scripted()
    )
    client = Client(sidecar)
    client.send({
        "op": "hello",
        "notebookPath": "/tmp/side.ipynb",
        "agentId": "command",
        "persistent": True,
        "options": {"historyPersist": False},
    })
    assert client.recv() == {"op": "status", "state": "ready"}

    client.send({
        "op": "prompt",
        "roundId": "r1",
        "prompt": "add a helper",
        "snapshot": json.dumps({"cells": []}),
        "snapshotSeq": 0,
        "promptCellId": "p1",
        "kernelInfo": {"name": "python3", "language": "python"},
        "limits": {},
    })
    frames = client.recv_until("done")
    tools = [f for f in frames if f.get("op") == "tool"]
    assert tools and tools[0]["name"] == "add_cell"
    assert tools[0]["args"]["body"] == "x = 1"
    assert all(f["roundId"] == "r1" for f in frames)
    client.close()


def test_a_prompt_before_hello_is_refused_rather_than_crashing(sidecar):
    client = Client(sidecar)
    client.send({"op": "prompt", "prompt": "hi"})
    assert "before hello" in client.recv()["message"]
    client.close()


def test_a_malformed_frame_is_reported_not_fatal(sidecar):
    client = Client(sidecar)
    client.sock.sendall(Frame.encode("not json").replace(b"\x81", b"\x81"))
    # Server-encoded frames are unmasked; send a masked one the normal way.
    client.send({"op": "hello", "notebookPath": "/tmp/x.ipynb"})
    frame = client.recv()
    assert frame["op"] in ("error", "status")
    client.close()


def test_an_origin_outside_the_allow_list_is_refused():
    port = free_port()
    shutdown = _run_sidecar(
        Sidecar("127.0.0.1", port, allow_origin="https://only.example.org")
    )
    try:
        head, _ = http_get(port, "/health", origin="https://someone-else.example.org")
        assert "403" in head
        head, _ = http_get(port, "/health", origin="https://only.example.org")
        assert "200 OK" in head
    finally:
        shutdown()
