"""Any binary that reads a prompt and writes tokens: `claude -p`, and friends (docs/06).

Two modes:

* **one-shot** (default) -- spawn `argv + [prompt]` per turn and read stdout to
  EOF. The transcript is replayed into the prompt, so it works with anything
  that answers on stdout.
* **persistent** -- keep the child alive and exchange newline-delimited JSON,
  as `claude -p --input-format stream-json --output-format stream-json` does.
  Claude Code refuses that without `--verbose`, so it is added when missing.
  A failed handshake falls back to one-shot rather than failing the round, and
  the fallback drops the stream-json flags: left in, they made the one-shot
  retry fail the same way, so the fallback could never succeed.

A reply with nothing on stdout is an error, not an empty answer. Agent CLIs
exit 0 having said nothing -- `agy -p` does when a tool it wanted was denied
-- and their reason is on stderr, which is what the user needs to see.

`argv` is split with shlex and executed with shell=False, so no user string
ever reaches /bin/sh. The command is the user's own, on their own machine, so
this is footgun avoidance rather than a security boundary (docs/06).
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import socket
from typing import AsyncIterator

from .base import Adapter, AdapterSpec, HealthStatus, Message

HANDSHAKE_TIMEOUT = float(os.environ.get("VYBE_COMMAND_HANDSHAKE", "10"))

# Flags that only mean something to a stream-json child. Each entry is the flag
# and how many values follow it.
_STREAM_ONLY_FLAGS = {
    "--input-format": 1,
    "--output-format": 1,
    "--include-partial-messages": 0,
}


class CommandAdapter(Adapter):
    def __init__(
        self,
        spec: AdapterSpec,
        argv: str | list[str],
        persistent: bool = False,
        api_key_env: str = "",
        cwd: str | None = None,
    ) -> None:
        self.spec = spec
        self.argv = shlex.split(argv) if isinstance(argv, str) else list(argv)
        self.persistent = persistent
        self.api_key_env = api_key_env
        # Where the agent starts. The runner gives each session an empty
        # directory, so an agent that goes looking for files finds none.
        self.cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        if persistent:
            self.argv = _with_verbose(self.argv)

    # -- health ------------------------------------------------------------

    async def health(self) -> HealthStatus:
        if not self.argv:
            return HealthStatus(False, "no command configured", self.spec.id, "")
        binary = shutil.which(self.argv[0])
        if binary is None:
            return HealthStatus(
                False, f"{self.argv[0]!r} is not on the server's PATH", self.spec.id, ""
            )
        if self.api_key_env and not os.environ.get(self.api_key_env):
            return HealthStatus(
                False,
                f"{self.api_key_env} is not set in the server's environment",
                self.spec.id,
                binary,
            )
        return HealthStatus(
            True,
            "persistent" if self.persistent else "one-shot",
            self.spec.id,
            binary,
            socket.gethostname(),
        )

    # -- streaming ---------------------------------------------------------

    async def stream(
        self, messages: list[Message], cancel: asyncio.Event
    ) -> AsyncIterator[str]:
        if self.persistent:
            try:
                async for chunk in self._stream_persistent(messages, cancel):
                    yield chunk
                return
            except (OSError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
                # Falling back beats failing the round; the user is told which
                # mode answered through the health detail.
                await self.close()
                self.persistent = False
                self.argv = _one_shot_argv(self.argv)
                self.spec.requires_config.append(
                    f"persistent handshake failed ({exc}); using one-shot"
                )
        async for chunk in self._stream_once(_replay(messages), cancel):
            yield chunk

    async def _stream_once(
        self, prompt: str, cancel: asyncio.Event
    ) -> AsyncIterator[str]:
        proc = await asyncio.create_subprocess_exec(
            *self.argv,
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Nothing to read: an agent that waits on stdin for a prompt it was
            # already given as an argument would otherwise hang the round.
            stdin=asyncio.subprocess.DEVNULL,
            cwd=self.cwd,
            # Its own process group, so cancelling kills the whole tree rather
            # than orphaning children of a wrapper script.
            start_new_session=True,
        )
        said_anything = False
        try:
            assert proc.stdout is not None
            while True:
                if cancel.is_set():
                    _terminate(proc)
                    return
                block = await proc.stdout.read(256)
                if not block:
                    break
                text = block.decode("utf-8", errors="replace")
                said_anything = said_anything or bool(text.strip())
                yield text
            await proc.wait()
            if proc.returncode not in (0, None) or not said_anything:
                assert proc.stderr is not None
                err = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
                if proc.returncode not in (0, None):
                    raise RuntimeError(
                        f"{self.argv[0]} exited {proc.returncode}: {err[:400]}"
                    )
                raise RuntimeError(
                    f"{self.argv[0]} replied with nothing"
                    + (f": {err[:400]}" if err else " (and said nothing on stderr)")
                )
        finally:
            if proc.returncode is None:
                _terminate(proc)

    async def _stream_persistent(
        self, messages: list[Message], cancel: asyncio.Event
    ) -> AsyncIterator[str]:
        proc = await self._ensure_child()
        assert proc.stdin is not None and proc.stdout is not None
        request = {
            "type": "user",
            "message": {"role": "user", "content": messages[-1].content},
        }
        proc.stdin.write((json.dumps(request) + "\n").encode())
        await proc.stdin.drain()

        # With --include-partial-messages the text arrives twice: as deltas
        # while it is written, then whole in the closing assistant message.
        # Once any delta has been seen the whole message is the duplicate.
        streamed = False
        said_anything = False
        while True:
            if cancel.is_set():
                await self.close()
                return
            line = await asyncio.wait_for(
                proc.stdout.readline(), timeout=HANDSHAKE_TIMEOUT * 30
            )
            if not line:
                raise OSError("child closed stdout mid-turn")
            try:
                event = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind in ("result", "done"):
                if event.get("is_error"):
                    raise RuntimeError(
                        f"{self.argv[0]}: {str(event.get('result') or event.get('subtype'))[:400]}"
                    )
                if not said_anything:
                    raise RuntimeError(f"{self.argv[0]} replied with nothing")
                return
            delta = _delta_of(event)
            if delta is not None:
                streamed = True
                text = delta
            elif kind == "assistant" and streamed:
                continue
            else:
                text = _text_of(event)
            if text:
                said_anything = said_anything or bool(text.strip())
                yield text

    async def _ensure_child(self) -> asyncio.subprocess.Process:
        if self._proc is not None and self._proc.returncode is None:
            return self._proc
        self._proc = await asyncio.create_subprocess_exec(
            *self.argv,
            cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        return self._proc

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            _terminate(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()


def _terminate(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.terminate()
    except ProcessLookupError:
        pass


def _with_verbose(argv: list[str]) -> list[str]:
    """Claude Code will not emit stream-json in print mode without --verbose."""
    wants_stream = any(
        a == "--output-format" and i + 1 < len(argv) and argv[i + 1] == "stream-json"
        for i, a in enumerate(argv)
    ) or "--output-format=stream-json" in argv
    if wants_stream and "--verbose" not in argv:
        return [*argv, "--verbose"]
    return list(argv)


def _one_shot_argv(argv: list[str]) -> list[str]:
    """The same command with the flags only a stream-json child understands removed."""
    out: list[str] = []
    skip = 0
    for arg in argv:
        if skip:
            skip -= 1
            continue
        name = arg.split("=", 1)[0]
        if name in _STREAM_ONLY_FLAGS:
            skip = 0 if "=" in arg else _STREAM_ONLY_FLAGS[name]
            continue
        out.append(arg)
    return out


def _replay(messages: list[Message]) -> str:
    """One-shot has no memory, so the transcript goes into the prompt."""
    if len(messages) == 1:
        return messages[0].content
    parts = []
    for m in messages:
        if m.role == "assistant":
            parts.append(f"[your previous reply]\n{m.content}")
        else:
            parts.append(m.content)
    return "\n\n".join(parts)


def _delta_of(event: dict) -> str | None:
    """Text from a partial-message event, or None if this is not one.

    The shape `claude -p --include-partial-messages` emits:
    {"type": "stream_event", "event": {"type": "content_block_delta",
     "delta": {"type": "text_delta", "text": "..."}}}
    """
    if event.get("type") != "stream_event":
        return None
    inner = event.get("event")
    if not isinstance(inner, dict) or inner.get("type") != "content_block_delta":
        return ""
    delta = inner.get("delta")
    if isinstance(delta, dict) and delta.get("type") == "text_delta":
        return str(delta.get("text", ""))
    return ""


def _text_of(event: dict) -> str:
    """Pull text out of the shapes stream-json actually emits."""
    if isinstance(event.get("delta"), dict):
        return event["delta"].get("text", "") or ""
    message = event.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict)
            )
    if isinstance(event.get("text"), str):
        return event["text"]
    return ""
