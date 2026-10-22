"""`jarvis chat`: the terminal text chat, driven by a scripted terminal against a REAL Core (real link, approvals
service and history) with a fake model: no network, no browser, no audio device, no sleeps (every wait is an event)."""
import asyncio
import io
import sqlite3
import time

import pytest

from conftest import WAIT_S, open_face, until
from jarves import approvals as appr
from jarves import chat_cli, cli, logs
from jarves.brain import Brain
from jarves.chat_cli import ChatSession, LinkTransport, StdinLines

EOF_ = object()
CTRL_C = object()


class Block:
    """A terminal read that stays open until the test (or nobody) answers it."""
    def __init__(self):
        self.future = asyncio.get_running_loop().create_future()


class Script:
    """Scripted stdin with the semantics of StdinLines: an abandoned read stays pending for the next read."""
    def __init__(self, *items):
        self.items = list(items)
        self.prompts: list[tuple[str, bool]] = []
        self._pending = None

    async def read(self, prompt, *, secret=False):
        self.prompts.append((prompt, secret))
        if self._pending is not None:
            fut, self._pending = self._pending, None
            return await self._wait(fut)
        item = self.items.pop(0)
        if callable(item):
            item = item()
        if isinstance(item, Block):
            self._pending = item.future
            return await self._wait(item.future, keep=True)
        if item is EOF_:
            raise EOFError
        if item is CTRL_C:
            raise KeyboardInterrupt
        return item

    async def _wait(self, fut, keep=False):
        if keep:
            self._pending = fut
        try:
            return await asyncio.shield(fut)
        finally:
            if fut.done():
                self._pending = None


class FakeAgent:
    """Stands in for the model: `script(agent, text, on_delta)` is awaited for every user message."""
    def __init__(self, core, script=None):
        self.core, self.script = core, script
        self.seen: list[str] = []
        self.seeded = None
        self.resets = 0
        self.cancelled = asyncio.Event()

    def available(self):
        return True, "ok", ""

    def describe(self):
        return "fake model (test)"

    def reset_conversation(self):
        self.resets += 1

    async def seed_history(self, turns):
        self.seeded = list(turns)

    async def reply(self, text, on_delta=None):
        self.seen.append(text)
        return await self.script(self, text, on_delta)


async def stream(on_delta, *pieces):
    for p in pieces:
        on_delta(p)
        await asyncio.sleep(0)


def install(core, script):
    agent = FakeAgent(core, script)
    core.agent = agent
    core.brain = Brain(core.registry, agent)
    return agent


async def start_chat(core, *lines, silent=False, verbose=False):
    out = io.StringIO()
    tr = await LinkTransport.connect(core.home, core.cfg.link.port)
    sc = Script(*lines)
    session = ChatSession(tr, sc, out, silent=silent, verbose=verbose,
                          history=chat_cli.History(core.home / "chat_history.txt"))
    return session, sc, out


async def finish(task):
    return await asyncio.wait_for(task, WAIT_S)


@pytest.fixture
def spy(core, monkeypatch):
    """Everything that could make noise or light the face during a chat. Also pins the clock to daytime: the real
    quiet hours (22:00-07:00) must not decide a test; the quiet-hours tests switch them on explicitly."""
    monkeypatch.setattr(core.ops.alerts, "quiet_now", lambda: False)
    calls = {"speak": [], "face": [], "chime": []}
    real_face = core.set_face

    async def speak(text, cache=False):
        calls["speak"].append(text)

    async def face(state):
        calls["face"].append(state)
        await real_face(state)

    async def chime(*a, **k):
        calls["chime"].append(a)
    core.speak = speak
    core.set_face = face
    core._chime = chime
    return calls


class PopupSpy:
    """A second approval place (the real popup is a window): records open/close, can answer first."""
    name = "popup"

    def __init__(self, core):
        self.core = core
        self.opened = asyncio.Event()
        self.closed: list = []
        self.requests: list = []

    def available(self, a):
        return True

    async def open(self, a):
        self.requests.append(a)
        self.opened.set()

    async def close(self, a):
        self.closed.append((a.id, a.state, a.channel))


# ---------------------------------------------------------------------------------------------- basics
async def test_streamed_hebrew_round_trip_with_tool_line_and_no_speech(core, spy):
    async def script(agent, text, on_delta):
        res = await core.registry.call("get_time", {})          # one tool call -> one short line
        assert res.ok
        await stream(on_delta, "שלום ", "עולם", "!")
        return "שלום עולם!"
    agent = install(core, script)
    session, sc, out = await start_chat(core, "מה שלומך היום", EOF_)
    assert await finish(asyncio.create_task(session.run())) == 0
    text = out.getvalue()
    assert agent.seen == ["מה שלומך היום"]                        # Hebrew in, unchanged
    assert "jarvis> שלום עולם!" in text                           # logical order, never reversed
    assert "  > get_time  ok" in text
    assert text.index("get_time") < text.index("שלום")
    assert spy == {"speak": [], "face": [], "chime": []}          # text mode: nothing speaks, nothing lights up
    assert core.tts.spoken == []
    assert "you [text]> " in sc.prompts[0][0]


async def test_reply_without_streaming_is_shown_once_and_stored_like_a_typed_message(core, spy):
    async def script(agent, text, on_delta):
        return "answer: " + text
    install(core, script)
    session, sc, out = await start_chat(core, "plain question", EOF_)
    await finish(asyncio.create_task(session.run()))
    assert out.getvalue().count("jarvis> answer: plain question") == 1
    assert core.history is not None
    # the same core REPLY record the dashboard/face text path writes; the history store files it as "typed"
    def stored():
        con = sqlite3.connect(f"file:{(core.home / 'store.db').as_posix()}?mode=ro", uri=True)
        try:
            return con.execute("SELECT s.kind FROM sessions s JOIN turns t ON t.session_id = s.id").fetchall()
        except sqlite3.Error:
            return []
        finally:
            con.close()
    rows = await until(stored)
    assert rows[0][0] == "typed"


async def test_multiline_input_and_history_file(core, spy, tmp_path):
    async def script(agent, text, on_delta):
        return "ok"
    agent = install(core, script)
    session, sc, out = await start_chat(core, "first line \\", "second line", '"""', "block a", "block b", '"""',
                                        "/help", EOF_)
    await finish(asyncio.create_task(session.run()))
    assert agent.seen == ["first line \nsecond line", "block a\nblock b"]
    hist = (core.home / "chat_history.txt").read_text(encoding="utf-8")
    assert "first line \nsecond line" in hist and "block a\nblock b" in hist
    assert "/exit" in out.getvalue()                               # /help lists the commands


async def test_stdin_lines_reads_a_pipe_and_ends_with_eof():
    lines = StdinLines(io.StringIO("one\nשתיים\n"), io.StringIO())
    assert await lines.read("> ") == "one"
    assert await lines.read("> ") == "שתיים"
    with pytest.raises(EOFError):
        await lines.read("> ")


# ---------------------------------------------------------------------------------------------- approvals
async def _approval_script(core, answer_holder):
    async def script(agent, text, on_delta):
        ok = await core.registry.ask("Send a message to Dana Levi <dana@example.com>?", title="Send a message",
                                     kind="message", risk="high",
                                     details={"recipient": "Dana Levi <dana@example.com>", "text": "hello there",
                                              "command": "export KEY=sk-abcdefghijklmnop123456"})
        answer_holder.append(ok)
        return "sent" if ok else "not sent"
    return script


@pytest.mark.parametrize("typed,expect", [("y", True), ("yes", True), ("כן", True), ("n", False), ("", False)])
async def test_approval_answered_in_the_terminal(core, spy, typed, expect):
    got = []
    install(core, await _approval_script(core, got))
    session, sc, out = await start_chat(core, "send it", typed, EOF_)
    await finish(asyncio.create_task(session.run()))
    text = out.getvalue()
    assert got == [expect]
    assert ("jarvis> sent" if expect else "jarvis> not sent") in text
    assert "Send a message?" in text and "hello there" in text      # the popup's plain description, in the terminal
    assert "Dana" in text and "Levi" not in text and "dana@example.com" not in text   # first name only
    assert "sk-abcdefghijklmnop123456" not in text and "sk-***" in text               # secrets never printed
    assert spy["speak"] == []                                        # no spoken approval in text mode
    hist = appr.read_history(core.home, 5)
    assert hist[-1]["channel"] == "terminal" and hist[-1]["decision"] == ("approved" if expect else "denied")


async def test_popup_answer_first_closes_the_terminal_request(core, spy):
    popup = PopupSpy(core)
    core.approvals.channels.append(popup)
    got = []
    install(core, await _approval_script(core, got))
    block = Block()
    session, sc, out = await start_chat(core, "send it", block, "", EOF_)
    run = asyncio.create_task(session.run())
    await asyncio.wait_for(popup.opened.wait(), WAIT_S)
    aid = popup.requests[0].id
    await until(lambda: sc.prompts[-1][0].startswith("Approve?"))     # the terminal is asking too
    ok, why = core.approvals.answer(aid, True, channel="popup")      # the owner clicked Approve in the popup
    assert ok, why
    await until(lambda: "Answered elsewhere" in out.getvalue())      # the terminal noticed the popup's answer
    block.future.set_result("")                                      # the abandoned read ends later: nothing is lost
    assert await finish(run) == 0
    text = out.getvalue()
    assert got == [True] and "jarvis> sent" in text
    assert "Answered elsewhere: approved (popup)" in text
    assert popup.closed and popup.closed[0][1:] == ("approved", "popup")
    assert appr.read_history(core.home, 3)[-1]["channel"] == "popup"


async def test_terminal_answer_closes_the_popup(core, spy):
    popup = PopupSpy(core)
    core.approvals.channels.append(popup)
    got = []
    install(core, await _approval_script(core, got))
    session, sc, out = await start_chat(core, "send it", "n", EOF_)
    await finish(asyncio.create_task(session.run()))
    assert got == [False] and popup.closed and popup.closed[0][1:] == ("denied", "terminal")


async def test_typed_code_is_asked_hidden_and_a_wrong_code_is_retried(core, spy):
    got = []

    async def script(agent, text, on_delta):
        ok = await core.registry.ask("Run: format d:", title="Run a dangerous command", kind="shell", risk="high",
                                     typed=True, details={"command": "format d:"})
        got.append(ok)
        return "ran" if ok else "refused"
    install(core, script)

    def code():
        return core.approvals.pending()[0]["typed_code"]
    session, sc, out = await start_chat(core, "do it", "0000", code, EOF_)
    await finish(asyncio.create_task(session.run()))
    assert got == [True]
    secret_reads = [p for p in sc.prompts if p[1]]
    assert len(secret_reads) == 2 and all("hidden" in p[0] for p in secret_reads)
    text = out.getvalue()
    assert "Wrong code" in text and "jarvis> ran" in text


async def test_approvals_command_lists_pending_and_history(core, spy):
    async def script(agent, text, on_delta):
        return "x"
    install(core, script)
    core.approvals._audit_simple("Earlier", "p", "tool", "low", "model", {}, "approved", "dashboard")
    session, sc, out = await start_chat(core, "/approvals", EOF_)
    await finish(asyncio.create_task(session.run()))
    text = out.getvalue()
    assert "Nothing is waiting" in text and "approved" in text and "dashboard" in text


# ---------------------------------------------------------------------------------------------- cancel
async def test_ctrl_c_cancels_the_answer_and_the_chat_continues(core, spy):
    started = asyncio.Event()

    async def script(agent, text, on_delta):
        if text == "slow":
            on_delta("partial ")
            started.set()
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                agent.cancelled.set()
                raise
        return "second answer"
    agent = install(core, script)
    session, sc, out = await start_chat(core, "slow", "again", EOF_)
    run = asyncio.create_task(session.run())
    await asyncio.wait_for(started.wait(), WAIT_S)
    session.interrupt()                                              # what the SIGINT handler calls
    assert await finish(run) == 0
    assert agent.cancelled.is_set()
    text = out.getvalue()
    assert "(cancelled)" in text and "jarvis> second answer" in text


async def test_ctrl_c_at_the_prompt_only_hints_and_clears(core, spy):
    async def script(agent, text, on_delta):
        return "fine"
    install(core, script)
    session, sc, out = await start_chat(core, CTRL_C, "hello", EOF_)
    await finish(asyncio.create_task(session.run()))
    assert "(cleared)" in out.getvalue() and "jarvis> fine" in out.getvalue()
    session2, _, out2 = await start_chat(core, EOF_)
    session2.interrupt()
    assert "Ctrl+D" in out2.getvalue()
    await session2.t.close()


async def test_cancel_during_an_approval_denies_it(core, spy):
    got = []
    popup = PopupSpy(core)
    core.approvals.channels.append(popup)
    install(core, await _approval_script(core, got))
    block = Block()
    session, sc, out = await start_chat(core, "send it", block, "", EOF_)
    run = asyncio.create_task(session.run())
    await asyncio.wait_for(popup.opened.wait(), WAIT_S)
    session.interrupt()
    await until(lambda: "(cancelled)" in out.getvalue())
    block.future.set_result("")
    await finish(run)
    assert not got                                                   # the cancelled turn never got an answer
    assert popup.closed and popup.closed[0][1] == "cancelled"
    assert "(cancelled)" in out.getvalue()


# ---------------------------------------------------------------------------------------------- silent hours
async def test_silent_flag_never_speaks_and_refuses_voice(core, spy):
    async def script(agent, text, on_delta):
        return "quiet answer"
    install(core, script)
    session, sc, out = await start_chat(core, "/voice on", "hello", "/mode", EOF_, silent=True)
    await finish(asyncio.create_task(session.run()))
    text = out.getvalue()
    assert "[silent]" in text and "you [silent]> " in sc.prompts[0][0]
    assert "cannot be turned on" in text and "jarvis> quiet answer" in text
    assert "session: silent" in text
    assert spy == {"speak": [], "face": [], "chime": []} and core.tts.spoken == []


async def test_quiet_hours_make_the_session_silent_and_the_core_enforces_it(core, spy, monkeypatch):
    async def script(agent, text, on_delta):
        return "night answer"
    install(core, script)
    monkeypatch.setattr(core.ops.alerts, "quiet_now", lambda: True)
    session, sc, out = await start_chat(core, "/quiet", "hi", EOF_)
    await finish(asyncio.create_task(session.run()))
    text = out.getvalue()
    assert "[silent] quiet hours 22:00-07:00" in text and "ACTIVE" in text
    assert spy["speak"] == [] and spy["face"] == [] and core.tts.spoken == []
    # a client that insists on voice still gets silence: the Core decides
    tr = await LinkTransport.connect(core.home, core.cfg.link.port)
    await tr.send("chat.hello", {})
    await tr.send("chat.turn", {"id": "t1", "text": "speak please", "voice": True, "silent": False})
    done = None
    while done is None:
        t, p = await asyncio.wait_for(tr.recv(), WAIT_S)
        done = p if t == "chat.done" else None
    assert done["silent"] is True and done["spoken"] is False
    assert spy["speak"] == [] and spy["face"] == []
    await tr.close()


async def test_voice_on_speaks_the_same_session_when_it_is_not_silent(core, spy):
    async def script(agent, text, on_delta):
        return "spoken answer"
    install(core, script)
    session, sc, out = await start_chat(core, "/voice on", "hello", "/voice off", "again", EOF_)
    await finish(asyncio.create_task(session.run()))
    assert "Voice on" in out.getvalue() and "Voice off" in out.getvalue()
    assert spy["speak"] == ["spoken answer"]                          # only the turn while voice was on
    assert "you [voice]> " in "".join(p for p, _ in sc.prompts)


async def test_a_session_turns_silent_when_quiet_hours_begin(core, spy, monkeypatch):
    async def script(agent, text, on_delta):
        if text == "go quiet":
            monkeypatch.setattr(core.ops.alerts, "quiet_now", lambda: True)
        return "answer"
    install(core, script)
    session, sc, out = await start_chat(core, "/voice on", "go quiet", "after", EOF_)
    await finish(asyncio.create_task(session.run()))
    assert "quiet hours began" in out.getvalue()
    assert spy["speak"] == ["answer"]                                 # the first turn only; "after" is silent
    assert "you [silent]> " in sc.prompts[-1][0]


# ---------------------------------------------------------------------------------------------- commands
async def test_status_model_new_and_undo(core, spy):
    async def script(agent, text, on_delta):
        return "ok"
    agent = install(core, script)
    ws = core.workspace
    ws.write("USER.md", "version one\n", by="user")
    ws.write("USER.md", "version two\n", by="user")
    session, sc, out = await start_chat(core, "/status", "/model", "/new", "/undo", "y", "/bogus", "/exit")
    assert await finish(asyncio.create_task(session.run())) == 0
    text = out.getvalue()
    assert "Core: " in text and "fake model (test)" in text
    assert agent.resets == 1 and "New conversation." in text
    assert "Restored: USER.md" in text and ws.read("USER.md") == "version one\n"
    assert "Unknown command" in text


async def test_resume_loads_an_earlier_conversation(core, spy):
    from jarves.store import Store
    now = time.time()
    st = Store(core.home / "store.db")
    st.ingest({"code": "REPLY", "ts": now - 3000, "level": "INFO", "component": "core", "typed": True, "ms": 100,
               "utterance": "מה הבירה של צרפת", "text": "פריז", "session": "s1"})
    st.commit()
    st.close()

    async def script(agent, text, on_delta):
        return "ok"
    agent = install(core, script)
    session, sc, out = await start_chat(core, "/resume", "1", "/exit")
    await finish(asyncio.create_task(session.run()))
    text = out.getvalue()
    assert "מה הבירה של צרפת" in text and "Resumed" in text and "jarvis> פריז" in text
    assert agent.seeded == [("user", "מה הבירה של צרפת"), ("assistant", "פריז")]


async def test_resume_with_nothing_stored(core, spy):
    async def script(agent, text, on_delta):
        return "ok"
    install(core, script)
    session, sc, out = await start_chat(core, "/resume", "/exit")
    await finish(asyncio.create_task(session.run()))
    assert "No earlier conversations" in out.getvalue()


async def test_verbose_shows_codes_and_failed_tools_are_marked(core, spy):
    async def script(agent, text, on_delta):
        await core.registry.call("no_such_tool", {})
        await core.registry.call("get_time", {})
        return "done"
    install(core, script)
    session, sc, out = await start_chat(core, "go", EOF_, verbose=True)
    await finish(asyncio.create_task(session.run()))
    text = out.getvalue()
    assert "no_such_tool  FAILED [TOOL_UNKNOWN]" in text and "get_time  ok [ok]" in text


async def test_core_disconnect_ends_the_chat_with_an_error(core, spy):
    started = asyncio.Event()

    async def script(agent, text, on_delta):
        started.set()
        await asyncio.sleep(3600)
    install(core, script)
    session, sc, out = await start_chat(core, "wait", EOF_)
    run = asyncio.create_task(session.run())
    await asyncio.wait_for(started.wait(), WAIT_S)
    await core.link.stop()
    for ws in list(core.link.clients):
        await ws.close()
    assert await finish(run) == 1
    assert "connection to the Core closed" in out.getvalue()


# ---------------------------------------------------------------------------------------------- link / CLI
async def test_the_face_role_cannot_use_chat_messages(core):
    face = await open_face(core)
    await face.send("chat.turn", {"id": "x", "text": "hi"})
    p = await face.wait("error")
    assert p["code"] == "LINK_FORBIDDEN"
    await face.ws.close()


def test_cli_chat_and_text_alias_parse(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "cmd_chat", lambda a, c, h: seen.append((a.cmd, a.silent, a.verbose, a.wait)) or 0)
    assert cli.main(["chat", "--silent", "-v"]) == 0
    assert cli.main(["text", "--wait", "5"]) == 0
    assert seen == [("chat", True, True, 60.0), ("text", False, False, 5.0)]


def test_ensure_core_starts_the_background_core_once_and_waits(monkeypatch, home, cfg):
    answers = iter([None, None, None, {"core": "ready"}])
    spawned = []
    monkeypatch.setattr(cli, "_ws_call", lambda *a, **k: next(answers))
    monkeypatch.setattr(cli, "_background_spawn", lambda h, show: spawned.append(show))
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    assert cli.ensure_core(home, cfg, wait=30) is True
    assert spawned == [False]


def test_ensure_core_gives_up_when_the_core_never_answers(monkeypatch, home, cfg):
    monkeypatch.setattr(cli, "_ws_call", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_background_spawn", lambda h, show: None)
    import time as _t
    ticks = iter(range(0, 1000, 20))
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    monkeypatch.setattr(_t, "monotonic", lambda: next(ticks))
    assert cli.ensure_core(home, cfg, wait=30) is False


def test_chat_log_messages_are_fixed_literals():
    """Privacy rule: no f-string / formatted message in any logs.* call of the chat modules."""
    import ast
    from pathlib import Path
    for name in ("chat_service.py", "chat_cli.py"):
        tree = ast.parse((Path(chat_cli.__file__).parent / name).read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in
                    ("info", "warn", "error", "debug", "verbose") and getattr(n.func.value, "id", "") == "logs"):
                assert len(n.args) >= 3, (name, n.lineno)
                assert all(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in n.args[:3]), \
                    (name, n.lineno)
