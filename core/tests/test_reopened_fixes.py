"""JAR-268: the five reopened tickets (JAR-51/188/191/217/220) each had a fix that a later edit could undo silently.

Root cause (docs/reports/2026-11-12-reopened-tickets.md): every ticket was closed at PR merge, before the check it
named had run on main (nightly, Windows shard, phone, CI). These source pins keep each merged fix in place, so a
regression shows up in the ordinary PR run instead of as another reopened ticket. Hermetic: they only read files.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_jar51_every_nightly_playwright_install_includes_ffmpeg():
    # The nightly real-recording test needs Playwright's ffmpeg; nightly.yml lost it while jarves-checks.yml had it.
    installs = [ln for ln in _read(".github/workflows/nightly.yml").splitlines() if "playwright install" in ln]
    assert installs, "nightly.yml has no Playwright install step"
    for ln in installs:
        for cmd in re.findall(r"playwright install[^;&|]*", ln):
            assert "ffmpeg" in cmd, f"nightly Playwright install without ffmpeg: {cmd.strip()}"


def test_jar188_reminder_completes_only_after_the_notification_is_posted():
    src = _read("android/app/src/main/kotlin/ai/jarves/companion/Alarms.kt")
    receiver = src[src.index("class AlarmReceiver"):]
    receiver = receiver[:receiver.index("\n}\n") if "\n}\n" in receiver else len(receiver)]
    assert "store.complete(" not in receiver, "AlarmReceiver marks a reminder done before it is shown"
    assert receiver.count("fireReminder(") >= 2, "both the alarm and the boot catch-up path must use fireReminder"
    assert re.search(r"fun fireReminder\([^)]*\)[^=]*=\s*\n?\s*show\(item\)\.also \{ if \(it\) store\.complete", src)


def test_jar191_phone_fit_test_waits_for_the_type_box_before_asserting():
    src = _read("core/tests/test_mobile_browser_e2e.py")
    body = src[src.index("def test_browser_tab_fits_390_and_360_px_in_english_and_hebrew"):]
    wait = body.find('wait_for_selector("#bvType", state="visible"')
    check = body.find('page.is_visible("#bvType")')
    assert wait != -1 and check != -1 and wait < check, "the in-control step must wait for #bvType (#1049)"


def test_jar217_fake_review_counter_is_append_only():
    src = _read("core/tests/test_projectmgr.py")
    bump = src[src.index("def bump(name):"):src.index("def say(text):")]
    assert '"a"' in bump and "int(" not in bump, "the fake's review counter went back to read-modify-write (#1051)"
    assert "def test_fake_review_counter_survives_a_concurrent_reviewer" in src


def test_jar220_web_search_switch_on_and_off_stay_tested():
    src = _read("core/tests/test_web_search.py")
    assert src.count("def test_") >= 6, "the chat web-search tests (switch on, off, Hebrew, sources) were removed"
    assert "web_search" in src and "Sources:" in src


# JAR-276: a quarantined test runs in the non-blocking job, so a regression of these fixes would pass PR CI and come
# back only as another reopened ticket. The tests that verify the five fixes must stay in the blocking run.
REOPENED_PROOF_TESTS = (
    "test_mobile_browser_e2e.py::test_browser_tab_fits_390_and_360_px_in_english_and_hebrew",   # JAR-191 (#1049)
    "test_projectmgr.py::test_parallel_tasks_merge_and_dependent_gets_handoff",                 # JAR-217 (#1051)
    "test_projectmgr.py::test_fake_review_counter_survives_a_concurrent_reviewer",              # JAR-217
)


def reopened_proofs_in_quarantine(ledger: Path) -> list[str]:
    from conftest import quarantined_ids
    q = quarantined_ids(ledger)
    return [t for t in REOPENED_PROOF_TESTS + tuple(f"test_reopened_fixes.py::{n}" for n in _PINS) if t in q]


_PINS = [n for n in dir() if n.startswith("test_jar")]


def test_jar276_reopened_ticket_proofs_are_not_quarantined():
    from conftest import QUARANTINE_LEDGER
    found = reopened_proofs_in_quarantine(QUARANTINE_LEDGER)
    assert not found, f"a reopened ticket's proof is quarantined (non-blocking): {found}"


def test_jar276_guard_catches_the_pre_fix_ledger(tmp_path):
    # The ledger as it was before JAR-345 lifted #1049: the guard must name the JAR-191 test.
    ledger = tmp_path / "quarantine.toml"
    ledger.write_text('[[test]]\nid = "%s"\nissue = 1049\n' % REOPENED_PROOF_TESTS[0], encoding="utf-8")
    assert reopened_proofs_in_quarantine(ledger) == [REOPENED_PROOF_TESTS[0]]
