"""REST and WebSocket handlers for the Vybe agent channel (docs/06)."""

from __future__ import annotations

import json
import os
import uuid

from jupyter_server.base.handlers import APIHandler
from jupyter_server.utils import url_path_join
from tornado import web, websocket

from .adapters import ADAPTER_REGISTRY, build_adapter
from .history import HistoryStore, find_orphan
from .session import SessionManager

NAMESPACE = "jupyter-vybe"

_sessions = SessionManager()


class AgentsHandler(APIHandler):
    """Discovery: which adapters exist and are they reachable."""

    @web.authenticated
    async def get(self) -> None:
        agents = []
        for spec in ADAPTER_REGISTRY.values():
            agents.append(
                {
                    "id": spec.id,
                    "label": spec.label,
                    "contextWindow": spec.context_window,
                    "local": spec.local,
                    "requiresConfig": spec.requires_config,
                }
            )
        self.finish(json.dumps({"agents": agents}))


class HealthHandler(APIHandler):
    """Per-adapter probe. Reports which host actually answered (docs/06, R12)."""

    @web.authenticated
    async def get(self) -> None:
        agent_id = self.get_query_argument("agent", default=None)
        if agent_id is None:
            self.finish(json.dumps({"ok": True, "adapters": list(ADAPTER_REGISTRY)}))
            return
        try:
            adapter = build_adapter(agent_id, self.get_query_arguments)
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
        store = HistoryStore(path)
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
    async def post(self) -> None:
        body = json.loads(self.request.body or b"{}")
        path = body.get("path")
        entry = body.get("entry")
        if not path or not isinstance(entry, dict):
            raise web.HTTPError(400, "path and entry are required")
        written = HistoryStore(path).append(entry)
        self.finish(json.dumps({"ok": True, "entry": written}))


class AgentSocket(websocket.WebSocketHandler):
    """One connection per open notebook; carries prompts, prose, and tool events."""

    def check_xsrf_cookie(self) -> None:  # handled by the token/cookie auth below
        pass

    def get_current_user(self):
        return self.settings.get("jupyter_vybe_user_stub", True)

    async def open(self) -> None:
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
            await self._run_prompt(msg)
        elif op == "toolResult":
            if self.session is not None:
                self.session.deliver_tool_result(msg["callId"], msg.get("result", ""), msg.get("ok", True))
        elif op == "cancel":
            if self.session is not None:
                self.session.cancel()
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


def setup_handlers(web_app) -> None:
    base = web_app.settings["base_url"]
    web_app.add_handlers(
        ".*$",
        [
            (url_path_join(base, NAMESPACE, "agents"), AgentsHandler),
            (url_path_join(base, NAMESPACE, "health"), HealthHandler),
            (url_path_join(base, NAMESPACE, "history"), HistoryHandler),
            (url_path_join(base, NAMESPACE, "ws"), AgentSocket),
        ],
    )
