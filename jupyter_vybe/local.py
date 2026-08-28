"""`python -m jupyter_vybe.local` — the client-side sidecar (D2, v1.1).

The case this exists for: Jupyter on a compute cluster, the model on your own
laptop. A reverse tunnel covers HTTP backends, but not `claude -p` or any other
CLI whose credentials live on the machine it runs on. The browser is already on
your laptop, so it can talk to a server there directly — provided that server
answers Chrome's Private Network Access preflight, which Ollama does not and
this does.

It is not a second implementation of anything. `jupyter_vybe.adapters` and
`AgentSession` have no dependency on Tornado or the server extension (gate D2
in scripts/check-invariants.sh keeps it that way), so this file is transport
and nothing else: a WebSocket speaking exactly the protocol handlers.py
speaks, and a health endpoint.

Standard library only, deliberately. Anything you must install on a second
machine before the tool works is friction the tool pays for.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import uuid
from typing import Any

from .adapters import ADAPTER_REGISTRY, build_adapter
from .session import SessionManager

WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
DEFAULT_PORT = 8137

# Opcodes we care about; anything else is either ignored or fatal to the frame
# loop, which is the correct response to a client speaking a protocol we do not.
OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x1, 0x2, 0x8, 0x9, 0xA


class Frame:
    """Minimal RFC 6455 framing: text in, text out, no extensions."""

    @staticmethod
    def encode(payload: str, opcode: int = OP_TEXT) -> bytes:
        data = payload.encode("utf-8")
        header = bytearray([0x80 | opcode])
        length = len(data)
        if length < 126:
            header.append(length)
        elif length < (1 << 16):
            header.append(126)
            header += struct.pack(">H", length)
        else:
            header.append(127)
            header += struct.pack(">Q", length)
        return bytes(header) + data

    @staticmethod
    async def read(reader: asyncio.StreamReader) -> tuple[int, bytes] | None:
        head = await reader.readexactly(2)
        opcode = head[0] & 0x0F
        masked = bool(head[1] & 0x80)
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", await reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", await reader.readexactly(8))[0]
        mask = await reader.readexactly(4) if masked else b""
        data = await reader.readexactly(length)
        if masked:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return opcode, data


def _accept_key(key: str) -> str:
    digest = hashlib.sha1((key + WS_MAGIC).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _cors_headers(origin: str) -> list[str]:
    """Chrome gates an HTTPS page reaching 127.0.0.1 behind these.

    `Access-Control-Allow-Private-Network` is the whole reason the sidecar can
    do what browser-direct cannot (docs/13 R12). The origin is echoed rather
    than starred because credentials-bearing requests reject a star, and
    because a sidecar on a laptop should not be quietly readable by every page
    the user has open.
    """
    return [
        f"Access-Control-Allow-Origin: {origin or '*'}",
        "Access-Control-Allow-Private-Network: true",
        "Access-Control-Allow-Credentials: true",
        "Access-Control-Allow-Headers: content-type",
        "Access-Control-Allow-Methods: GET, POST, OPTIONS",
        "Vary: Origin",
    ]


class Sidecar:
    def __init__(self, host: str, port: int, allow_origin: str = "") -> None:
        self.host = host
        self.port = port
        self.allow_origin = allow_origin
        self.sessions = SessionManager()

    # -- request handling --------------------------------------------------

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request = await self._read_request(reader)
            if request is None:
                return
            method, path, headers = request
            origin = headers.get("origin", "")
            if self.allow_origin and origin and origin != self.allow_origin:
                await self._respond(writer, 403, {"error": "origin not allowed"}, origin)
                return
            if method == "OPTIONS":
                await self._preflight(writer, origin)
            elif path.startswith("/health"):
                await self._health(writer, origin)
            elif path.startswith("/agents"):
                await self._agents(writer, origin)
            elif path.startswith("/ws"):
                await self._websocket(reader, writer, headers)
            else:
                await self._respond(writer, 404, {"error": "not found"}, origin)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            with_suppressed_close(writer)

    async def _read_request(
        self, reader: asyncio.StreamReader
    ) -> tuple[str, str, dict[str, str]] | None:
        line = await reader.readline()
        if not line:
            return None
        parts = line.decode("latin-1").split()
        if len(parts) < 2:
            return None
        method, path = parts[0], parts[1]
        headers: dict[str, str] = {}
        while True:
            raw = await reader.readline()
            if raw in (b"\r\n", b"\n", b""):
                break
            name, _, value = raw.decode("latin-1").partition(":")
            headers[name.strip().lower()] = value.strip()
        return method, path, headers

    async def _respond(
        self, writer: asyncio.StreamWriter, status: int, body: Any, origin: str
    ) -> None:
        payload = json.dumps(body).encode("utf-8")
        lines = [
            f"HTTP/1.1 {status} {'OK' if status == 200 else 'ERROR'}",
            "Content-Type: application/json",
            f"Content-Length: {len(payload)}",
            *_cors_headers(origin),
            "Connection: close",
            "",
            "",
        ]
        writer.write("\r\n".join(lines).encode("latin-1") + payload)
        await writer.drain()

    async def _preflight(self, writer: asyncio.StreamWriter, origin: str) -> None:
        lines = [
            "HTTP/1.1 204 No Content",
            *_cors_headers(origin),
            "Content-Length: 0",
            "Connection: close",
            "",
            "",
        ]
        writer.write("\r\n".join(lines).encode("latin-1"))
        await writer.drain()

    async def _health(self, writer: asyncio.StreamWriter, origin: str) -> None:
        await self._respond(
            writer,
            200,
            {"ok": True, "hostname": socket.gethostname(), "pid": os.getpid()},
            origin,
        )

    async def _agents(self, writer: asyncio.StreamWriter, origin: str) -> None:
        agents = [
            {
                "id": spec.id,
                "label": spec.label,
                "contextWindow": spec.context_window,
                "local": spec.local,
                "requiresConfig": spec.requires_config,
            }
            for spec in ADAPTER_REGISTRY.values()
        ]
        await self._respond(writer, 200, {"agents": agents}, origin)

    # -- the websocket -----------------------------------------------------

    async def _websocket(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        headers: dict[str, str],
    ) -> None:
        key = headers.get("sec-websocket-key")
        if not key:
            await self._respond(writer, 400, {"error": "not a websocket upgrade"}, "")
            return
        handshake = [
            "HTTP/1.1 101 Switching Protocols",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Accept: {_accept_key(key)}",
            *_cors_headers(headers.get("origin", "")),
            "",
            "",
        ]
        writer.write("\r\n".join(handshake).encode("latin-1"))
        await writer.drain()

        session = None
        send_lock = asyncio.Lock()

        async def send(payload: dict) -> None:
            async with send_lock:
                writer.write(Frame.encode(json.dumps(payload)))
                await writer.drain()

        while True:
            try:
                frame = await Frame.read(reader)
            except (asyncio.IncompleteReadError, ConnectionResetError):
                break
            if frame is None:
                break
            opcode, data = frame
            if opcode == OP_CLOSE:
                break
            if opcode == OP_PING:
                writer.write(Frame.encode(data.decode("utf-8", "replace"), OP_PONG))
                await writer.drain()
                continue
            if opcode != OP_TEXT:
                continue
            try:
                msg = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                await send({"op": "error", "message": "malformed frame (not JSON)"})
                continue
            session = await self._dispatch(msg, session, send)

        # A dropped socket mid-round is a cancel; we never auto-retry (docs/06).
        if session is not None:
            session.cancel()

    async def _dispatch(self, msg: dict, session, send) -> Any:
        op = msg.get("op")
        if op == "hello":
            session = self.sessions.acquire(
                notebook_path=msg["notebookPath"],
                agent_id=msg.get("agentId", "ollama-qwen25-coder-7b"),
                persistent=bool(msg.get("persistent", True)),
                options=msg.get("options", {}),
            )
            await send({"op": "status", "state": "ready"})
            return session
        if session is None:
            await send({"op": "error", "message": f"{op} before hello"})
            return session
        if op == "prompt":
            round_id = msg.get("roundId") or uuid.uuid4().hex
            try:
                async for event in session.run(msg):
                    event["roundId"] = round_id
                    await send(event)
            except Exception as exc:  # noqa: BLE001 — surfaced, never swallowed
                await send({
                    "op": "error",
                    "roundId": round_id,
                    "message": f"{type(exc).__name__}: {exc}",
                    "retriable": True,
                })
                await send({"op": "done", "roundId": round_id, "stopReason": "error"})
        elif op == "toolResult":
            session.deliver_tool_result(
                msg["callId"], msg.get("result", ""), msg.get("ok", True)
            )
        elif op == "cancel":
            session.cancel()
        elif op == "reset":
            session.reset()
        elif op != "bye":
            await send({"op": "error", "message": f"unknown op {op!r}"})
        return session

    # -- lifecycle ---------------------------------------------------------

    async def serve(self) -> None:
        server = await asyncio.start_server(self.handle, self.host, self.port)
        where = ", ".join(str(s.getsockname()[1]) for s in server.sockets or [])
        print(
            f"jupyter-vybe sidecar on http://{self.host}:{where}\n"
            f"  agents: {', '.join(ADAPTER_REGISTRY)}\n"
            f"  set agent.host to 'client' in JupyterLab settings to use it.",
            flush=True,
        )
        async with server:
            await server.serve_forever()


def with_suppressed_close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
    except Exception:  # noqa: BLE001 — closing is best effort
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m jupyter_vybe.local",
        description="Run the vybe agent on this machine, for a Jupyter running elsewhere.",
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("VYBE_SIDECAR_PORT", DEFAULT_PORT))
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Loopback by default. Binding wider exposes your model and your "
        "shell to the network; do it only behind something that authenticates.",
    )
    parser.add_argument(
        "--allow-origin",
        default=os.environ.get("VYBE_SIDECAR_ORIGIN", ""),
        help="Restrict to one JupyterLab origin, e.g. https://cluster.example.org",
    )
    args = parser.parse_args(argv)
    sidecar = Sidecar(args.host, args.port, args.allow_origin)
    try:
        asyncio.run(sidecar.serve())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
