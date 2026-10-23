"""`jarvis ask --full` / `jarvis tell`: ONE message to the running Core through the same chat protocol as `jarvis chat`.
Driven against a REAL Core (real link, approvals) with a fake model, like test_chat.py: offline, silent, no sleeps."""
import asyncio
import io
import json

import pytest

from conftest import WAIT_S
from jarves import chat_cli, cli
from jarves.chat_cli import AskSession, LinkTransport
from test_chat import EOF_, Script, finish, install, spy, stream  # noqa: F401  (spy is a fixture)


class FakeStdin(io.StringIO):
    def __init__(self, text="", tty=False):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty


async def ask(core, text, *, as_json=False, speak=False, stdin=None, timeout=WAIT_S):
    out, err = io.StringIO(), io.StringIO()
    code = await finish(asyncio.create_task(chat_cli.run_ask(
        core.home, core.cfg.link.port, text, as_json=as_json, speak=speak, timeout=timeout,
        stdin=stdin or FakeStdin(), stdout=out, stderr=err)))
    return code, out.getvalue(), err.getvalue()


async def test_answer_streamed_tool_lines_and_exit_zero_and_silent(core, spy):
    async def script(agent, text, on_delta):
        await core.registry.call("get_time", {})
        await stream(on_delta, "יש ", "שתי הודעות")
        return "יש שתי הודעות"
    agent = install(core, script)
    code, out, err = await ask(core, "מה חדש בוואטסאפ")
    assert code == 0 and agent.seen == ["מה חדש בוואטסאפ"]
    assert "jarvis> יש שתי הודעות" in out and "  > get_time  ok [ok]" in out
    assert out.index("get_time") < out.index("יש שתי")
    assert spy == {"speak": [], "face": [], "chime": []} and core.tts.spoken == []


async def test_unstreamed_answer_is_printed_once(core, spy):
    async def script(agent, text, on_delta):
        return "plain answer"
    install(core, script)
    code, out, err = await ask(core, "hi")
    assert code == 0 and out.count("plain answer") == 1


async def test_json_shape_and_stdout_carries_only_the_object(core, spy):
    async def script(agent, text, on_delta):
        await core.registry.call("get_time", {})
        await core.registry.call("no_such_tool", {})
        return "done"
    install(core, script)
    code, out, err = await ask(core, "go", as_json=True)
    data = json.loads(out)                                    # the whole stdout is one JSON object
    assert code == 0 and data["ok"] is True and data["text"] == "done" and data["code"]
    assert {"name": "get_time", "ok": True, "code": "ok"} in data["tools"]
    assert [t["ok"] for t in data["tools"] if t["name"] == "no_such_tool"] == [False]
    assert set(data) == {"ok", "text", "tools", "code"}
    assert "get_time" in err                                  # the human lines go to stderr


async def test_core_internal_failure_exits_non_zero(core, spy):
    async def script(agent, text, on_delta):
        raise RuntimeError("boom")
    install(core, script)
    code, out, err = await ask(core, "x", as_json=True)
    data = json.loads(out)
    assert code == 1 and data["ok"] is False and data["code"] == "CORE_INTERNAL"


async def test_timeout_cancels_and_exits_non_zero(core, spy):
    started = asyncio.Event()

    async def script(agent, text, on_delta):
        started.set()
        await asyncio.sleep(3600)
    install(core, script)
    code, out, err = await ask(core, "slow", as_json=True, timeout=0.3)
    assert code == 1 and json.loads(out)["code"] == "TIMEOUT" and started.is_set()


async def test_non_interactive_stdin_denies_an_approval(core, spy):
    got = []

    async def script(agent, text, on_delta):
        ok = await core.registry.ask("Send a message to Dana?", title="Send a message", kind="message", risk="high",
                                     details={"recipient": "Dana", "text": "hello"})
        got.append(ok)
        return "sent" if ok else "not sent"
    install(core, script)
    code, out, err = await ask(core, "send it", stdin=FakeStdin("y\n"))
    assert got == [False] and "not sent" in out.splitlines()[-1]  # a piped "y" is never an approval
    assert "approval denied" in err and code == 0             # the answer itself was normal


async def test_interactive_terminal_can_approve(core, spy):
    got = []

    async def script(agent, text, on_delta):
        got.append(await core.registry.ask("Send?", title="Send", kind="message", risk="high", details={}))
        return "sent" if got[-1] else "not sent"
    install(core, script)
    out = io.StringIO()
    tr = await LinkTransport.connect(core.home, core.cfg.link.port)
    session = AskSession(tr, Script("y", EOF_), out, silent=True, verbose=True)
    res = await finish(asyncio.create_task(session.ask("send it")))
    assert got == [True] and res["ok"] and res["text"] == "sent"


async def test_speak_flag_is_off_by_default_and_turn_is_silent(core, spy):
    sent = []
    real = LinkTransport.send

    async def send(self, t, payload=None):
        if t == "chat.turn":
            sent.append(payload)
        await real(self, t, payload)
    LinkTransport.send = send
    try:
        async def script(agent, text, on_delta):
            return "ok"
        install(core, script)
        await ask(core, "hi")
    finally:
        LinkTransport.send = real
    assert sent[0]["silent"] is True and sent[0]["voice"] is False


def test_text_comes_from_words_or_piped_stdin():
    assert chat_cli.read_ask_text(["a", "b"], FakeStdin("ignored")) == "a b"
    assert chat_cli.read_ask_text([], FakeStdin("from pipe\n")) == "from pipe"
    assert chat_cli.read_ask_text([], FakeStdin("typed", tty=True)) == ""


def test_cli_parse_full_tell_and_json(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "cmd_ask_full", lambda a, c, h: seen.append((a.cmd, a.text, a.json, a.speak, a.timeout)) or 0)
    assert cli.main(["ask", "--full", "--json", "hello", "world"]) == 0
    assert cli.main(["tell", "--speak", "--timeout", "9", "hi"]) == 0
    assert cli.main(["ask", "--full"]) == 0
    assert seen == [("ask", ["hello", "world"], True, False, 300.0), ("tell", ["hi"], False, True, 9.0),
                    ("ask", [], False, False, 300.0)]


def test_no_text_and_core_unreachable_exit_non_zero(monkeypatch, home, cfg):
    monkeypatch.setattr("sys.stdin", FakeStdin(""))
    assert cli.main(["ask", "--full"]) == 2
    monkeypatch.setattr(cli, "ensure_core", lambda *a, **k: False)
    assert cli.main(["tell", "hello"]) == 1


def test_plain_ask_stays_offline_and_points_to_full(monkeypatch, capsys, home, cfg):
    from jarves.brain import Reply
    called = []
    monkeypatch.setattr(cli, "cmd_ask_full", lambda *a: called.append(1) or 0)
    monkeypatch.setattr("jarves.app.make_agent", lambda *a, **k: object())

    class B:
        def __init__(self, *a): pass

        async def respond(self, text):
            return Reply("I can't check your WhatsApp messages", "ai")
    monkeypatch.setattr("jarves.brain.Brain", B)
    rc = cli.main(["ask", "check", "whatsapp"])
    cap = capsys.readouterr()
    assert not called and rc == 0
    assert "I can't check" in cap.out and "--full" in cap.err
    with pytest.raises(SystemExit):
        cli.main(["ask", "--help"])
    assert "--full" in capsys.readouterr().out


def test_parity_lists_the_one_shot_commands_and_they_parse(monkeypatch):
    from jarves import cli_parity
    assert cli_parity.FEATURES["text_chat"] == "chat" and cli_parity.ALSO["text_chat"] == ("ask --full", "tell")
    monkeypatch.setattr(cli, "cmd_ask_full", lambda a, c, h: 0)
    for cmd in cli_parity.ALSO["text_chat"]:
        assert cli.main([*cmd.split(), "hi"]) == 0
