"""#1029: offline reminders must not change the installed app before part 2 lands, and must not show text on a lock screen.

Hermetic source pins (the Android unit tests need Gradle; these run in the ordinary Python suite). UNVERIFIED on a device.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALARMS = ROOT / "android/app/src/main/kotlin/ai/jarves/companion/Alarms.kt"
MANIFEST = ROOT / "android/app/src/main/AndroidManifest.xml"


def _code(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)        # comments may name the permissions; only real entries count


def test_1029_no_boot_receiver_or_alarm_permissions_until_part_two():
    manifest = _code(MANIFEST)
    for gone in ("RECEIVE_BOOT_COMPLETED", "SCHEDULE_EXACT_ALARM", "USE_EXACT_ALARM", "BOOT_COMPLETED", "AlarmReceiver"):
        assert gone not in manifest, f"{gone} is registered: the app would start at boot with no off switch (#1029)"


def test_1029_missed_reminders_are_shown_after_boot_not_dropped():
    src = ALARMS.read_text(encoding="utf-8")
    boot = src[src.index("Intent.ACTION_BOOT_COMPLETED"):]
    boot = boot[:boot.index("}")]
    assert re.search(r"for \(item in store\.rescheduleAll\(\)\) fireReminder\(", boot), "boot handler drops missed reminders"


def test_1029_reminder_text_is_hidden_on_the_lock_screen():
    src = ALARMS.read_text(encoding="utf-8")
    show = src[src.index("private fun show(context: Context, item: Item)"):]
    assert ".setVisibility(NotificationCompat.VISIBILITY_PRIVATE)" in show, "reminder text shows on a locked phone (#1029)"
    public = re.search(r"\.setPublicVersion\((.*?)\.build\(\)\s*,?\s*\)", show, flags=re.S)
    assert public, "a private reminder needs a public version for the lock screen"
    assert "item.text" not in public.group(1) and "setContentText" not in public.group(1), "the lock-screen version leaks the text"
