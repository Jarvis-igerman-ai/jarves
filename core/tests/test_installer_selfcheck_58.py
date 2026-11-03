"""#58: the installer offers a first-run self-check on the finish page, never in silent installs, with EN+HE text."""
from pathlib import Path

ISS = (Path(__file__).resolve().parents[2] / "packaging" / "windows" / "jarves.iss").read_text(encoding="utf-8")


def test_selfcheck_run_entry_is_interactive_only():
    rows = [ln for ln in ISS.splitlines() if "selftest" in ln and ln.startswith("Filename:")]
    assert len(rows) == 1
    row = rows[0]
    assert "postinstall" in row and "skipifsilent" in row
    assert "{cm:SelfCheck}" in row
    assert "Check: not IsAdmin;" in row  # never elevated: selftest would write root-owned data


def test_selfcheck_text_in_both_languages():
    assert "english.SelfCheck=" in ISS
    assert "hebrew.SelfCheck=" in ISS
