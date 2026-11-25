"""The command adapter: any binary that writes tokens to stdout (docs/06)."""

import asyncio
import os
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


def test_options_reach_the_adapter_from_the_session(tmp_path):
    """The hello frame's options are how a command is configured at all."""
    from jupyter_vybe.session import AgentSession

    session = AgentSession(
        str(tmp_path / "n.ipynb"),
        "command",
        True,
        {"command": "claude -p", "historyPersist": False},
    )
    assert session.adapter.argv == ["claude", "-p"]


# -- persistent mode against the shapes Claude Code actually emits ------------

# A stand-in for `claude -p --input-format stream-json --output-format
# stream-json --include-partial-messages`: one reply per stdin line, streamed
# as deltas and then repeated whole, exactly as recorded from Claude Code 2.1.
FAKE_STREAM_JSON = r'''
import json, sys
for line in sys.stdin:
    text = "<vybe:add_cell>\nx = 1\n</vybe:add_cell>"
    emit = lambda e: (sys.stdout.write(json.dumps(e) + "\n"), sys.stdout.flush())
    emit({"type": "system", "subtype": "init", "tools": []})
    emit({"type": "stream_event", "event": {"type": "message_start"}})
    for piece in (text[:10], text[10:]):
        emit({"type": "stream_event", "event": {"type": "content_block_delta",
              "delta": {"type": "text_delta", "text": piece}}})
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})
    emit({"type": "result", "subtype": "success", "result": text})
'''


def test_stream_json_needs_verbose_so_it_is_added():
    adapter = CommandAdapter(
        SPEC, "claude -p --input-format stream-json --output-format stream-json", persistent=True
    )
    assert adapter.argv[-1] == "--verbose"


def test_verbose_is_not_added_twice_or_to_one_shot():
    persistent = CommandAdapter(
        SPEC, "claude -p --verbose --output-format stream-json", persistent=True
    )
    assert persistent.argv.count("--verbose") == 1
    assert "--verbose" not in CommandAdapter(SPEC, "claude -p").argv


def test_the_one_shot_fallback_drops_flags_only_stream_json_understands():
    """Kept in, they made the fallback fail exactly as the handshake had."""
    from jupyter_vybe.adapters.command import _one_shot_argv

    argv = [
        "claude", "-p", "--input-format", "stream-json", "--output-format=stream-json",
        "--include-partial-messages", "--verbose", "--model", "haiku",
    ]
    assert _one_shot_argv(argv) == ["claude", "-p", "--verbose", "--model", "haiku"]


def test_partial_messages_stream_and_the_closing_copy_is_not_repeated():
    adapter = CommandAdapter(SPEC, [sys.executable, "-c", FAKE_STREAM_JSON], persistent=True)

    async def go():
        chunks = [c async for c in adapter.stream([Message("user", "hi")], asyncio.Event())]
        await adapter.close()
        return chunks

    chunks = asyncio.run(go())
    assert len(chunks) == 2, "the deltas should arrive as they are written"
    assert "".join(chunks) == "<vybe:add_cell>\nx = 1\n</vybe:add_cell>"


def test_a_silent_success_is_an_error_carrying_the_agents_reason():
    """`agy -p` exits 0 with nothing on stdout when a tool it wanted was denied."""
    adapter = CommandAdapter(
        SPEC,
        [sys.executable, "-c", "import sys; sys.stderr.write('a tool was auto-denied')"],
    )
    with pytest.raises(RuntimeError, match="replied with nothing: a tool was auto-denied"):
        _collect(adapter)


def test_the_agent_starts_in_the_directory_it_is_given(tmp_path):
    adapter = CommandAdapter(
        SPEC, [sys.executable, "-c", "import os; print(os.getcwd())"], cwd=str(tmp_path)
    )
    assert os.path.realpath(_collect(adapter).strip()) == os.path.realpath(str(tmp_path))
