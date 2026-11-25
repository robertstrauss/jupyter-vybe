"""`jupyter-vybe-local connect` -- your agent, on your machine, for a Jupyter elsewhere.

The case this exists for: Jupyter on a cluster or a remote box, and the agent
on your laptop, where `claude`, `codex` or `agy` is already logged in to your
subscription. Nothing to re-authenticate, no API key to store anywhere.

**It dials out; nothing listens.** An earlier version was a server on
127.0.0.1 that the browser connected to. A WebSocket is not covered by CORS,
so any web page open in the same browser could connect to it as well -- and
the session it reached would run whatever command the page named. Verified,
not theorised (docs/13 R16). Now this process opens one authenticated
connection to the Jupyter server, the same way the browser does, and the
server relays the notebook's prompts down it.

**What the server can make it do is answer.** The agent and its flags are
chosen here, on the command line, when you start it. Frames from the server
carry a conversation and nothing else: no command, no argument, no path, no
environment. A server that is compromised, or shared with people you do not
trust, can ask your agent a question; it cannot run anything on your machine
by any route this file provides.

The agent itself is the other half of that promise, because an agent CLI has
tools of its own and a notebook can contain text written to talk it into
using them. Each preset is configured to have none, or as close to none as
the CLI allows, and runs in an empty directory (see PRESETS).

Standard library only, deliberately: anything you must install on a second
machine before the tool works is friction the tool pays for.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import shutil
import socket
import ssl
import struct
import sys
import tempfile
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from .adapters import ADAPTER_REGISTRY, Message, build_adapter
from .adapters.base import AdapterSpec
from .adapters.command import CommandAdapter

WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA

# Sessions kept alive at once; one per open notebook. A persistent agent is a
# process, so this is also a bound on how many of those can pile up.
MAX_SESSIONS = 8


# -- presets -----------------------------------------------------------------


@dataclass
class Preset:
    """An agent CLI, configured so the only thing it can do is reply.

    `head` and `tail` surround the model flag, because some CLIs want their
    prompt flag last (`agy ... -p <prompt>`). `containment` is what was
    verified about the CLI's tools, in words a user can check.
    """

    label: str
    head: list[str]
    tail: list[str] = field(default_factory=list)
    model_flag: str = "--model"
    persistent: bool = False
    containment: str = ""

    def argv(self, model: str = "") -> list[str]:
        middle = [self.model_flag, model] if model else []
        return [*self.head, *middle, *self.tail]


PRESETS: dict[str, Preset] = {
    "claude": Preset(
        "Claude Code",
        [
            "claude", "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",
            # No built-in tools, no MCP servers (which would otherwise include
            # every claude.ai connector on the account), no slash commands or
            # skills triggered by text in a notebook.
            "--tools", "",
            "--strict-mcp-config",
            "--disable-slash-commands",
        ],
        persistent=True,
        containment="no tools: built-ins off (--tools ''), MCP off (--strict-mcp-config)",
    ),
    "codex": Preset(
        "Codex",
        ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral"],
        model_flag="-m",
        containment="read-only sandbox; refused to read a planted file in testing",
    ),
    "agy": Preset(
        "Antigravity",
        # Headless agy denies tools that need permission (commands) but reads
        # files without asking: given a notebook saying so, it read a planted
        # file and listed a directory. So every tool is denied outright, for
        # this session only. The `(*)` form is required -- a bare "read_file"
        # rule did not stop the read. Plan mode is not used: in it agy always
        # reaches for a tool, and with tools denied it then says nothing.
        ["agy", "--settings", json.dumps({"permissions": {"deny": [
            "read_file(*)", "list_dir(*)", "edit_file(*)", "write_file(*)",
            "run_command(*)", "manage_task(*)", "search_web(*)", "read_url(*)",
        ]}})],
        tail=["-p"],
        containment="every tool denied for the session (--settings permissions.deny)",
    ),
    "gemini": Preset(
        "Gemini CLI",
        ["gemini"],
        tail=["-p"],
        containment="Gemini CLI's default headless approvals (not verified here)",
    ),
}


@dataclass
class AgentChoice:
    """What this runner answers with -- fixed for the life of the process."""

    name: str
    label: str
    local: bool
    context_window: int
    containment: str
    factory: object  # () -> adapter

    def build(self, workdir: str):
        return self.factory(workdir)  # type: ignore[operator]


def choose_agent(name: str, model: str = "", command: str = "") -> AgentChoice:
    """Resolve the --agent / --command arguments into something that makes adapters."""
    if command:
        spec = AdapterSpec("command", f"custom: {command}", 200000)
        return AgentChoice(
            "command", command.split()[0], False, 200000,
            "whatever this command allows -- you chose it",
            lambda wd: CommandAdapter(spec, command, cwd=wd),
        )
    if name in PRESETS:
        preset = PRESETS[name]
        argv = preset.argv(model)
        spec = AdapterSpec(name, preset.label, 200000)
        return AgentChoice(
            name, preset.label, False, 200000, preset.containment,
            lambda wd: CommandAdapter(
                AdapterSpec(spec.id, spec.label, spec.context_window),
                list(argv), persistent=preset.persistent, cwd=wd,
            ),
        )
    if name in ADAPTER_REGISTRY and name not in ("command", "runner", "openai-compat"):
        spec = ADAPTER_REGISTRY[name]
        return AgentChoice(
            name, spec.label, spec.local, spec.context_window,
            "a model with no tools",
            lambda wd: build_adapter(name),
        )
    known = [*PRESETS, *(k for k in ADAPTER_REGISTRY if k not in ("command", "runner", "openai-compat"))]
    raise SystemExit(f"unknown agent {name!r}; choose one of: {', '.join(known)}, or use --command")


# -- websocket client --------------------------------------------------------


class Frame:
    """Minimal RFC 6455 framing. Client frames are masked, as the RFC requires."""

    @staticmethod
    def encode(payload: str | bytes, opcode: int = OP_TEXT, mask: bool = True) -> bytes:
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        header = bytearray([0x80 | opcode])
        length = len(data)
        bit = 0x80 if mask else 0
        if length < 126:
            header.append(bit | length)
        elif length < (1 << 16):
            header.append(bit | 126)
            header += struct.pack(">H", length)
        else:
            header.append(bit | 127)
            header += struct.pack(">Q", length)
        if not mask:
            return bytes(header) + data
        key = os.urandom(4)
        return bytes(header) + key + bytes(b ^ key[i % 4] for i, b in enumerate(data))

    @staticmethod
    async def read(reader: asyncio.StreamReader) -> tuple[int, bool, bytes]:
        head = await reader.readexactly(2)
        fin = bool(head[0] & 0x80)
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
        return opcode, fin, data


def _accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + WS_MAGIC).encode()).digest()).decode()


class AuthRejected(Exception):
    """The server said no to the token. Retrying will not change its mind."""


@dataclass
class Target:
    host: str
    port: int
    secure: bool
    path: str
    token: str

    @property
    def display(self) -> str:
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.host}:{self.port}{self.path.rsplit('jupyter-vybe/', 1)[0]}"


def parse_target(url: str, token: str = "") -> Target:
    """Accept whatever the user copied: the lab URL, with or without ?token=.

    `http://host:8888/lab/tree/x.ipynb?token=abc` and `http://host:8888/`
    both mean the same server; JupyterHub's `/user/<name>/lab` keeps its
    prefix, because that prefix is the server's base URL.
    """
    if "://" not in url:
        url = "http://" + url
    parts = urlparse(url)
    secure = parts.scheme in ("https", "wss")
    query_token = parse_qs(parts.query).get("token", [""])[0]
    base = parts.path or "/"
    for marker in ("/lab", "/tree", "/notebooks", "/doc"):
        index = base.find(marker)
        if index != -1:
            base = base[:index]
            break
    if not base.endswith("/"):
        base += "/"
    return Target(
        host=parts.hostname or "127.0.0.1",
        port=parts.port or (443 if secure else 80),
        secure=secure,
        path=f"{base}jupyter-vybe/runner",
        token=token or query_token,
    )


class Connection:
    """One client WebSocket. Sends are serialised; pings are answered inline."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, target: Target, timeout: float = 10.0) -> "Connection":
        context = ssl.create_default_context() if target.secure else None
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                target.host, target.port, ssl=context,
                server_hostname=target.host if context else None,
            ),
            timeout,
        )
        key = base64.b64encode(os.urandom(16)).decode()
        default_port = 443 if target.secure else 80
        host_header = target.host if target.port == default_port else f"{target.host}:{target.port}"
        lines = [
            f"GET {target.path} HTTP/1.1",
            f"Host: {host_header}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
            # No Origin header: this is not a browser, and Jupyter treats a
            # request without one as coming from a script, which it is.
        ]
        if target.token:
            lines.append(f"Authorization: token {target.token}")
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)
        status_line = head.split(b"\r\n", 1)[0].decode("latin-1")
        status = status_line.split(" ")[1] if " " in status_line else "?"
        if status in ("401", "403"):
            writer.close()
            raise AuthRejected(
                f"the server refused the token ({status_line}). Copy the full URL from "
                "JupyterLab, including ?token=..., or pass --token."
            )
        if status == "404":
            writer.close()
            raise ConnectionError(
                f"{status_line}: is jupyter-vybe installed on that server, and is "
                "the URL its JupyterLab address?"
            )
        if status != "101":
            writer.close()
            raise ConnectionError(f"unexpected answer to the upgrade: {status_line}")
        if _accept_key(key).encode() not in head:
            writer.close()
            raise ConnectionError("the server's handshake did not match; not a WebSocket server?")
        return cls(reader, writer)

    async def send(self, frame: dict) -> None:
        async with self._lock:
            self.writer.write(Frame.encode(json.dumps(frame)))
            await self.writer.drain()

    async def recv(self) -> dict | None:
        """The next JSON message, or None when the connection closes."""
        parts: list[bytes] = []
        while True:
            opcode, fin, data = await Frame.read(self.reader)
            if opcode == OP_PING:
                async with self._lock:
                    self.writer.write(Frame.encode(data, OP_PONG))
                    await self.writer.drain()
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                return None
            if opcode in (OP_TEXT, OP_CONT):
                parts.append(data)
                if not fin:
                    continue
                try:
                    return json.loads(b"".join(parts).decode("utf-8"))
                except json.JSONDecodeError:
                    parts = []
                    continue

    def close(self) -> None:
        try:
            self.writer.write(Frame.encode(b"", OP_CLOSE))
            self.writer.close()
        except Exception:  # noqa: BLE001 -- closing is best effort
            pass


# -- the runner --------------------------------------------------------------


class Runner:
    """Answers the server's `stream` frames with the chosen agent, and nothing else."""

    def __init__(self, choice: AgentChoice, out=None, show_replies: bool = False) -> None:
        self.choice = choice
        self.out = out or sys.stdout
        self.show_replies = show_replies
        self._sessions: OrderedDict[str, tuple[object, tempfile.TemporaryDirectory]] = OrderedDict()
        self._streams: dict[str, asyncio.Event] = {}
        self._tasks: set[asyncio.Task] = set()

    def hello(self) -> dict:
        return {
            "op": "hello",
            "agent": self.choice.name,
            "label": self.choice.label,
            "hostname": socket.gethostname(),
            "local": self.choice.local,
            "contextWindow": self.choice.context_window,
        }

    def _say(self, text: str) -> None:
        print(f"{time.strftime('%H:%M:%S')}  {text}", file=self.out, flush=True)

    def _session(self, key: str):
        if key in self._sessions:
            self._sessions.move_to_end(key)
            return self._sessions[key][0]
        # Each session gets an empty directory to start in, so an agent that
        # goes looking for files around it finds nothing of yours.
        workdir = tempfile.TemporaryDirectory(prefix="vybe-agent-")
        adapter = self.choice.build(workdir.name)
        self._sessions[key] = (adapter, workdir)
        while len(self._sessions) > MAX_SESSIONS:
            _, (old, old_dir) = self._sessions.popitem(last=False)
            self._spawn(self._close_adapter(old, old_dir))
        return adapter

    async def _close_adapter(self, adapter, workdir) -> None:
        close = getattr(adapter, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:  # noqa: BLE001
                pass
        workdir.cleanup()

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def handle(self, frame: dict, conn: Connection) -> None:
        """Dispatch one frame. The vocabulary is closed: unknown ops are ignored.

        Note what is *not* read from any frame: a command, argv, model, path,
        environment or option. The only data taken from the server are the
        conversation text, a caption to print, and opaque ids.
        """
        op = frame.get("op")
        if op == "stream":
            stream_id = str(frame.get("id", ""))
            cancel = asyncio.Event()
            self._streams[stream_id] = cancel
            self._spawn(self._stream(frame, stream_id, cancel, conn))
        elif op == "cancel":
            event = self._streams.get(str(frame.get("id", "")))
            if event is not None:
                event.set()
        elif op == "warm":
            adapter = self._session(str(frame.get("session", "")))
            warm = getattr(adapter, "warm", None)
            if warm is not None:
                self._spawn(_quietly(warm()))
        elif op == "close":
            entry = self._sessions.pop(str(frame.get("session", "")), None)
            if entry is not None:
                self._spawn(self._close_adapter(*entry))

    async def _stream(self, frame: dict, stream_id: str, cancel: asyncio.Event, conn: Connection) -> None:
        notebook = os.path.basename(str(frame.get("notebook", ""))) or "notebook"
        caption = " ".join(str(frame.get("caption", "")).split())[:100]
        messages = [
            Message(str(m.get("role", "user")), str(m.get("content", "")))
            for m in frame.get("messages", [])
            if isinstance(m, dict)
        ]
        self._say(f"{notebook} > {caption or '(prompt)'}")
        started = time.monotonic()
        chars = 0
        try:
            adapter = self._session(str(frame.get("session", "")))
            async for chunk in adapter.stream(messages, cancel):
                chars += len(chunk)
                if self.show_replies:
                    print(chunk, end="", file=self.out, flush=True)
                await conn.send({"op": "chunk", "id": stream_id, "text": chunk})
            if self.show_replies:
                print(file=self.out)
            if cancel.is_set():
                self._say(f"   cancelled after {time.monotonic() - started:.1f}s")
            else:
                self._say(f"   answered in {time.monotonic() - started:.1f}s ({chars} chars)")
            await conn.send({"op": "end", "id": stream_id})
        except (ConnectionError, OSError) as exc:
            self._say(f"   lost the connection mid-reply: {exc}")
        except Exception as exc:  # noqa: BLE001 -- reported to the notebook, never swallowed
            # A RuntimeError is how adapters report an agent's own failure;
            # the server re-raises it as one, so naming it here would say it twice.
            message = str(exc) if isinstance(exc, RuntimeError) else f"{type(exc).__name__}: {exc}"
            self._say(f"   failed: {message}")
            try:
                await conn.send({"op": "error", "id": stream_id, "message": message})
            except (ConnectionError, OSError):
                pass
        finally:
            self._streams.pop(stream_id, None)

    def cancel_all(self) -> None:
        for event in self._streams.values():
            event.set()

    async def shutdown(self) -> None:
        self.cancel_all()
        for key in list(self._sessions):
            adapter, workdir = self._sessions.pop(key)
            await self._close_adapter(adapter, workdir)

    async def serve(self, target: Target, once: bool = False) -> int:
        """Connect, answer until disconnected, reconnect. Exit only on a refused token."""
        delay = 1.0
        while True:
            try:
                conn = await Connection.open(target)
            except AuthRejected as exc:
                self._say(f"error: {exc}")
                return 2
            except (OSError, asyncio.TimeoutError, ConnectionError) as exc:
                if once:
                    self._say(f"error: cannot reach {target.display}: {exc}")
                    return 1
                self._say(f"cannot reach {target.display} ({exc}); retrying in {delay:.0f}s")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            delay = 1.0
            await conn.send(self.hello())
            self._say(
                f"connected to {target.display} as {self.choice.label}. "
                "Choose \"The agent on your own machine\" in JupyterLab's Vybe settings."
            )
            try:
                while True:
                    frame = await conn.recv()
                    if frame is None:
                        break
                    if frame.get("op") == "replaced":
                        # Another runner took over -- most likely this same
                        # command started again elsewhere. Reconnecting would
                        # evict it in turn, and the two would take turns
                        # evicting each other for as long as both ran.
                        self._say(
                            f"stopped: {frame.get('reason', 'another runner connected')}. "
                            "Only one agent connection is kept per server."
                        )
                        return 0
                    await self.handle(frame, conn)
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                pass
            finally:
                conn.close()
                self.cancel_all()
            if once:
                return 0
            self._say("disconnected; reconnecting")


async def _quietly(coro) -> None:
    try:
        await coro
    except Exception:  # noqa: BLE001 -- warming is best effort
        pass


# -- entry point -------------------------------------------------------------


def _preflight(choice: AgentChoice) -> str | None:
    """A missing binary is better found now than on the first prompt."""
    if choice.name in PRESETS:
        binary = PRESETS[choice.name].head[0]
        if shutil.which(binary) is None:
            return f"{binary!r} is not on PATH on this machine"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jupyter-vybe-local",
        description=(
            "Answer a JupyterLab's Vybe prompts with an agent on this machine. "
            "Connects out to the server; listens on nothing."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    connect = sub.add_parser("connect", help="connect to a JupyterLab and answer its prompts")
    connect.add_argument(
        "url",
        help="the JupyterLab URL, as copied from the browser (a ?token=... in it is used)",
    )
    connect.add_argument(
        "--agent", default="claude",
        help=f"one of {', '.join(PRESETS)}, or a local model id such as ollama-qwen25-coder-7b",
    )
    connect.add_argument("--model", default="", help="passed to the agent CLI's own model flag")
    connect.add_argument(
        "--command", default="",
        help="run this instead of a preset (prompt appended as the last argument); "
        "its tools are your responsibility",
    )
    connect.add_argument(
        "--token", default=os.environ.get("JUPYTER_TOKEN", ""),
        help="the server's token, if not in the URL (default: $JUPYTER_TOKEN)",
    )
    connect.add_argument("--show-replies", action="store_true", help="print each reply as it streams")
    args = parser.parse_args(argv)

    choice = choose_agent(args.agent, args.model, args.command)
    problem = _preflight(choice)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 1
    target = parse_target(args.url, args.token)
    if not target.token:
        print(
            "warning: no token given. That works only if the server needs none; "
            "copy the URL with its ?token=..., or pass --token.",
            file=sys.stderr,
        )
    print(f"agent: {choice.label} -- {choice.containment}", flush=True)
    runner = Runner(choice, show_replies=args.show_replies)

    async def run() -> int:
        try:
            return await runner.serve(target)
        finally:
            await runner.shutdown()

    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
