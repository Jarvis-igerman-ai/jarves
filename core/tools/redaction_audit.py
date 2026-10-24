"""SW-V4 package B: the planted-data redaction audit (spec 6.1 layer 9, agent rows AR-01..AR-05).

    python tools/redaction_audit.py --both-modes [--json] [--keep DIR]

Plants secrets (API keys, OAuth/JWT, Google, Telegram, Meta, GitHub, DPAPI, the dashboard link token, a bridge bearer,
a password, a Windows user-profile path) and personal data (e-mails, phone numbers, Hebrew and English names, message
bodies, a calendar title, an address, a Hebrew text with niqqud) at every input of a REAL Core (the app.Core object on
a throw-away TEST home, offline, with fake audio / speech / player and a fake provider), then searches EVERY output for
them in raw, JSON-escaped, URL-encoded, base64, UTF-16LE and Hebrew NFC/NFD form.

What is driven: typed turns (face link `user.text`), local tool commands, a tool that crashes and one that fails with
private text, a provider error injected by the fake AI agent (BackendTrace stream error + failed response, the
BrainUnavailable path), GPT-Live provider errors and info messages (LiveVoice.handle_event), a third-party library
warning with a URL token, crashes (thread, asyncio handler, main excepthook, faulthandler dump), the approvals audit,
a background task, health text, a synthetic call of EVERY logs.* call site of the package with planted values in its
content/pii/secret fields, log rotation, the dashboard log view + log download, `status --json` (the Core snapshot),
a self-check ticket draft and a `jarvis diag` bundle.

Rules (exit code 1 when one is broken):
  * every sink, both modes: 0 secret hits;
  * privacy ON: 0 content/pii hits in every diagnostic sink;
  * every `jarvis diag` bundle, both modes: 0 content/pii hits;
  * functional stores (memory.db, the workspace notes, lists and schedules the user asked for) keep the user's own
    data by design in both modes; they must hold 0 secrets and are never part of a bundle (checked too).
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import logging
import os
import re
import socket
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
import zipfile
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
if str(CORE / "src") not in sys.path:
    sys.path.insert(0, str(CORE / "src"))

# ---------------------------------------------------------------------------------------------- the planted corpus
# Each value is unique and searchable. Secrets must never appear anywhere; content/pii must not appear in privacy
# mode or in any bundle.
SECRETS = {
    "openai_key": "sk-proj-PLANTEDk3y9a8b7c6d5e4f3g2h1",
    "openai_admin_key": "sk-admin-PLANTEDadm1n0p9o8i7u6y5",
    "claude_session": "sk-ant-oat01-PLANTEDcl4ud3s3ss10n0abc",
    "jwt_id_token": "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJQTEFOVEVEand0In0.PLANTEDjwtSignatureXYZ12345",
    "google_access": "ya29.PLANTEDg00gl3acc3ssT0k3n",
    "google_refresh": "1//0gPLANTEDr3fr3shT0k3nabcdefg",
    "google_client_secret": "GOCSPX-PLANTEDcl13ntS3cr3t",
    "telegram_bot": "7123456789:AAPLANTEDt3l3gr4mB0tT0k3nABCDEFGHIJ",
    "meta_token": "EAAPLANTEDm3t4T0k3n1234567890abcdef",
    "github_token": "ghp_PLANTEDg1thubT0k3n1234567890abcdefgh",
    "dpapi_blob": "AQAAANCMnd8BFdERjHoAwE/Cl+sBAAAAPLANTEDdp4p1Bl0b==",
    "dashboard_link": "PLANTEDd4shL1nkT0k3n42",
    "bridge_bearer": "PLANTEDbr1dg3B34r3r777",
    "password": "PLANTEDpassw0rd",
    "user_profile": "PlantedWinUser",
}
CONTENT = {
    "message_body": "the vault code is PLANTEDB0DY and my diagnosis is PLANTEDMEDICAL",
    "hebrew_body": "הקוד של הכספת הוא סודיפלנטד",
    "calendar_title": "PLANTEDCAL therapy with Dr Zuckerbrotplanted",
    "provider_text": "server said PLANTEDPROVIDER for user text",
    "niqqud": "יְעֵלִיתפְּלַנְט",
}
PII = {
    "email": "dana.planted@example.org",
    "phone_intl": "+972-54-765-4321",
    "phone_local": "0547654321",
    "hebrew_name": "יעליתה שמרלינגר",
    "english_name": "Rivka Plantedson",
    "address": "12 Plantedstreet Avenue",
}
# what is searched for each planted value (a distinctive part: a masked "<12 chars>" never matches)
NEEDLES: dict[str, list[str]] = {
    **{f"secret:{k}": [v] for k, v in SECRETS.items() if k not in ("dashboard_link", "bridge_bearer", "password",
                                                                     "user_profile", "dpapi_blob")},
    "secret:dashboard_link": [SECRETS["dashboard_link"]], "secret:bridge_bearer": [SECRETS["bridge_bearer"]],
    "secret:password": [SECRETS["password"]], "secret:user_profile": [SECRETS["user_profile"]],
    "secret:dpapi_blob": ["PLANTEDdp4p1Bl0b"],
    "content:message_body": ["PLANTEDB0DY", "PLANTEDMEDICAL"], "content:hebrew_body": ["סודיפלנטד"],
    "content:calendar_title": ["PLANTEDCAL", "Zuckerbrotplanted"], "content:provider_text": ["PLANTEDPROVIDER"],
    "content:niqqud": [CONTENT["niqqud"]],
    "pii:email": ["dana.planted@example.org", "dana.planted"], "pii:phone_intl": ["765-4321", "7654321"],
    "pii:phone_local": ["0547654321"], "pii:hebrew_name": ["יעליתה", "שמרלינגר"],
    "pii:english_name": ["Plantedson"], "pii:address": ["Plantedstreet"],
}
# a private sentence carrying one of everything (used where a single text goes in)
MIXED = (f"{CONTENT['message_body']} / {CONTENT['hebrew_body']} / {PII['hebrew_name']} {PII['email']} "
         f"{PII['phone_intl']} {PII['address']} key {SECRETS['openai_key']}")
SECRET_ONLY = (f"{SECRETS['openai_key']} {SECRETS['jwt_id_token']} {SECRETS['google_access']} "
               f"{SECRETS['google_refresh']} {SECRETS['telegram_bot']} Bearer {SECRETS['bridge_bearer']} "
               f"https://127.0.0.1:8770/?t={SECRETS['dashboard_link']} password={SECRETS['password']} "
               f"C:\\Users\\{SECRETS['user_profile']}\\AppData {SECRETS['github_token']} {SECRETS['meta_token']} "
               f"{SECRETS['google_client_secret']} {SECRETS['dpapi_blob']} {SECRETS['claude_session']} "
               f"{SECRETS['openai_admin_key']}")


# ---------------------------------------------------------------------------------------------- the scanner
def forms(needle: str) -> dict[str, bytes]:
    """The encodings a value can take in an output: raw UTF-8, JSON-escaped (\\uXXXX), URL-encoded, base64 (any
    alignment), UTF-16LE, and the other Unicode normalization (NFC <-> NFD)."""
    out: dict[str, bytes] = {"raw": needle.encode("utf-8"),
                             "json": json.dumps(needle)[1:-1].encode("ascii"),
                             "url": urllib.parse.quote(needle, safe="").encode("ascii"),
                             "url_plus": urllib.parse.quote_plus(needle).encode("ascii"),
                             "utf16le": needle.encode("utf-16-le")}
    for norm in ("NFC", "NFD"):
        n = unicodedata.normalize(norm, needle)
        if n != needle:
            out[norm.lower()] = n.encode("utf-8")
            out[norm.lower() + "_json"] = json.dumps(n)[1:-1].encode("ascii")
    raw = needle.encode("utf-8")
    for pad in range(3):                    # base64 of the needle inside any longer text: the stable middle part
        enc = base64.b64encode(b"\0" * pad + raw)
        start = (pad * 4 + 2) // 3 + (1 if pad else 0)
        mid = enc[start:len(enc) - 4]
        if len(mid) >= 8:
            out[f"base64_{pad}"] = mid
    return {k: v for k, v in out.items() if v}


_FORMS = {key: [(enc, b) for n in ns for enc, b in forms(n).items()] for key, ns in NEEDLES.items()}


def scan_bytes(data: bytes) -> list[tuple[str, str]]:
    """[(planted key, encoding)] found in data."""
    hits = []
    for key, fs in _FORMS.items():
        for enc, b in fs:
            if b in data:
                hits.append((key, enc))
                break
    return hits


def scan_blob(name: str, data: bytes) -> dict[str, list[tuple[str, str]]]:
    """A file's hits; a zip is opened and each member scanned (zip members are deflated: raw bytes say nothing)."""
    if data[:4] == b"PK\x03\x04":
        out = {}
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for m in z.namelist():
                    out.update(scan_blob(f"{name}!{m}", z.read(m)))
            return out
        except zipfile.BadZipFile:
            pass
    h = scan_bytes(data)
    return {name: h} if h else {}


# Functional stores keep the user's own data by design (in both modes): memory, notes, lists, schedules, the
# workspace and contacts. They are checked for secrets only, and never shipped in a bundle.
FUNCTIONAL = ("memory.db", "workspace/", "features.json", "config.toml", "faces.db",
              "backups/")      # a local backup is a copy of these stores (and of store.db): secrets are left out


def is_functional(rel: str) -> bool:
    return any(rel == f or rel.startswith(f) or rel.startswith(f + "-") for f in FUNCTIONAL)


def scan_tree(root: Path) -> dict[str, list[tuple[str, str]]]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            try:
                out.update(scan_blob(p.relative_to(root).as_posix(), p.read_bytes()))
            except OSError:
                pass
    return out


# ---------------------------------------------------------------------------------------------- the drive
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _FakeAgent:
    """The provider fake: every typed question that reaches the AI runs the REAL provider-error code paths with
    planted text, then fails the way an unreachable/erroring provider fails."""

    def __init__(self) -> None:
        self.n = 0
        self.cfg = None

    def instructions(self) -> str:              # what the backend is given (memory, user facts): planted
        return f"You are JARVES. The user's facts: {MIXED}. Calendar: {CONTENT['calendar_title']}."

    async def reply(self, text: str) -> str:
        from jarves.brain import BackendTrace, BrainUnavailable
        self.n += 1
        tr = BackendTrace("ai", "LIVE_BACKEND", delegation=f"d{self.n}")
        tr.feed({"type": "response.created", "response": {"id": f"resp_{self.n}", "model": "gpt-test"}})
        tr.feed({"type": "response.web_search_call.searching"})
        tr.feed({"type": "response.output_item.done", "item": {"type": "web_search_call", "status": "completed",
                 "action": {"type": "search", "query": f"{PII['english_name']} {PII['address']}"}}})
        tr.feed({"type": "response.reasoning_summary_text.done", "text": MIXED})
        tr.feed({"type": "response.output_text.done", "text": MIXED})
        tr.feed({"type": "error", "error": {"code": "server_error", "message": f"{CONTENT['provider_text']} {MIXED}"}})
        tr.finish("failed", {"error": {"code": "invalid_request", "message": f"{CONTENT['provider_text']} {MIXED}"}})
        if self.n % 2:
            raise BrainUnavailable("AI_ERROR", f"{CONTENT['provider_text']} {MIXED}", "try again")
        raise RuntimeError(f"{CONTENT['provider_text']} {MIXED}")          # an unexpected provider exception


def _skill():
    """A Skill whose tools fail with private text (tool errors), one crashes with it."""
    from pydantic import BaseModel

    from jarves.skills import Skill, Tool, ToolResult

    class A(BaseModel):
        text: str = ""

    class Planted(Skill):
        name = "planted"

        def tools(self):
            async def fail(a: A) -> ToolResult:
                return ToolResult(False, f"could not send {a.text}", {"to": PII["email"], "body": a.text},
                                  code="SEND_FAILED")

            async def crash(a: A) -> ToolResult:
                raise ValueError(f"crashed on {a.text}")
            return [Tool("planted_fail", "fail with private text", fail, A, permission="read"),
                    Tool("planted_crash", "crash with private text", crash, A, permission="read")]
    return Planted()


def _synthetic_calls() -> int:
    """Call EVERY logs.* call site of the package once (its own component, code, message and field names) with
    planted values in its content / pii / secret fields, and secrets only in its safe fields."""
    from jarves import logs
    sys.path.insert(0, str(CORE))
    from tools import log_lint
    calls, _ = log_lint.scan(logs.FIELD_CLASS)
    for c in calls:
        fields = {}
        for f in c["fields"]:
            cls = logs.field_class(f)
            fields[f] = SECRET_ONLY if cls == logs.SAFE else MIXED
        lvl = {"info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR, "debug": logging.DEBUG,
               "verbose": logging.DEBUG}.get(c["func"], logging.INFO)
        logs.log(c["component"] or "synthetic", c["code"] or "SYNTHETIC", c["msg"] or "wrapper call", lvl, **fields)
    return len(calls)


def _crashes(home: Path, loop) -> None:
    from jarves import logs
    try:                                                          # main-thread unhandled exception (excepthook)
        raise RuntimeError(f"main crash {MIXED}")
    except RuntimeError:
        et, ev, tb = sys.exc_info()
        with contextlib.redirect_stderr(io.StringIO()) as err:
            sys.excepthook(et, ev, tb)
        (home / "stderr_capture.txt").write_text(err.getvalue(), encoding="utf-8")

    def boom():                                                   # a thread that dies (threading.excepthook)
        raise ValueError(f"thread crash {MIXED}")
    t = threading.Thread(target=boom, name="planted-crash")
    t.start()
    t.join()
    loop.call_exception_handler({"message": f"task exception {MIXED}",           # asyncio handler (as installed)
                                 "exception": KeyError(f"asyncio crash {MIXED}")})
    import faulthandler                                           # native crash dump: frames only
    fh = logs.enable_faulthandler(home / "logs")
    if fh is not None:
        faulthandler.dump_traceback(fh, all_threads=True)
        fh.flush()
        faulthandler.disable()
        fh.close()


async def drive(home: Path, private: bool) -> dict[str, bytes]:
    """Run the real Core on `home` with planted inputs; returns the extra (non-file) outputs to scan."""
    import numpy as np  # noqa: F401  (the fakes below need it)
    from websockets.asyncio.client import connect

    from jarves import approvals, diagbundle, logs, selfcheck
    from jarves.app import Core
    from jarves.config import Config
    from jarves.dashboard import Dashboard
    from jarves.skills import SkillRegistry
    from jarves.skills.music import FakePlayer
    from jarves.tasks import LookupResult, TaskManager
    from jarves.voice.audio import SimAudio
    from jarves.voice.live import LiveVoice

    class STT:
        loaded_name = "fake"

        async def transcribe(self, audio):
            return MIXED

    class TTS:
        def check(self):
            return True, "cloud", "fake"

        async def synth(self, text):
            import numpy
            return numpy.zeros(1600, numpy.float32), 16000

    extra: dict[str, bytes] = {}
    cfg = Config()
    cfg.link.port = _free_port()
    cfg.log.privacy = private
    cfg.log.max_bytes = 60_000                    # force rotations (jarves.log.1, .2 ...)
    cfg.log.level = "DEBUG"
    cfg.voice.greeting = False
    cfg.voice.greeting_delay_s = 0
    console = io.StringIO()
    core = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=STT(), tts=TTS(), console_logs=False,
                use_real_audio=False)
    await core.start()
    con = logging.StreamHandler(console)          # the terminal formatter, as `jarvis run` shows it
    con.setFormatter(logs.ConsoleFormatter(color=False))
    con.setLevel(logging.DEBUG)
    logs._logger.addHandler(con)
    try:
        core.registry.register(_skill())
        from jarves import context_trace
        fake = _FakeAgent()
        context_trace.attach_brain(fake, home=home, cfg=cfg, memory=core.memory, registry=core.registry)
        core.brain.agent = fake
        rec = context_trace.recorder(home, cfg)             # a live conversation's context trace (attach_live API)
        tr = context_trace.Trace(rec, "live", logs.privacy())
        tr.save_start(context_trace.snapshot({"instructions": fake.instructions(), "voice": "x"},
                                             memory=core.memory, registry=core.registry, private=logs.privacy()))
        tr.event("user", "user said", MIXED)
        tr.event("tool_call", "tool called", MIXED, data={"arguments": MIXED})
        tr.end("planted")
        rec.flush()
        ws = await connect(f"ws://127.0.0.1:{cfg.link.port}")
        await ws.send(json.dumps({"type": "hello", "payload": {"token": core.link.token, "client": "godot",
                                                                "protocol": 1}}))

        async def wait(t, timeout=45.0):
            kinds = (t,) if isinstance(t, str) else tuple(t)

            async def go():
                while True:
                    m = json.loads(await ws.recv())
                    if m["type"] in kinds:
                        return m["payload"]
            return await asyncio.wait_for(go(), timeout)
        await wait("hello.ok")
        for text in (MIXED, f"תזכור ש{PII['hebrew_name']} גרה ב{PII['address']}", "מה השעה?",
                     f"what is {CONTENT['calendar_title']} with {PII['email']}", CONTENT["niqqud"]):
            await ws.send(json.dumps({"type": "user.text", "payload": {"text": text}}))
            # A typed turn ends with a reply (assistant.text) or, when the provider raises an unexpected exception
            # (the fake agent's every second call), with the Core's "error" message to the face: the CORE_TASK_CRASH
            # path kills the request task and tells the owner, no reply text exists. Both are a finished turn.
            await wait(("assistant.text", "error"))
        for tool in ("planted_fail", "planted_crash"):
            await core.registry.call(tool, {"text": MIXED}, origin="model")
        lv = LiveVoice(SkillRegistry(), lambda: "")              # GPT-Live provider errors + info + transcripts
        await lv.handle_event({"type": "error", "error": {"code": "invalid_value", "message": MIXED}})
        await lv.handle_event({"type": "info", "message": MIXED})
        await lv.handle_event({"type": "error", "error": {"code": "x", "message": MIXED,
                                                          "client_event_id": "jarves_lang_1"}})
        # FAKE test fixture: SECRETS/MIXED are synthetic planted values (not credentials); this audit proves they never leak.
        logging.getLogger("httpx").warning(f"HTTP Request: GET https://api.example/?access_token="
                                           f"{SECRETS['google_access']}&q={PII['email']} {MIXED}")
        # FAKE test fixture: MIXED holds synthetic planted values only.
        logging.getLogger("openai").error(f"Error code: 400 - {{'message': '{MIXED}'}}")
        core.health.set("ai", "degraded", f"ChatGPT plan ({PII['email']}) {SECRETS['openai_key']}",
                        f"sign in again as {PII['email']}")

        async def runner(description, on_progress=None, *, reservation=None):
            if on_progress:
                on_progress("searching", f"מחפש: {description}")
            return LookupResult(MIXED, 1, {"input_tokens": 5})
        tm = TaskManager(runner, store=home / "tasks.jsonl")
        done = asyncio.Event()
        tm.on_finished = lambda t: done.set()
        await tm.start(f"find {PII['english_name']} {CONTENT['message_body']}")
        await asyncio.wait_for(done.wait(), 45)
        await tm.shutdown()
        confirm = approvals.audited(home, lambda prompt: _true())
        await confirm(f"Send to {PII['email']}: {CONTENT['message_body']}", title=f"email {PII['english_name']}",
                      kind="email")
        approvals.append_audit(home / "approvals" / "audit.jsonl",
                               {"ts": time.time(), "kind": "email", "title": f"to {PII['hebrew_name']}",
                                "prompt": "<5 chars>", "details": {"to": PII["email"], "body": MIXED,
                                                                   "subject": CONTENT["calendar_title"]}})
        n_calls = _synthetic_calls()
        _crashes(home, asyncio.get_running_loop())
        snap = core.snapshot()
        extra["status_json"] = json.dumps(snap, ensure_ascii=False, default=str).encode("utf-8")
        for h in logs._logger.handlers:
            h.flush()
        await ws.close()
    finally:
        logs._logger.removeHandler(con)
        await core.stop()
    extra["console"] = console.getvalue().encode("utf-8")
    # dashboard: the log view and the log download, as served
    d = Dashboard(home, cfg).start()
    try:
        import http.client
        tok = d.url.split("t=")[1]
        cookie = f"{__import__('jarves.dashboard', fromlist=['COOKIE']).COOKIE}={tok}"
        for path in ("/api/logs?limit=5000&noisy=1", "/export/logs.jsonl?noisy=1"):
            c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=30)
            c.request("GET", path, headers={"Host": f"127.0.0.1:{d.port}", "Cookie": cookie})
            r = c.getresponse()
            extra[f"dashboard{path.split('?')[0]}"] = r.read()
            c.close()
    finally:
        d.stop()
    # self-check ticket draft (meant for sharing) and the diag bundle (meant for sharing)
    try:
        view = selfcheck.SelfCheck(home, cfg).run(trigger="manual", ai=False)
        extra["ticket_draft"] = json.dumps(selfcheck.ticket_draft(view), ensure_ascii=False).encode("utf-8")
    except Exception as e:                             # reported, never hidden
        extra["ticket_draft_error"] = type(e).__name__.encode()
    from jarves import backup                       # a local backup (functional copy): must hold no secret
    (home / "notes_with_key.txt").write_text(f"my key {SECRETS['google_refresh']}", encoding="utf-8")
    backup.create(home, "manual", note=MIXED)
    (home / "notes_with_key.txt").unlink()
    out = home.parent / f"diag-{'on' if private else 'off'}.zip"
    diagbundle.build(home, cfg, out, lines=5000, status=snap)
    extra["diag_zip"] = out.read_bytes()
    extra["_calls"] = str(n_calls).encode()
    return extra


async def _true() -> bool:
    return True


def audit(private: bool, workdir: Path) -> dict:
    """One mode: drive, scan, apply the rules. Returns {"mode", "sinks": {name: {...}}, "violations": [...]}."""
    from jarves import logs
    home = workdir / f"home-{'on' if private else 'off'}"
    home.mkdir(parents=True, exist_ok=True)
    env_keep = {k: os.environ.get(k) for k in ("JARVES_HOME", "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN",
                                               "HOMEASSISTANT_TOKEN", "OPENAI_ADMIN_KEY")}
    os.environ.update(JARVES_HOME=str(home), OPENAI_ADMIN_KEY=SECRETS["openai_admin_key"],
                      TELEGRAM_BOT_TOKEN=SECRETS["telegram_bot"], HOMEASSISTANT_TOKEN=SECRETS["jwt_id_token"])
    os.environ.pop("OPENAI_API_KEY", None)           # offline: no real provider is ever called
    try:
        extra = asyncio.run(drive(home, private))
    finally:
        for k, v in env_keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        logs.set_privacy(True)
    n_calls = int(extra.pop("_calls", b"0"))
    found = scan_tree(home)
    for k, v in extra.items():
        found.update(scan_blob(f"<{k}>", v))
    sinks: dict[str, dict] = {}
    for p in sorted(home.rglob("*")):
        if p.is_file():
            sinks.setdefault(p.relative_to(home).as_posix(), {})
    for k in extra:
        sinks.setdefault(f"<{k}>", {})
    for name, hits in found.items():
        top = name.split("!")[0]
        s = sinks.setdefault(top, {})
        for key, enc in hits:
            s.setdefault(key.split(":")[0], []).append(f"{key}@{enc}" + (f" in {name.split('!', 1)[1]}" if "!" in name else ""))
    violations = []
    for name, s in sinks.items():
        bundle = name in ("<diag_zip>", "<ticket_draft>")
        functional = is_functional(name)
        if s.get("secret"):
            violations.append(f"{name}: SECRET {s['secret']}")
        if (private and not functional) or bundle:
            for cls in ("content", "pii"):
                if s.get(cls):
                    violations.append(f"{name}: {cls.upper()} {s[cls]}")
    must = ["logs/jarves.log", "logs/jarves.log.1", "store.db", "health.json", "approvals/audit.jsonl", "tasks.jsonl",
            "logs/crash.txt", "<console>", "<status_json>", "<dashboard/api/logs>", "<dashboard/export/logs.jsonl>",
            "<diag_zip>"]
    for m in must:                                   # an audit that did not produce a sink proves nothing
        if m not in sinks:
            violations.append(f"{m}: sink was not produced by the drive")
    if not private:                                  # positive control: the planted data really went in
        for group in (("logs/jarves.log", "logs/jarves.log.1"), ("store.db", "store.db-wal")):
            if not any(sinks.get(g, {}).get("content") for g in group):
                violations.append(f"{group[0]}: planted content never reached it in development mode "
                                  "(the drive is broken, the audit proves nothing)")
    if "ticket_draft_error" in extra:
        violations.append(f"<ticket_draft>: could not be produced ({extra['ticket_draft_error'].decode()})")
    return {"mode": "privacy ON" if private else "privacy OFF", "synthetic_call_sites": n_calls, "sinks": sinks,
            "violations": violations}


_LEAK_CATEGORIES = ("SECRET", "CONTENT", "PII")


def mask_violation(v: str, sinks: object = ()) -> str:
    """A violation line safe to print: a leaked value must never be echoed (CI logs are public). Leak lines are
    rebuilt from fixed labels only: the sink name (looked up among the known sink names, never sliced out of the
    line), the category and the length. Not one character of the leaked part is kept."""
    for cat in _LEAK_CATEGORIES:
        for sink in sinks:
            head = f"{sink}: {cat} "
            if v.startswith(head):
                return f"{sink}: {cat} leak, {len(v) - len(head)} chars masked"
    if any(f": {cat} " in v for cat in _LEAK_CATEGORIES):
        return f"leak line masked ({len(v)} chars)"
    return v                                         # structural messages carry no planted value


def main(argv: list[str]) -> int:
    modes = [True, False] if "--both-modes" in argv or not any(a in argv for a in ("--on", "--off")) else \
        [True] if "--on" in argv else [False]
    keep = argv[argv.index("--keep") + 1] if "--keep" in argv else None
    work = Path(keep) if keep else Path(tempfile.mkdtemp(prefix="jarves-redaction-"))
    reports = [audit(m, work) for m in modes]
    if "--json" in argv:
        print(json.dumps([{**r, "violations": [mask_violation(v, r["sinks"]) for v in r["violations"]]} for r in reports],
                         indent=1, ensure_ascii=False))
    else:
        for r in reports:
            print(f"== {r['mode']}: {len(r['sinks'])} sinks, {r['synthetic_call_sites']} call sites driven")
            for name, s in sorted(r["sinks"].items()):
                tag = "functional" if is_functional(name) else "bundle" if name in ("<diag_zip>", "<ticket_draft>") \
                    else "diagnostic"
                counts = " ".join(f"{c}={len(s.get(c, []))}" for c in ("secret", "content", "pii"))
                print(f"  {name:<44} {tag:<11} {counts}")
            print(f"  violations: {len(r['violations'])}")
            for v in r["violations"]:
                print("   ", mask_violation(v, r["sinks"]))
    return 1 if any(r["violations"] for r in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
