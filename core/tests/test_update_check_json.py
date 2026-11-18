"""`jarvis update check/status --json` (JAR-456 part a): one machine-readable line, exit 1 on a failed check."""
from __future__ import annotations

import json
import sys

from jarves import cli, features, updates


def _line(capsys):
    return json.loads(capsys.readouterr().out.strip())


def test_update_status_json_reads_the_cache_and_never_checks(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")
    checks = []
    monkeypatch.setattr(updates.UpdateChecker, "check", lambda self, force=False: checks.append(force))
    for st, code in (({"available": False, "error": None}, 0), ({"available": False, "error": "offline"}, 1),
                     ({"available": True, "error": None, "latest": {"version": "9.9"}}, 0)):
        monkeypatch.setattr(updates.UpdateChecker, "status", lambda self, st=st: {"checked_at": "now", **st})
        assert cli.main(["update", "status", "--json"]) == code
        line = _line(capsys)
        assert (line["available"], line["error"]) == (st["available"], st["error"])
        assert line["latest"] == (st.get("latest") or {}).get("version")
    assert checks == []  # status --json never runs the network check


def test_update_check_json_goes_through_the_real_check_branch(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(features.Features, "enabled", lambda self, name: True)
    for st, code in (({"available": False, "error": "network unreachable"}, 1), ({"available": False, "error": None}, 0)):
        monkeypatch.setattr(updates.UpdateChecker, "check", lambda self, force=False, st=st: {"checked_at": "now", **st})
        assert cli.main(["update", "check", "--json"]) == code
        assert (_line(capsys)["error"]) == st["error"]


def test_json_is_rejected_for_other_update_actions(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")
    for action in (["install"], ["auto", "status"], ["check", "--rollback"]):
        assert cli.main(["update", *action, "--json"]) == 2
        assert "--json" in capsys.readouterr().out
