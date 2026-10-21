"""SW-V4 H (#17) assisted dialing: contact resolution (Hebrew / English / ל prefix, ambiguity, several numbers, Google
Contacts), E.164 and the digit read-back, the taint rule (a number from e-mail / web / message / recalled text is never
dialled; a spoken number must really have been said), emergency numbers, `tel:` handler detection with a FAKE registry,
approvals (recipient, number, source, app), deny and a real timeout, the rate limit, hashes-only audit, the Core tools,
`jarvis dial`, the dashboard route and the catalog wiring.

Nothing here places a call or opens a window: the opener is a recording fake (the conftest guard `_no_real_browser`
refuses the real ones anyway), the registry is a dict, the contacts are in memory."""
from __future__ import annotations

import asyncio
import json
import re

import pytest

from conftest import until
from jarves import approvals, dialing
from jarves import features as feat
from jarves.config import Config
from jarves.features import Features
from jarves.skills import Skill, SkillRegistry, Tool, ToolResult
from jarves.skills.local_contacts import ContactArgs
from test_dashboard import dash, req  # noqa: F401  (fixture + helper)

DANA = "+972541234567"
DANA_DIGITS = "541234567"


class FakeReg:
    """The handful of registry reads detect_handler makes; no winreg."""

    def __init__(self, progid=None, command=None, tel_command=None):
        self.progid, self.command, self.tel_command = progid, command, tel_command

    def get(self, root, path, value=""):
        if root == "HKCU" and path.endswith("UserChoice") and value == "ProgId":
            return self.progid
        if root == "HKCR" and self.progid and path == f"{self.progid}\\shell\\open\\command":
            return self.command
        if root == "HKCR" and path == "tel\\shell\\open\\command":
            return self.tel_command
        return None


PHONE_LINK = FakeReg("PhoneExperienceHost.tel", '"C:\\Program Files\\WindowsApps\\Microsoft.YourPhone_1\\PhoneExperienceHost.exe" "%1"')


def row(label="", name="", phone="", phone_label="", source="user"):
    d = {"label": label, "email": "x@example.com", "name": name, "phone": phone}
    if phone_label:
        d["phone_label"] = phone_label
    return {"text": json.dumps({k: v for k, v in d.items() if v}, ensure_ascii=False), "source": source}


class Rig:
    def __init__(self, home, rows=(), book=None, google=None, registry=PHONE_LINK, said=None, paired="data_found", **kw):
        self.opened: list[str] = []
        self.said = list(said or [])
        self.asked: list[dict] = []
        self.answer = True
        self.t = 1000.0
        if book:
            from jarves.channels_common import write_private_json
            from jarves.whatsapp import contacts_file
            write_private_json(contacts_file(home), book)
        self.dialer = dialing.Dialer(home, contacts=lambda: list(rows), google=google, registry=registry,
                                     phone_link=lambda: paired, opener=self._open, clock=lambda: self.t,
                                     wall=lambda: self.t, owner_said=lambda: self.said, **kw)
        self.reg = SkillRegistry()
        self.reg.confirm = self.confirm
        self.reg.register(dialing.DialingSkill(self.dialer, self.reg))
        self.reg.allowed_actions |= dialing.ALLOWED_ACTIONS

    def _open(self, url):
        self.opened.append(url)
        return True

    async def confirm(self, prompt, **meta):
        self.asked.append({"prompt": prompt, **meta})
        return self.answer
    confirm.accepts_meta = True

    async def call(self, name, args, origin="model"):
        return await self.reg.call(name, args, origin=origin)

    async def dial(self, args):
        r = await self.call("dial_prepare", args)
        if not r.ok:
            return r
        return await self.call("dial_open", {"draft_id": r.data["draft_id"]})


@pytest.fixture
def rig(home):
    return Rig(home, rows=[row("אשתי", "דנה", DANA)], said=["תתקשר לדנה"])


# ====================================================================== numbers
@pytest.mark.parametrize("raw,expect", [
    ("054-123-4567", DANA), ("0541234567", DANA), ("+972 54 123 4567", DANA), ("00972541234567", DANA),
    ("972541234567", DANA), ("(054) 123 4567", DANA), ("03-123-4567", "+97231234567"), ("+1 202 555 0143", "+12025550143"),
    ("541234567", None), ("054123", None), ("+972541234", None), ("abc", None), ("", None), ("054-123-4567 ext 5", None),
    ("0000000000", None), ("+9725412345678901", None)])
def test_normalize_e164(raw, expect):
    assert dialing.normalize(raw) == expect


def test_readback_is_digit_by_digit_in_both_languages():
    en = dialing.readback(DANA, "en")
    assert en == "plus nine seven two, five four one, two three four, five six seven"
    he = dialing.readback(DANA, "he")
    assert he.startswith("פלוס תשע שבע שתיים") and "אחד" in he
    assert re.sub(r"\D", "", DANA) not in en                      # never the number as one block
    assert dialing.masked(DANA) == "+972 54 •••• 567" and DANA_DIGITS not in dialing.masked(DANA)


@pytest.mark.parametrize("said,ok", [
    (["תתקשר ל-054-123-4567"], True), (["call 0541234567"], True), (["zero five four one two three four five six seven"], True),
    (["אפס חמש ארבע אחת שתיים שלוש ארבע חמש שש שבע"], True), (["+972 54 123 4567 please"], True),
    (["call Dana"], False), (["054-123-4568"], False), ([], False)])
def test_heard_in_accepts_only_what_the_owner_really_said(said, ok):
    assert dialing.heard_in(DANA, said) is ok


def test_heard_in_needs_one_utterance_not_a_patchwork():
    assert dialing.heard_in(DANA, ["054", "1234567"]) is False         # patchwork across turns is not enough


@pytest.mark.parametrize("raw,expect", [("100", "100"), ("112", "112"), ("משטרה", "100"), ("למשטרה", "100"), ("police", "100"),
                                        ("אמבולנס", "101"), ("911", "911"), ("1000", None), ("Dana", None), ("054-123-4567", None)])
def test_emergency_detection(raw, expect):
    assert dialing.is_emergency(raw) == expect


# ====================================================================== names
@pytest.mark.parametrize("said,names,expect,fuzzy", [
    ("דנה", ["דנה", "דני"], ["דנה"], False), ("לדנה", ["דנה", "דני"], ["דנה"], False),
    ("ל־דנה", ["דנה"], ["דנה"], False), ("Dana", ["Dana Levi", "Dan"], ["Dana Levi"], False),
    ("dana", ["Dana Levi", "Dana Cohen"], ["Dana Levi", "Dana Cohen"], False),
    ("דנה", ["Dana Levi"], ["Dana Levi"], True), ("לאה", ["לאה", "אלה"], ["לאה"], False),
    ("לאה", ["אלה"], [], False), ("Moshe", ["Dana"], [], False)])
def test_name_matching(said, names, expect, fuzzy):
    got, fz = dialing.name_matches(said, names)
    assert got == expect and (fz == fuzzy or not expect)


# ====================================================================== the 12 scenarios (tool path, approval fake)
async def test_s01_known_name_reads_back_asks_and_opens_the_handler_link(rig):
    r = await rig.call("dial_prepare", {"name": "דנה"})
    assert r.ok and r.code == "DIAL_READY"
    assert "פלוס תשע שבע שתיים" in r.message and DANA not in r.message                 # digit by digit, not a block
    assert r.data["to"] == "דנה" and r.data["source"] == "הזיכרון המקומי" and rig.opened == [] and rig.asked == []
    assert "same reply" in r.data["rule"]
    o = await rig.call("dial_open", {"draft_id": r.data["draft_id"]})
    assert o.ok and o.code == "DIAL_OPENED" and "Phone Link" in o.message
    assert rig.opened == [f"tel:{DANA}"]
    a = rig.asked[0]
    assert a["details"] == {"recipient": "דנה", "number": DANA, "number_source": "הזיכרון המקומי", "app": "Phone Link"}
    assert DANA in a["prompt"] and "Phone Link" in a["prompt"] and a["kind"] == "call" and a["risk"] == "medium"
    assert a["voice"] is False                                      # never called before: on-screen approval only


async def test_s02_ambiguous_name_asks_which_and_opens_nothing(home):
    rig = Rig(home, rows=[row(name="Dana Levi", phone=DANA), row(name="Dana Cohen", phone="+972501112233")])
    r = await rig.call("dial_prepare", {"name": "Dana"})
    assert not r.ok and r.code == "DIAL_AMBIGUOUS" and set(r.data["choices"]) == {"Dana Levi", "Dana Cohen"}
    assert "Dana Levi" in r.message and rig.opened == [] and rig.asked == [] and "draft_id" not in r.data
    again = await rig.call("dial_prepare", {"name": "Dana Cohen"})
    assert again.ok and again.data["to"] == "Dana Cohen"


async def test_s03_unknown_name_never_guesses(rig):
    r = await rig.call("dial_prepare", {"name": "יוסי"})
    assert not r.ok and r.code == "DIAL_UNKNOWN_CONTACT" and "draft_id" not in r.data and rig.opened == []
    assert (await rig.call("dial_prepare", {})).code == "DIAL_NEED_TARGET"
    assert (await rig.call("dial_open", {"draft_id": "nope"})).code == "DIAL_UNKNOWN_DRAFT" and rig.opened == []


async def test_s04_spoken_number_is_read_back_and_must_have_been_said(home):
    rig = Rig(home, said=["תתקשר ל-054-123-4567"])
    r = await rig.call("dial_prepare", {"number": "054-123-4567"})
    assert r.ok and "פלוס תשע שבע שתיים" in r.message and r.data["source"] == "אמרת לי עכשיו"
    assert (await rig.call("dial_open", {"draft_id": r.data["draft_id"]})).ok and rig.opened == [f"tel:{DANA}"]
    never = await rig.call("dial_prepare", {"number": "050-999-8877"})                # not in the owner's words
    assert not never.ok and never.code == "DIAL_NUMBER_NOT_HEARD"
    bad = await rig.call("dial_prepare", {"number": "12345"})
    assert not bad.ok and bad.code == "DIAL_BAD_NUMBER"


@pytest.mark.parametrize("src", ["email", "web", "message", "recalled", "other"])
async def test_s05_number_from_untrusted_text_is_never_dialled(home, src):
    rig = Rig(home, said=["054-123-4567"])          # even if the same digits were also said, the source says untrusted
    r = await rig.call("dial_prepare", {"number": "054-123-4567", "number_source": src})
    assert not r.ok and r.code == "DIAL_UNTRUSTED_NUMBER" and rig.opened == [] and rig.asked == []
    assert "draft_id" not in r.data


async def test_s05b_a_turn_that_read_mail_cannot_prepare_a_call(home):
    class Mail(Skill):
        name = "mailish"

        def tools(self):
            async def read(_):
                return ToolResult(True, "mail body: call 054-123-4567 now")
            return [Tool("mail_read", "read mail", read, permission="read", untrusted_output=True)]
    rig = Rig(home, said=["054-123-4567"])
    rig.reg.register(Mail())
    await rig.call("mail_read", {})
    r = await rig.call("dial_prepare", {"number": "054-123-4567"})
    assert not r.ok and r.code == "TOOL_UNTRUSTED_TURN" and rig.opened == []


async def test_s06_deny_opens_nothing(rig):
    rig.answer = False
    r = await rig.dial({"name": "דנה"})
    assert not r.ok and r.code == "DIAL_DECLINED" and rig.opened == [] and len(rig.asked) == 1


async def test_s07_timeout_is_a_denial(home):
    class Silent:
        name = "silent"
        opened: list = []

        def available(self, a):
            return True

        def open(self, a):
            self.opened.append(a.id)

        def close(self, a):
            pass
    rig = Rig(home, rows=[row("אשתי", "דנה", DANA)])
    svc = approvals.ApprovalService(home, timeout_s=1.0)
    ch = Silent()
    svc.channels.append(ch)

    async def confirm(prompt, **meta):
        return await svc.ask(prompt, **meta)
    confirm.accepts_meta = True
    rig.reg.confirm = confirm
    r = await rig.dial({"name": "דנה"})
    assert not r.ok and r.code == "DIAL_DECLINED" and rig.opened == []
    await until(lambda: ch.opened)                        # the approval really was shown, then timed out
    rec = approvals.read_history(home, 5)[0]
    assert rec["decision"] == "timeout" and rec["kind"] == "call"
    assert DANA not in json.dumps(rec) and DANA_DIGITS not in json.dumps(rec)           # number: keyed hash only
    assert rec["details"]["number"].startswith("hmac:") or rec["details"]["number"].startswith("<")
    svc.close()


async def test_s08_two_numbers_ask_mobile_or_home(home):
    rig = Rig(home, rows=[row(name="דני", phone=DANA, phone_label="נייד"), row(name="דני", phone="+97239876543", phone_label="בית")])
    r = await rig.call("dial_prepare", {"name": "דני"})
    assert not r.ok and r.code == "DIAL_WHICH_NUMBER" and set(r.data["choices"]) == {"נייד", "בית"}
    assert rig.opened == []
    home_nr = await rig.call("dial_prepare", {"name": "דני", "which": "home"})
    assert home_nr.ok
    assert (await rig.call("dial_open", {"draft_id": home_nr.data["draft_id"]})).ok and rig.opened == ["tel:+97239876543"]


async def test_s09_english_name_and_google_contacts_when_local_has_none(home):
    asked_google: list[str] = []

    async def google(q):
        asked_google.append(q)
        return [{"name": "Dana Levi", "numbers": [{"value": "054-123-4567", "label": "mobile"}]}]
    rig = Rig(home, google=google)
    r = await rig.call("dial_prepare", {"name": "Dana"})
    assert r.ok and r.data["source"] == "אנשי הקשר בגוגל"
    assert asked_google == ["Dana"]
    assert (await rig.call("dial_open", {"draft_id": r.data["draft_id"]})).ok and rig.opened == [f"tel:{DANA}"]
    assert rig.asked[0]["details"]["number_source"] == "אנשי הקשר בגוגל"


async def test_google_is_not_asked_when_the_local_book_matches_and_a_failure_is_unknown(home):
    calls = []

    async def google(q):
        calls.append(q)
        raise RuntimeError("boom")
    rig = Rig(home, rows=[row("אשתי", "דנה", DANA)], google=google)
    assert (await rig.call("dial_prepare", {"name": "דנה"})).ok and calls == []
    nobody = await rig.call("dial_prepare", {"name": "גיל"})
    assert nobody.code == "DIAL_UNKNOWN_CONTACT" and calls == ["גיל"]                   # failure = no guess, no crash


async def test_s10_hebrew_prefix_and_transliteration(home):
    rig = Rig(home, rows=[row(name="Dana Levi", phone=DANA)])
    r = await rig.call("dial_prepare", {"name": "לדנה"})            # "to Dana" in Hebrew spelling, Latin contact
    assert r.ok and r.data["guessed_name"] is True and r.data["to"] == "Dana Levi"
    rig2 = Rig(home, rows=[row(name="דנה", phone=DANA)])
    assert (await rig2.call("dial_prepare", {"name": "לדנה"})).data["guessed_name"] is False


async def test_s11_no_handler_says_so_and_asks_nothing(home):
    rig = Rig(home, rows=[row(name="דנה", phone=DANA)], registry=FakeReg())
    r = await rig.dial({"name": "דנה"})
    assert not r.ok and r.code == "DIAL_NO_HANDLER" and "assisted_dialing" in r.message and rig.asked == [] and rig.opened == []


async def test_s12_phone_link_not_paired(home):
    rig = Rig(home, rows=[row(name="דנה", phone=DANA)], paired="not_installed")
    r = await rig.dial({"name": "דנה"})
    assert not r.ok and r.code == "DIAL_PHONE_LINK_NOT_PAIRED" and rig.asked == [] and rig.opened == []
    other = Rig(home, rows=[row(name="דנה", phone=DANA)], registry=FakeReg("Teams.tel", "ms-teams.exe %1"), paired="not_installed")
    r2 = await other.dial({"name": "דנה"})                                              # not Phone Link: pairing is irrelevant
    assert r2.ok and "Microsoft Teams" in r2.message and other.opened == [f"tel:{DANA}"]


# ====================================================================== more rules
async def test_emergency_just_opens_the_dialer_with_no_approval_and_no_rate_limit(home):
    rig = Rig(home, rate=(1, 600.0))

    async def never(prompt, **meta):
        raise AssertionError("an emergency number must not wait for an approval")
    never.accepts_meta = True
    rig.reg.confirm = never
    for word, number in (("100", "100"), ("משטרה", "100"), ("112", "112"), ("אמבולנס", "101")):
        r = await rig.call("dial_prepare", {"name": word} if not word.isdigit() else {"number": word})
        assert r.ok and r.code == "DIAL_EMERGENCY_OPENED" and number in r.message
        assert rig.opened[-1] == f"tel:{number}"
    assert len(rig.opened) == 4
    none = Rig(home, registry=FakeReg())
    r = await none.call("dial_prepare", {"number": "112"})
    assert not r.ok and r.code == "DIAL_EMERGENCY_NO_HANDLER" and "112" in r.message and none.opened == []


async def test_rate_limit_counts_openings_per_window(home):
    rig = Rig(home, rows=[row(name="דנה", phone=DANA)], rate=(2, 600.0))
    assert (await rig.dial({"name": "דנה"})).ok and (await rig.dial({"name": "דנה"})).ok
    third = await rig.dial({"name": "דנה"})
    assert not third.ok and third.code == "DIAL_RATE_LIMITED" and len(rig.opened) == 2 and len(rig.asked) == 2
    rig.t += 601                                                    # the window passed
    assert (await rig.dial({"name": "דנה"})).ok and len(rig.opened) == 3


async def test_a_known_number_may_be_approved_by_voice_a_new_one_only_on_screen(rig):
    assert (await rig.dial({"name": "דנה"})).ok
    assert (await rig.dial({"name": "דנה"})).ok
    assert [a["voice"] for a in rig.asked] == [False, True]


async def test_a_draft_expires_and_is_one_shot(rig):
    r = await rig.call("dial_prepare", {"name": "דנה"})
    rig.t += dialing.DRAFT_TTL_S + 1
    assert (await rig.call("dial_open", {"draft_id": r.data["draft_id"]})).code == "DIAL_UNKNOWN_DRAFT"
    r2 = await rig.call("dial_prepare", {"name": "דנה"})
    assert (await rig.call("dial_open", {"draft_id": r2.data["draft_id"]})).ok
    again = await rig.call("dial_open", {"draft_id": r2.data["draft_id"]})
    assert again.code == "DIAL_UNKNOWN_DRAFT" or again.data.get("already_done")     # V4-VC-3 replay
    assert len(rig.opened) == 1                                                    # never opened twice
    assert len(rig.opened) == 1


async def test_an_opener_that_fails_is_reported(rig):
    rig.dialer.opener = lambda url: False
    r = await rig.dial({"name": "דנה"})
    assert not r.ok and r.code == "DIAL_OPEN_FAILED"
    rig.dialer.opener = lambda url: (_ for _ in ()).throw(OSError("x"))
    assert (await rig.dial({"name": "דנה"})).code == "DIAL_OPEN_FAILED"


async def test_memory_rows_not_made_by_the_owner_are_ignored(home):
    rig = Rig(home, rows=[row(name="דנה", phone=DANA, source="auto"), row(name="גיל", phone="+972501112233")])
    assert (await rig.call("dial_prepare", {"name": "דנה"})).code == "DIAL_UNKNOWN_CONTACT"      # learned from a chat/mail
    assert (await rig.call("dial_prepare", {"name": "גיל"})).ok


async def test_remember_saves_name_and_number_in_the_private_book(home):
    rig = Rig(home, said=["המספר של גיל הוא 050-111-2233, תזכור"])
    r = await rig.call("dial_prepare", {"name": "גיל", "number": "050-111-2233", "remember": True})
    assert r.ok
    again = Rig(home, said=[])
    assert (await again.call("dial_prepare", {"name": "גיל"})).data["source"] == "מספרים שאמרת לי"


async def test_audit_and_logs_hold_hashes_never_the_number_or_name(rig, home):
    await rig.dial({"name": "דנה"})
    await rig.call("dial_prepare", {"number": "050-999-8877", "number_source": "email"})
    text = (home / "dialing" / "audit.jsonl").read_text(encoding="utf-8")
    recs = [json.loads(x) for x in text.splitlines()]
    assert [r["event"] for r in recs] == ["prepared", "opened", "refused"]
    for needle in (DANA_DIGITS, "דנה", "9998877", "tel:"):
        assert needle not in text
    assert all(re.fullmatch(r"[0-9a-f]{16}", r["number_hash"]) for r in recs)
    assert recs[0]["number_hash"] == recs[1]["number_hash"] and recs[0]["name_hash"] == recs[1]["name_hash"]
    seen = (home / "dialing" / "seen.json").read_text(encoding="utf-8")
    assert DANA_DIGITS not in seen and rig.dialer.seen_before(DANA)
    salt = json.loads((home / "dialing" / "salt.json").read_text(encoding="utf-8"))["salt"]
    assert len(salt) == 32
    assert rig.dialer.digest(DANA) != __import__("hashlib").sha256(DANA.casefold().encode()).hexdigest()[:16]   # salted


def test_the_approvals_audit_keeps_a_keyed_hash_of_the_number(home, monkeypatch):
    from jarves import logs
    monkeypatch.setattr(logs, "_privacy", False)                 # development mode: text is kept, the number still is not
    svc = approvals.ApprovalService(home, timeout_s=1.0)

    async def go():
        svc.channels.append(type("C", (), {"name": "c", "available": lambda s, a: True, "open": lambda s, a: None,
                                           "close": lambda s, a: None})())
        task = asyncio.ensure_future(svc.ask("Open Phone Link to call Dana on " + DANA + "?", title="dial_open", kind="call",
                                             details={"recipient": "Dana", "number": DANA}))
        await until(lambda: svc.has_pending())
        assert svc.pending()[0]["details"]["number"] == DANA                      # the popup / dashboard SHOW it
        svc.answer(svc.pending()[0]["id"], False, channel="dashboard")
        return await task
    assert asyncio.run(go()) is False
    rec = approvals.read_history(home, 1)[0]
    assert rec["details"]["number"].startswith(("hmac:", "<hidden>")) and DANA_DIGITS not in json.dumps(rec)
    svc.close()


def test_spoken_question_names_only_the_kind_and_the_first_name():
    en, he = approvals.spoken_kind("dial_open", "call", {"recipient": "Dana Levi", "number": DANA})
    assert en == "Open the phone dialer to Dana?" and he == "לפתוח את החייגן אל Dana?" and DANA_DIGITS not in en + he
    en2, he2 = approvals.spoken_kind("dial_open", "call", {"recipient": "דנה"})
    assert he2 == "לפתוח את החייגן לדנה?"
    assert approvals.spoken_kind("dial_open", "call", {})[0] == "Open the phone dialer?"


def test_the_popup_labels_the_dial_details_in_both_languages():
    from jarves import approval_popup as pop
    req = {"title": "dial_open", "prompt": "Open Phone Link to call Dana?", "kind": "call",
           "details": {"recipient": "Dana", "number": DANA, "number_source": "your saved contacts", "app": "Phone Link"}}
    for lang, labels in (("en", ["Recipient", "Number", "Where the number came from", "App"]), ("he", ["נמען", "מספר", "מאיפה המספר", "אפליקציה"])):
        rows = {k: v for k, v, _ in pop.detail_rows(req, lang)}
        assert all(label in rows for label in labels), (lang, rows)
        assert rows[labels[1]] == DANA
    assert pop.STRINGS["en"]["ask"]["call"] and pop.STRINGS["he"]["ask"]["call"]


# ====================================================================== handler detection (fake registry)
@pytest.mark.parametrize("reg,kind,name", [
    (PHONE_LINK, "phone_link", "Phone Link"),
    (FakeReg("Teams.tel", "C:\\Teams\\ms-teams.exe %1"), "teams", "Microsoft Teams"),
    (FakeReg("Skype.tel", "skype.exe %1"), "skype", "Skype"),
    (FakeReg("Acme.tel", "acme.exe %1"), "other", "Acme.tel"),
    (FakeReg(None, None, "C:\\Phone\\YourPhone.exe %1"), "phone_link", "Phone Link"),     # no UserChoice: the class default
    (FakeReg(), "none", "")])
def test_detect_handler(reg, kind, name):
    h = dialing.detect_handler(reg)
    assert (h.kind, h.name) == (kind, name) and h.usable is (kind != "none")


def test_phone_link_state_from_files_only(tmp_path):
    assert dialing.phone_link_state(str(tmp_path)) == "not_installed"
    pkg = tmp_path / "Packages" / "Microsoft.YourPhone_8wekyb3d8bbwe" / "LocalState"
    pkg.mkdir(parents=True)
    assert dialing.phone_link_state(str(tmp_path)) == "no_data"
    (pkg / "x.db").write_bytes(b"x")
    assert dialing.phone_link_state(str(tmp_path)) == "data_found"


def test_default_opener_only_ever_opens_a_validated_tel_link(_no_real_browser):
    assert dialing.default_open("https://evil.example/") is False and dialing.default_open("tel:+97254;rm") is False
    assert dialing.default_open("file:///c:/windows/system32/calc.exe") is False
    assert _no_real_browser == []
    assert dialing.default_open(f"tel:{DANA}") is True and _no_real_browser == [f"tel:{DANA}"]       # the guard recorded it


# ====================================================================== feature, catalog, Core
def test_feature_is_registered_safe_off_grouped_bundled_guided_and_declared():
    from jarves import capabilities as cap
    from jarves import guides
    from jarves import setup_wizard as sw
    sw.load_addons()
    f = feat.CATALOG["assisted_dialing"]
    assert f.default_enabled is False and f.risk == "medium" and "assisted_dialing" in sw.SAFE_OFF
    assert "assisted_dialing" in {i for _, _, _, ids in sw.GROUPS for i in ids}
    assert "assisted_dialing" in sw.BUNDLE
    assert any("assisted_dialing" in g.features for g in guides.load().values())
    assert cap.view("assisted_dialing")["problems"] == [] and cap.view("assisted_dialing")["declared"]
    assert cap.view("assisted_dialing")["targets"]["linux_arm64_pi"]["status"] == "planned"
    rows = [r for r in sw.check_addons()["rows"] if r["id"] == "assisted_dialing"]
    assert rows and all(r["ok"] for r in rows), rows


async def test_core_tools_follow_the_switch_and_the_owner_words_ring(core, monkeypatch):
    monkeypatch.setattr(dialing, "_is_windows", lambda: True)          # the probe is Windows-only; CI also runs Linux
    assert "dial_prepare" not in {t.name for t in core.registry.tools()}                  # SAFE_OFF: hidden until switched on
    off = await core.registry.call("dial_prepare", {"name": "דנה"})
    assert not off.ok and off.code == "FEATURE_OFF"
    core.features.set("assisted_dialing", True)
    names = {t.name for t in core.registry.tools()}
    assert {"dial_prepare", "dial_open"} <= names and core.registry.allowed_actions >= dialing.ALLOWED_ACTIONS
    opened: list[str] = []
    core.dialer.opener = lambda url: opened.append(url) or True
    core.dialer._registry = PHONE_LINK
    core.dialer._phone_link = lambda: "data_found"
    core.memory.add(json.dumps({"label": "אשתי", "email": "d@example.com", "name": "דנה", "phone": DANA}, ensure_ascii=False),
                    source="user", kind="contact")
    asked: list[dict] = []

    async def confirm(prompt, **meta):
        asked.append(meta)
        return True
    confirm.accepts_meta = True
    core.registry.confirm = confirm
    r = await core.registry.call("dial_prepare", {"name": "אשתי"})
    assert r.ok and r.data["to"] in ("אשתי", "דנה")
    assert (await core.registry.call("dial_open", {"draft_id": r.data["draft_id"]})).ok and opened == [f"tel:{DANA}"]
    # a number is only dialled if the owner said it: _owner_answer feeds the in-memory ring
    nope = await core.registry.call("dial_prepare", {"number": "050-111-2233"})
    assert nope.code == "DIAL_NUMBER_NOT_HEARD"
    core._owner_answer("תתקשר ל-050-111-2233", "voice")
    assert (await core.registry.call("dial_prepare", {"number": "050-111-2233"})).ok


def test_the_contact_memory_accepts_an_optional_phone_and_keeps_old_rows_unchanged():
    a = ContactArgs(label="אשתי", email="d@example.com", phone="054-123-4567")
    assert a.phone == DANA
    assert ContactArgs(label="x", email="d@example.com").phone == ""
    with pytest.raises(ValueError):
        ContactArgs(label="x", email="d@example.com", phone="12")


# ====================================================================== CLI
@pytest.fixture
def windows(monkeypatch):
    """The feature probe says 'Windows with a dialer'; the registry and Phone Link state are fakes."""
    monkeypatch.setattr(dialing, "_is_windows", lambda: True)
    monkeypatch.setattr(dialing, "detect_handler", lambda reg=None: dialing.Handler("phone_link", "Phone Link"))
    monkeypatch.setattr(dialing, "phone_link_state", lambda *a: "data_found")


def test_cli_dial_is_off_by_default(home, capsys, windows):
    from jarves import cli
    assert cli.main(["dial", "דנה"]) == 2
    assert "jarvis features on assisted_dialing" in capsys.readouterr().err


def test_cli_dial_a_typed_number_with_on_screen_approval(home, monkeypatch, capsys, windows):
    from jarves import cli
    Features(Config(), home).set("assisted_dialing", True)
    opened: list[str] = []
    monkeypatch.setattr(dialing, "default_open", lambda url: opened.append(url) or True)
    asked: list[dict] = []

    def fake_ask(h):
        async def ask(prompt, **meta):
            asked.append({"prompt": prompt, **meta})
            return True
        return ask
    monkeypatch.setattr(dialing, "_dashboard_ask", fake_ask)
    assert cli.main(["dial", "054-123-4567", "--name", "דנה", "--yes-on-screen", "--json"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["code"] == "DIAL_OPENED"
    assert opened == [f"tel:{DANA}"] and asked[0]["details"]["number"] == DANA and asked[0]["details"]["recipient"] == "דנה"
    assert cli.main(["dial", "100", "--yes-on-screen"]) == 0 and opened[-1] == "tel:100" and len(asked) == 1    # no approval
    capsys.readouterr()
    assert cli.main(["dial", "דנה", "--yes-on-screen"]) == 1                                                    # unknown name
    assert "אין לי מספר" in capsys.readouterr().err or "no number" in capsys.readouterr().err


def test_cli_dial_resolves_a_saved_name_and_a_deny_opens_nothing(home, monkeypatch, capsys, windows):
    from jarves import cli
    from jarves.channels_common import write_private_json
    from jarves.whatsapp import contacts_file
    Features(Config(), home).set("assisted_dialing", True)
    write_private_json(contacts_file(home), {"דנה": "972541234567"})
    monkeypatch.setattr(dialing, "default_open", lambda url: pytest.fail("a denied call must not open anything"))

    def deny(h):
        async def ask(prompt, **meta):
            return False
        return ask
    monkeypatch.setattr(dialing, "_dashboard_ask", deny)
    assert cli.main(["dial", "לדנה", "--yes-on-screen"]) == 1
    cap = capsys.readouterr()
    assert "פלוס תשע שבע שתיים" in cap.out and ("לא אושר" in cap.err or "Not approved" in cap.err)


def test_cli_dial_without_a_tty_needs_the_on_screen_flag(home, monkeypatch, capsys, windows):
    from jarves import cli
    Features(Config(), home).set("assisted_dialing", True)
    monkeypatch.setattr(dialing, "default_open", lambda url: pytest.fail("must not open"))
    assert cli.main(["dial", "054-123-4567"]) == 1
    assert "--yes-on-screen" in capsys.readouterr().err


# ====================================================================== dashboard
def test_dashboard_lists_masked_contacts_and_one_click_call(dash, home, monkeypatch, windows):
    off = json.loads(req(dash, "GET", "/api/dialing")[1])
    assert off["enabled"] is False and off["contacts"] == []
    assert req(dash, "POST", "/api/dialing/call", headers={"X-Jarves": "1"}, body={"name": "דנה"})[0].status == 403
    assert req(dash, "POST", "/api/features/assisted_dialing", headers={"X-Jarves": "1"}, body={"enabled": True})[0].status == 200
    from jarves.channels_common import write_private_json
    from jarves.whatsapp import contacts_file
    write_private_json(contacts_file(home), {"דנה": "972541234567"})
    d = json.loads(req(dash, "GET", "/api/dialing")[1])
    assert d["enabled"] and d["handler"]["app"] == "Phone Link"
    assert d["contacts"][0]["name"] == "דנה" and DANA_DIGITS not in json.dumps(d) and "•" in json.dumps(d, ensure_ascii=False)
    opened: list[str] = []
    monkeypatch.setattr(dialing, "default_open", lambda url: opened.append(url) or True)
    asked: list[dict] = []

    def approve(h):
        async def ask(prompt, **meta):
            asked.append(meta)
            return True
        return ask
    monkeypatch.setattr(dialing, "_dashboard_ask", approve)
    r3, body = req(dash, "POST", "/api/dialing/call", headers={"X-Jarves": "1"}, body={"name": "דנה"})
    j = json.loads(body)
    assert r3.status == 200 and j["ok"] and j["code"] == "DIAL_OPENED" and opened == [f"tel:{DANA}"]
    assert j["message_he"].startswith("פתחתי") and j["message"].startswith("I opened")
    assert asked[0]["details"]["number"] == DANA
    j2 = json.loads(req(dash, "POST", "/api/dialing/call", headers={"X-Jarves": "1"}, body={"name": "גיל"})[1])
    assert j2["ok"] is False and j2["code"] == "DIAL_UNKNOWN_CONTACT" and len(opened) == 1


def test_dashboard_page_and_catalogs_are_wired():
    from pathlib import Path
    from jarves import dashboard
    static = Path(dashboard.__file__).parent / "dashboard_static"
    html = (static / "index.html").read_text(encoding="utf-8")
    js = (static / "dialing.js").read_text(encoding="utf-8")
    assert '/static/dialing.js' in html and 'data-page="calls"' in html and "PAGES.calls = async function" in js
    assert "innerHTML" not in js and "eval(" not in js
    for code in ("en", "he"):
        cat = (static / "locales" / f"{code}.js").read_text(encoding="utf-8")
        assert '"Phone calls"' in cat and '"Phone Link not set up"' in cat
    assert "jarves.dialing" in dashboard.PLUGIN_MODULES
