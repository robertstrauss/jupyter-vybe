"""`jupyter-vybe-doctor`: the checks exist because these things happened."""

import json
from pathlib import Path

import pytest

from jupyter_vybe import doctor


def test_it_names_where_the_package_actually_is():
    check = doctor.check_python_package()
    assert check.status == doctor.OK
    assert "jupyter_vybe" in check.detail


def test_a_schema_named_for_the_wrong_plugin_is_a_failure(tmp_path, monkeypatch):
    """The bug that made every setting silently sit at its default.

    JupyterLab keys a schema by <package>:<basename>. When the file was called
    plugin.json and the plugin was jupyter-vybe:settings, registry.load found
    nothing and nothing anyone changed in the settings pane had any effect.
    """
    ext = tmp_path / "jupyter-vybe"
    (ext / "schemas" / "jupyter-vybe").mkdir(parents=True)
    (ext / "schemas" / "jupyter-vybe" / "plugin.json").write_text("{}")
    monkeypatch.setattr(doctor, "_labextension_dirs", lambda: [ext])

    check = doctor.check_settings_schema()

    assert check.status == doctor.FAIL
    assert "plugin.json" in check.detail
    assert "silently" in check.hint


def test_a_correctly_named_schema_passes(tmp_path, monkeypatch):
    ext = tmp_path / "jupyter-vybe"
    (ext / "schemas" / "jupyter-vybe").mkdir(parents=True)
    (ext / "schemas" / "jupyter-vybe" / "settings.json").write_text("{}")
    monkeypatch.setattr(doctor, "_labextension_dirs", lambda: [ext])
    assert doctor.check_settings_schema().status == doctor.OK


def test_mismatched_frontend_and_backend_versions_are_flagged(tmp_path, monkeypatch):
    """The exact shape of "I fixed it and nothing changed"."""
    ext = tmp_path / "jupyter-vybe"
    ext.mkdir(parents=True)
    (ext / "package.json").write_text(json.dumps({"version": "0.9.0"}))
    monkeypatch.setattr(doctor, "_labextension_dirs", lambda: [ext])
    monkeypatch.setattr(doctor, "_version", lambda: "1.1.0")

    check = doctor.check_labextension()

    assert check.status == doctor.WARN
    assert "0.9.0" in check.detail and "1.1.0" in check.detail


def test_a_missing_frontend_is_a_failure_with_the_fix_in_it(monkeypatch):
    monkeypatch.setattr(doctor, "_labextension_dirs", lambda: [])
    check = doctor.check_labextension()
    assert check.status == doctor.FAIL
    assert "pip install" in check.hint


def test_an_unwritable_history_is_a_warning_not_a_failure(monkeypatch, tmp_path):
    """Losing the record is bad; refusing to answer prompts would be worse."""
    import jupyter_vybe.history as history

    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    monkeypatch.setattr(history, "DEFAULT_ROOT", blocked / "history")

    check = doctor.check_history_writable()

    assert check.status == doctor.WARN
    assert "rounds still work" in check.hint


def test_an_agent_that_cannot_be_reached_is_reported_not_raised(monkeypatch):
    import asyncio

    class Broken:
        async def health(self):
            raise ConnectionRefusedError("nothing is listening")

    monkeypatch.setattr(doctor, "build_adapter", lambda *a, **k: Broken())
    checks = asyncio.run(doctor.check_agents(only="ollama-qwen25-coder-7b"))

    assert len(checks) == 1
    assert checks[0].status == doctor.WARN
    assert "nothing is listening" in checks[0].detail


def test_the_render_puts_the_hint_only_on_problems():
    assert "→" not in doctor.Check(doctor.OK, "fine", hint="unused").render()
    assert "→ do this" in doctor.Check(doctor.FAIL, "broken", hint="do this").render()
