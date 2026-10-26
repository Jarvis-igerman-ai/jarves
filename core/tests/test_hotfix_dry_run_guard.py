"""#515 safety guard: --dry-run on ANY hotfix subcommand (before or after the subcommand) changes nothing.
Fake install tree, fake hooks, frozen clock; never touches a real install."""
from __future__ import annotations

import argparse
import hashlib
import types

import pytest

from jarves import hotfix as hf
from jarves import hotfix_recover as rec
from test_hotfix_install import FakeHooks, source, tree, write  # noqa: F401  (tree is a fixture)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    ids = iter(["20261026-100000", "20261026-110000", "20261026-120000"])
    monkeypatch.setattr(hf, "_now_id", lambda: next(ids))


def snapshot(*roots):
    out = {}
    for r in roots:
        for p in sorted(r.rglob("*")):
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir"
    return out


def parse(argv):
    p = argparse.ArgumentParser()
    hf.add_cli(p.add_subparsers(dest="cmd"))
    return p.parse_args(argv)


@pytest.fixture
def applied(tree, monkeypatch):
    inst, src, home = tree
    write(src, "a.py", "A=2\n")
    write(src, "new.py", "N=1\n")
    hf.apply(inst, source(src), home, FakeHooks().hooks())
    fake = FakeHooks()
    monkeypatch.setattr(hf, "default_hooks", lambda cfg, home, inst: fake.hooks())
    monkeypatch.setattr("jarves.cli._cli_features", lambda cfg, home: types.SimpleNamespace(active=lambda f: True))
    return inst, src, home, fake


FLAG_POSITIONS = [
    ["hotfix", "{a}", "--dry-run"],
    ["hotfix", "--dry-run", "{a}"],
]


@pytest.mark.parametrize("action", ["revert", "status", "apply"])
@pytest.mark.parametrize("argv", FLAG_POSITIONS)
def test_cli_dry_run_both_positions_touch_nothing(applied, capsys, action, argv):
    inst, src, home, fake = applied
    extra = ["--from", str(src), "--install", str(inst.parent), "--no-restart"]
    a = parse([x.format(a=action) for x in argv] + extra)
    assert a.dry_run is True and a.action == action
    before = snapshot(inst.parent, home)
    assert hf.cmd_hotfix(a, None, home) == 0
    assert snapshot(inst.parent, home) == before                     # byte-identical tree and manifest
    assert fake.calls == []                                          # no backup, stop, start or doctor hook
    out = capsys.readouterr().out
    if action == "revert":
        assert "would restore" in out and "a.py" in out and "new.py" in out and "20261026-100000" in out
        assert "restored the original files" not in out


def test_revert_without_dry_run_still_reverts(applied):
    inst, src, home, fake = applied
    a = parse(["hotfix", "revert", "--install", str(inst.parent), "--no-restart"])
    assert hf.cmd_hotfix(a, None, home) == 0
    assert (inst / "a.py").read_text() == "A=1\n" and not (inst / "new.py").exists()


def test_recover_dry_run_both_positions_touch_nothing(applied):
    inst, src, home, fake = applied
    before = snapshot(inst.parent, home)
    for argv in (["--dry-run"], ["--latest", "--dry-run"], ["--dry-run", "--latest"]):
        assert rec.main(argv + ["--home", str(home), "--install", str(inst)]) == 0
    assert rec.main(["--list", "--dry-run", "--home", str(home), "--install", str(inst)]) == 0
    assert snapshot(inst.parent, home) == before
