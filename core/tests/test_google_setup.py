"""`jarvis google start` (WP-K), fully offline: a fake Downloads folder, a fake browser that "downloads" the client
file and answers Google's sign-in, a mock Google (test_google.FakeGoogle) and a fake clipboard. No account, no
network, no real browser."""
import asyncio
import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from jarves import dashboard, google_setup
from jarves.config import Config
from jarves.features import Features
from jarves.google_auth import ALL_SCOPES, CALENDAR_SCOPES, NOT_CONNECTED_HE, GoogleAuth
from jarves.skills import SkillRegistry
from jarves.skills.google import register_google

from test_dashboard import req
from test_google import CLIENT, FakeGoogle, seed


def _api_id(url: str) -> str:
    """The API the opened console URL enables (exact apiid query value, not a substring of the URL)."""
    return (parse_qs(urlparse(url).query).get("apiid") or [""])[0]

W = {"X-Jarves": "1"}


class Google(FakeGoogle):
    """FakeGoogle + the Gmail profile endpoint the self-check reads, and switchable API errors."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.disabled_api = None

    def handler(self, r: httpx.Request) -> httpx.Response:
        if self.disabled_api and self.disabled_api in r.url.path:
            self.calls.append(r)
            return httpx.Response(403, json={"error": {"code": 403, "status": "PERMISSION_DENIED", "errors": [
                {"reason": "accessNotConfigured"}]}})
        if r.url.path == "/gmail/v1/users/me/profile":
            self.calls.append(r)
            return httpx.Response(200, json={"emailAddress": "me@example.com", "messagesTotal": 3})
        return super().handler(r)


class Browser:
    """Fake system browser: opening the client page drops a client_secret file in Downloads; opening Google's
    consent page answers the loopback redirect (code, or error=access_denied)."""

    def __init__(self, downloads: Path, client=CLIENT, error=None):
        self.downloads, self.client, self.error = downloads, client, error
        self.opened: list[str] = []
        self.scopes: list[str] = []

    def open_console(self, url: str) -> bool:
        self.opened.append(url)
        if "/auth/clients" in url and self.client is not None:
            (self.downloads / "client_secret_123-abc.apps.googleusercontent.com.json").write_text(
                json.dumps(self.client), encoding="utf-8")
        return True

    def open_consent(self, url: str) -> bool:
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self.scopes = q["scope"].split()

        async def hit():
            await asyncio.sleep(0.05)
            p = {"state": q["state"], **({"error": self.error} if self.error else {"code": "4/abc"})}
            async with httpx.AsyncClient() as c:
                await c.get(q["redirect_uri"], params=p)
        asyncio.get_running_loop().create_task(hit())
        return True


def make(home, g, b, answers=(), **kw):
    out, clip = [], []
    it = iter(answers)
    auth = GoogleAuth(home, transport=g.transport(), open_browser=b.open_consent)
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return next(it, "")

    wz = google_setup.Wizard(home, Config(), auth=auth, ask=ask, say=out.append,
                             open_url=b.open_console, clipboard=lambda t: clip.append(t) or True,
                             downloads=b.downloads, sleep=lambda s: None, which=lambda n: None,
                             watch_timeout_s=kw.pop("watch_timeout_s", 2), **kw)
    wz.test_prompts = prompts
    return wz, out, clip


@pytest.fixture
def dl(tmp_path):
    d = tmp_path / "Downloads"
    d.mkdir()
    return d


def test_first_time_walks_every_step_takes_the_download_signs_in_checks_and_enables(home, dl):
    g, b = Google(), Browser(dl)
    wz, out, clip = make(home, g, b, answers=["me@example.com"])
    assert wz.run() == 0
    text = "\n".join(out)
    # one console page per step, in order, and the values to paste went to the clipboard
    assert [u.split("console.cloud.google.com")[1].split("?")[0] for u in b.opened] == [
        "/projectcreate", "/flows/enableapi", "/flows/enableapi", "/auth/branding", "/auth/audience", "/auth/scopes",
        "/auth/clients"]
    assert clip[0] == "JARVES home" and "me@example.com" in clip and google_setup.scopes_text() in clip
    # the client file was taken from Downloads (moved: the download is gone) and stored privately
    assert not list(dl.glob("client_secret*.json")) and GoogleAuth(home).client()["client_id"].startswith("123-")
    assert GoogleAuth(home).status()["connected"] and set(b.scopes) == set(ALL_SCOPES)
    # live self-check hit the calendar list and the Gmail profile, read-only
    paths = [c.url.path for c in g.api_calls()]
    assert "/calendar/v3/users/me/calendarList" in paths and "/gmail/v1/users/me/profile" in paths
    assert all(c.method == "GET" for c in g.api_calls())
    f = Features(Config(), home)
    assert f.enabled("google_calendar") and f.enabled("gmail_read") and not f.enabled("gmail_send")
    st = google_setup.load_state(home)
    assert st["completed_at"] and st["check"]["calendar"] == "ok" and set(st["steps"]) == set(google_setup.STEP_IDS)
    # spoken-style summary with example phrases; never the secret, a token or the address
    assert "What is on my calendar tomorrow?" in text and "jarvis google start" in text
    assert not any("\u0590" <= c <= "\u05ff" for c in text + "".join(wz.test_prompts))
    assert "[1/7] Create a Google Cloud project" in text and "Browser page: https://" in text
    assert "GOCSPX" not in text and "1//rt-secret" not in text and "ya29" not in text and "me@example.com" not in text


def test_quit_is_resumable_and_done_steps_are_skipped(home, dl):
    g, b = Google(), Browser(dl, client=None)
    # email skipped, project done (project id skipped), calendar API done, quit at step 3
    wz, out, _ = make(home, g, b, answers=["", "", "", "", "q"])
    assert wz.run() == 1
    assert set(google_setup.load_state(home)["steps"]) == {"project", "calendar_api"}
    b2 = Browser(dl)
    wz2, out2, _ = make(home, g, b2, answers=[""] * 10)
    assert wz2.run() == 0
    assert "/projectcreate" not in " ".join(b2.opened) and any(_api_id(u) == "gmail.googleapis.com" for u in b2.opened)
    assert sum("Done earlier:" in line for line in out2) == 2


def test_weekly_reconnect_only_signs_in_again(home, dl):
    seed(home)
    a = GoogleAuth(home)
    a._save({"needs_reconnect": True, "scopes": list(ALL_SCOPES), "last_error": "GOOGLE_AUTH_EXPIRED"})
    g, b = Google(), Browser(dl)
    wz, out, _ = make(home, g, b)
    assert wz.run() == 0
    assert b.opened == [] and GoogleAuth(home).status()["connected"]          # no console steps at all
    assert any("7 days" in line for line in out)


def test_already_connected_only_checks(home, dl):
    seed(home)
    g, b = Google(), Browser(dl)
    wz, out, _ = make(home, g, b)
    assert wz.run() == 0 and b.opened == [] and b.scopes == []                # no sign-in either
    assert google_setup.load_state(home)["check"]["gmail"] == "ok"


def test_calendar_only_skips_gmail_and_asks_only_calendar(home, dl):
    g, b = Google(scope=" ".join(CALENDAR_SCOPES)), Browser(dl)
    wz, _, clip = make(home, g, b, calendar_only=True)
    assert wz.run() == 0
    assert not any("gmail" in u for u in b.opened) and set(b.scopes) == set(CALENDAR_SCOPES)
    assert google_setup.scopes_text(True) in clip
    assert "/gmail/v1/users/me/profile" not in [c.url.path for c in g.api_calls()]
    f = Features(Config(), home)
    assert f.enabled("google_calendar") and f._state["enabled"].get("gmail_read") is None   # not switched by us


def test_access_denied_explains_test_user_and_opens_audience(home, dl):
    seed(home)
    GoogleAuth(home).file.unlink()
    g, b = Google(), Browser(dl, error="access_denied")
    wz, out, _ = make(home, g, b, answers=["q"])
    assert wz.run() == 1
    text = "\n".join(out)
    assert "test user" in text
    assert any(u.split("?")[0].endswith("/auth/audience") for u in b.opened)
    assert google_setup.load_state(home)["last_error"] == "GOOGLE_CONSENT_DENIED"


def test_disabled_api_found_by_the_self_check(home, dl):
    seed(home)
    g, b = Google(), Browser(dl)
    g.disabled_api = "/calendarList"
    wz, out, _ = make(home, g, b)
    assert wz.run() == 1
    assert any("Calendar API" in line for line in out)
    assert any(_api_id(u) == "calendar-json.googleapis.com" for u in b.opened)
    assert google_setup.load_state(home)["last_error"] == "API_DISABLED_CALENDAR"


def test_web_client_file_is_rejected_with_a_fix(home, dl):
    g = Google()
    (dl / "client_secret_web.json").write_text(json.dumps({"web": {"client_id": "x"}}), encoding="utf-8")
    b = Browser(dl, client=None)
    wz, out, _ = make(home, g, b, answers=["q"])
    wz.run()
    assert any("Desktop app" in line for line in out)
    assert not GoogleAuth(home).client()


def test_no_download_times_out_with_a_clear_next_step(home, dl):
    g, b = Google(), Browser(dl, client=None)
    wz, out, _ = make(home, g, b, answers=[""] * 10, watch_timeout_s=0)
    assert wz.run() == 1
    assert any("Download JSON" in line for line in out)


def test_gcloud_used_only_when_installed_and_the_user_agrees(home, dl):
    g, b = Google(), Browser(dl)
    ran = []

    class R:
        returncode = 0
        stdout = "[]"
    wz, out, _ = make(home, g, b, answers=["", "", "n"] + [""] * 10)
    wz.which = lambda n: "C:/gcloud/gcloud.cmd"
    wz.run_cmd = lambda args, **kw: ran.append(args) or R()
    assert wz.run() == 0
    assert [r[1:3] for r in ran] == [["projects", "list"], ["projects", "create"], ["services", "enable"]]
    assert {"calendar-json.googleapis.com", "gmail.googleapis.com"} <= set(ran[2])   # exact argv entries
    assert not any("/projectcreate" in u or "enableapi" in u for u in b.opened)   # those steps were done by gcloud
    pid = google_setup.load_state(home)["project_id"]
    assert pid.startswith("jarves-home-") and any(f"project={pid}" in u for u in b.opened)
    # declined -> never run
    ran.clear()
    wz2, _, _ = make(home / "other", g, Browser(dl), answers=["", "n"] + [""] * 10)
    wz2.which = lambda n: "gcloud"
    wz2.run_cmd = lambda args, **kw: ran.append(args) or R()
    wz2.run()
    assert ran == []


@pytest.mark.parametrize("calendar_only", [False, True])
def test_gcloud_resume_selects_accessible_project_and_resets_old_checkmarks(home, dl, calendar_only):
    google_setup.save_state(home, {"email": "me@example.com", "project_id": "wrong-project",
                                  "steps": {s: 1 for s in google_setup.STEP_IDS}})
    wz, out, _ = make(home, Google(), Browser(dl), answers=["", "1", "q"], calendar_only=calendar_only)
    wz.which = lambda _: "gcloud"
    ran = []

    def run(args, **kw):
        ran.append(args)
        return type("R", (), {"returncode": 0, "stdout": json.dumps([
            {"projectId": "jarves-home-123456", "name": "JARVES home", "lifecycleState": "ACTIVE"}])})()

    wz.run_cmd = run
    assert wz.run() == 1  # user stops at the first remaining browser page
    st = google_setup.load_state(home)
    assert st["project_id"] == "jarves-home-123456"
    assert set(st["steps"]) == {"project", "calendar_api"} | (set() if calendar_only else {"gmail_api"})
    assert len(ran) == 2 and ran[1][1:3] == ["services", "enable"]
    assert ("gmail.googleapis.com" in set(ran[1])) is not calendar_only   # exact argv entry
    assert wz.open_url.__self__.opened == [google_setup.step_url("branding", st["project_id"])]
    assert any("not accessible" in line for line in out)


@pytest.mark.parametrize("failure", ["list", "invalid_json", "enable", "timeout"])
def test_gcloud_failure_stops_before_browser_without_exposing_raw_output(home, dl, failure):
    wz, out, _ = make(home, Google(), Browser(dl), answers=["", "", "1"])
    wz.which = lambda _: "gcloud"

    def run(args, **kw):
        if failure == "timeout":
            raise google_setup.subprocess.TimeoutExpired(args, 180)
        listing = args[1:3] == ["projects", "list"]
        failed = failure == ("list" if listing else "enable")
        return type("R", (), {"returncode": 1 if failed else 0, "stderr": "private-secret",
                              "stdout": "private-secret" if failure == "invalid_json" or failed else
                              '[{"projectId":"jarves-home-123456","name":"JARVES home"}]'})()

    wz.run_cmd = run
    assert wz.run() == 1
    assert not wz.open_url.__self__.opened
    assert "private-secret" not in "\n".join(out)
    assert any("gcloud" in line for line in out)
    assert not google_setup.load_state(home)["steps"].get("calendar_api")


def test_gcloud_resume_same_project_preserves_consent_progress(home, dl):
    google_setup.save_state(home, {"project_id": "jarves-home-123456", "email": "me@example.com",
                                  "steps": {"project": 1, "branding": 1}})
    wz, _, _ = make(home, Google(), Browser(dl), answers=["", "", "q"])
    wz.which = lambda _: "gcloud"
    wz.run_cmd = lambda *a, **kw: type("R", (), {"returncode": 0, "stdout":
        '[{"projectId":"jarves-home-123456","name":"JARVES home"}]'})()
    assert wz.run() == 1
    assert google_setup.load_state(home)["steps"]["branding"] == 1
    assert wz.open_url.__self__.opened == [google_setup.step_url("audience", "jarves-home-123456")]


def test_testing_days_left_and_fixes():
    now = time.time()
    assert google_setup.testing_days_left(now - 2 * 86400, now) == 5.0
    assert google_setup.testing_days_left(now - 9 * 86400, now) == 0.0
    assert google_setup.testing_days_left(0) is None
    for code in ("GOOGLE_CONSENT_DENIED", "oauth:redirect_uri_mismatch", "oauth:invalid_grant", "GOOGLE_SCOPE_MISSING"):
        f = google_setup.fix_for(code)
        assert f["he"] and f["en"]
    assert google_setup.fix_for("oauth:invalid_grant")["en"].endswith("jarvis google start")


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    from jarves import google_auth
    path = tmp_path / "release-oauth.json"
    path.write_text(json.dumps(CLIENT), encoding="utf-8")
    monkeypatch.setattr(google_auth, "APP_CLIENT_FILE", path)
    return path


def test_new_user_with_app_client_goes_straight_to_consent_without_cloud_setup(home, dl, app_client):
    g, b = Google(), Browser(dl)
    wz, out, clip = make(home, g, b)
    wz.ask = lambda _: pytest.fail("App connection must not ask setup questions")
    wz.which = lambda _: pytest.fail("App connection must not need gcloud")
    assert wz.run() == 0
    assert not b.opened and not clip and not wz.auth.client_file.exists()
    assert wz.auth.status()["client_source"] == "app"
    assert wz.auth.file.exists() and wz.auth.load()["refresh_token"]
    assert "7 days" not in "\n".join(out)
    # One app registration, separate user token stores; never expose registration values in status/checklist.
    other = GoogleAuth(home / "another-user")
    assert other.client() and not other.load() and not other.status()["connected"]
    public = json.dumps(google_setup.checklist(home))
    assert "GOCSPX" not in public and "123-abc" not in public and "rt-secret" not in public


def test_app_client_dashboard_offers_connect_without_developer_steps(dash, home, dl, app_client, monkeypatch):
    st, data = send(dash, "GET", "/api/google/setup")
    assert st == 200 and data["app_client"] and data["state"] == "ready_to_sign_in"
    assert data["steps"] == [] and "Connect Google" in data["next_en"]
    assert data["testing_days_left"] is None
    called = []
    monkeypatch.setattr(google_setup, "_login_thread", lambda *args: called.append(args))
    try:
        st, result = send(dash, "POST", "/api/google/start")
        assert st == 200 and result["started"] and called
        assert not GoogleAuth(home).client_file.exists()
    finally:
        google_setup._LOGIN["running"] = False


@pytest.mark.parametrize("invalid", [{"web": CLIENT["installed"]}, {"installed": {"client_id": 123}},
                                    {"installed": {"client_id": "not-a-google-client"}},
                                    {"installed": {"client_id": CLIENT["installed"]["client_id"], "client_secret": {}}}])
def test_invalid_release_client_is_not_used(home, app_client, invalid):
    app_client.write_text(json.dumps(invalid), encoding="utf-8")
    auth = GoogleAuth(home)
    assert not auth.client() and not auth.status()["client_configured"]


def test_private_client_override_takes_precedence_over_app_registration(home, app_client):
    other = {"installed": {"client_id": "private.apps.googleusercontent.com", "client_secret": "private"}}
    p = home / "private-input.json"
    p.write_text(json.dumps(other), encoding="utf-8")
    auth = GoogleAuth(home)
    auth.import_client(p)
    assert auth.client() == other["installed"]
    assert auth.status()["client_source"] == "private" and not google_setup.checklist(home)["app_client"]


async def test_app_user_google_help_uses_connect_button_instead_of_developer_cli(home, app_client):
    reg = SkillRegistry()
    register_google(reg, home)
    result = await reg.call("google_connect_help", {})
    assert result.ok and result.data["setup"] == "app"
    assert "Connect Google" in result.message and "command" not in result.data


async def test_installed_app_missing_google_registration_reports_maintainer_blocker(home, monkeypatch):
    from jarves import updates
    monkeypatch.setattr(updates, "build_info", lambda: {"commit": "release-build"})
    reg = SkillRegistry()
    register_google(reg, home)
    result = await reg.call("google_connect_help", {})
    assert result.code == "GOOGLE_APP_NOT_CONFIGURED" and not result.ok
    assert result.data["setup"] == "maintainer_required" and "command" not in result.data


def test_explicit_calendar_write_consent_can_precede_feature_enable(home, monkeypatch):
    from argparse import Namespace
    from jarves import google_auth
    requested = []

    async def sign_in(self, scopes):
        requested.extend(scopes)
        return {"scopes": [google_auth.short_scope(s) for s in scopes]}

    monkeypatch.setattr(google_auth.GoogleAuth, "sign_in", sign_in)
    args = Namespace(action="login", client=None, calendar_only=True, calendar_write=True)
    assert google_auth.cmd_google(args, Config(), home) == 0
    assert google_auth.CALENDAR_WRITE in requested
    assert not Features(Config(), home).enabled("calendar_write")


# ---------------------------------------------------------------------- voice: not connected -> how to connect
async def test_not_connected_he_says_how_to_connect_and_picks_up_a_new_connection(home):
    reg = SkillRegistry()
    assert register_google(reg, home) is None
    res = await reg.call("google_connect_help", {})
    assert res.ok and res.message == NOT_CONNECTED_HE and "תריץ במחשב jarvis google start" in res.message
    seed(home)                                               # the user ran `jarvis google start` meanwhile
    res = await reg.call("google_connect_help", {})
    assert res.ok and "calendar" in reg.skills and "gmail" in reg.skills
    assert "google_connect_help" not in [t.name for t in reg.tools()]


def test_feature_reason_tells_him_what_to_say(home):
    f = Features(Config(), home)
    assert "jarvis google start" in f.reason("google_calendar")
    # Voice summaries bound each reason; the complete setup command remains in the generated feature reference.
    assert "jarvis google start" in f.capabilities_markdown()
    assert "Features that are OFF" in f.front_note(2000)


# ---------------------------------------------------------------------- dashboard Connections + Home
@pytest.fixture
def dash(home, monkeypatch, dl):
    monkeypatch.setattr(google_setup, "downloads_dir", lambda: dl)
    d = dashboard.Dashboard(home, Config()).start()
    yield d
    d.stop()


def send(d, method, path, body=None):
    r, data = req(d, method, path, headers=W, body=body if body is not None else {})
    return r.status, json.loads(data or b"{}")


def test_connections_checklist_steps_and_start_without_client(dash, home, dl):
    st, g = send(dash, "GET", "/api/google/setup")
    assert st == 200 and g["state"] == "not_connected" and "jarvis google start" in g["next_en"]
    assert [s["id"] for s in g["steps"]] == google_setup.STEP_IDS and not any(s["done"] for s in g["steps"])
    assert all(s["url"].startswith("https://console.cloud.google.com/") for s in g["steps"])
    st, g = send(dash, "POST", "/api/google/step/branding", {"done": True})
    assert st == 200 and next(s for s in g["steps"] if s["id"] == "branding")["done"]
    assert send(dash, "POST", "/api/google/step/nope", {})[0] == 404
    st, r = send(dash, "POST", "/api/google/start")
    assert st == 409 and r["fix"]["step"] == "client" and "Download JSON" in r["error"]
    assert send(dash, "POST", "/api/google/test")[0] == 409


def test_connections_start_takes_the_download_and_signs_in(dash, home, dl, monkeypatch):
    (dl / "client_secret_1.json").write_text(json.dumps(CLIENT), encoding="utf-8")
    started = []
    monkeypatch.setattr(google_setup, "_login_thread", lambda *a: started.append(a))
    st, r = send(dash, "POST", "/api/google/start")
    assert st == 200 and r["started"] and started
    assert not list(dl.glob("client_secret*")) and GoogleAuth(home).client()
    google_setup._LOGIN["running"] = False
    st, g = send(dash, "GET", "/api/google/setup")
    assert g["state"] == "ready_to_sign_in" and next(s for s in g["steps"] if s["id"] == "client")["done"]


def test_connections_shows_reconnect_and_days_left_without_secrets(dash, home):
    seed(home, connected_at=time.time() - 3 * 86400)
    st = google_setup.load_state(home)
    st["email"] = "me@example.com"
    google_setup.save_state(home, st)
    s, g = send(dash, "GET", "/api/google/setup")
    assert g["state"] == "connected" and 3.9 <= g["testing_days_left"] <= 4.1
    raw = json.dumps(g)
    assert "me@example.com" not in raw and "GOCSPX" not in raw and "rt-secret" not in raw
    GoogleAuth(home)._save({"needs_reconnect": True})
    s, g = send(dash, "GET", "/api/google/setup")
    assert g["state"] == "needs_reconnect" and "Reconnect" in g["next_en"]


def test_home_page_has_simple_tiles_and_one_hint(dash, home):
    st, h = send(dash, "GET", "/api/home")
    assert st == 200 and h["status"] == "off" and "jarvis" in h["next"]["en"]          # no Core running here
    assert {c["id"] for c in h["connections"]} == {"google", "ai", "music"}
    assert set(h["today"]) == {"cost", "conversations", "voice_minutes"} and "open" in h["issues"]
    assert h["privacy"]["label_en"] and "on" in h["features"] and h["last_conversation"] is None
    r, page = req(dash, "GET", "/static/home.js")
    assert r.status == 200 and b"PAGES.connections" in page and b"PAGES.home" in page
