"""The command adapter: any binary that writes tokens to stdout (docs/06)."""

import asyncio
import sys

import pytest

from jupyter_vybe.adapters import Message, build_adapter
from jupyter_vybe.adapters.command import CommandAdapter, _replay
from jupyter_vybe.adapters.base import AdapterSpec

SPEC = AdapterSpec("command", "command", 1000)


def _collect(adapter, messages=None):
    async def go():
        out = ""
        async for chunk in adapter.stream(
            messages or [Message("user", "hello")], asyncio.Event()
        ):
            out += chunk
        return out

    return asyncio.run(go())


def test_one_shot_reads_stdout_to_eof():
    adapter = CommandAdapter(SPEC, [sys.executable, "-c", "import sys; print(sys.argv[1])"])
    assert _collect(adapter).strip() == "hello"


def test_a_failing_command_reports_its_stderr_rather_than_silence():
    adapter = CommandAdapter(
        SPEC,
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
    )
    with pytest.raises(RuntimeError, match="boom"):
        _collect(adapter)


def test_health_names_the_missing_binary():
    status = asyncio.run(CommandAdapter(SPEC, "no-such-binary-xyz").health())
    assert not status.ok
    assert "no-such-binary-xyz" in status.detail


def test_health_names_a_missing_api_key_env():
    status = asyncio.run(
        CommandAdapter(SPEC, [sys.executable], api_key_env="VYBE_NOT_SET_XYZ").health()
    )
    assert not status.ok
    assert "VYBE_NOT_SET_XYZ" in status.detail


def test_argv_is_split_but_never_handed_to_a_shell():
    adapter = CommandAdapter(SPEC, "echo 'two words'")
    # shlex keeps the quoted argument together...
    assert adapter.argv == ["echo", "two words"]
    # ...and nothing here is a shell string, so a `;` in a prompt is inert.
    assert all(isinstance(a, str) for a in adapter.argv)


def test_one_shot_replays_the_transcript_because_it_has_no_memory():
    prompt = _replay(
        [
            Message("user", "first request"),
            Message("assistant", "<vybe:add_cell>x</vybe:add_cell>"),
            Message("user", "second request"),
        ]
    )
    assert "first request" in prompt
    assert "[your previous reply]" in prompt
    assert prompt.endswith("second request")


def test_a_single_message_is_passed_through_unchanged():
    assert _replay([Message("user", "just this")]) == "just this"


def test_the_registry_builds_it_from_settings():
    adapter = build_adapter("command", {"command": "claude -p", "persistent": "true"})
    assert adapter.argv == ["claude", "-p"]
    assert adapter.persistent is True
