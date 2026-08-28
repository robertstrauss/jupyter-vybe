"""Shared fixtures.

`make_session` exists because four test files were each hand-building an
AgentSession through `__new__` -- to skip build_adapter -- and every new field
on the session broke all four. One builder means adding a field is a one-line
change here rather than a scavenger hunt.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jupyter_vybe.session import AgentSession


def build_session(adapter: Any, notebook_path: str = "/n.ipynb", **overrides: Any):
    """An AgentSession with a stub adapter and nothing else touched.

    Constructed through __new__ deliberately: the real __init__ builds an
    adapter from the registry, and a unit test should not be reaching for
    Ollama.
    """
    session = AgentSession.__new__(AgentSession)
    defaults: dict[str, Any] = {
        "notebook_path": notebook_path,
        "agent_id": "scripted",
        "persistent": True,
        "options": {},
        "adapter": adapter,
        "messages": [],
        "turns": 0,
        "snapshot_seq": -1,
        "touched": 0.0,
        "_cancel": asyncio.Event(),
        "_tool_waiters": {},
        "_cells": [],
        "_prose": "",
        # Off by default: a unit test should not write to $HOME.
        "history_enabled": False,
        "store": None,
        "history_location": "user",
        "echo_prompt": False,
    }
    defaults.update(overrides)
    for name, value in defaults.items():
        setattr(session, name, value)
    return session


@pytest.fixture()
def make_session():
    return build_session
