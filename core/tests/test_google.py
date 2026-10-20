"""Google Calendar + Gmail Skills and sign-in (WP-G), fully offline: a mock Google (httpx.MockTransport) serves
fixture JSON shaped like the API reference; no account, no network, no browser."""
import asyncio
import base64
import copy
import json
import time
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.policy import default as email_policy
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from jarves import logs
from jarves.brain import Brain, match_intent
from jarves.google_auth import (ALL_SCOPES, CALENDAR_SCOPES, GMAIL_COMPOSE, GMAIL_READ, RECONNECT_HE, GoogleAuth,
                                GoogleAuthError, google_status)
from jarves.health import Health
from jarves.skills import SkillRegistry
from jarves.skills.google import (GOOGLE_ALLOWED_ACTIONS, CalendarSkill, GmailSkill, GoogleApi, clean_text,
                                  register_google, resolve_day)
from jarves.skills.music import FakePlayer, MusicSkill

TZ = timezone(timedelta(hours=3))
NOW = datetime(2026, 10, 19, 10, 0, tzinfo=TZ)          # a Monday
FIX = json.loads((Path(__file__).parent / "fixtures" / "google_api.json").read_text(encoding="utf-8"))
CLIENT = {"installed": {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-test",
                        "redirect_uris": ["http://localhost"]}}


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _encode(o):
    if isinstance(o, dict):
        return {k: (_b64(v[4:]) if k == "data" and isinstance(v, str) and v.startswith("B64:") else _encode(v))
                for k, v in o.items()}
    if isinstance(o, list):
        return [_encode(x) for x in o]
    return o


class FakeGoogle:
    """Mock oauth2.googleapis.com + Calendar v3 + Gmail v1."""

    def __init__(self, scope=" ".join(ALL_SCOPES)):
        self.fix = _encode(copy.deepcopy(FIX))
        self.scope, self.refresh_error, self.api_status, self.reject_token = scope, None, None, None
        self.calls: list[httpx.Request] = []
        self.forms: list[dict] = []
        self.sent: list[str] = []
        self.drafts: dict[str, dict] = {}
        self.n = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        u, path = req.url, req.url.path
        if u.host == "oauth2.googleapis.com":
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            self.forms.append(form)
            if path == "/revoke":
                return httpx.Response(200)
            if form["grant_type"] == "refresh_token" and self.refresh_error:
                return httpx.Response(400, json={"error": self.refresh_error, "error_description": "Token has been "
                                                 "expired or revoked."})
            self.n += 1
            body = {"access_token": f"ya29.at-{self.n}", "expires_in": 3599, "scope": self.scope,
                    "token_type": "Bearer"}
            if form["grant_type"] == "authorization_code":
                body["refresh_token"] = "1//rt-secret"
            return httpx.Response(200, json=body)
        if self.reject_token and req.headers.get("authorization") == f"Bearer {self.reject_token}":
            return httpx.Response(401, json={"error": {"code": 401, "status": "UNAUTHENTICATED"}})
        if self.api_status:
            return httpx.Response(self.api_status, json={"error": {"code": self.api_status, "status": "X"}})
        if path == "/calendar/v3/users/me/calendarList":
            return httpx.Response(200, json=self.fix["calendarList"])
        if path.startswith("/calendar/v3/calendars/") and path.endswith("/events"):
            from urllib.parse import unquote
            cid = unquote(req.url.raw_path.decode().split("/calendars/")[1].split("/events")[0].split("?")[0])
            return httpx.Response(200, json=self.fix["events"][cid])
        if path == "/gmail/v1/users/me/messages":
            q = u.params.get("q", "")
            if "from:" in q and ("דנה" in q or "dana" in q):
                return httpx.Response(200, json={"messages": [{"id": "m2"}]})
            if "from:" in q:
                return httpx.Response(200, json={"resultSizeEstimate": 0})
            return httpx.Response(200, json=self.fix["messages_list"])
        if path.startswith("/gmail/v1/users/me/messages/"):
            m = self.fix["messages"].get(path.rsplit("/", 1)[1])
            if m is None:
                return httpx.Response(404, json={"error": {"code": 404}})
            if u.params.get("format") == "metadata":
                want = {h.lower() for h in u.params.get_list("metadataHeaders")}
                m = {**m, "payload": {"headers": [h for h in m["payload"]["headers"] if h["name"].lower() in want]}}
            return httpx.Response(200, json=m)
        if path == "/gmail/v1/users/me/drafts" and req.method == "POST":
            did = f"r-{len(self.drafts) + 1}"
            self.drafts[did] = json.loads(req.content)
            return httpx.Response(200, json={"id": did, "message": {"id": "dm1"}})
        if path == "/gmail/v1/users/me/drafts/send":
            did = json.loads(req.content)["id"]
            self.sent.append(did)
            return httpx.Response(200, json={"id": "sent1", "labelIds": ["SENT"]})
        return httpx.Response(404, json={"error": {"code": 404}})

    def transport(self):
        return httpx.MockTransport(self.handler)

    def api_calls(self):
        return [c for c in self.calls if c.url.host != "oauth2.googleapis.com"]


def seed(home: Path, scopes=ALL_SCOPES, expires_in=3600, **extra) -> GoogleAuth:
    a = GoogleAuth(home)
    (home / "google").mkdir(parents=True, exist_ok=True)
    (home / "google" / "client.json").write_text(json.dumps(CLIENT), encoding="utf-8")
    a._save({"access_token": "ya29.at-0", "refresh_token": "1//rt-secret", "scopes": list(scopes),
             "expires_at": time.time() + expires_in, "connected_at": time.time(), **extra})
    return a


@pytest.fixture
def g():
    return FakeGoogle()


@pytest.fixture
def setup(home, g):
    seed(home)
    auth = GoogleAuth(home, transport=g.transport())
    api = GoogleApi(auth, g.transport())
    reg = SkillRegistry()
    t = [100.0]
    reg.clock = lambda: t[0]
    cal = CalendarSkill(api, now=lambda: NOW, tz=TZ)
    mail = GmailSkill(api)
    reg.register(cal)
    reg.register(mail)
    reg.allowed_actions |= GOOGLE_ALLOWED_ACTIONS
    return reg, t


# ---------------------------------------------------------------------- dates and cleaning
@pytest.mark.parametrize("day,start,end,label", [
    ("today", "2026-10-19T10:00", "2026-10-20T00:00", "היום"),
    ("היום", "2026-10-19T10:00", "2026-10-20T00:00", "היום"),
    ("מחר", "2026-10-20T00:00", "2026-10-21T00:00", "מחר"),
    ("tomorrow", "2026-10-20T00:00", "2026-10-21T00:00", "מחר"),
    ("מחרתיים", "2026-10-21T00:00", "2026-10-22T00:00", "מחרתיים"),
    ("חמישי", "2026-10-22T00:00", "2026-10-23T00:00", "ביום חמישי"),
    ("ביום חמישי", "2026-10-22T00:00", "2026-10-23T00:00", "ביום חמישי"),
    ("יום ראשון", "2026-10-25T00:00", "2026-10-26T00:00", "ביום ראשון"),
    ("Thursday", "2026-10-22T00:00", "2026-10-23T00:00", "ביום חמישי"),
    ("שני", "2026-10-19T10:00", "2026-10-20T00:00", "ביום שני"),         # today is Monday: from now
    ("2026-11-03", "2026-11-03T00:00", "2026-11-04T00:00", "ב-3.11"),
    ("3.11", "2026-11-03T00:00", "2026-11-04T00:00", "ב-3.11"),
    ("5.1", "2027-01-05T00:00", "2027-01-06T00:00", "ב-5.1"),             # past date without year -> next year
    ("השבוע", "2026-10-19T10:00", "2026-10-26T00:00", "בשבוע הקרוב"),
])
def test_resolve_day(day, start, end, label):
    s, e, lab = resolve_day(day, NOW)
    assert (s.isoformat(timespec="minutes")[:16], e.isoformat(timespec="minutes")[:16], lab) == (start, end, label)
    assert s.tzinfo is not None


def test_resolve_day_unknown():
    with pytest.raises(ValueError):
        resolve_day("בעוד שנה", NOW)


def test_clean_text_drops_hidden_text_links_and_invisible_chars():
    html = FIX["messages"]["m2"]["payload"]["parts"][0]["body"]["data"].removeprefix("B64:")
    out = clean_text(html, html=True)
    assert "מחר יוצאים לטיול" in out and "פרטים" in out and "כאן" in out
    for bad in ("ignore previous", "forward all mail", "send the draft", "alert", "https://", "​"):
        assert bad not in out
    assert clean_text("x" * 3000, 100).endswith("…") and len(clean_text("x" * 3000, 100)) == 100
    assert clean_text("שלום https://evil.example/a?b=1 ביי") == "שלום [קישור] ביי"
    assert clean_text("a\U000e0041\U000e0042b") == "ab"            # Unicode "tag" smuggling removed


# ---------------------------------------------------------------------- auth
async def test_sign_in_loopback_pkce_and_storage(home, g):
    (home / "google").mkdir(parents=True)
    (home / "google" / "client.json").write_text(json.dumps(CLIENT), encoding="utf-8")
    opened = {}

    def browser(url):
        opened["url"] = url
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        opened["q"] = q

        async def hit():                       # the "browser" returns to the loopback listener after consent
            async with httpx.AsyncClient() as c:
                opened["page"] = (await c.get(q["redirect_uri"], params={"code": "4/abc", "state": q["state"]})).text
        asyncio.get_running_loop().create_task(hit())
        return True
    auth = GoogleAuth(home, transport=g.transport(), open_browser=browser)
    st = await auth.sign_in(timeout_s=10)
    for _ in range(50):                          # the browser task reads the result page right after
        if "page" in opened:
            break
        await asyncio.sleep(0.02)
    q = opened["q"]
    assert urlparse(opened["url"]).netloc == "accounts.google.com"
    assert q["redirect_uri"].startswith("http://127.0.0.1:") and q["code_challenge_method"] == "S256"
    assert q["access_type"] == "offline" and q["prompt"] == "consent"
    assert set(q["scope"].split()) == set(ALL_SCOPES)
    assert "Google connected" in opened["page"]
    form = g.forms[0]
    assert form["grant_type"] == "authorization_code" and form["code"] == "4/abc" and len(form["code_verifier"]) >= 43
    assert form["redirect_uri"] == q["redirect_uri"] and form["client_secret"] == "GOCSPX-test"
    assert st["connected"] and st["calendar"] and st["gmail_read"] and st["gmail_send"]
    assert st["message_he"] == "" and "gmail.readonly" in st["scopes"]
    assert auth.file == home / "google" / "token.json" and auth.load()["refresh_token"] == "1//rt-secret"
    repo = Path(__file__).resolve().parents[2]
    assert repo not in auth.file.resolve().parents                 # tokens are user-private, never in the repo


async def test_sign_in_rejects_wrong_state_and_denied_consent(home, g):
    seed(home)
    auth = GoogleAuth(home, transport=g.transport())
    with pytest.raises(GoogleAuthError) as e:
        await auth.complete({"code": "x", "state": "evil"}, "good", "v", "http://127.0.0.1:1")
    assert e.value.code == "GOOGLE_BAD_STATE"
    with pytest.raises(GoogleAuthError) as e:
        await auth.complete({"error": "access_denied", "state": "s"}, "s", "v", "http://127.0.0.1:1")
    assert e.value.code == "GOOGLE_CONSENT_DENIED"
    assert g.forms == []


async def test_sign_in_without_client_explains_setup(home):
    with pytest.raises(GoogleAuthError) as e:
        await GoogleAuth(home).sign_in(timeout_s=1)
    assert e.value.code == "GOOGLE_NO_CLIENT" and "GOOGLE_SETUP" in e.value.action


def test_import_client_accepts_only_desktop_clients(home, tmp_path):
    auth = GoogleAuth(home)
    web = tmp_path / "client_secret_web.json"
    web.write_text(json.dumps({"web": {"client_id": "x"}}), encoding="utf-8")
    with pytest.raises(GoogleAuthError):
        auth.import_client(web)
    ok = tmp_path / "client_secret_desktop.json"
    ok.write_text(json.dumps(CLIENT), encoding="utf-8")
    auth.import_client(ok)
    assert auth.client() == {"client_id": CLIENT["installed"]["client_id"], "client_secret": "GOCSPX-test"}


async def test_refresh_when_expired(home, g):
    seed(home, expires_in=-10)
    auth = GoogleAuth(home, transport=g.transport())
    assert await auth.access_token(CALENDAR_SCOPES) == "ya29.at-1"
    assert g.forms[0]["grant_type"] == "refresh_token"
    assert auth.load()["refresh_token"] == "1//rt-secret"          # Google keeps the refresh token
    assert await auth.access_token(CALENDAR_SCOPES) == "ya29.at-1" and len(g.forms) == 1


async def test_weekly_reconsent_is_graceful(home, g):
    """Testing-mode refresh tokens die after 7 days (invalid_grant): status says reconnect, tools say it in Hebrew."""
    seed(home, expires_in=-10)
    g.refresh_error = "invalid_grant"
    log_dir = home / "logs"
    logs.setup(log_dir, "DEBUG", console=False)
    reg = SkillRegistry()
    register_google(reg, home, transport=g.transport(), now=lambda: NOW, tz=TZ)
    res = await reg.call("calendar_list_events", {"day": "מחר"})
    assert not res.ok and res.code == "GOOGLE_AUTH_EXPIRED" and res.message == RECONNECT_HE
    assert "צריך לחבר מחדש את גוגל" in res.message
    st = google_status(home)
    assert st["connected"] is False and st["needs_reconnect"] is True and st["last_error"] == "GOOGLE_AUTH_EXPIRED"
    assert st["message_he"] == RECONNECT_HE
    assert "refresh_token" not in GoogleAuth(home).load()
    # the Skills stay registered after a restart, still answering "reconnect" (no crash, no Google call)
    reg2, health = SkillRegistry(), Health()
    register_google(reg2, home, health, transport=g.transport())
    assert reg2.get("gmail_search") is not None and health.components["google"].status == "degraded"
    res = await reg2.call("gmail_search", {})
    assert res.code == "GOOGLE_AUTH_EXPIRED"
    text = "".join(p.read_text(encoding="utf-8", errors="replace") for p in log_dir.glob("*.log*"))
    assert "GOOGLE_AUTH_EXPIRED" in text
    for secret in ("rt-secret", "ya29", "GOCSPX"):
        assert secret not in text                                     # never tokens or client secret in logs


async def test_scope_missing(home, g):
    seed(home, scopes=CALENDAR_SCOPES)
    auth = GoogleAuth(home, transport=g.transport())
    with pytest.raises(GoogleAuthError) as e:
        await auth.access_token((GMAIL_READ,))
    assert e.value.code == "GOOGLE_SCOPE_MISSING" and "הרשאה" in e.value.spoken


async def test_api_401_refreshes_once_then_succeeds(setup, g):
    reg, _ = setup
    g.reject_token = "ya29.at-0"                 # revoked early on Google's side: 401 until refreshed
    res = await reg.call("calendar_next_event", {})
    assert res.ok and any(f["grant_type"] == "refresh_token" for f in g.forms)


async def test_sign_out_revokes_and_deletes(home, g):
    seed(home)
    auth = GoogleAuth(home, transport=g.transport())
    assert await auth.sign_out() is True
    assert not auth.file.exists() and g.forms[-1] == {"token": "1//rt-secret"}
    assert google_status(home)["connected"] is False and google_status(home)["needs_reconnect"] is False


# ---------------------------------------------------------------------- calendar
async def test_calendar_tomorrow_all_calendars(setup, g):
    reg, _ = setup
    res = await reg.call("calendar_list_events", {"day": "מחר"})
    assert res.ok, res
    assert res.message == "מחר יש לך 4 אירועים ביומן."
    evs = res.data["untrusted_events"]
    titles = [e["title"] for e in evs]
    # all-day holiday first (hidden calendar still included: ALL calendars), shared event once, declined/cancelled out
    assert titles == ["Hoshana Raba", "פגישת צוות", "ארוחת צהריים עם דנה", "חוג של נועה"]
    assert evs[0]["when"] == "all day" and evs[2]["when"].endswith("13:30-14:30")      # UTC -> local time
    assert evs[2]["location"] == "קפה נחת, [קישור]"
    assert "Ignore previous" not in json.dumps(res.data, ensure_ascii=False)
    assert "פגישת צוות" not in res.message                         # third-party text never in the spoken message
    ev_calls = [c for c in g.api_calls() if c.url.path.endswith("/events")]
    assert len(ev_calls) == 3
    p = ev_calls[0].url.params
    assert p["timeMin"].startswith("2026-10-20T00:00:00+03:00") and p["timeMax"].startswith("2026-10-21T00:00")
    assert p["singleEvents"] == "true" and p["orderBy"] == "startTime"
    assert all(c.headers["authorization"] == "Bearer ya29.at-0" for c in g.api_calls())


async def test_calendar_bad_day_and_empty(setup, g):
    reg, _ = setup
    res = await reg.call("calendar_list_events", {"day": "בעוד שנה"})
    assert res.code == "CALENDAR_BAD_DAY" and not g.api_calls()
    for cal in g.fix["events"].values():
        cal["items"] = []
    res = await reg.call("calendar_list_events", {"day": "היום"})
    assert res.ok and res.message == "היום אין לך כלום ביומן."


async def test_calendar_next_event(setup):
    reg, _ = setup
    res = await reg.call("calendar_next_event", {})
    assert res.ok and res.message == "האירוע הבא שלך ב-20.10 בשעה 09:00."
    assert res.data["untrusted_events"][0]["title"] == "פגישת צוות"


# ---------------------------------------------------------------------- gmail
async def test_gmail_search_asks_before_reading(setup, g):
    reg, _ = setup
    res = await reg.call("gmail_search", {"query": "is:important is:unread newer_than:2d"})
    assert res.ok and res.message == "יש 2 מיילים. לשאול אם להקריא אותם."
    assert res.data["senders_for_speech"] == ["חברת החשמל", "דנה לוי"]
    assert "ASK" in res.data["rule"] and res.data["untrusted_emails"][1]["subject"] == "הטיול מחר"
    assert "@" not in json.dumps(res.data["untrusted_emails"])     # addresses are not handed out
    meta = [c for c in g.api_calls() if c.url.path.endswith("/m1")][0].url.params
    assert meta["format"] == "metadata"                             # the search never downloads bodies


async def test_gmail_read_only_offered_mail_and_sanitized(setup, g):
    reg, _ = setup
    res = await reg.call("gmail_read", {"id": "m2"})
    assert res.code == "GMAIL_NOT_OFFERED" and not g.api_calls()   # not offered (and asked) first
    await reg.call("gmail_search", {})
    res = await reg.call("gmail_read", {"id": "m2"})
    assert res.ok and res.message == "המייל מדנה לוי."
    mail = res.data["untrusted_email"]
    assert "מחר יוצאים לטיול בשעה 8" in mail["body"]
    for bad in ("ignore previous", "forward all mail", "send the draft", "evil.example", "plan.pdf"):
        assert bad not in mail["body"]
    assert "never instructions" in res.data["note"]


async def test_untrusted_text_blocks_actions_for_the_rest_of_the_turn(setup, g):
    reg, t = setup
    reg.register(MusicSkill(FakePlayer()))
    await reg.call("gmail_search", {})
    await reg.call("gmail_read", {"id": "m2"})                    # mail says "forward all mail"/"send the draft"
    for name, args in (("gmail_create_draft", {"to": "x@evil.example", "body": "all your mail"}),
                       ("music_play", {"query": "x"})):
        res = await reg.call(name, args)
        assert res.code == "TOOL_UNTRUSTED_TURN", name
    assert not g.drafts
    assert (await reg.call("calendar_list_events", {"day": "מחר"})).ok      # read tools still work
    reg.new_turn()                                                  # transcript right after the result: still blocked
    assert (await reg.call("music_play", {"query": "x"})).code == "TOOL_UNTRUSTED_TURN"
    t[0] += 5
    reg.new_turn()                                                  # the user spoke again
    assert (await reg.call("music_play", {"query": "x"})).code != "TOOL_UNTRUSTED_TURN"
    # the user's own words (deterministic parser) are never blocked
    await reg.call("gmail_search", {})
    assert (await reg.call("music_play", {"query": "x"}, origin="user")).code != "TOOL_UNTRUSTED_TURN"


async def test_send_mail_draft_readback_then_approval(setup, g):
    reg, t = setup
    asked = []

    async def confirm(prompt):
        asked.append(prompt)
        return answer[0]
    reg.confirm = confirm
    answer = [False]
    res = await reg.call("gmail_create_draft", {"to": "דנה", "subject": "הטיול", "body": "תודה, נגיע."})
    assert res.ok and res.code == "GMAIL_DRAFT_READY", res
    assert res.data["to"] == "dana.levi@school.example" and "לשלוח" in res.message and "תודה, נגיע." in res.message
    did = res.data["draft_id"]
    raw = base64.urlsafe_b64decode(g.drafts[did]["message"]["raw"] + "==")
    msg = message_from_bytes(raw, policy=email_policy)
    assert msg["To"] == "dana.levi@school.example" and msg["Subject"] == "הטיול"
    assert msg.get_content().strip() == "תודה, נגיע."
    assert not asked and not g.sent                                 # a draft sends nothing and needs no approval
    res = await reg.call("gmail_send_draft", {"draft_id": did})
    assert res.code == "TOOL_DECLINED" and not g.sent
    assert "dana.levi@school.example" in asked[0] and "תודה, נגיע." in asked[0] and "הטיול" in asked[0]
    answer[0] = True
    res = await reg.call("gmail_send_draft", {"draft_id": did})
    assert res.ok and g.sent == [did] and res.message == "שלחתי את המייל לדנה לוי."
    # even the user's own words ask first; drafts JARVES did not write are never sent
    asked.clear()
    res = await reg.call("gmail_send_draft", {"draft_id": "r-999"}, origin="user")
    assert asked and res.code == "GMAIL_UNKNOWN_DRAFT" and g.sent == [did]


async def test_send_without_compose_scope_is_not_allowed(home, g):
    seed(home, scopes=CALENDAR_SCOPES + (GMAIL_READ,))
    reg = SkillRegistry()
    register_google(reg, home, transport=g.transport())
    assert "gmail_create_draft" not in reg.allowed_actions
    res = await reg.call("gmail_create_draft", {"to": "a@b.example", "body": "x"})
    assert res.code == "TOOL_FORBIDDEN" and not g.drafts


async def test_recipient_ambiguous_or_unknown(setup, g):
    reg, _ = setup
    res = await reg.call("gmail_create_draft", {"to": "dana", "body": "hi"})
    assert res.code == "GMAIL_RECIPIENT_AMBIGUOUS"
    assert {c["address"] for c in res.data["choices"]} == {"dana.levi@school.example", "dana.cohen@work.example"}
    res = await reg.call("gmail_create_draft", {"to": "יוסי", "body": "hi"})
    assert res.code == "GMAIL_RECIPIENT_UNKNOWN" and "מה הכתובת" in res.message
    assert not g.drafts


# ---------------------------------------------------------------------- wiring
def test_register_google_only_when_connected(home, g):
    reg, health = SkillRegistry(), Health()
    assert register_google(reg, home, health) is None
    assert reg.get("calendar_list_events") is None and health.components["google"].status == "disabled"
    assert google_status(home)["message_he"].startswith("גוגל עוד לא מחובר")
    seed(home)
    reg, health = SkillRegistry(), Health()
    register_google(reg, home, health)
    assert {"calendar_list_events", "calendar_next_event", "gmail_search", "gmail_read", "gmail_create_draft",
            "gmail_send_draft"} <= {t.name for t in reg.tools()}
    assert "gmail_create_draft" in reg.allowed_actions and "gmail_send_draft" not in reg.allowed_actions
    assert reg.get("gmail_send_draft").permission == "destructive"
    assert health.components["google"].status == "ok"
    st = google_status(home)
    assert st["connected"] and st["scopes"] == ["calendar.events.readonly", "calendar.calendarlist.readonly",
                                                 "gmail.readonly", "gmail.compose"]


def test_calendar_only_connection_registers_no_gmail(home):
    seed(home, scopes=CALENDAR_SCOPES)
    reg = SkillRegistry()
    register_google(reg, home)
    assert reg.get("calendar_list_events") and reg.get("gmail_search") is None


@pytest.mark.parametrize("text,tool,args", [
    ("מה יש לי מחר ביומן", "calendar_list_events", {"day": "מחר"}),
    ("מה יש לי מחר ביומן?", "calendar_list_events", {"day": "מחר"}),
    ("ג'ארביס, מה יש לי ביומן היום", "calendar_list_events", {"day": "היום"}),
    ("מה יש לי ביום חמישי ביומן", "calendar_list_events", {"day": "ביום חמישי"}),
    ("what's on my calendar tomorrow", "calendar_list_events", {"day": "tomorrow"}),
    ("מתי הפגישה הבאה שלי", "calendar_next_event", {}),
    ("יש מיילים חשובים?", "gmail_search", {"query": "is:important is:unread newer_than:2d"}),
    ("יש לי מיילים חדשים", "gmail_search", {"query": "is:unread newer_than:1d"}),
])
def test_hebrew_intents(text, tool, args):
    i = match_intent(text)
    assert i is not None and (i.tool, i.args) == (tool, args)


def test_no_intent_for_sending_or_unrelated():
    assert match_intent("תשלח מייל לדנה שנגיע") is None              # composing needs the model + read-back
    assert match_intent("מה יש לי מחר") is None                      # no "calendar" word -> model decides
    assert match_intent("תנגן יומן של אהבה").tool == "music_play"


async def test_brain_offline_calendar_and_mail(setup):
    reg, _ = setup
    brain = Brain(reg, None)
    r = await brain.respond("מה יש לי מחר ביומן")
    assert r.ok and r.source == "command" and r.text == "מחר יש לך 4 אירועים ביומן."
    r = await brain.respond("יש מיילים חשובים?")
    assert r.ok and "לשאול אם להקריא" in r.text


def test_dashboard_google_status_route(home):
    """Dashboard plugin route: status with the Hebrew reconnect line; never tokens or the client secret."""
    import http.client
    from jarves import dashboard
    from jarves.config import Config
    seed(home, needs_reconnect=True)
    GoogleAuth(home)._save({"needs_reconnect": True, "scopes": list(ALL_SCOPES), "last_error": "GOOGLE_AUTH_EXPIRED"})
    d = dashboard.Dashboard(home, Config()).start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=5)
        c.request("GET", "/api/google", headers={"Host": f"127.0.0.1:{d.port}",
                                                 "Cookie": f"{dashboard.COOKIE}={d.url.split('t=')[1]}"})
        r = c.getresponse()
        body = r.read().decode()
        c.close()
    finally:
        d.stop()
    assert r.status == 200, body
    st = json.loads(body)
    assert st["needs_reconnect"] is True and st["message_he"] == RECONNECT_HE and st["client_configured"] is True
    assert "GOCSPX" not in body and "rt-secret" not in body


def test_cli_status_not_connected(home, capsys):
    from jarves.cli import main
    assert main(["google", "status"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["connected"] is False and out["client_configured"] is False


def test_cli_logout(home, capsys, monkeypatch):
    from jarves import google_auth
    from jarves.cli import main
    seed(home)
    g = FakeGoogle()
    real = google_auth.GoogleAuth.__init__

    def init(self, h, **kw):
        real(self, h, transport=g.transport())
    monkeypatch.setattr(google_auth.GoogleAuth, "__init__", init)
    assert main(["google", "logout"]) == 0
    assert "disconnected" in capsys.readouterr().out and not (home / "google" / "token.json").exists()
