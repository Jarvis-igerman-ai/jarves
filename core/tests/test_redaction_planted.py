"""SW-V4 package B: planted secrets and personal data at each sink WRITER, in both privacy modes (unit level).

The corpus and the scanner (raw, JSON-escaped, URL-encoded, base64, UTF-16LE, Hebrew NFC/NFD) are the ones of
tools/redaction_audit.py; the system-level run of a real Core is tests/test_redaction_audit.py.
Rules: a secret never reaches any sink in either mode; content/pii never reach a diagnostic sink in privacy mode;
in development mode the content IS written (positive control: the test would notice a sink that drops everything).
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import sys
import threading
import urllib.parse
from pathlib import Path

import pytest

from jarves import approvals, cli, context_trace, diagbundle, logs, logview, privacy, selfcheck
from jarves.health import Health

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
from tools import redaction_audit as ra  # noqa: E402

MODES = [pytest.param(True, id="privacy_on"), pytest.param(False, id="privacy_off")]


def classes(data: bytes | str) -> dict[str, list[str]]:
    data = data.encode("utf-8") if isinstance(data, str) else data
    out: dict[str, list[str]] = {}
    for key, enc in ra.scan_bytes(data):
        out.setdefault(key.split(":")[0], []).append(f"{key}@{enc}")
    return out


def check(data, private: bool, *, expect_content: bool = True) -> None:
    c = classes(data)
    assert not c.get("secret"), c["secret"]
    if private:
        assert not c.get("content") and not c.get("pii"), c
    elif expect_content:                                   # positive control
        assert c.get("content") or c.get("pii"), "planted content never reached this sink"


@pytest.fixture
def logdir(tmp_path):
    yield tmp_path / "logs"
    logs.set_privacy(True)


def flush():
    for h in logs._logger.handlers:
        h.flush()


# ---------------------------------------------------------------------------------------------- the scanner itself
@pytest.mark.parametrize("enc", ["raw", "json", "url", "base64", "utf16le", "nfd", "nfc"])
def test_the_scanner_finds_every_encoding(enc):
    """The audit's sensitivity: each encoding of a planted value is found inside other text."""
    for value in (ra.SECRETS["openai_key"], ra.PII["hebrew_name"], ra.CONTENT["niqqud"]):
        if enc == "raw":
            blob = ("x " + value + " y").encode("utf-8")
        elif enc == "json":
            blob = json.dumps({"a": "x " + value}).encode("ascii")          # ensure_ascii: \\uXXXX for Hebrew
        elif enc == "url":
            blob = ("q=" + urllib.parse.quote("x " + value)).encode("ascii")
        elif enc == "base64":
            blob = b"data:" + base64.b64encode(("pad-" + value + "-end").encode("utf-8"))
        elif enc == "utf16le":
            blob = ("x " + value).encode("utf-16-le")
        else:
            import unicodedata
            blob = unicodedata.normalize(enc.upper(), "x " + value).encode("utf-8")
        assert ra.scan_bytes(blob), (enc, value)
    assert not ra.scan_bytes("<52 chars> pii:3f2a sk-*** <email> <phone>".encode())    # masks are not hits


def test_redact_removes_every_planted_secret_shape():
    out = logs.redact(ra.SECRET_ONLY)
    assert not classes(out).get("secret"), out
    for v in ra.SECRETS.values():
        if v not in (ra.SECRETS["password"], ra.SECRETS["user_profile"], ra.SECRETS["dashboard_link"],
                     ra.SECRETS["bridge_bearer"]):
            assert logs.has_secret(v), v                       # backups leave such a file out
    assert not logs.has_secret("Bearer *** sk-*** basic configuration of the voice")
    assert "basic configuration" in logs.redact("the basic configuration file")    # prose is not a credential


# ---------------------------------------------------------------------------------------------- jarves.log / console
@pytest.mark.parametrize("private", MODES)
def test_every_registered_field_class_in_the_file_and_console(logdir, private):
    path = logs.setup(logdir, privacy=private, console=False)
    buf = io.StringIO()
    con = logging.StreamHandler(buf)
    con.setFormatter(logs.ConsoleFormatter(color=False))
    logs._logger.addHandler(con)
    try:
        for name, cls in logs.FIELD_CLASS.items():
            if name in ("level", "component", "code"):
                continue
            value = ra.SECRET_ONLY if cls == logs.SAFE else ra.MIXED
            logs.info("planted", "PLANTED_FIELD", "a planted field", **{name: value})
        logs.info("planted", "PLANTED_UNKNOWN", "an unregistered field", never_registered=ra.MIXED,
                  nested_safe_parent={"text": ra.MIXED, "items": [{"email": ra.PII["email"]}]})
        logs.warn("planted", "PLANTED_ERR", "a provider error", **logs.error_fields(RuntimeError(ra.MIXED)))
        try:
            raise ValueError(ra.MIXED)
        except ValueError:
            logs.error("planted", "PLANTED_EXC", "an exception with private text", exc=True)
        flush()
    finally:
        logs._logger.removeHandler(con)
    raw = path.read_text(encoding="utf-8")
    check(raw, private)
    check(buf.getvalue(), private)
    exc = json.loads([x for x in raw.splitlines() if "PLANTED_EXC" in x][0])
    assert exc["exc_type"] == "ValueError" and exc["exc_frames"] and "test_redaction_planted.py" in exc["exc_frames"][-1]


@pytest.mark.parametrize("private", MODES)
def test_third_party_loggers_and_crash_hooks(logdir, private, monkeypatch):
    path = logs.setup(logdir, privacy=private, console=False)
    logging.getLogger("httpx").warning(f"HTTP Request: GET https://x/?access_token={ra.SECRETS['google_access']}"
                                       f"&t={ra.SECRETS['dashboard_link']} {ra.MIXED}")
    logging.getLogger("websockets.client").error(ra.MIXED)
    logging.getLogger("httpx").info(f"GET https://x/?token={ra.SECRETS['dashboard_link']}")   # INFO: not even kept
    t = threading.Thread(target=lambda: (_ for _ in ()).throw(KeyError(ra.MIXED)), name="planted")
    t.start()
    t.join()
    err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err)
    try:
        raise RuntimeError(ra.MIXED)
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    loop = asyncio.new_event_loop()
    try:
        logs.asyncio_handler(loop, {"message": ra.MIXED, "exception": OSError(ra.MIXED)})
    finally:
        loop.close()
    flush()
    raw = path.read_text(encoding="utf-8")
    check(raw, private)
    check(err.getvalue(), True, expect_content=False)          # the terminal line: type only, both modes
    recs = [json.loads(x) for x in raw.splitlines()]
    assert {r["crash_kind"] for r in recs if r["code"] == "CRASH"} == {"thread", "main", "asyncio"}
    assert any(r["code"] == "LIB_LOG" and r["component"] == "lib:httpx" for r in recs)
    assert not any("token=" in json.dumps(r) and "***" not in json.dumps(r) for r in recs)


def test_faulthandler_dump_has_frames_only(tmp_path):
    import faulthandler
    fh = logs.enable_faulthandler(tmp_path)
    try:
        faulthandler.dump_traceback(fh, all_threads=False)
        fh.flush()
    finally:
        faulthandler.disable()
        fh.close()
    text = (tmp_path / "crash.txt").read_text(encoding="utf-8")
    assert "File" in text and "line" in text and not classes(text)


# ---------------------------------------------------------------------------------------------- store.db, traces
@pytest.mark.parametrize("private", MODES)
def test_history_store_and_context_traces(tmp_path, private):
    from jarves import store as store_mod
    from jarves.config import Config
    cfg = Config()
    cfg.log.privacy = private
    logs.setup(tmp_path / "logs", privacy=private, console=False)
    try:
        sink = store_mod.attach(tmp_path, cfg)
        logs.info("live", "LIVE_STARTED", "GPT-Live session started", model="m", backend_model="b")
        logs.info("live", "LIVE_USER_SAID", "you said", text=ra.MIXED)
        logs.info("live", "LIVE_TOOL_CALL", "tool", tool="t", args={"to": ra.PII["email"], "body": ra.MIXED})
        logs.info("skills", "TOOL_RESULT", "tool finished", tool="t", ok=False, result_code="X")
        logs.warn("live", "LIVE_ERROR", "provider", provider_error=ra.MIXED, error_code="bad")
        logs.info("live", "VOICE_ENDED", "voice session ended", reason="idle", voice_seconds=3.0)
        rec = context_trace.recorder(tmp_path, cfg)
        tr = context_trace.Trace(rec, "live", private)
        tr.save_start(context_trace.snapshot({"instructions": ra.MIXED}, private=private))
        tr.event("user", "user said", ra.MIXED, data={"arguments": ra.MIXED, "tool": "t"})
        tr.end("done")
        rec.flush()
    finally:
        logs.remove_sink(sink) if sink is not None else None
        sink.close() if sink is not None else None
        context_trace.close_home(tmp_path)
        logs.set_privacy(True)
    data = b"".join(p.read_bytes() for p in tmp_path.glob("store.db*"))
    check(data, private)


# ---------------------------------------------------------------------------------------------- functional sinks
@pytest.mark.parametrize("private", MODES)
def test_approvals_audit(tmp_path, private, monkeypatch):
    monkeypatch.setattr(logs, "_privacy", private)
    rec = {"ts": 1, "kind": "email", "title": f"email {ra.PII['english_name']}", "prompt": "<4 chars>",
           "details": {"to": ra.PII["email"], "body": ra.MIXED, "subject": ra.CONTENT["calendar_title"]}}
    approvals.append_audit(tmp_path / "audit.jsonl", rec)
    shell = {**rec, "kind": "shell", "prompt": f"curl -H 'Authorization: Bearer {ra.SECRETS['bridge_bearer']}'"}
    approvals.append_audit(tmp_path / "audit.jsonl", shell)
    raw = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    c = classes(raw.splitlines()[0])
    assert not c.get("secret") and (not private or not (c.get("content") or c.get("pii")))
    assert not classes(raw).get("secret") and "curl" in raw          # a shell command stays readable (security)


@pytest.mark.parametrize("private", MODES)
def test_health_text(tmp_path, private, monkeypatch):
    monkeypatch.setattr(logs, "_privacy", private)
    h = Health(tmp_path / "health.json")
    h.set("ai", "degraded", f"ChatGPT plan ({ra.PII['email']}) {ra.SECRETS['openai_key']} {ra.PII['phone_intl']}",
          f"sign in again as {ra.PII['email']}")
    for blob in ((tmp_path / "health.json").read_text(encoding="utf-8"), json.dumps(h.snapshot()),
                 json.dumps(h.components_view())):
        check(blob, private)


def test_task_history_never_keeps_a_secret(tmp_path, monkeypatch):
    from jarves.tasks import LookupResult, TaskManager

    async def go(private):
        monkeypatch.setattr(logs, "_privacy", private)
        done = asyncio.Event()

        async def runner(d, on_progress=None, *, reservation=None):
            return LookupResult(ra.MIXED, 1, {"input_tokens": 3})
        tm = TaskManager(runner, store=tmp_path / f"t{private}.jsonl", on_finished=lambda t: done.set())
        await tm.start(ra.MIXED)
        await asyncio.wait_for(done.wait(), 45)
        await tm.shutdown()
        check((tmp_path / f"t{private}.jsonl").read_text(encoding="utf-8"), private)
    for private in (True, False):
        asyncio.run(go(private))


# ---------------------------------------------------------------------------------------------- re-masking old data
OLD_LINES = [json.dumps({"ts": "2026-10-19T10:00:00+00:00", "level": "ERROR", "component": "voice", "code": "LIVE_ERROR",
                         "msg": f"Live error bad: {ra.MIXED}", "exc": f"ValueError: {ra.MIXED}", "text": ra.MIXED,
                         "status": 400}, ensure_ascii=False),
             f"not json at all: {ra.MIXED}"]


def test_mask_existing_logs_and_the_log_view_handle_old_free_text_lines(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "jarves.log").write_text("\n".join(OLD_LINES) + "\n", encoding="utf-8")
    shown = logview.present({**json.loads(OLD_LINES[0]), "_ts": 1.0, "_file": "jarves.log", "_line": 1}, True)
    check(json.dumps(shown, ensure_ascii=False), True)
    assert shown["code"] == "LIVE_ERROR" and shown["status"] == 400
    assert privacy.mask_logs(tmp_path) == 2
    raw = (tmp_path / "logs" / "jarves.log").read_text(encoding="utf-8")
    check(raw, True)
    assert json.loads(raw.splitlines()[1]) == {"unparsed": f"<{len(OLD_LINES[1])} chars>"}


def test_issue_15_repro_2_private_line_masks_msg_and_text():
    """#15 repro 2: `cli._private_line` of {"msg": private, "text": private} - both are masked now."""
    line = json.dumps({"msg": "my secret medical phrase", "text": "my secret medical phrase"})
    out = cli._private_line(line)
    assert "medical" not in out
    d = json.loads(out)
    assert d["msg"].startswith("<") and d["text"].startswith("<")
    assert cli._private_line("not json: my secret medical phrase") == ""        # dropped, never copied


def test_ticket_draft_and_evidence_lines_quote_only_templates():
    old = {"_t": 1.0, "level": "ERROR", "component": "voice", "code": "X", "msg": ra.MIXED}
    new = {**old, "msg": "the voice provider reported an error", "v": logs.FORMAT_VERSION}
    assert ra.MIXED[:20] not in selfcheck._line(old) and "the voice provider reported an error" in selfcheck._line(new)
    view = {"id": 1, "started": 1.0, "trigger": "cli", "days": 7, "score": 50, "metrics": {},
            "findings": [{"state": "open", "title": f"crash for {ra.PII['email']}", "fingerprint": "f", "rank": 1,
                          "severity": "high", "fix": "x", "effort": "minutes",
                          "cause": f"key {ra.SECRETS['openai_key']} phone {ra.PII['phone_intl']}",
                          "evidence": [selfcheck._line(old), f"token {ra.SECRETS['telegram_bot']}"]}]}
    d = selfcheck.ticket_draft(view)
    c = classes(json.dumps(d, ensure_ascii=False))
    assert not c.get("secret") and not [h for h in c.get("pii", []) if "email" in h or "phone" in h]


def test_privacy_state_says_development_mode_plainly(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from jarves.config import Config
    cfg = Config()
    cfg.log.privacy = False
    ctx = SimpleNamespace(home=tmp_path, cfg=cfg)
    st = privacy.state(ctx)
    assert st["banner_en"] == "Privacy: off — conversation text is kept in logs" and "פרטיות" in st["banner_he"]
    cfg.log.privacy = True
    assert privacy.state(ctx)["banner_en"] == ""
    cfg.log.privacy = False
    monkeypatch.setenv("JARVES_CLI_LANG", "en")
    assert cli._privacy_note(cfg, tmp_path) == logs.PRIVACY_OFF_NOTE
    monkeypatch.setenv("JARVES_CLI_LANG", "he")
    assert cli._privacy_note(cfg, tmp_path) == logs.PRIVACY_OFF_NOTE_HE
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / logs.PRIVACY_FILE).write_text('{"privacy": true}', encoding="utf-8")
    assert cli._privacy_note(cfg, tmp_path) == ""                  # the runtime switch wins


def test_backups_leave_out_files_with_any_token_shape(tmp_path):
    from jarves import backup
    for name, value in (("a.txt", ra.SECRETS["google_refresh"]), ("b.json", ra.SECRETS["telegram_bot"]),
                        ("c.md", ra.SECRETS["meta_token"]), ("d.toml", ra.SECRETS["dpapi_blob"])):
        p = tmp_path / name
        p.write_text(f"x {value}", encoding="utf-8")
        assert backup._secret_reason(p, name) == "contains a credential", name
    ok = tmp_path / "notes.md"
    ok.write_text("the basic configuration, Bearer ***", encoding="utf-8")
    assert backup._secret_reason(ok, "notes.md") is None


def test_diag_sanitize_is_the_shared_rule():
    s = diagbundle.sanitize(f"{ra.SECRET_ONLY} {ra.PII['email']} {ra.PII['phone_intl']}")
    c = classes(s)
    assert not c.get("secret") and not c.get("pii")


def test_mask_existing_history_also_masks_the_approvals_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "_privacy", False)                      # written in development mode
    rec = {"ts": 1, "kind": "email", "title": f"email {ra.PII['english_name']}", "prompt": ra.MIXED,
           "details": {"to": ra.PII["email"], "body": ra.MIXED}}
    approvals.append_audit(tmp_path / "approvals" / "audit.jsonl", rec)
    assert classes((tmp_path / "approvals" / "audit.jsonl").read_text(encoding="utf-8")).get("pii")
    (tmp_path / "logs").mkdir()
    privacy.mask_logs(tmp_path)
    check((tmp_path / "approvals" / "audit.jsonl").read_text(encoding="utf-8"), True)


def test_crash_file_has_a_size_limit(tmp_path):
    import faulthandler
    (tmp_path / "crash.txt").write_text("x" * (logs.CRASH_MAX_BYTES + 10), encoding="utf-8")
    fh = logs.enable_faulthandler(tmp_path)
    faulthandler.disable()
    fh.close()
    assert (tmp_path / "crash.txt.1").stat().st_size > logs.CRASH_MAX_BYTES
    assert (tmp_path / "crash.txt").stat().st_size == 0
