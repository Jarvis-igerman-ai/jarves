"""plugins.d/<feature>.toml: a feature module is registered by its own file, not by a line in a shared tuple."""
from __future__ import annotations

import argparse

from jarves import cli, dashboard, plugin_registry as pr, setup_wizard


def _dir(tmp_path):
    (tmp_path / "b.toml").write_text('dashboard = ["json"]\naddons = ["os"]\n', encoding="utf-8")
    (tmp_path / "a.toml").write_text('dashboard = ["re", "json"]\ncli = ["argparse"]\n', encoding="utf-8")
    return tmp_path


def test_names_follow_file_order_without_repeats(tmp_path):
    d = _dir(tmp_path)
    assert pr.names("dashboard", d) == ("re", "json") and pr.names("addons", d) == ("os",) and pr.names("cli", d) == ("argparse",)
    assert [m.__name__ for m in pr.modules("cli", d)] == ["argparse"]


def test_the_registered_plugin_files_are_valid_and_their_modules_import():
    for kind in pr.KINDS:
        assert [m.__name__ for m in pr.modules(kind)] == list(pr.names(kind))
    for m in pr.modules("cli"):
        assert callable(m.add_cli) and isinstance(m.COMMANDS, dict)


def test_the_dashboard_and_wizard_lists_end_with_the_plugin_modules():
    assert dashboard.PLUGIN_MODULES[len(dashboard.PLUGIN_MODULES) - len(pr.names("dashboard")):] == pr.names("dashboard")
    assert setup_wizard.ADDON_MODULES[len(setup_wizard.ADDON_MODULES) - len(pr.names("addons")):] == pr.names("addons")
    assert len(set(dashboard.PLUGIN_MODULES)) == len(dashboard.PLUGIN_MODULES)
    assert len(set(setup_wizard.ADDON_MODULES)) == len(setup_wizard.ADDON_MODULES)


def test_a_plugin_cli_module_gets_its_command_parsed_and_dispatched(tmp_path, monkeypatch, capsys):
    import types
    mod = types.ModuleType("jarves_demo_cli")
    mod.add_cli = lambda sub: sub.add_parser("demoplugin", help="demo").add_argument("--x", default="1")
    mod.COMMANDS = {"demoplugin": lambda a, cfg, home: print("demo ran", a.x) or 0}
    monkeypatch.setitem(__import__("sys").modules, "jarves_demo_cli", mod)
    monkeypatch.setattr(pr, "names", lambda kind, directory=None: ("jarves_demo_cli",) if kind == "cli" else ())
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    assert cli.main(["demoplugin", "--x", "7"]) == 0
    assert "demo ran 7" in capsys.readouterr().out
