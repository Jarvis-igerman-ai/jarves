"""Self-awareness (#61): jarvis about / whatsnew / roadmap / ticket reuse release_notes and issue_registry."""
from jarves import cli, issue_registry as ir, release_notes, self_aware

ROADMAP = """# R

## Current milestone classification

| Milestone | Development status | Validation |
| --- | --- | --- |
| **V4** | **CLOSED** see [plan](x.md). | more |
| **V5** | Partial. | more |

## Later
| A | B |
| --- | --- |
| x | y |
"""
NOTES = "# C\n\n## 9.9.9 (test)\n\n- Added (x): one thing\n- Fixed: another\n\n## 9.9.8\n\n- Fixed: old\n"


def test_roadmap_lines_plain_and_short():
    ok, lines = self_aware.roadmap_lines(ROADMAP)
    assert ok and lines[1] == "- V4: CLOSED see plan." and lines[2] == "- V5: Partial."
    assert not any("x:" in x for x in lines) and not self_aware.roadmap_lines("no table")[0]


def test_whatsnew(monkeypatch, capsys):
    monkeypatch.setattr(release_notes, "find_changelog", lambda: NOTES)
    ok, lines = self_aware.whatsnew_lines("9.9.9")
    assert ok and lines[0] == "Version 9.9.9 (test)" and "- Added (x): one thing" in lines
    assert not self_aware.whatsnew_lines("1.2.3")[0]


def test_about_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    assert cli.main(["about"]) == 0
    out = capsys.readouterr().out
    assert "JARVES" in out and "Data:" in out and "Install:" in out


def test_ticket_previews_and_never_sends(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    sent = []
    monkeypatch.setattr(ir, "_http", lambda *a, **k: sent.append(a) or (201, {}))
    assert cli.main(["ticket", "show", "the", "battery", "level"]) == 0
    out = capsys.readouterr().out
    assert "NOTHING is sent" in out and "show the battery level" in out and not sent
    rows = [r for r in ir.Registry(tmp_path).list(None) if r["area"] == "ticket"]
    assert len(rows) == 1
    assert cli.main(["ticket", "list"]) == 0 and rows[0]["id"] in capsys.readouterr().out
    assert cli.main(["ticket", "see", "C:/Users/me/secret.txt"]) == 2          # a path is refused
    assert len([r for r in ir.Registry(tmp_path).list(None) if r["area"] == "ticket"]) == 1


def test_ticket_send_needs_approval(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    monkeypatch.setattr(ir, "find_token", lambda: "")
    monkeypatch.setattr(ir, "_http", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    r = self_aware.record_ticket(tmp_path, "faster startup please")
    assert cli.main(["ticket", "send", r["id"]]) == 1                             # not a tty: asks for --yes, sends nothing
    assert "approval needed" in capsys.readouterr().out
    assert cli.main(["ticket", "send", r["id"], "--yes"]) == 0                    # approved, no token: saved to a file
    assert "saved" in capsys.readouterr().out
