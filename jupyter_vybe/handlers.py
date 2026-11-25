"""REST and WebSocket handlers for the Vybe agent channel (docs/06)."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

from jupyter_server.base.handlers import APIHandler
from jupyter_server.utils import ensure_async, url_path_join
from tornado import web, websocket

from .adapters import ADAPTER_REGISTRY, HUB, Message, build_adapter
from .completion import (
    build_fim_prompt,
    build_predict_prompt,
    clean,
    clean_prediction,
    limit_lines,
    looks_useless,
    should_be_multiline,
    strip_fences,
)
from .history import HistoryStore, find_orphan
from .session import SessionManager
from .sockets import AuthenticatedSocket, RunnerSocket

NAMESPACE = "jupyter-vybe"

_sessions = SessionManager()


class AgentsHandler(APIHandler):
    """Discovery: which adapters exist and are they reachable."""

    @web.authenticated
    async def get(self) -> None:
        agents = []
        for spec in ADAPTER_REGISTRY.values():
            entry = {
                "id": spec.id,
                "label": spec.label,
                "contextWindow": spec.context_window,
                "local": spec.local,
                "requiresConfig": spec.requires_config,
            }
            if spec.id == "runner" and HUB.info is not None:
                # What is actually on the other end, not what might be.
                info = HUB.info
                entry["label"] = f"{info.label} on {info.hostname or 'your machine'}"
                entry["local"] = info.local
                entry["contextWindow"] = info.context_window or spec.context_window
                entry["connected"] = True
            elif spec.id == "runner":
                entry["connected"] = False
            agents.append(entry)
        self.finish(json.dumps({"agents": agents}))


class DefaultsHandler(APIHandler):
    """The shipped prompt text, so the settings panel can start from it.

    An empty global-prompt setting means "use the default", which tells the
    user nothing about what the default says. The panel fills the box with
    this instead of leaving them to guess at what they are overriding.
    """

    @web.authenticated
    async def get(self) -> None:
        from .assembler import _load

        self.finish(json.dumps({"globalPrompt": _load("global.md")}))


class HealthHandler(APIHandler):
    """Per-adapter probe. Reports which host actually answered (docs/06, R12)."""

    @web.authenticated
    async def get(self) -> None:
        agent_id = self.get_query_argument("agent", default=None)
        if agent_id is None:
            self.finish(json.dumps({"ok": True, "adapters": list(ADAPTER_REGISTRY)}))
            return
        try:
            # Health is a GET, so its options come from the query string.
            options = {
                k: v[0].decode() if isinstance(v[0], bytes) else v[0]
                for k, v in self.request.query_arguments.items()
                if v
            }
            adapter = build_adapter(agent_id, options)
        except KeyError:
            raise web.HTTPError(404, f"unknown agent {agent_id!r}")
        status = await adapter.health()
        self.finish(json.dumps(status.to_json()))


class HistoryHandler(APIHandler):
    """The prompt record for one notebook (docs/05, D12).

    GET returns it; POST appends. The verdict on a proposal is the one entry
    the server cannot know on its own -- accept and reject happen entirely in
    the frontend -- so the frontend posts it here rather than the record being
    silently incomplete.
    """

    @web.authenticated
    async def get(self) -> None:
        path = self.get_query_argument("path")
        limit = self.get_query_argument("limit", default=None)
        location = self.get_query_argument("location", default="user")
        store = HistoryStore(path, location=location)
        entries = store.read(int(limit) if limit else None)
        payload = {
            "path": path,
            "file": str(store.path),
            "entries": entries,
        }
        if not entries:
            # Renamed outside JupyterLab? Say so, rather than showing an empty
            # pane that looks like nothing ever happened.
            orphan = find_orphan(os.path.basename(path))
            if orphan is not None:
                payload["orphan"] = str(orphan)
        self.finish(json.dumps(payload))

    @web.authenticated
    async def patch(self) -> None:
        """A rename, or adopting an orphan: carry the record forward (docs/05).

        JupyterLab sees renames it performs; one done in a terminal it does
        not, which is what find_orphan is for on the read side.
        """
        body = json.loads(self.request.body or b"{}")
        old, new = body.get("from"), body.get("to")
        if not old or not new:
            raise web.HTTPError(400, "from and to are required")
        location = body.get("location", "user")
        source = HistoryStore(old, location=location)
        if body.get("adoptFile"):
            # Adopting an orphan: the record's own path is gone, so the file is
            # named directly rather than derived from a notebook path.
            candidate = Path(body["adoptFile"])
            if candidate.parent != source.path.parent and location != "adjacent":
                raise web.HTTPError(400, "refusing to adopt a file outside the store")
            source = HistoryStore(old, location=location)
            source.path.parent.mkdir(parents=True, exist_ok=True)
            if candidate != source.path and candidate.exists():
                source.path.write_text(candidate.read_text(encoding="utf-8"), encoding="utf-8")
        store = source.rekey(new)
        self.finish(json.dumps({"ok": True, "file": str(store.path)}))

    @web.authenticated
    async def post(self) -> None:
        body = json.loads(self.request.body or b"{}")
        path = body.get("path")
        entry = body.get("entry")
        if not path or not isinstance(entry, dict):
            raise web.HTTPError(400, "path and entry are required")
        written = HistoryStore(path, location=body.get("location", "user")).append(entry)
        self.finish(json.dumps({"ok": True, "entry": written}))


class CompletionHandler(APIHandler):
    """Inline completion, fill-in-the-middle (D5, v1.1).

    A separate route from the round WebSocket on purpose: this has a latency
    budget measured in hundreds of milliseconds, and it must be cancellable by
    the client simply dropping the request. Nothing here touches a session, so
    a completion in flight can never disturb a round.
    """

    @web.authenticated
    async def post(self) -> None:
        body = json.loads(self.request.body or b"{}")
        prefix = body.get("prefix", "")
        suffix = body.get("suffix", "")
        agent_id = body.get("agentId", "ollama-qwen25-coder-7b")
        multiline = bool(body.get("multiline", False))
        max_tokens = int(body.get("maxTokens", 64))
        timeout = float(body.get("timeoutSeconds", 4))
        if not prefix.strip() and not suffix.strip():
            self.finish(json.dumps({"items": []}))
            return
        try:
            adapter = build_adapter(agent_id, body.get("options", {}))
        except KeyError:
            raise web.HTTPError(404, f"unknown agent {agent_id!r}")

        model = getattr(adapter, "model", "") or agent_id
        prompt, is_raw = build_fim_prompt(prefix, suffix, model)
        # Kept so the raw path can rebuild an instruction prompt if the
        # endpoint turns out not to serve /v1/completions.
        self._prefix, self._suffix = prefix, suffix
        try:
            text = await asyncio.wait_for(
                self._generate(adapter, prompt, is_raw, max_tokens, timeout),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            # Silence beats a late suggestion: by now the user has typed on.
            self.finish(json.dumps({"items": [], "timedOut": True}))
            return
        except Exception as exc:  # noqa: BLE001 — reported, never raised at the editor
            self.finish(json.dumps({"items": [], "error": f"{type(exc).__name__}: {exc}"}))
            return

        text = clean(text, suffix, should_be_multiline(prefix, multiline))
        items = [] if looks_useless(text, prefix) else [{"insertText": text}]
        self.finish(json.dumps({"items": items}))

    async def _generate(self, adapter, prompt: str, is_raw: bool, max_tokens: int, timeout: float) -> str:
        raw = getattr(adapter, "complete_raw", None)
        if is_raw and raw is not None:
            try:
                return await raw(prompt, max_tokens, timeout)
            except NotImplementedError:
                # The endpoint has no raw route after all; ask in words below.
                prompt, _ = build_fim_prompt(self._prefix, self._suffix, "")
        # No raw endpoint: fall back to a normal turn and take what comes.
        # Worse completions, but every adapter can do it.
        out = ""
        cancel = asyncio.Event()
        async for chunk in adapter.stream([Message("user", prompt)], cancel):
            out += chunk
            if len(out) > max_tokens * 8:
                cancel.set()
                break
        return out


class PredictHandler(APIHandler):
    """Predict the rest of a cell, streaming (v1.2).

    Streams because ghost text that appears a word at a time reads as the
    model working, and ghost text that appears after two silent seconds reads
    as the editor having hung. The response is newline-delimited JSON, flushed
    as it arrives; the client renders the accumulated text after every frame.

    A separate route from the round WebSocket, like the completion route it
    replaces: this must be cancellable by the client simply dropping the
    request, and nothing here touches a session, so a prediction in flight can
    never disturb a round.
    """

    @web.authenticated
    async def post(self) -> None:
        body = json.loads(self.request.body or b"{}")
        script = body.get("script", "")
        cell = int(body.get("cell", 1))
        typed = body.get("typed", "")
        max_lines = max(1, int(body.get("maxLines", 1)))
        hints = body.get("hints", "")
        agent_id = body.get("agentId") or "ollama-qwen25-coder-7b"
        timeout = float(body.get("timeoutSeconds", 8))

        if not script.strip():
            self.finish("")
            return
        try:
            adapter = build_adapter(agent_id, body.get("options", {}))
        except KeyError:
            raise web.HTTPError(404, f"unknown agent {agent_id!r}")

        prompt = build_predict_prompt(script, cell, max_lines, hints)
        self.set_header("Content-Type", "application/x-ndjson")

        cancel = asyncio.Event()
        text = ""
        try:
            async def pump() -> None:
                nonlocal text
                async for chunk in adapter.stream([Message("user", prompt)], cancel):
                    text += chunk
                    # Cleaned every time rather than at the end: the fence the
                    # model opens on line one must never reach the editor, not
                    # even for the frame before the closing one arrives.
                    shown = clean_prediction(text, typed, max_lines)
                    self.write(json.dumps({"text": shown}) + "\n")
                    await self.flush()
                    if _finished(text, max_lines, typed):
                        cancel.set()
                        break

            await asyncio.wait_for(pump(), timeout=timeout)
        except asyncio.TimeoutError:
            cancel.set()
        except asyncio.CancelledError:
            # The user typed on, or asked for something else. Nothing to say.
            cancel.set()
            raise
        except Exception as exc:  # noqa: BLE001 -- never raised at the editor
            self.write(json.dumps({"error": f"{type(exc).__name__}: {exc}"}) + "\n")
        self.finish()

    def on_connection_close(self) -> None:
        # Dropping the request is how the client cancels; without this the
        # generation runs on and holds the model against the next one.
        super().on_connection_close()


def _finished(text: str, max_lines: int, typed: str = "") -> bool:
    """Stop as soon as the answer is as long as it is allowed to be.

    Models overshoot the line limit, and every token past it is one the user
    waits for and never sees. "Allowed" is the limiter's judgement, not a line
    count: a line that opens a block is not finished however short it is, so
    the generation runs on until the code would parse.

    Detected by the limiter having something to cut. While it returns the text
    unchanged there is nothing yet to trim, which is the same thing as the
    model not having finished the line.
    """
    body = strip_fences(text)
    return bool(body) and limit_lines(body, max_lines, typed) != body


class AgentSocket(AuthenticatedSocket):
    """One connection per open notebook; carries prompts, prose, and tool events."""

    async def open(self, *args, **kwargs) -> None:
        await ensure_async(super().open(*args, **kwargs))
        self.session = None
        self.round_id = None

    async def on_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self._error(None, "malformed frame (not JSON)")
            return

        op = msg.get("op")
        if op == "hello":
            self.session = _sessions.acquire(
                notebook_path=msg["notebookPath"],
                agent_id=msg.get("agentId", "ollama-qwen25-coder-7b"),
                persistent=bool(msg.get("persistent", True)),
                options=msg.get("options", {}),
            )
            await self._send({"op": "status", "state": "ready"})
        elif op == "prompt":
            # A task, not an await. Tornado reads no further frame from this
            # socket until on_message returns -- not the cancel, not the
            # result of a kernel lookup the round is itself waiting for, and
            # not the pong to Jupyter's keep-alive ping, which closes the
            # socket after 90 s without one. Awaited, any round longer than
            # that was cut off mid-reply.
            self._round = asyncio.ensure_future(self._run_prompt(msg))
        elif op == "toolResult":
            if self.session is not None:
                self.session.deliver_tool_result(msg["callId"], msg.get("result", ""), msg.get("ok", True))
        elif op == "cancel":
            if self.session is not None:
                self.session.cancel()
        elif op == "warm":
            # Fire and forget: the client is not waiting on this, and a
            # notebook that is only being read should never block on it.
            if self.session is not None:
                asyncio.ensure_future(self.session.warm())
        elif op == "reset":
            if self.session is not None:
                self.session.reset()
        elif op == "bye":
            self.close()
        else:
            await self._error(msg.get("roundId"), f"unknown op {op!r}")

    async def _run_prompt(self, msg: dict) -> None:
        if self.session is None:
            await self._error(msg.get("roundId"), "prompt before hello")
            return
        round_id = msg.get("roundId") or uuid.uuid4().hex
        self.round_id = round_id
        try:
            async for event in self.session.run(msg):
                event["roundId"] = round_id
                await self._send(event)
        except Exception as exc:  # surfaced to the user, never swallowed
            await self._error(round_id, f"{type(exc).__name__}: {exc}", retriable=True)
            await self._send({"op": "done", "roundId": round_id, "stopReason": "error"})

    async def _send(self, payload: dict) -> None:
        try:
            self.write_message(json.dumps(payload))
        except websocket.WebSocketClosedError:
            if self.session is not None:
                self.session.cancel()

    async def _error(self, round_id, message: str, retriable: bool = False) -> None:
        await self._send({"op": "error", "roundId": round_id, "message": message, "retriable": retriable})

    def on_close(self) -> None:
        # A dropped socket mid-round is a cancel; we never auto-retry (docs/06).
        if self.session is not None:
            self.session.cancel()
        super().on_close()


def setup_handlers(web_app) -> None:
    base = web_app.settings["base_url"]
    web_app.add_handlers(
        ".*$",
        [
            (url_path_join(base, NAMESPACE, "agents"), AgentsHandler),
            (url_path_join(base, NAMESPACE, "health"), HealthHandler),
            (url_path_join(base, NAMESPACE, "defaults"), DefaultsHandler),
            (url_path_join(base, NAMESPACE, "history"), HistoryHandler),
            (url_path_join(base, NAMESPACE, "complete"), CompletionHandler),
            (url_path_join(base, NAMESPACE, "predict"), PredictHandler),
            (url_path_join(base, NAMESPACE, "ws"), AgentSocket),
            (url_path_join(base, NAMESPACE, "runner"), RunnerSocket),
        ],
    )
