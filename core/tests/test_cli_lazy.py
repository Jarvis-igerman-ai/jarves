"""Perf overhaul 4.5 (#159): `jarvis <command>` imports only the module that owns the command. The routing tables in
cli.py must match what each module's add_cli() registers, help output must not change, and a plain command such as
`jarvis status` must not import the ~30 other command modules (2-4 s on the owner's PC before)."""
import argparse
import contextlib
import importlib
import io
import os
import statistics
import subprocess
import sys
from pathlib import Path

import pytest

from jarves import cli

SRC = Path(cli.__file__).resolve().parents[1]
ROWS = [r for r in (*cli._EXT_EARLY, *cli._EXT_LATE) if r]


@pytest.mark.parametrize("row", ROWS, ids=[r[0] for r in ROWS])
def test_table_matches_add_cli(row):
    mod_name, cmds, handlers = row
    mod = importlib.import_module(f"jarves.{mod_name}")
    sub = argparse.ArgumentParser().add_subparsers(dest="cmd")
    mod.add_cli(sub)
    assert tuple(sub.choices) == cmds
    for c in cmds:
        assert callable(cli._handler(("x", mod, cmds, handlers), c))


def _full_choices() -> list[str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        cli.main(["--help"])
    line = next(x for x in buf.getvalue().splitlines() if x.strip().startswith("{"))
    return line.strip().strip("{}").split(",")


def test_every_command_is_routed():
    known = set(cli._INLINE_COMMANDS) | {c for r in ROWS for c in r[1]}
    assert set(_full_choices()) <= known
    assert not set(cli._INLINE_COMMANDS) & {c for r in ROWS for c in r[1]}


@pytest.mark.parametrize("argv,want", [
    (["status"], "status"), (["--no-rtl-fix", "status"], "status"), (["--config", "x.toml", "agents", "list"], "agents"),
    (["--profile=work", "undo"], "undo"), (["--help"], None), ([], None), (["--prof", "work", "status"], None),
    (["no-such-command"], None), (["--config"], None), (["text", "--help"], "text"),
])
def test_requested_command(argv, want):
    assert cli._requested_command(argv) == want


@pytest.mark.parametrize("cmd", ["agents", "release-notes", "undo", "profile", "selftest", "status", "dashboard"])
def test_partial_help_is_identical(cmd, monkeypatch):
    def help_text() -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
            cli.main([cmd, "--help"])
        return buf.getvalue()
    partial = help_text()
    monkeypatch.setattr(cli, "_requested_command", lambda argv: None)
    assert help_text() == partial


def _run(code: str, home: Path) -> str:
    env = dict(os.environ, JARVES_HOME=str(home), PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=180).stdout


def test_status_does_not_import_other_command_modules(tmp_path):
    out = _run("import sys\nimport jarves.cli as c\ntry: c.main(['status'])\nexcept SystemExit: pass\n"
               "print('MODS', ' '.join(sorted(m for m in sys.modules if m.startswith('jarves'))))", tmp_path)
    mods = set(next(x for x in out.splitlines() if x.startswith("MODS")).split()[1:])
    for heavy in ("jarves.telegram_channel", "jarves.orchestrator.surfaces", "jarves.whatsapp_admin", "jarves.diag.service",
                  "jarves.projectmgr.surfaces", "jarves.cli_parity", "jarves.voice.audio"):
        assert heavy not in mods, heavy
    assert len(mods) < 60, len(mods)                 # 105 jarves modules before #159


def test_parser_for_one_command_is_faster_than_all(tmp_path):
    """Median of 3 fresh processes each: registering the modules for ONE command costs a fraction of all of them."""
    code = ("import time\nimport jarves.cli as c\nt = time.perf_counter()\nc._ext_modules({!r})\n"
            "print('T', time.perf_counter() - t)")

    def t(want) -> float:
        out = _run(code.format(want), tmp_path)
        return float(next(x for x in out.splitlines() if x.startswith("T")).split()[1])
    full = statistics.median(t(None) for _ in range(3))
    one = statistics.median(t("status") for _ in range(3))
    assert one < full * 0.5, (one, full)
