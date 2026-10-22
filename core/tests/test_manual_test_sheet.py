"""tools/manual_test_sheet.py: changelog parsing, row selection, ordering, the committed 4.0.0 sheet (no network)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("manual_test_sheet", ROOT / "tools" / "manual_test_sheet.py")
mts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mts)

CL = """# Changelog

## 5.1 (unreleased)

- Added: Telegram polling; faster boot.
- Changed: dialing asks twice.

## 5.0.0

- Added: one thing.
"""

TOML = """
[[row]]
order = 20
group = "B"
do = "second {version}"
expect = "ok | pipe"

[[row]]
order = 10
group = "A"
do = "first"
expect = "ok"

[[row]]
order = 30
group = "B"
do = "tele"
expect = "x"
optional = true
match = ["telegram"]

[[row]]
order = 40
group = "B"
do = "rocket"
expect = "x"
match = ["rocket"]
"""


def test_section_version_matching():
    assert "Telegram" in mts.changelog_section(CL, "5.1.0")
    assert mts.changelog_section(CL, "5.0").strip() == "- Added: one thing."
    assert mts.changelog_section(CL, "9.9") == ""


def test_items_split():
    items = mts.changelog_items(mts.changelog_section(CL, "5.1"))
    assert items == ["Telegram polling", "faster boot", "dialing asks twice"]


def test_build_orders_selects_and_flags(tmp_path):
    (tmp_path / "c.md").write_text(CL, encoding="utf-8")
    (tmp_path / "s.toml").write_text(TOML, encoding="utf-8")
    text = mts.build("5.1.0", tmp_path / "c.md", tmp_path / "s.toml")
    lines = [ln for ln in text.splitlines() if ln.startswith("| M")]
    assert [ln.split("|")[2].strip() for ln in lines] == ["first", "second 5.1.0", "tele"]   # rocket not in changelog
    assert "M03 (optional)" in lines[2]
    assert "ok \\| pipe" in lines[1]
    assert all("[ ] pass [ ] fail [ ] blocked" in ln for ln in lines)
    assert "Changelog items without a scripted row" in text and "- faster boot" in text
    assert "- Telegram polling" not in text


def test_missing_version_and_duplicate_order(tmp_path):
    (tmp_path / "c.md").write_text(CL, encoding="utf-8")
    (tmp_path / "s.toml").write_text(TOML, encoding="utf-8")
    assert mts.main(["7.7", "--changelog", str(tmp_path / "c.md"), "--standing", str(tmp_path / "s.toml"),
                     "--out", str(tmp_path / "o.md")]) == 2
    (tmp_path / "d.toml").write_text(TOML + '\n[[row]]\norder = 10\ngroup="A"\ndo="x"\nexpect="y"\n', encoding="utf-8")
    try:
        mts.load_rows(tmp_path / "d.toml")
    except ValueError as e:
        assert "duplicate" in str(e)
    else:
        raise AssertionError("duplicate order accepted")


def test_cli_writes_file(tmp_path):
    (tmp_path / "c.md").write_text(CL, encoding="utf-8")
    (tmp_path / "s.toml").write_text(TOML, encoding="utf-8")
    out = tmp_path / "sub" / "o.md"
    assert mts.main(["5.1", "--changelog", str(tmp_path / "c.md"), "--standing", str(tmp_path / "s.toml"),
                     "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# Manual test sheet: JARVES 5.1")


def test_real_standing_rows_and_committed_sheet():
    rows = mts.load_rows(ROOT / "tools" / "manual_test_standing.toml")
    assert all(r["do"].strip() and r["expect"].strip() for r in rows)
    text = mts.build("4.0.0", ROOT / "CHANGELOG.md", ROOT / "tools" / "manual_test_standing.toml")
    n = sum(1 for ln in text.splitlines() if ln.startswith("| M"))
    assert 25 <= n <= 35
    assert "optional" in text
    committed = ROOT / "docs" / "reviews" / "MANUAL_TEST_4.0.0.md"
    assert committed.read_text(encoding="utf-8").startswith("# Manual test sheet: JARVES 4.0.0")


def test_owner_check_script_is_read_only():
    import re
    src = (ROOT / "packaging" / "owner_upgrade_check.ps1").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    bad = re.findall(r"\b(?:Set|New|Remove|Start|Stop|Install|Out|Add|Copy|Move|Invoke)-(?!RestMethod|StrictMode)\w+", code)
    assert not bad, f"the owner check must only read: {bad}"
    assert "-Method Get" in code and "latest.json" in code and "https://" in code
    assert "jarvis update" not in code and "--rollback" not in code
