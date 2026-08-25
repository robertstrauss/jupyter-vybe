"""Assemble the full agent prompt from the five template files (docs/08)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS = Path(__file__).parent / "prompts"


@lru_cache(maxsize=None)
def _load(name: str) -> str:
    path = PROMPTS / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


def kernel_banner(kernel: dict) -> str:
    """Pinned context — never subject to the truncation ladder (D13, docs/09)."""
    language = kernel.get("language") or "unknown"
    name = kernel.get("name") or "unknown"
    version = kernel.get("version") or ""
    if language == "unknown":
        return (
            "Kernel: unknown — this notebook declares no kernel and has no code.\n"
            "You cannot tell which language to write. Ask which language, in one short "
            "sentence of prose, and emit no cells."
        )
    return (
        f"Kernel: {name} — language {language} {version}".rstrip()
        + f"\nWrite code in {language} unless the user asks otherwise."
    )


def assemble_initial(
    *,
    notebook_json: str,
    kernel: dict,
    prompt_cell_id: str,
    user_prompt: str,
    global_prompt: str = "",
    tooling: str | None = None,
    notebook_mode: str = "Full notebook state:",
) -> str:
    template = _load("template.md")
    values = {
        "UNIVERSAL_PROMPT": _load("universal.md"),
        "TOOLING_PROMPT": tooling if tooling is not None else _load("tooling.md"),
        "KERNEL_BANNER": kernel_banner(kernel),
        "NOTEBOOK_MODE": notebook_mode,
        "NOTEBOOK_JSON": notebook_json,
        "PROMPT_CELL_ID": prompt_cell_id,
        "GLOBAL_PROMPT": global_prompt.strip() or _load("global.md"),
        "USER_PROMPT": user_prompt,
    }
    return _substitute(template, values)


def assemble_followup(*, notebook_diff: str, kernel: dict, user_prompt: str) -> str:
    """Persistent-session turns send a diff, not the whole notebook (docs/09)."""
    return (
        f"{kernel_banner(kernel)}\n\n"
        f"Changes to the notebook since your last turn:\n\n{notebook_diff}\n\n"
        f"## Request\n\n{user_prompt}\n"
    )


def _substitute(template: str, values: dict[str, str]) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def missing_tags(template: str | None = None) -> list[str]:
    """Any {{TAG}} left unfilled — used by the assembler tests."""
    import re

    return re.findall(r"\{\{([A-Z_]+)\}\}", template if template is not None else _load("template.md"))
