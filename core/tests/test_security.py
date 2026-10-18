"""Issue #9 security regressions: CR-03 tool permissions, CR-07 OAuth file races, CR-08 link roles, CR-09 logs."""
import asyncio
import json
import threading
import time

import httpx
import pytest
import websockets
from pydantic import BaseModel
from websockets.asyncio.client import connect

from conftest import free_port, open_face
from jarves import logs
from jarves.chatgpt_auth import ChatGPTAuth, ChatGPTAuthError
from jarves.deviceport import INBOUND
from jarves.health import Health
from jarves.link import ALLOWED, DEVICE_EVENTS, GodotLink
from jarves.memory import MemoryStore
from jarves.skills import Skill, SkillRegistry, Tool, ToolResult
from jarves.skills.builtin import MemorySkill
from jarves.skills.music import FakePlayer, MusicSkill


# ============================================================== CR-03 permissions
class Recorder:
    def __init__(self, answer: bool) -> None:
        self.answer, self.prompts = answer, []

    async def __call__(self, prompt: str) -> bool:
        self.prompts.append(prompt)
        return self.answer


def mem_registry(tmp_path, answer=False):
    store = MemoryStore(tmp_path / "m.db")
    r = SkillRegistry()
    r.register(MemorySkill(store))
    r.confirm = Recorder(answer)
    return r, store


async def test_model_cannot_delete_memories_without_approval(tmp_path):
    r, store = mem_registry(tmp_path, answer=False)
    ids = [store.add(f"coffee fact {i}") for i in range(3)]
    # the model "insists": extra fields claiming consent are ignored, the policy still asks the user
    for name, args in [("forget", {"id": ids[0], "confirmed": True}), ("forget", {"match": "coffee"}),
                       ("forget_all", {"user_said_yes": True})]:
        res = await r.call(name, args)                      # default origin is "model"
        assert res.code == "TOOL_DECLINED", name
    assert store.count() == 3 and len(r.confirm.prompts) == 3


async def test_model_forget_with_approval_reports_count(tmp_path):
    r, store = mem_registry(tmp_path, answer=True)
    for i in range(3):
        store.add(f"coffee fact {i}")
    store.add("tea")
    res = await r.call("forget", {"match": "coffee"}, origin="model")
    assert res.ok and res.data["deleted"] == 3 and store.count() == 1
    assert "3" in r.confirm.prompts[-1]


async def test_user_forget_single_record_needs_no_confirmation_but_many_do(tmp_path):
    r, store = mem_registry(tmp_path, answer=False)
    one = store.add("my cat is Mitzi")
    store.add("likes coffee a")
    store.add("likes coffee b")
    assert (await r.call("forget", {"id": one}, origin="user")).ok                 # exactly one record
    assert (await r.call("forget", {"match": "Mitzi"}, origin="user")).code == "NOT_FOUND"
    res = await r.call("forget", {"match": "coffee"}, origin="user")               # two records -> confirm
    assert res.code == "TOOL_DECLINED" and store.count() == 2 and "2" in r.confirm.prompts[-1]
    assert (await r.call("forget_all", origin="user")).code == "TOOL_DECLINED"     # everything -> confirm
    r.confirm.answer = True
    assert (await r.call("forget", {"match": "coffee"}, origin="user")).data["deleted"] == 2


class Connectors(Skill):
    name = "connectors"

    def __init__(self) -> None:
        self.effects: list[str] = []

    def tools(self):
        class Mail(BaseModel):
            to: str
            body: str

        async def send(a):
            self.effects.append(f"mail:{a.to}")
            return ToolResult(True, "sent")

        async def wipe(_):
            self.effects.append("wipe")
            return ToolResult(True, "wiped")

        async def legacy(_):
            self.effects.append("legacy")
            return ToolResult(True, "ran")
        return [Tool("email_send", "Send an e-mail", send, Mail, permission="action"),
                Tool("wipe_calendar", "Delete all events", wipe, permission="destructive"),
                Tool("no_policy", "registered without a permission", legacy),
                Tool("weird", "unknown permission", legacy, permission="admin")]


async def test_unapproved_connector_and_unknown_permissions_are_denied():
    r = SkillRegistry()
    c = Connectors()
    r.register(c)
    r.confirm = Recorder(True)                       # even a user who says yes to everything
    mail = {"to": "boss@example.com", "body": "I quit"}
    assert (await r.call("email_send", mail)).code == "TOOL_FORBIDDEN"
    assert (await r.call("email_send", mail, origin="user")).code == "TOOL_FORBIDDEN"
    assert (await r.call("no_policy")).code == "TOOL_FORBIDDEN"
    assert (await r.call("weird", origin="user")).code == "TOOL_FORBIDDEN"
    assert (await r.call("email_send", mail, origin="admin")).code == "TOOL_FORBIDDEN"   # bogus origin
    assert c.effects == [] and r.confirm.prompts == []
    r.confirm.answer = False
    assert (await r.call("wipe_calendar")).code == "TOOL_DECLINED" and c.effects == []    # new destructive tool
    r.allowed_actions.add("email_send")                                                  # explicit policy allow
    assert (await r.call("email_send", mail)).ok and c.effects == ["mail:boss@example.com"]


async def test_preauthorized_music_actions_run_from_the_model():
    r = SkillRegistry()
    p = FakePlayer()
    r.register(MusicSkill(p))
    r.confirm = Recorder(False)
    assert (await r.call("music_volume", {"percent": 30})).ok and r.confirm.prompts == []
    assert (await r.call("music_status")).code != "TOOL_FORBIDDEN"


class Slow(Skill):
    name = "slow"

    def __init__(self) -> None:
        self.applied, self.read_done, self.hung_cancelled = [], False, False

    def tools(self):
        async def act(_):
            await asyncio.sleep(0.2)
            self.applied.append("done")
            return ToolResult(True, "ok")

        async def look(_):
            await asyncio.sleep(0.2)
            self.read_done = True
            return ToolResult(True, "ok")

        async def hang(_):
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                self.hung_cancelled = True
                raise
            return ToolResult(True, "late")
        return [Tool("act", "side effect", act, permission="action"),
                Tool("look", "read", look, permission="read"),
                Tool("hang", "slow side effect", hang, permission="action", timeout_s=0.05)]


async def test_side_effects_survive_turn_cancellation_but_reads_do_not():
    r = SkillRegistry(allowed_actions={"act", "hang"})
    s = Slow()
    r.register(s)
    for name in ("act", "look"):
        t = asyncio.create_task(r.call(name))
        await asyncio.sleep(0.05)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    await asyncio.sleep(0.3)
    assert s.applied == ["done"] and not s.read_done
    assert (await r.call("hang")).code == "TOOL_TIMEOUT"          # the timeout still applies
    await asyncio.sleep(0.01)
    assert s.hung_cancelled
    await r.drain()


# ============================================================== CR-07 OAuth file races
class SlowOAuth:
    """Token/revoke endpoint whose refresh takes a while (widens the race window)."""

    def __init__(self, delay=0.3) -> None:
        self.delay, self.n, self.used, self.revoked = delay, 0, [], []
        self.lock = threading.Lock()

    async def handler(self, req: httpx.Request) -> httpx.Response:
        from urllib.parse import parse_qs
        form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
        if req.url.path.endswith("/revoke"):
            self.revoked.append(form["token"])
            return httpx.Response(200)
        with self.lock:
            self.used.append(form["refresh_token"])
            self.n += 1
            n = self.n
        await asyncio.sleep(self.delay)
        return httpx.Response(200, json={"access_token": f"at-{n}", "refresh_token": f"rt-{n}",
                                         "expires_in": 3600, "scope": "chatgpt.tokens.use.direct"})


def seed(tmp_path, srv) -> ChatGPTAuth:
    a = ChatGPTAuth(tmp_path, transport=httpx.MockTransport(srv.handler))
    a._save({"client_id": "cid", "email": "me@example.com", "id_token": "x.y.z", "access_token": "at-0",
             "refresh_token": "rt-0", "scopes": ["chatgpt.tokens.use.direct"], "expires_at": 0})
    return a


def other_process(tmp_path, srv, fn):
    """A second ChatGPTAuth on its own event loop in a thread: stands in for the CLI process."""
    out: dict = {}

    def run():
        try:
            out["result"] = asyncio.run(fn(ChatGPTAuth(tmp_path, transport=httpx.MockTransport(srv.handler))))
        except Exception as e:      # noqa: BLE001 - reported to the test
            out["error"] = e
    th = threading.Thread(target=run)
    th.start()
    return th, out


async def test_refresh_and_model_cache_never_lose_the_rotated_token(tmp_path):
    srv = SlowOAuth()
    auth = seed(tmp_path, srv)

    async def cli(a2: ChatGPTAuth):
        for i in range(30):                       # hammer the model cache during the refresh
            a2.save_models([{"slug": f"m{i}"}])
            await asyncio.sleep(0.01)
        return await a2.access_token()            # a concurrent refresh in "another process"
    th, out = other_process(tmp_path, srv, cli)

    async def core_models():
        for i in range(30):
            auth.save_models([{"slug": f"core{i}"}])
            await asyncio.sleep(0.01)
    tok, _ = await asyncio.gather(auth.access_token(), core_models())
    await asyncio.to_thread(th.join)
    assert "error" not in out, out
    acct = auth.load()
    assert srv.used == ["rt-0"]                   # one refresh; rt-0 was never presented twice
    assert acct["refresh_token"] == "rt-1" and tok == "at-1" and out["result"] == "at-1"
    assert "models" not in acct and auth.models()          # cache is separate and intact
    assert "token" not in (tmp_path / "chatgpt" / "models.json").read_text(encoding="utf-8")
    assert not (tmp_path / "chatgpt" / "refresh.lock").exists()


async def test_logout_during_refresh_never_resurrects_tokens(tmp_path):
    srv = SlowOAuth()
    auth = seed(tmp_path, srv)
    auth.save_models([{"slug": "m"}])

    async def cli_logout(a2: ChatGPTAuth):
        await asyncio.sleep(0.1)                 # while the Core's refresh is in flight
        return await a2.sign_out()
    th, out = other_process(tmp_path, srv, cli_logout)
    tok = await auth.access_token()
    await asyncio.to_thread(th.join)
    assert "error" not in out, out
    assert tok == "at-1"
    acct = auth.load()
    assert "refresh_token" not in acct and "access_token" not in acct and not auth.status()["signed_in"]
    assert srv.revoked == ["rt-1"]                # the latest rotated token was revoked, not a stale one
    assert auth.models() == []
    with pytest.raises(ChatGPTAuthError) as e:
        await auth.access_token()
    assert e.value.code == "CHATGPT_SIGNED_OUT"


async def test_logout_before_refresh_and_same_process_tasks(tmp_path):
    srv = SlowOAuth(delay=0.1)
    auth = seed(tmp_path, srv)

    async def late_refresh():
        await asyncio.sleep(0.02)
        return await auth.access_token()
    res = await asyncio.gather(auth.sign_out(forget=False), late_refresh(), return_exceptions=True)
    assert isinstance(res[1], ChatGPTAuthError) and res[1].code == "CHATGPT_SIGNED_OUT"
    assert srv.used == [] and "refresh_token" not in auth.load() and auth.load()["client_id"] == "cid"
    await auth.mark_revoked()
    assert "refresh_token" not in auth.load()


# ============================================================== CR-08 link roles
async def hello(core, client):
    ws = await connect(f"ws://127.0.0.1:{core.cfg.link.port}")
    payload = {"token": core.link.token}
    if client is not None:
        payload["client"] = client
    await ws.send(json.dumps({"type": "hello", "payload": payload}))
    return ws


async def recv_type(ws, t, timeout=3):
    while True:
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if m["type"] == t:
            return m["payload"]


async def test_cli_cannot_confirm_or_talk(core):
    core.memory.add("secret plan")
    f = await open_face(core)
    cli = await hello(core, "cli")
    assert (await recv_type(cli, "hello.ok"))["role"] == "cli"
    await f.send("user.text", {"text": "תשכח את הכל"})
    req = await f.wait("confirm.request")
    await cli.send(json.dumps({"type": "confirm.reply", "payload": {"id": req["id"], "approved": True}}))
    assert (await recv_type(cli, "error"))["code"] == "LINK_FORBIDDEN"
    await cli.send(json.dumps({"type": "user.text", "payload": {"text": "תשכח את הכל"}}))
    assert (await recv_type(cli, "error"))["code"] == "LINK_FORBIDDEN"
    assert core.memory.count() == 1               # the CLI's approval was never dispatched
    await f.send("confirm.reply", {"id": req["id"], "approved": False})
    await f.wait("assistant.text")
    assert core.memory.count() == 1
    await cli.close()


@pytest.mark.parametrize("name", ["admin", "Godot", "", None])
async def test_spoofed_client_name_is_rejected(core, name):
    ws = await hello(core, name)
    with pytest.raises(websockets.ConnectionClosed) as e:
        await ws.recv()
    assert e.value.rcvd.code == 4403


async def test_face_cannot_shut_down(core):
    f = await open_face(core)
    await f.send("core.shutdown")
    assert (await f.wait("error"))["code"] == "LINK_FORBIDDEN"
    await asyncio.sleep(0.1)
    assert not core.stop_event.is_set()


def test_role_tables_cover_device_events():
    assert set(INBOUND) == set(DEVICE_EVENTS) and DEVICE_EVENTS <= ALLOWED["face"]
    assert "core.shutdown" not in ALLOWED["face"] and not {"confirm.reply", "user.text"} & ALLOWED["cli"]


async def test_dispatcher_receives_client_identity():
    seen = []

    async def on_message(msg, client):
        seen.append((msg["type"], client.role, client.id))
    link = GodotLink("127.0.0.1", free_port(), "tok", Health(), on_message)
    await link.start()
    try:
        ws = await connect(f"ws://127.0.0.1:{link.port}")
        await ws.send(json.dumps({"type": "hello", "payload": {"token": "tok", "client": "godot"}}))
        await recv_type(ws, "hello.ok")
        fid = link.face_client_id()
        await ws.send(json.dumps({"type": "confirm.reply", "payload": {"id": "1", "approved": True}}))
        for _ in range(50):
            if seen:
                break
            await asyncio.sleep(0.02)
        assert seen == [("confirm.reply", "face", fid)] and fid
        await ws.close()
        for _ in range(50):
            if link.face_client_id() is None:
                break
            await asyncio.sleep(0.02)
        assert link.face_client_id() is None
    finally:
        await link.stop()


# ============================================================== CR-09 logs
def test_nested_private_strings_never_reach_the_log(tmp_path):
    path = logs.setup(tmp_path / "logs", console=False)
    logs.info("device", "DEVICE_EVENT", "device event x",
              payload={"phrase": "secret words", "x": [{"text": "hidden diary"}, {"deep": {"Email": "me@ex.com"}}],
                       "nested": {"access_token": "tok123456", "list": [["transcript-ish", {"query": "my query"}]]},
                       "note": "bearer abcdefghij"},
              items=[{"content": "private body"}], id="action", n=3)
    logs.info("device", "DEVICE_EVENT", "y", payload={"message": {"body": "mail body"}})
    for h in logs._logger.handlers:
        h.flush()
    raw = path.read_text(encoding="utf-8")
    for secret in ("secret words", "hidden diary", "me@ex.com", "tok123456", "my query", "private body",
                   "mail body", "abcdefghij"):
        assert secret not in raw, secret
    line = json.loads(raw.splitlines()[0])
    assert line["id"] == "action" and line["n"] == 3 and line["level"] == "INFO"     # safe values kept
    assert line["payload"]["phrase"] == "<12 chars>" and line["payload"]["x"][1]["deep"]["Email"] == "<9 chars>"


def test_safe_fields_allow_list():
    p = {"id": "action", "muted": True, "phrase": "secret words", "blob": {"text": "x"}, "level": 0.5,
         "label": "y" * 200}
    out = logs.safe_fields(p, allow=("id", "muted", "level", "phrase", "label"))
    assert out["id"] == "action" and out["muted"] is True and out["level"] == 0.5
    assert "phrase" not in out and "blob" not in out and out["label"] == "<200 chars>"
    assert set(out["omitted_keys"]) == {"phrase", "blob"}
    assert logs.safe_fields("not a dict") == {}
    assert "secret" not in json.dumps(out)
