"""The two WebSockets that can make something run, and how they are guarded.

Kept apart from handlers.py for one reason: the runner socket speaks the
relay vocabulary (adapters/relay.py), not the browser's, and the T-FRAMES gate
checks that every op this package sends *to the browser* is one the frontend
handles. Frames on this file's runner socket go to `jupyter-vybe-local`.
"""

from __future__ import annotations

import json

from jupyter_server.base.handlers import JupyterHandler
from jupyter_server.base.websocket import WebSocketMixin
from jupyter_server.utils import ensure_async
from tornado import web, websocket

from .adapters import HUB


class AuthenticatedSocket(WebSocketMixin, JupyterHandler, websocket.WebSocketHandler):
    """A WebSocket that Jupyter authenticates, exactly as it does a terminal.

    Both sockets here can make something run -- the agent socket a command
    adapter on this server, the runner socket an agent on the user's machine
    -- so both need what a terminal needs: a logged-in user (token or cookie),
    an authorizer that agrees, and Jupyter's origin check (WebSocketMixin).

    This replaces a stub that returned a user for every request and skipped
    the XSRF check. With it, any process that could reach the port could open
    the agent socket without the token and run a command through it.
    """

    # The agent socket can run a command on this server, which is what a
    # kernel does; authorizers know that resource, and JupyterHub maps it to
    # the same access as the server itself.
    auth_resource = "kernels"

    async def get(self, *args, **kwargs) -> None:
        user = self.current_user
        if not user:
            raise web.HTTPError(403)
        if self.authorizer is not None and not await ensure_async(
            self.authorizer.is_authorized(self, user, "execute", self.auth_resource)
        ):
            raise web.HTTPError(403)
        await ensure_async(super().get(*args, **kwargs))


class RunnerSocket(AuthenticatedSocket):
    """Where `jupyter-vybe-local connect` dials in from the user's machine.

    Authenticated like any other Jupyter client -- the runner sends the token
    in an Authorization header. Everything it says is delivered to the hub;
    everything the hub says goes back down. The vocabulary is in
    adapters/relay.py, and it has no word for "run this".
    """

    async def open(self, *args, **kwargs) -> None:
        await ensure_async(super().open(*args, **kwargs))
        self._attached = False

    async def on_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if msg.get("op") == "hello":
            HUB.attach(self, msg)
            self._attached = True
            info = HUB.info
            self.log.info(
                "jupyter-vybe: agent %r connected from %s", info.label, info.hostname or "?"
            )
            await self.send({"op": "attached"})
        elif self._attached:
            HUB.deliver(self, msg)

    async def send(self, frame: dict) -> None:
        try:
            self.write_message(json.dumps(frame))
        except websocket.WebSocketClosedError:
            HUB.detach(self)
            raise RuntimeError("the agent connection on your machine closed")

    def hang_up(self, reason: str) -> None:
        try:
            self.write_message(json.dumps({"op": "replaced", "reason": reason}))
        except websocket.WebSocketClosedError:
            pass
        self.close(1000, reason[:100])

    def on_close(self) -> None:
        HUB.detach(self)
        super().on_close()
