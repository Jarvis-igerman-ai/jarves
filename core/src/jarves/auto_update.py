"""Auto-update (issue #17): the owner no longer types `jarvis update install` by hand, but NOTHING about the trust
rules of updates.py changes. This module only decides WHEN to run the very same signed path:

    UpdateChecker.check()  ->  prepare_install(approved=True)  ->  launch_installer(silent=True, restart=True)

so the signed manifest (keys built into the app), SHA256 + size, the second hash right before start, "installed
builds ignore env/config overrides", "only newer", the owner's chosen channel and the backup + rollback all apply
unchanged. There is no new download source and no new trust. On top of that it is stricter: it refuses any update that
is not from the signed manifest, even in a source checkout.

Switch `auto_update`. Default (owner decision): it FOLLOWS THE CHANNEL: ON for beta-channel users, OFF for stable-channel
users, as long as the owner never set it. An explicit on/off always wins and is never overwritten by a channel change
or an upgrade (features.py default(); the explicit value lives in features.json). Switching channel never installs
anything and a downgrade is never automatic. The channel itself is chosen on the first-run prompt (Updates page /
notice) or any time with `jarvis update --channel`. When ON, per newer version:
  1. a plain EN+HE notice BEFORE ("update now / later / turn off"), then a grace time (NOTICE_GRACE_S);
  2. install only at a quiet moment: no voice/chat session, nobody listening, no pending approval, no running coding
     agent, not in quiet hours / paused alerts (the Core passes `busy()`); "update now" is the owner's explicit choice;
  3. after the restart a notice AFTER (what changed, how to roll back).
Failure: an update that fails (download, hash, backup, setup never ran) changes nothing and the old version keeps
working; retries back off (1 h, 4 h, 12 h), and after GIVE_UP_AFTER failures for one version it stops and tells the
owner once. A newer version, or "update now", starts again. A crash-looping new version is handled by the existing
watch (updates.note_start -> approval -> `jarvis update --rollback`); it is never rolled back without the owner's yes,
because a rollback restores the data from before the update.

Privacy: no personal data leaves the PC (the check is the existing one); logs hold codes and versions only.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Callable

from . import logs, updates

FEATURE = "auto_update"
STATE_FILE = "auto_update.json"          # in <home>/updates/ (the updater's own folder, never user data)
NOTICE_GRACE_S = 15 * 60                 # between the "before" notice and the earliest automatic install
LATER_S = 24 * 3600                      # "later" = ask again after a day
BACKOFF_S = (3600, 4 * 3600, 12 * 3600)  # wait after failure 1, 2, 3
GIVE_UP_AFTER = 3                        # failures per version, then stop and tell the owner once
TICK_S = 600                             # how often the Core looks (the update check itself is cached for a day)

CHOICES = ("now", "later", "off")

PLATFORMS = {
    "windows": {"status": "implemented_unverified",
                "evidence": "core/tests/test_auto_update.py (fake signed release server, fake installer: on/off, quiet "
                            "moment, notices, signed only, older refused, failure keeps the old version, back-off, "
                            "channel); never run against a real installer or a published release"},
    "linux_arm64_pi": {"status": "planned", "blocker": "there is no Pi package to install",
                       "roadmap_issue": updates.PI_ISSUE}}

CODES = {   # plain words for the owner; the same codes are what logs and `jarvis update auto status` show
    "off": ("העדכון האוטומטי כבוי", "Auto-update is off"),
    "not_applicable": ("אין מתקין לפלטפורמה הזו עדיין", "There is no installer for this platform yet"),
    "no_update": ("אין גרסה חדשה", "No newer version"),
    "check_failed": ("בדיקת העדכון לא זמינה כרגע", "The update check is unavailable right now"),
    "unsigned_refused": ("עדכון בלי חתימה תקינה לא מותקן", "An update without a valid signature is not installed"),
    "announced": ("הודעתי על עדכון; מתקין בהמשך ברגע שקט", "Announced an update; installing later at a quiet moment"),
    "waiting_grace": ("ממתין לפני התקנה", "Waiting before installing"),
    "snoozed": ("דחית את העדכון", "You postponed the update"),
    "busy": ("לא מתקין עכשיו: אתה משתמש בג'רביס", "Not installing now: JARVES is in use"),
    "backoff": ("הניסיון הקודם נכשל; ממתין לפני ניסיון נוסף", "The last try failed; waiting before trying again"),
    "gave_up": ("הפסקתי לנסות את הגרסה הזו", "Stopped trying this version"),
    "installing": ("ההתקנה התחילה", "Installation started"),
    "failed": ("העדכון נכשל; הגרסה הנוכחית ממשיכה לעבוד", "The update failed; the current version keeps working"),
}


def state_path(home: Path) -> Path:
    return Path(home) / "updates" / STATE_FILE


def _load(home: Path) -> dict:
    return updates._read_json(state_path(home))


def _save(home: Path, d: dict) -> None:
    updates._write_json(state_path(home), d)


def mark_channel_chosen(home: Path) -> None:
    """The owner picked a channel (first-run prompt, dashboard, `jarvis update --channel`): the prompt is not shown again."""
    st = _load(home)
    if not st.get("channel_chosen"):
        st["channel_chosen"] = True
        _save(home, st)


def choose_channel(cfg, home: Path, channel: str, auto: bool | None = None) -> dict:
    """The first-run choice (EN+HE prompt): channel + the 'automatic updates' checkbox (default ON when beta is
    picked, OFF when stable). Nothing is installed. The checkbox is stored as an explicit switch ONLY when it differs
    from the channel default, so otherwise the default keeps following the channel. Never called by an upgrade or a
    silent install: only by the owner's click / command."""
    from . import features
    updates.set_channel(home, cfg, channel)                        # also marks the choice as made
    if auto is not None and auto != (channel == "beta"):
        features.Features(cfg, home).set(FEATURE, bool(auto), by="user")
    mark_channel_chosen(home)
    return {"channel": channel, "auto_update": is_enabled(cfg, home)}


def is_enabled(cfg, home: Path) -> bool:
    """The auto_update switch AND the update check (auto-update is the check plus an install)."""
    from . import features
    try:
        f = features.Features(cfg, home)
        return f.enabled(FEATURE) and f.enabled("update_check")
    except Exception:
        return False


class AutoUpdater:
    def __init__(self, home: Path, *, checker: Callable[[], updates.UpdateChecker],
                 enabled: Callable[[], bool] = lambda: True, busy: Callable[[], list[str]] = lambda: [],
                 notify: Callable[[dict], None] = lambda n: None, launch: Callable | None = None,
                 now: Callable[[], float] = time.time, current: Callable[[], str] | None = None,
                 cfg=None) -> None:
        self.home, self.cfg = Path(home), cfg
        self.checker, self.enabled, self.busy, self.notify = checker, enabled, busy, notify
        self.launch = launch or updates.launch_installer
        self.now = now
        self.current = current or (lambda: updates.build_info()["version"])
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ state
    def state(self) -> dict:
        return _load(self.home)

    def _emit(self, st: dict, kind: str, version: str, display: str, **extra) -> None:
        he, en = _notice_text(kind, display, **extra)
        n = {"kind": kind, "version": version, "title_he": he[0], "title_en": en[0], "text_he": he[1],
             "text_en": en[1], "ts": self.now()}
        st["notice"] = n
        try:
            self.notify(n)
        except Exception as e:                        # a notice that cannot be shown never blocks anything
            logs.warn("updates", "AUTO_UPDATE_NOTICE_FAILED", "notice not delivered", error_type=type(e).__name__)
        logs.info("updates", "AUTO_UPDATE_NOTICE", "owner notice", alert_kind=kind, version=version)

    def status(self) -> dict:
        """For `jarvis update auto status` and the dashboard: switch, what it is waiting for, the notice."""
        st, now = self.state(), self.now()
        if self._reconcile(st):
            _save(self.home, st)
        on = bool(self.enabled())
        code = st.get("last") or ("off" if not on else "no_update")
        if not on:
            code = "off"
        return {"enabled": on, "code": code, "message_he": CODES.get(code, CODES["failed"])[0],
                "message_en": CODES.get(code, CODES["failed"])[1], "target": st.get("target"),
                "target_display": st.get("target_display"), "snoozed_until": st.get("snooze_until") or None,
                "next_try": st.get("next_try") or None if (st.get("next_try") or 0) > now else None,
                "failures": int(st.get("fail_count") or 0), "gave_up": st.get("gave_up"), "notice": st.get("notice"),
                "busy": list(st.get("busy") or []), "channel": st.get("channel"),
                "default_from_channel": self._default_note(), "channel_chosen": bool(st.get("channel_chosen")),
                **self._next(st, on, now), "last_result": st.get("last_result"),
                "rollback_possible": bool(updates.rollback_info(self.home)["possible"])}

    def _reconcile(self, st: dict) -> bool:
        """#294: the installed version is (at least) the announced one, e.g. the owner installed it by hand: the
        announcement is done. Clear the pending target and notice, and record 'installed <version> at <time>' unless
        an automatic install already recorded that version. True when the state changed."""
        target = st.get("target")
        if not target or (target != self.current() and (updates.version_tuple(target) is None or updates.is_newer(target, self.current()))):
            return False
        for k in ("target", "target_display", "announced_ts", "snooze_until", "next_try", "now_requested", "busy",
                  "last", "gave_up"):
            st.pop(k, None)
        if (st.get("notice") or {}).get("kind") == "announced":
            st.pop("notice", None)
        r = st.get("last_result") or {}
        if not (r.get("version") == target and r.get("ok")):
            self._result(st, target, True, None)
            st["last_result"]["manual"] = True
        return True

    def _next(self, st: dict, on: bool, now: float) -> dict:
        """When the next automatic step is planned: {next_at: epoch seconds | None, next_kind: what happens then}.
        next_kind: 'check' (look for a newer version), 'install' (install at the first quiet moment from then on),
        'quiet_moment' (due now, waiting only for a quiet moment), or None (off / gave up / nothing planned)."""
        if not on or (st.get("gave_up") and st.get("gave_up") == st.get("target")):
            return {"next_at": None, "next_kind": None}
        if not st.get("target"):
            checked = float(updates._read_json(self.home / updates.STATE_FILE).get("checked_ts") or 0)
            due = checked + updates.CHECK_INTERVAL_S if checked else now
            return {"next_at": max(due, now), "next_kind": "check"}
        if st.get("now_requested"):
            return {"next_at": max(float(st.get("next_try") or 0), now), "next_kind": "install"}
        due = max(float(st.get("announced_ts") or now) + NOTICE_GRACE_S, float(st.get("snooze_until") or 0),
                  float(st.get("next_try") or 0))
        if due <= now:
            return {"next_at": now, "next_kind": "quiet_moment"}
        return {"next_at": due, "next_kind": "install"}

    def _result(self, st: dict, version: str, ok: bool, code: str | None) -> None:
        """The last finished attempt, for status / the Updates page: when, which version, ok or the error code, and
        whether a rollback is possible (a failed attempt changed nothing; a good one can still be rolled back)."""
        st["last_result"] = {"ts": self.now(), "version": version, "ok": ok, "code": code,
                             "rollback_possible": bool(updates.rollback_info(self.home)["possible"])}

    def _default_note(self) -> str | None:
        """'beta' / 'stable' when the value is only the channel default (the owner never set it), else None."""
        try:
            from . import features
            from .config import Config
            f = features.Features(self.cfg if self.cfg is not None else Config(), self.home)
            f.enabled(FEATURE)
            if FEATURE not in f._state["enabled"]:
                return "beta" if f.enabled("beta_channel") else "stable"
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------ the owner's answer to the notice
    def answer(self, choice: str, set_switch: Callable[[bool], None] | None = None) -> dict:
        if choice not in CHOICES:
            raise ValueError(f"choice must be one of {CHOICES}")
        with self._lock:
            st = self.state()
            if choice == "later":
                st["snooze_until"] = self.now() + LATER_S
                st["now_requested"] = False
            elif choice == "now":
                st["now_requested"] = True
                st["snooze_until"] = 0
                st.pop("gave_up", None)               # the owner asks again: the give-up is lifted
                st["fail_count"], st["next_try"] = 0, 0
            else:
                st["now_requested"] = False
                if set_switch is not None:
                    set_switch(False)
            _save(self.home, st)
        logs.info("updates", "AUTO_UPDATE_ANSWER", "owner answered the update notice", mode=choice)
        return self.status()

    # ------------------------------------------------------------------ one look (the Core calls this every TICK_S)
    def _first_run_prompt(self) -> None:
        """One-time EN+HE prompt to choose stable or beta (and automatic updates). It only asks: until the owner
        answers, the channel stays stable and nothing changes; an upgrade or a silent install never answers for him.
        Someone who already chose beta explicitly (features.json) is not asked."""
        st = self.state()
        if st.get("channel_chosen") or st.get("prompted"):
            return
        try:
            from . import features
            from .config import Config
            f = features.Features(self.cfg if self.cfg is not None else Config(), self.home)
            f.enabled("beta_channel")
            if "beta_channel" in f._state["enabled"]:
                st["channel_chosen"] = True
                _save(self.home, st)
                return
        except Exception:
            pass
        st["prompted"] = True
        self._emit(st, "choose", "", "")
        _save(self.home, st)

    def tick(self) -> str:
        with self._lock:
            self._first_run_prompt()
            code = self._tick()
            st = self.state()
            if st.get("last") != code:
                st["last"] = code
                _save(self.home, st)
            return code

    def _tick(self) -> str:
        if not self.enabled():
            return "off"
        if sys.platform != "win32":
            return "not_applicable"
        now = self.now()
        st = self.state()
        self._after_restart(st)
        if self._reconcile(st):
            _save(self.home, st)
        chk = self.checker()
        s = chk.check()                                            # cached for a day; no personal data
        if s.get("error"):
            return "check_failed"
        if not s["available"]:
            st.update(target=None, target_display=None, announced_ts=0, now_requested=False)
            _save(self.home, st)
            return "no_update"
        rel = s["latest"]
        if rel.get("source") != "hosted":
            logs.warn("updates", "AUTO_UPDATE_UNSIGNED", "update has no signed manifest; not installing",
                      version=rel.get("version"))
            return "unsigned_refused"
        ver, disp = rel["version"], rel.get("display") or rel["version"]
        if st.get("target") != ver or st.get("channel") != chk.channel:     # a new version, or the owner switched channel
            st.update(target=ver, target_display=disp, channel=chk.channel, announced_ts=0, snooze_until=0,
                      now_requested=False, fail_count=0, next_try=0)
            st.pop("gave_up", None)
        if st.get("gave_up") == ver:
            _save(self.home, st)
            return "gave_up"
        if not st.get("announced_ts"):
            st["announced_ts"] = now
            self._emit(st, "before", ver, disp, default=self._default_note() == "beta")
            _save(self.home, st)
            return "announced"
        forced = bool(st.get("now_requested"))
        if not forced:
            if float(st.get("snooze_until") or 0) > now:
                _save(self.home, st)
                return "snoozed"
            if now - float(st["announced_ts"]) < NOTICE_GRACE_S:
                _save(self.home, st)
                return "waiting_grace"
            if float(st.get("next_try") or 0) > now:
                _save(self.home, st)
                return "backoff"
            reasons = list(self.busy() or [])
            st["busy"] = reasons
            if reasons:
                _save(self.home, st)
                return "busy"
        elif float(st.get("next_try") or 0) > now:
            return "backoff"
        st["busy"] = []
        return self._install(st, chk, ver, disp)

    # ------------------------------------------------------------------ install (the existing signed path)
    def _install(self, st: dict, chk: updates.UpdateChecker, ver: str, disp: str) -> str:
        st["installing"] = {"from": self.current(), "to": ver, "display": disp, "ts": self.now()}
        st["now_requested"] = False
        _save(self.home, st)
        try:
            r = chk.prepare_install(approved=True)             # re-verifies the signed manifest, SHA256, size; backs up
            if r.get("signed") is not True:
                raise updates.UpdateError("UPDATE_UNSIGNED", "not a signed update")
            self.launch(r["installer"], verified=r, silent=True, restart=True)   # hashes the file again before start
        except updates.UpdateError as e:
            return self._failed(st, ver, e.code)
        except Exception as e:                                 # a crash in the updater must not touch the old version
            return self._failed(st, ver, "UPDATE_ERROR_" + type(e).__name__)
        logs.info("updates", "AUTO_UPDATE_INSTALLING", "auto-update started the signed installer", version=ver)
        return "installing"

    def _failed(self, st: dict, ver: str, code: str) -> str:
        n = int(st.get("fail_count") or 0) + 1
        st.pop("installing", None)
        st["fail_count"] = n
        st["last_error"] = code
        st["next_try"] = self.now() + BACKOFF_S[min(n, len(BACKOFF_S)) - 1]
        self._result(st, ver, False, code)
        logs.warn("updates", "AUTO_UPDATE_FAILED", "auto-update failed; the current version keeps working",
                  version=ver, code_name=code, attempts=n)
        if n >= GIVE_UP_AFTER:
            st["gave_up"] = ver
            self._emit(st, "gave_up", ver, st.get("target_display") or ver, code=code)
        _save(self.home, st)
        return "gave_up" if n >= GIVE_UP_AFTER else "failed"

    # ------------------------------------------------------------------ after the restart (Core start)
    def _after_restart(self, st: dict) -> None:
        inst = st.get("installing")
        if not inst:
            return
        pend = updates._read_json(updates._updates_dir(self.home) / updates.PENDING_FILE)
        cur = self.current()
        if cur == inst.get("to") and pend.get("state") in ("ok", "installing"):
            if pend.get("state") == "ok":
                self._emit(st, "after", inst["to"], inst.get("display") or inst["to"], prev=inst.get("from") or "")
                self._result(st, inst["to"], True, None)
                st.pop("installing", None)
                st["fail_count"], st["next_try"] = 0, 0
                _save(self.home, st)
        elif cur == inst.get("from") and pend.get("state") == "not_applied":
            # setup was cancelled / failed and the old version started again: a failure like any other
            self._failed(st, inst["to"], "UPDATE_NOT_APPLIED")

    def after_start(self) -> str:
        """The Core calls this once it is ready (right after updates.mark_healthy): the 'after' notice, or the failure
        bookkeeping when setup never ran."""
        with self._lock:
            st = self.state()
            self._after_restart(st)
            h = updates.take_health_notice(self.home)
            pend = updates._read_json(updates._updates_dir(self.home) / updates.PENDING_FILE) if h else {}
            if h and h.get("to") and self.current() == h.get("to") and pend.get("state") == "ok":
                # review #260 F6: the version was only slow; it is running now and marked itself healthy. No alarming
                # "did not start" notice and no give-up for the version that works.
                logs.info("updates", "UPDATE_HEALTH_LATE_READY", "the update became ready after the health check gave up",
                          version=h.get("to"), alert_kind=h.get("status"))
                h = None
            if h:                                         # the installer's health check acted on the update (#164)
                st = self.state()
                bad = h.get("to") or ""
                if bad:
                    # never auto-retry a version that did not become ready (each try would leave another
                    # failed-upgrade-health-* folder); the owner's own "Update now" lifts this
                    st.pop("installing", None)
                    st["gave_up"], st["last_error"] = bad, "UPDATE_HEALTH_" + str(h["status"]).upper()
                    # review P3-8: its own last result, never "not installed, nothing changed" (it WAS installed and
                    # may have migrated the data)
                    self._result(st, bad, False, st["last_error"])
                    st["last_result"]["health"] = h["status"]
                self._emit(st, h["status"], bad, bad, prev=h.get("from") or "")
                _save(self.home, st)
            return (self.state().get("notice") or {}).get("kind", "")


def _notice_text(kind: str, display: str, **x) -> tuple[tuple[str, str], tuple[str, str]]:
    """((title_he, text_he), (title_en, text_en)). Plain words; no personal data."""
    if kind == "before":
        he_d = "עדכון אוטומטי פועל כברירת מחדל בערוץ הבטא. " if x.get("default") else ""
        en_d = "Automatic updates are on by default on the beta channel. " if x.get("default") else ""
        return (("יש עדכון לג'רביס", f"{he_d}גרסה {display} מוכנה. אתקין אותה לבד ברגע שקט (לא באמצע שיחה או אישור), "
                 "אחרי גיבוי, ואפשר לחזור אחורה. אפשר: לעדכן עכשיו, אחר כך, או לכבות עדכון אוטומטי "
                 "(בעמוד Updates או jarvis update auto now / later / off)."),
                ("A JARVES update is ready", f"{en_d}Version {display} is ready. I will install it by myself at a quiet moment "
                 "(never in the middle of a conversation or an approval), after a backup, and you can go back. You can: "
                 "update now, later, or turn auto-update off (Updates page, or jarvis update auto now / later / off)."))
    if kind == "choose":
        return (("איזה ערוץ עדכונים?", "יציב: רק גרסאות שנבדקו ואושרו (ברירת המחדל, בלי עדכון אוטומטי). בטא: כל בנייה "
                 "ירוקה מוקדם, עם עדכון אוטומטי כברירת מחדל (אפשר לכבות). שתיהן חתומות, מגובות וניתנות להחזרה. "
                 "לבחור: עמוד Updates או jarvis update --channel stable|beta. עד שתבחר נשאר יציב ושום דבר לא מותקן לבד."),
                ("Which update channel?", "Stable: only tested, signed-off releases (the default, no automatic updates). "
                 "Beta: every green build early, with automatic updates on by default (you can turn them off). Both are "
                 "signed, backed up and can be rolled back. Choose on the Updates page or with jarvis update --channel "
                 "stable|beta. Until you choose, you stay on stable and nothing installs by itself."))
    if kind == "after":
        return (("ג'רביס עודכן", f"עודכן לגרסה {display}. הנתונים שלך נשמרו וגובו לפני העדכון. מה השתנה: "
                 "jarvis release-notes. אם משהו לא עובד: jarvis update --rollback (או כפתור החזרה בעמוד Updates)."),
                ("JARVES was updated", f"Updated to version {display}. Your data was kept and backed up before the update. "
                 "What changed: jarvis release-notes. If something is wrong: jarvis update --rollback (or the Roll back "
                 "button on the Updates page)."))
    if kind == "health_failed":
        return (("העדכון לא עלה; החזרה מלאה מחכה לאישור שלך", f"גרסה {display} לא עלתה כמו שצריך אחרי ההתקנה. הנתונים כבר "
                 "הותאמו לגרסה החדשה, אז לא החזרתי לבד רק את התוכנה (הגרסה הקודמת לא הייתה עולה עליהם). כדי לחזור: "
                 "jarvis update --rollback (או הכפתור Roll back בעמוד Updates); זה מחזיר גם את הגיבוי של הנתונים "
                 "שנעשה לפני העדכון, ורק אחרי אישור שלך."),
                ("The update did not start; a full undo waits for your OK", f"Version {display} did not start properly "
                 "after setup. Your data had already been upgraded for it, so I did not put back the previous program "
                 "alone (it would not start on that data). To go back: jarvis update --rollback (or the Roll back "
                 "button on the Updates page); it also restores the data backup made before the update, and only "
                 "after your OK."))
    if kind in ("rolled_back", "rollback_failed"):
        prev = x.get("prev") or ""
        if kind == "rolled_back":
            return (("העדכון בוטל והגרסה הקודמת חזרה", f"גרסה {display} לא עלתה כמו שצריך אחרי ההתקנה, אז החזרתי את התוכנה "
                     f"הקודמת{' ' + prev if prev else ''}. הנתונים שלך לא נגעו ושמורים. אפשר לנסות שוב מאוחר יותר "
                     "בעמוד Updates; אם זה חוזר, דווח עם jarvis report."),
                    ("The update was undone, the previous version is back", f"Version {display} did not start properly "
                     f"after setup, so I put the previous program back{' (' + prev + ')' if prev else ''}. Your data was "
                     "not touched and is safe. You can try again later on the Updates page; if it repeats, report it "
                     "with jarvis report."))
        return (("העדכון לא עלה וההחזרה האוטומטית לא הצליחה", f"גרסה {display} לא עלתה, ולא הצלחתי להחזיר את התוכנה "
                 "הקודמת לבד. הנתונים שלך לא נגעו. הרץ jarvis update --rollback או התקן מחדש את הגרסה הקודמת."),
                ("The update failed and the automatic undo did not work", f"Version {display} did not start and I could "
                 "not put the previous program back by myself. Your data was not touched. Run jarvis update --rollback "
                 "or reinstall the previous version."))
    return (("העדכון האוטומטי הפסיק לנסות", f"לא הצלחתי להתקין את גרסה {display} ({x.get('code', '')}) אחרי כמה ניסיונות. "
             "הגרסה הנוכחית ממשיכה לעבוד ושום דבר לא השתנה. אפשר ללחוץ עכשיו לעדכן ידנית בעמוד Updates."),
            ("Auto-update stopped trying", f"I could not install version {display} ({x.get('code', '')}) after several "
             "tries. The current version keeps working and nothing changed. You can update by hand on the Updates page."))


NEXT_TEXT = {   # next_kind -> (he, en); {t} is the local time
    "check": ("הבדיקה הבאה לעדכון: {t}", "Next update check: {t}"),
    "install": ("התקנה מתוכננת: לא לפני {t}, ברגע שקט", "Planned install: not before {t}, at a quiet moment"),
    "quiet_moment": ("ההתקנה ממתינה לרגע שקט (לא באמצע שיחה)", "The install is waiting for a quiet moment (never in a conversation)"),
}


HEALTH_RESULT = {   # last_result["health"] -> (he, en): setup's health check acted after the install (#164)
    "rolled_back": ("תוצאה אחרונה: גרסה {v} הותקנה אבל לא עלתה ({t}), אז התוכנה הקודמת הוחזרה. הנתונים לא נוגעו.",
                    "Last result: {v} was installed but did not start ({t}), so the previous program was put back. "
                    "Your data was not touched."),
    "rollback_failed": ("תוצאה אחרונה: גרסה {v} לא עלתה ({t}) וההחזרה האוטומטית לא הצליחה. הרץ jarvis update --rollback.",
                        "Last result: {v} did not start ({t}) and the automatic undo did not work. Run: "
                        "jarvis update --rollback."),
    "health_failed": ("תוצאה אחרונה: גרסה {v} לא עלתה ({t}). הנתונים כבר הותאמו לה, אז חזרה מלאה (נתונים ותוכנה) "
                      "מחכה לאישור שלך: jarvis update --rollback.",
                      "Last result: {v} did not start ({t}). Your data had already been upgraded for it, so a full "
                      "undo (data and program) waits for your OK: jarvis update --rollback."),
}


def _local(ts: float) -> str:
    return time.strftime("%d %b %H:%M", time.localtime(ts))


def status_lines(s: dict) -> list[str]:
    """The next planned step and the last result, in plain words (CLI `jarvis update auto status`)."""
    from . import clilang
    out: list[str] = []
    kind = s.get("next_kind")
    if kind in NEXT_TEXT and s.get("next_at"):
        he, en = NEXT_TEXT[kind]
        out.append(clilang.fmt(he.format(t=_local(s["next_at"])), en.format(t=_local(s["next_at"]))))
    r = s.get("last_result") or {}
    if r.get("version"):
        t = _local(float(r.get("ts") or 0))
        if r.get("ok"):
            out.append(clilang.fmt(f"תוצאה אחרונה: עודכן לגרסה {r['version']} ({t}). אפשר לחזור: jarvis update --rollback",
                                   f"Last result: updated to {r['version']} ({t}). To go back: jarvis update --rollback"))
        elif r.get("health") in HEALTH_RESULT:
            he, en = HEALTH_RESULT[r["health"]]
            out.append(clilang.fmt(he.format(v=r["version"], t=t), en.format(v=r["version"], t=t)))
        else:
            out.append(clilang.fmt(f"תוצאה אחרונה: גרסה {r['version']} לא הותקנה ({r.get('code') or ''}, {t}). "
                                   "שום דבר לא השתנה והגרסה הנוכחית עובדת.",
                                   f"Last result: {r['version']} was not installed ({r.get('code') or ''}, {t}). "
                                   "Nothing changed and the current version works."))
    return out


# ---------------------------------------------------------------------- the Core driver
def core_busy(core) -> list[str]:
    """Reasons NOT to install now (empty = a quiet moment). Reads the Core's live state; never blocks."""
    out: list[str] = []
    if getattr(core, "live", None) is not None:
        out.append("voice_session")
    if getattr(core, "listening", False):
        out.append("listening")
    if getattr(core, "stopping", False):
        out.append("stopping")
    try:
        if core.approvals.has_pending():
            out.append("pending_approval")
    except Exception:
        pass
    try:
        if getattr(core.chat, "turns", None):
            out.append("chat_turn")
    except Exception:
        pass
    try:
        from .orchestrator.envelope import LIVE_STATES
        busy_states = {"RUNNING", "WAITING_INPUT", "WAITING_APPROVAL"} & set(LIVE_STATES)
        o = getattr(core, "orchestrator", None)
        db = getattr(o, "db", None)
        if db is not None and any(s["state"] in busy_states for s in db.sessions(live_only=True)):
            out.append("coding_agent")
    except Exception:
        pass
    try:
        eng = core.ops.alerts
        if eng.quiet_now() or eng.paused():
            out.append("quiet_hours")
    except Exception:
        pass
    return out


def core_notify(core) -> Callable[[dict], None]:
    def notify(n: dict) -> None:
        core.ops.alerts.raise_("updates", f"{n['title_he']} / {n['title_en']}", f"{n['text_he']}\n\n{n['text_en']}",
                               priority="normal", kind="info", dedupe_key=f"auto_update:{n['kind']}:{n['version']}")
    return notify


def for_core(core) -> AutoUpdater:
    return AutoUpdater(core.home, cfg=core.cfg, checker=lambda: updates.UpdateChecker(core.home, cfg=core.cfg),
                       enabled=lambda: is_enabled(core.cfg, core.home), busy=lambda: core_busy(core),
                       notify=core_notify(core))


def start(core, *, first_delay_s: float = 300.0, every_s: float = TICK_S) -> threading.Thread:
    """A daemon thread next to the backup thread. The first look comes after the Core settled."""
    stop = threading.Event()
    au = for_core(core)

    def run() -> None:
        if stop.wait(first_delay_s):
            return
        while True:
            try:
                au.tick()
            except Exception as e:                          # never stops the Core; the next tick tries again
                logs.warn("updates", "AUTO_UPDATE_TICK_FAILED", "auto-update look failed", error_type=type(e).__name__)
            if stop.wait(every_s):
                return
    t = threading.Thread(target=run, name="jarves-auto-update", daemon=True)
    t.stop = stop                                           # type: ignore[attr-defined]
    t.start()
    return t


# ---------------------------------------------------------------------- feature switch + dashboard
def _register_feature() -> None:
    from . import features
    if FEATURE in features.CATALOG:
        return
    features.register(FEATURE, "עדכון אוטומטי", "Automatic updates", False, risk="medium",
                      needs=["update_check"], targets=PLATFORMS,
                      about="installs a newer signed JARVES by itself, only at a quiet moment (never in a conversation, "
                            "an approval or a coding-agent task), after a notice and a backup; same signed path as "
                            "`jarvis update install`; stable users get stable, beta users get beta; one command goes "
                            "back to the previous version")


_register_feature()


def _au(ctx) -> AutoUpdater:
    return AutoUpdater(ctx.home, cfg=getattr(ctx, "cfg", None),
                       checker=lambda: updates.UpdateChecker(ctx.home, cfg=getattr(ctx, "cfg", None)),
                       enabled=lambda: is_enabled(getattr(ctx, "cfg", None), ctx.home))


def set_switch(cfg, home: Path, on: bool, by: str) -> dict:
    """The one place that turns it on/off (CLI and dashboard). Turning it on also needs the update check."""
    from . import features
    f = features.Features(cfg, home)
    if on and not f.enabled("update_check"):
        raise updates.UpdateError("AUTO_UPDATE_NEEDS_CHECK", "turn the update check on first: jarvis features on update_check")
    try:
        f.set(FEATURE, on, by=by)
    except features.BetaLocked as e:                      # one friendly message, no traceback (#469)
        raise updates.UpdateError(e.code, f"{features.BETA_MSG_HE} / {features.BETA_MSG}") from e
    return {"enabled": on}


def api_auto(ctx, m, body):
    return 200, _au(ctx).status()


def api_auto_set(ctx, m, body):
    """POST {"enabled": bool}."""
    on = (body or {}).get("enabled")
    if not isinstance(on, bool):
        return 409, {"error": "send {\"enabled\": true|false}", "code": "BAD_REQUEST"}
    try:
        set_switch(getattr(ctx, "cfg", None), ctx.home, on, "dashboard")
    except updates.UpdateError as e:
        return 409, {"error": e.message, "code": e.code}
    return 200, _au(ctx).status()


def api_auto_channel(ctx, m, body):
    """POST {"channel": "stable"|"beta", "auto": bool|null}: the one-time first-run choice."""
    b = body or {}
    auto = b.get("auto")
    if auto is not None and not isinstance(auto, bool):
        return 409, {"error": "auto must be true or false", "code": "BAD_REQUEST"}
    try:
        r = choose_channel(getattr(ctx, "cfg", None), ctx.home, str(b.get("channel") or ""), auto)
    except updates.UpdateError as e:
        return 409, {"error": e.message, "code": e.code}
    return 200, {**_au(ctx).status(), **r}


def api_auto_answer(ctx, m, body):
    """POST {"choice": "now"|"later"|"off"}: the answer to the notice. 'now' only lifts the waiting; the install still
    runs the signed path at the next look (within TICK_S)."""
    choice = str((body or {}).get("choice") or "")
    if choice not in CHOICES:
        return 409, {"error": "choice must be now, later or off", "code": "BAD_REQUEST"}
    return 200, _au(ctx).answer(choice, set_switch=lambda on: set_switch(getattr(ctx, "cfg", None), ctx.home, on,
                                                                          "dashboard"))


def dashboard_routes(register_route) -> None:
    register_route("GET", r"/api/updates/auto", api_auto)
    register_route("POST", r"/api/updates/auto", api_auto_set)
    register_route("POST", r"/api/updates/auto/answer", api_auto_answer)
    register_route("POST", r"/api/updates/auto/channel", api_auto_channel)
