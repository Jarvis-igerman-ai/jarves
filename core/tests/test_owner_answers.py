"""Owner decisions 20 Oct (#17): spoken approval = ONE language + recipient first name; dashboard destructive actions
go through the real ApprovalService popup (no browser confirm)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarves import approvals as appr
from jarves import lists as lists_mod
from jarves import telegram_channel as tg
from jarves import whatsapp as wa
from jarves import instagram_channel as ig
from jarves.config import Config
from jarves.skills import SkillRegistry
from owner_harness import OwnerAnswers


# ------------------------------------------------------------------ (B) spoken question: one language, first name
def test_first_name_only():
    assert appr.first_name("Dana Levi") == "Dana"
    assert appr.first_name("דנה כהן") == "דנה"
    assert appr.first_name("Dana Levi <dana@example.com>") == "Dana"
    assert appr.first_name("dana@example.com") == ""
    assert appr.first_name("054-123-4567") == ""
    assert appr.first_name(None) == ""


def _approval(title, kind, details):
    return appr.Approval("a" * 16, title, "full prompt SECRET-TEXT", "", "medium", kind, "model", details, "", True,
                         0.0, 1.0)


def test_spoken_is_one_language_english_default_hebrew_on_request():
    a = _approval("whatsapp_open", "message", {"recipient": "דנה כהן"})
    assert a.spoken == "Send a WhatsApp message to דנה?"                  # default = English only
    assert a.spoken_in("en") == "Send a WhatsApp message to דנה?"
    assert a.spoken_in("he") == "לשלוח הודעת וואטסאפ לדנה?"            # ל prefix on the Hebrew first name
    for lang in ("en", "he", "", None, "xx"):
        s = a.spoken_in(lang)
        assert " / " not in s                                          # never both languages in one sentence
        assert "SECRET-TEXT" not in s and "כהן" not in s               # first name only, never details
    en = _approval("telegram_send", "message", {"recipient": "Dana Levi"})
    assert en.spoken_in("en") == "Send a Telegram message to Dana?"
    assert en.spoken_in("he") == "לשלוח הודעת טלגרם אל Dana?"           # Latin name: no glued prefix
    assert _approval("list_clear", "tool", {}).spoken_in("en") == "Delete something?"
    assert _approval("list_clear", "tool", {}).spoken_in("he") == "למחוק משהו?"


async def test_voice_channel_speaks_the_popup_language_only(home):
    from jarves.app import _VoiceApproval
    heard = []

    class Live:
        async def confirm_pending(self, prompt, voice=False):
            heard.append(prompt)

        def confirm_done(self):
            pass
    for lang, want in (("en", "Send an e-mail to Dana?"), ("he", "לשלוח מייל אל Dana?")):
        heard.clear()
        cfg = Config()
        cfg.ui.popup_language = lang
        svc = appr.ApprovalService(home, timeout_s=5)
        svc.channels.append(_VoiceApproval(SimpleNamespace(live=Live(), cfg=cfg)))
        task = asyncio.create_task(svc.ask("Send to dana@example.com: top secret", title="gmail_send_draft",
                                           kind="message", details={"recipient": "Dana"}))
        for _ in range(100):
            if heard:
                break
            await asyncio.sleep(0.05)
        assert heard == [want]
        svc.close()
        assert await task is False


async def test_whatsapp_send_path_passes_the_first_name_to_the_real_approval(home):
    svc = appr.ApprovalService(home, timeout_s=5)
    seen = []
    svc.on_ask.append(lambda a: (seen.append(a), svc.answer(a.id, False, channel="dashboard")))
    appr.api_approvals(SimpleNamespace(home=home), None, None)      # an open dashboard page = a place to answer
    reg = SkillRegistry()

    async def confirm(prompt, **meta):
        return await svc.ask(prompt, **meta)
    confirm.accepts_meta = True
    reg.confirm = confirm
    sk = wa.WhatsAppSkill(home, opener=lambda u: True)
    reg.register(sk)
    reg.allowed_actions |= wa.ALLOWED_ACTIONS
    d = await reg.call("whatsapp_draft", {"text": "לא להקריא את זה", "name": "דנה כהן", "phone": "054-123-4567"})
    r = await reg.call("whatsapp_open", {"draft_id": d.data["draft_id"]})
    assert not r.ok and seen
    assert seen[0].details == {"recipient": "דנה כהן"}
    assert seen[0].spoken_in("en") == "Send a WhatsApp message to דנה?"
    assert seen[0].spoken_in("he") == "לשלוח הודעת וואטסאפ לדנה?"
    assert "לא להקריא" not in seen[0].spoken_in("he") and "לא להקריא" in seen[0].prompt     # popup text unchanged


def _recipient_of(skill, tool_name, draft):
    skill._drafts["d1"] = draft
    tool = next(t for t in skill.tools() if t.name == tool_name)
    return tool.recipient(SimpleNamespace(draft_id="d1"))


def test_telegram_instagram_and_mail_send_tools_declare_the_recipient(home):
    from jarves.skills import google as g
    assert _recipient_of(tg.TelegramSkill(home), "telegram_send", {"name": "Dana Levi"}) == "Dana Levi"
    assert _recipient_of(ig.InstagramSkill(home), "instagram_send", {"name": "Dana Levi"}) == "Dana Levi"
    mail = g.GmailSkill(SimpleNamespace())
    assert _recipient_of(mail, "gmail_send_draft", {"name": "Dana Levi", "to": "d@x.com"}) == "Dana Levi"
    assert appr.first_name("Dana Levi") == "Dana"


# ------------------------------------------------------------------ (C) dashboard Clear -> ApprovalService popup
def _list_ctx(home):
    from jarves import dashboard
    ctx = dashboard.Ctx(home, Config())
    lists_mod.api_list_add(ctx, None, {"list_name": "shopping", "items": ["milk", "bread"]})
    return ctx


def test_clear_is_refused_without_an_approval_and_does_not_trust_the_page_flag(home):
    ctx = _list_ctx(home)
    try:
        st, body = lists_mod.api_list_op(ctx, None, {"op": "clear", "list_name": "shopping", "only_done": False,
                                                       "approved": True})
        assert st == 403 and body["needs_approval"]
        assert len(lists_mod.api_lists(ctx, None, None, {"name": ["shopping"]})[1]["items"]) == 2
    finally:
        ctx.close()


def test_clear_denied_by_owner_keeps_the_list_and_approved_clears_it(home):
    ctx = _list_ctx(home)
    try:
        with OwnerAnswers(home, False) as owner:
            st, _ = lists_mod.api_list_op(ctx, None, {"op": "clear", "list_name": "shopping", "only_done": False})
        assert st == 403 and len(owner.asked) == 1 and owner.asked[0]["kind"] == "tool"
        assert len(lists_mod.api_lists(ctx, None, None, {"name": ["shopping"]})[1]["items"]) == 2
        with OwnerAnswers(home, True) as owner:
            st, body = lists_mod.api_list_op(ctx, None, {"op": "clear", "list_name": "shopping", "only_done": False})
        assert st == 200 and body["cleared"] == 2 and len(owner.asked) == 1
        history = appr.read_history(home, 5)
        assert [h["decision"] for h in history[:2]] == ["approved", "denied"]
        assert history[0]["channel"] == "dashboard" and history[0]["source"] == "dashboard"
    finally:
        ctx.close()


async def test_dashboard_request_waits_on_the_running_core_service(home):
    """With a Core service attached (its loop running), the HTTP thread asks THAT service and waits for the answer."""
    svc = appr.attach(appr.ApprovalService(home, timeout_s=10))
    svc.loop = asyncio.get_running_loop()
    appr.api_approvals(SimpleNamespace(home=home), None, None)      # the dashboard page is open (a place to answer)
    ctx = _list_ctx(home)
    try:
        call =asyncio.create_task(asyncio.to_thread(
            lists_mod.api_list_op, ctx, None, {"op": "clear", "list_name": "shopping", "only_done": False}))
        for _ in range(100):
            if svc.pending():
                break
            await asyncio.sleep(0.05)
        waiting = svc.pending()
        assert len(waiting) == 1 and not call.done()                       # the request is blocked, waiting for the owner
        assert "Clear every item" in waiting[0]["prompt"] and waiting[0]["details"] == {"list": "shopping"}
        ok, why = svc.answer(waiting[0]["id"], True, channel="dashboard")
        assert ok, why
        st, body = await asyncio.wait_for(call, 10)
        assert st == 200 and body["cleared"] == 2
    finally:
        appr.detach(svc)
        ctx.close()


async def test_dashboard_request_times_out_to_a_refusal(home):
    svc = appr.attach(appr.ApprovalService(home, timeout_s=1))
    svc.loop = asyncio.get_running_loop()
    appr.api_approvals(SimpleNamespace(home=home), None, None)
    ctx = _list_ctx(home)
    try:
        st, body = await asyncio.to_thread(lists_mod.api_list_op, ctx, None,
                                           {"op": "clear", "list_name": "shopping", "only_done": False})
        assert st == 403 and body["needs_approval"]
        assert len(lists_mod.api_lists(ctx, None, None, {"name": ["shopping"]})[1]["items"]) == 2
    finally:
        appr.detach(svc)
        ctx.close()


def test_no_dashboard_page_uses_a_browser_confirm_for_destructive_actions():
    from pathlib import Path
    import jarves
    static = Path(jarves.__file__).with_name("dashboard_static")
    for name in ("m3.js", "m2.js", "home.js"):
        src = (static / name).read_text(encoding="utf-8")
        for needle in ("Clear every item", "Delete this report", "Delete note", "Delete job", "Uninstall ${s.title}",
                       "Disconnect Google?", "This cannot be undone"):
            assert f"confirm({needle}" not in src and f'confirm("{needle}' not in src
    m3 = (static / "m3.js").read_text(encoding="utf-8")
    assert "waiting for approval on your screen" in m3 and "ממתין לאישור על המסך שלך" in m3
