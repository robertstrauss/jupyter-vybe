"""`jupyter-vybe-doctor` — check an installation before blaming the model.

Every hour lost on this project was lost the same way: something was wrong one
layer below where anyone was looking. A frontend served from a stale bundle. A
settings schema whose file name did not match the plugin that loads it, so
every setting silently sat at its default. An adapter pointed at a model id the
server had never heard of, behind a /health endpoint that cheerfully said "ok".

Each check here exists because that specific thing actually happened.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .adapters import ADAPTER_REGISTRY, build_adapter

OK, WARN, FAIL = "ok", "warn", "fail"

_MARK = {OK: "✓", WARN: "!", FAIL: "✗"}


@dataclass
class Check:
    status: str
    title: str
    detail: str = ""
    hint: str = ""

    def render(self) -> str:
        line = f"  {_MARK[self.status]} {self.title}"
        if self.detail:
            line += f"\n      {self.detail}"
        if self.hint and self.status != OK:
            line += f"\n      → {self.hint}"
        return line


def _version() -> str:
    try:
        from ._version import __version__

        return __version__
    except Exception:  # noqa: BLE001 — a broken build is what we are looking for
        return "unknown"


def check_python_package() -> Check:
    spec = importlib.util.find_spec("jupyter_vybe")
    where = Path(spec.origin).parent if spec and spec.origin else None
    return Check(
        OK,
        f"jupyter_vybe {_version()}",
        f"{where}  (python {sys.version.split()[0]})",
    )


def _labextension_dirs() -> list[Path]:
    """Every place JupyterLab looks for a prebuilt extension."""
    roots = [Path(sys.prefix) / "share" / "jupyter" / "labextensions"]
    if os.environ.get("JUPYTER_PATH"):
        roots += [
            Path(p) / "labextensions" for p in os.environ["JUPYTER_PATH"].split(os.pathsep)
        ]
    roots.append(Path.home() / ".local" / "share" / "jupyter" / "labextensions")
    return [r / "jupyter-vybe" for r in roots if (r / "jupyter-vybe").exists()]


def check_labextension() -> Check:
    found = _labextension_dirs()
    if not found:
        return Check(
            FAIL,
            "frontend extension is not installed",
            hint="pip install --force-reinstall jupyter-vybe, then restart JupyterLab",
        )
    where = found[0]
    try:
        meta = json.loads((where / "package.json").read_text())
        version = meta.get("version", "?")
    except Exception:  # noqa: BLE001
        return Check(FAIL, "frontend extension is unreadable", str(where))

    detail = f"{where}  (version {version})"
    if where.is_symlink() or (where / "static").is_symlink():
        detail += "  [development install]"
    if version != _version() and _version() != "unknown":
        # The exact shape of "I fixed it but nothing changed".
        return Check(
            WARN,
            "frontend and backend versions disagree",
            f"frontend {version}, backend {_version()}",
            hint="pip install -e . && jupyter labextension develop --overwrite .",
        )
    return Check(OK, "frontend extension installed", detail)


def check_server_extension() -> Check:
    config = Path(sys.prefix) / "etc" / "jupyter" / "jupyter_server_config.d"
    enabled = list(config.glob("jupyter_vybe.json")) if config.exists() else []
    if not enabled:
        return Check(
            WARN,
            "server extension is not enabled by config",
            str(config),
            hint="jupyter server extension enable jupyter_vybe",
        )
    try:
        content = json.loads(enabled[0].read_text())
        on = content["ServerApp"]["jpserver_extensions"]["jupyter_vybe"]
    except Exception:  # noqa: BLE001
        on = False
    return (
        Check(OK, "server extension enabled", str(enabled[0]))
        if on
        else Check(
            FAIL,
            "server extension is present but disabled",
            hint="jupyter server extension enable jupyter_vybe",
        )
    )


def check_settings_schema() -> Check:
    """The bug that made every setting silently sit at its default.

    JupyterLab keys a schema by <package>:<basename>, so the file name has to
    match the plugin id that loads it. When it did not, registry.load() threw
    and nothing anyone changed in the settings pane had any effect.
    """
    for where in _labextension_dirs():
        schemas = where / "schemas" / "jupyter-vybe"
        if not schemas.exists():
            continue
        files = sorted(p.name for p in schemas.glob("*.json"))
        if "settings.json" in files:
            return Check(OK, "settings schema present", f"{schemas}/settings.json")
        return Check(
            FAIL,
            "settings schema is named for a different plugin",
            f"found {files} in {schemas}",
            hint="settings will silently stay at their defaults; rebuild the extension",
        )
    return Check(WARN, "settings schema not found", hint="is the frontend installed?")


def check_history_writable() -> Check:
    from .history import DEFAULT_ROOT

    try:
        DEFAULT_ROOT.mkdir(parents=True, exist_ok=True)
        probe = DEFAULT_ROOT / ".doctor-probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return Check(
            WARN,
            "prompt history is not writable",
            str(exc),
            hint="rounds still work; the record is what is lost (docs/05)",
        )
    return Check(OK, "prompt history writable", str(DEFAULT_ROOT))


async def check_agents(only: str | None = None) -> list[Check]:
    checks: list[Check] = []
    for agent_id, spec in ADAPTER_REGISTRY.items():
        if only and agent_id != only:
            continue
        if spec.requires_config and not only:
            checks.append(
                Check(OK, f"{agent_id}: needs configuration", ", ".join(spec.requires_config))
            )
            continue
        try:
            adapter = build_adapter(agent_id)
            status = await asyncio.wait_for(adapter.health(), timeout=10)
        except Exception as exc:  # noqa: BLE001 — the report is the point
            checks.append(Check(WARN, f"{agent_id}: {type(exc).__name__}", str(exc)[:120]))
            continue
        # The hostname is here because a tunnel aimed at the wrong machine
        # answers exactly like a working one (docs/13 R12).
        detail = status.detail
        if status.hostname:
            detail += f"  (answered by {status.hostname})"
        checks.append(
            Check(OK, agent_id, detail)
            if status.ok
            else Check(WARN, f"{agent_id}: not ready", detail)
        )
    return checks


def run(only: str | None = None) -> int:
    checks = [
        check_python_package(),
        check_labextension(),
        check_server_extension(),
        check_settings_schema(),
        check_history_writable(),
    ]
    print("jupyter-vybe doctor\n")
    print("Installation")
    for check in checks:
        print(check.render())

    print("\nAgents")
    agent_checks = asyncio.run(check_agents(only))
    for check in agent_checks:
        print(check.render())

    failures = [c for c in checks + agent_checks if c.status == FAIL]
    warnings = [c for c in checks + agent_checks if c.status == WARN]
    print()
    if failures:
        print(f"{len(failures)} problem(s) that will stop the extension working.")
    elif warnings:
        print(f"No blocking problems. {len(warnings)} thing(s) worth a look.")
    else:
        print("Everything checks out.")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="jupyter-vybe-doctor",
        description="Check a jupyter-vybe installation and its agents.",
    )
    parser.add_argument("--agent", help="probe only this agent id")
    args = parser.parse_args(argv)
    return run(args.agent)


if __name__ == "__main__":
    sys.exit(main())
