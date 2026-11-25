"""The agent on the user's machine, dialling out to a real Jupyter server (docs/06).

Driven end to end: a real jupyter_server with the extension loaded, a real
`jupyter-vybe-local` Runner connecting over a real socket with the real token,
and a stand-in agent CLI that prints a tool tag. The two security properties
this design exists for get tests of their own:

* nobody reaches either socket without Jupyter's authentication, and
* nothing the server sends can choose what runs on the user's machine.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import io
import json
import sys

import pytest
from pytest_jupyter.jupyter_core import *  # noqa: F401,F403 -- fixtures, this module only
from pytest_jupyter.jupyter_server import *  # noqa: F401,F403
from pytest_jupyter.pytest_tornasync import *  # noqa: F401,F403
from tornado.httpclient import HTTPClientError, HTTPRequest
from tornado.websocket import websocket_connect

from jupyter_vybe.adapters.relay import HUB, NOT_CONNECTED
from jupyter_vybe.local import Runner, Target, choose_agent, parse_target

NOTEBOOK = json.dumps({
    "cells": [
        {"id": "setup", "cell_type": "code", "source": "import numpy as np", "outputs": []},
        {"id": "p1", "cell_type": "code", "source": "> make x", "outputs": []},
    ],
    "metadata": {},
})

FAKE_AGENT = """
print('<vybe:add_cell type="code">')
print('x = 1')
print('</vybe:add_cell>')
"""


def on_server_loop(fn):
    """Run an async test on the loop the Jupyter server runs on.

    pytest-jupyter serves on its own loop and pytest-asyncio (auto mode, for
    the rest of the suite) would run the test on a different one, where the
    server is never serviced and every request times out. So these tests are
    synchronous to pytest and drive the server's loop themselves.
    """
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    params.append(inspect.Parameter("jp_asyncio_loop", inspect.Parameter.KEYWORD_ONLY))
    # The app does not listen by itself (new_httpserver=False); this fixture is
    # the listener, which jp_fetch and jp_ws_fetch otherwise pull in for you.
    params.append(inspect.Parameter("http_server_client", inspect.Parameter.KEYWORD_ONLY))

    @functools.wraps(fn)
    def wrapper(*args, jp_asyncio_loop, http_server_client, **kwargs):
        return jp_asyncio_loop.run_until_complete(fn(*args, **kwargs))

    wrapper.__signature__ = sig.replace(parameters=params)
    return wrapper


@pytest.fixture
def jp_server_config():
    return {"ServerApp": {"jpserver_extensions": {"jupyter_vybe": True}}}


@pytest.fixture(autouse=True)
def fresh_hub():
    HUB._link = None
    HUB.info = None
    HUB._streams.clear()
    yield
    HUB._link = None
    HUB.info = None
    HUB._streams.clear()


def _token(jp_serverapp) -> str:
    return jp_serverapp.identity_provider.token


def _target(jp_serverapp, jp_http_port, jp_base_url, token=None) -> Target:
    return Target(
        host="127.0.0.1",
        port=jp_http_port,
        secure=False,
        path=f"{jp_base_url}jupyter-vybe/runner",
        token=_token(jp_serverapp) if token is None else token,
    )


async def _round(ws, path: str, agent_id: str = "runner") -> list[dict]:
    await ws.write_message(json.dumps({
        "op": "hello", "notebookPath": path, "agentId": agent_id,
        "persistent": True, "options": {"historyPersist": False},
    }))
    assert json.loads(await ws.read_message())["state"] == "ready"
    await ws.write_message(json.dumps({
        "op": "prompt", "roundId": "r1", "prompt": "make x", "promptCellId": "p1",
        "snapshot": NOTEBOOK, "snapshotSeq": 0, "kernelInfo": {"language": "python"},
    }))
    frames = []
    while True:
        raw = await asyncio.wait_for(ws.read_message(), 20)
        assert raw is not None, "socket closed mid-round"
        frame = json.loads(raw)
        frames.append(frame)
        if frame["op"] == "done":
            return frames


async def _connected_runner(jp_serverapp, jp_http_port, jp_base_url, command: str):
    runner = Runner(choose_agent("x", command=command), out=io.StringIO())
    task = asyncio.ensure_future(
        runner.serve(_target(jp_serverapp, jp_http_port, jp_base_url), once=True)
    )
    for _ in range(100):
        if HUB.connected:
            return runner, task
        await asyncio.sleep(0.05)
    task.cancel()
    raise AssertionError(f"runner never attached: {runner.out.getvalue()}")


# -- authentication ------------------------------------------------------------


@pytest.mark.parametrize("route", ["ws", "runner"])
@on_server_loop
async def test_neither_socket_opens_without_the_token(jp_serverapp, jp_http_port, jp_base_url, route):
    """Both sockets could once be opened by any process that reached the port."""
    url = f"ws://127.0.0.1:{jp_http_port}{jp_base_url}jupyter-vybe/{route}"
    with pytest.raises(HTTPClientError) as err:
        await websocket_connect(HTTPRequest(url))
    assert err.value.code == 403


@on_server_loop
async def test_a_wrong_token_is_refused_too(jp_serverapp, jp_http_port, jp_base_url):
    url = f"ws://127.0.0.1:{jp_http_port}{jp_base_url}jupyter-vybe/ws?token=not-the-token"
    with pytest.raises(HTTPClientError) as err:
        await websocket_connect(HTTPRequest(url))
    assert err.value.code == 403


@pytest.mark.parametrize("route", ["ws", "runner"])
@on_server_loop
async def test_a_web_page_elsewhere_cannot_open_either_socket(
    jp_serverapp, jp_http_port, jp_base_url, route
):
    """What the old sidecar let any web page do. A page on another site has
    no token, and Jupyter refuses a foreign origin that does not present one.
    (With the token, Jupyter skips the origin check by design, as it does for
    kernels: the token is the secret, and a foreign page cannot know it.)"""
    url = f"ws://127.0.0.1:{jp_http_port}{jp_base_url}jupyter-vybe/{route}"
    with pytest.raises(HTTPClientError) as err:
        await websocket_connect(HTTPRequest(url, headers={"Origin": "https://evil.example"}))
    assert err.value.code == 403


@on_server_loop
async def test_the_token_opens_the_agent_socket(jp_ws_fetch):
    ws = await jp_ws_fetch("jupyter-vybe", "ws")
    await ws.write_message(json.dumps({
        "op": "hello", "notebookPath": "/auth.ipynb", "agentId": "runner",
        "options": {"historyPersist": False},
    }))
    assert json.loads(await ws.read_message()) == {"op": "status", "state": "ready"}
    ws.close()


@on_server_loop
async def test_the_runner_reports_a_refused_token_and_stops(jp_serverapp, jp_http_port, jp_base_url):
    out = io.StringIO()
    runner = Runner(choose_agent("x", command=f"{sys.executable} -c pass"), out=out)
    code = await runner.serve(_target(jp_serverapp, jp_http_port, jp_base_url, token="wrong"))
    assert code == 2
    assert "refused the token" in out.getvalue()


# -- the relay -------------------------------------------------------------------


@on_server_loop
async def test_a_prompt_is_answered_by_the_agent_on_the_users_machine(
    jp_serverapp, jp_http_port, jp_base_url, jp_ws_fetch, tmp_path
):
    agent = tmp_path / "agent.py"
    agent.write_text(FAKE_AGENT)
    command = f"{sys.executable} {agent}"
    runner, task = await _connected_runner(jp_serverapp, jp_http_port, jp_base_url, command)
    ws = await jp_ws_fetch("jupyter-vybe", "ws")
    frames = await _round(ws, "/relay.ipynb")
    tools = [f for f in frames if f["op"] == "tool"]
    assert [t["args"]["body"] for t in tools] == ["x = 1"]
    assert frames[-1]["stopReason"] == "end"
    assert "make x" in runner.out.getvalue(), "the user's terminal shows what was asked"
    ws.close()
    task.cancel()


@on_server_loop
async def test_without_a_runner_the_round_says_how_to_start_one(jp_ws_fetch):
    ws = await jp_ws_fetch("jupyter-vybe", "ws")
    frames = await _round(ws, "/nobody.ipynb")
    errors = [f["message"] for f in frames if f["op"] == "error"]
    assert errors and "jupyter-vybe-local connect" in errors[0]
    assert frames[-1]["stopReason"] == "error"
    ws.close()


@on_server_loop
async def test_an_agent_failure_reaches_the_notebook(jp_serverapp, jp_http_port, jp_base_url, jp_ws_fetch):
    command = f"{sys.executable} -c 'import sys; sys.stderr.write(\"not logged in\"); sys.exit(1)'"
    runner, task = await _connected_runner(jp_serverapp, jp_http_port, jp_base_url, command)
    ws = await jp_ws_fetch("jupyter-vybe", "ws")
    frames = await _round(ws, "/fails.ipynb")
    errors = [f["message"] for f in frames if f["op"] == "error"]
    assert errors and "not logged in" in errors[0]
    ws.close()
    task.cancel()


@on_server_loop
async def test_the_socket_keeps_listening_while_a_round_runs(
    jp_serverapp, jp_http_port, jp_base_url, jp_ws_fetch, tmp_path
):
    """A round awaited inside on_message blocked every later frame: the
    cancel, a kernel lookup's result, and the pong that Jupyter's keep-alive
    needs -- so any round past 90 s was closed under the user."""
    agent = tmp_path / "slow.py"
    agent.write_text("import time; time.sleep(30)")
    runner, task = await _connected_runner(
        jp_serverapp, jp_http_port, jp_base_url, f"{sys.executable} {agent}"
    )
    ws = await jp_ws_fetch("jupyter-vybe", "ws")
    await ws.write_message(json.dumps({
        "op": "hello", "notebookPath": "/slow.ipynb", "agentId": "runner",
        "options": {"historyPersist": False},
    }))
    await ws.read_message()
    await ws.write_message(json.dumps({
        "op": "prompt", "roundId": "r1", "prompt": "slow", "promptCellId": "p1",
        "snapshot": NOTEBOOK, "snapshotSeq": 0, "kernelInfo": {},
    }))
    await asyncio.sleep(0.5)
    await ws.write_message(json.dumps({"op": "cancel"}))
    started = asyncio.get_running_loop().time()
    while True:
        frame = json.loads(await asyncio.wait_for(ws.read_message(), 5))
        if frame["op"] == "done":
            break
    assert frame["stopReason"] == "cancelled"
    assert asyncio.get_running_loop().time() - started < 5
    ws.close()
    task.cancel()


@on_server_loop
async def test_the_agent_list_names_the_connected_runner(
    jp_serverapp, jp_http_port, jp_base_url, jp_fetch
):
    command = f"{sys.executable} -c pass"
    runner, task = await _connected_runner(jp_serverapp, jp_http_port, jp_base_url, command)
    response = await jp_fetch("jupyter-vybe", "agents")
    agents = {a["id"]: a for a in json.loads(response.body)["agents"]}
    assert agents["runner"]["connected"] is True
    health = json.loads((await jp_fetch("jupyter-vybe", "health", params={"agent": "runner"})).body)
    assert health["ok"] and health["hostname"]
    task.cancel()


@on_server_loop
async def test_a_second_runner_takes_over_and_the_first_stops(jp_serverapp, jp_http_port, jp_base_url):
    """Not reconnect: two runners that each reconnected when replaced would
    evict each other for as long as both were running."""
    first, first_task = await _connected_runner(
        jp_serverapp, jp_http_port, jp_base_url, f"{sys.executable} -c pass"
    )
    first_link = HUB._link
    second = Runner(choose_agent("x", command=f"{sys.executable} -c 'print(2)'"), out=io.StringIO())
    second_task = asyncio.ensure_future(
        second.serve(_target(jp_serverapp, jp_http_port, jp_base_url), once=True)
    )
    assert await asyncio.wait_for(first_task, 5) == 0
    assert "stopped" in first.out.getvalue()
    await asyncio.sleep(0.2)
    assert HUB.connected and HUB._link is not first_link
    second_task.cancel()


# -- what the server cannot make the runner do -------------------------------------


async def test_a_server_frame_cannot_choose_what_runs(tmp_path):
    """The agent is fixed when the runner starts. A frame naming a command,
    argv, model, path or environment changes nothing about what is executed."""
    planted = tmp_path / "PWNED"
    runner = Runner(
        choose_agent("x", command=f"{sys.executable} -c 'print(1)'"), out=io.StringIO()
    )
    sent: list[dict] = []

    class Conn:
        async def send(self, frame):
            sent.append(frame)

    hostile = {
        "op": "stream", "id": "s1", "session": "k",
        "command": f"touch {planted}", "argv": ["touch", str(planted)],
        "options": {"command": f"touch {planted}"}, "model": "x", "cwd": "/",
        "env": {"PATH": "/tmp"}, "agent": "custom",
        "messages": [{"role": "user", "content": "hi"}],
    }
    await runner.handle(hostile, Conn())
    for _ in range(100):
        if any(f["op"] in ("end", "error") for f in sent):
            break
        await asyncio.sleep(0.05)
    assert not planted.exists()
    assert "".join(f.get("text", "") for f in sent).strip() == "1"
    await runner.shutdown()


async def test_unknown_ops_are_ignored_rather_than_interpreted():
    runner = Runner(choose_agent("x", command=f"{sys.executable} -c pass"), out=io.StringIO())

    class Conn:
        async def send(self, frame):
            raise AssertionError(f"nothing should be sent: {frame}")

    for op in ("exec", "run", "shell", "spawn", "hello", "prompt"):
        await runner.handle({"op": op, "command": "touch /tmp/x"}, Conn())
    assert runner._sessions == {}


def test_every_preset_switches_the_agents_tools_off_or_down():
    from jupyter_vybe.local import PRESETS

    claude = PRESETS["claude"].argv()
    assert claude[claude.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in claude and "--disable-slash-commands" in claude
    codex = PRESETS["codex"].argv()
    assert codex[codex.index("--sandbox") + 1] == "read-only"
    agy = PRESETS["agy"].argv(model="m")
    assert agy[-1] == "-p", "agy reads the next argument as the prompt"
    denied = json.loads(agy[agy.index("--settings") + 1])["permissions"]["deny"]
    assert "read_file(*)" in denied and "run_command(*)" in denied


# -- the URL the user pastes -------------------------------------------------------


@pytest.mark.parametrize(
    "url, expect",
    [
        ("http://127.0.0.1:8888/lab/tree/a.ipynb?token=abc",
         ("127.0.0.1", 8888, False, "/jupyter-vybe/runner", "abc")),
        ("https://hub.example.org/user/rrs/lab?token=t",
         ("hub.example.org", 443, True, "/user/rrs/jupyter-vybe/runner", "t")),
        ("localhost:8899", ("localhost", 8899, False, "/jupyter-vybe/runner", "")),
    ],
)
def test_the_url_is_whatever_the_user_copied(url, expect):
    t = parse_target(url)
    assert (t.host, t.port, t.secure, t.path, t.token) == expect


def test_an_explicit_token_wins_over_the_url():
    assert parse_target("http://h:1/lab?token=old", token="new").token == "new"


def test_an_unknown_agent_is_refused_with_the_choices():
    with pytest.raises(SystemExit, match="claude"):
        choose_agent("not-an-agent")
