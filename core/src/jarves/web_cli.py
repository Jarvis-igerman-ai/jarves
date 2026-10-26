"""`jarvis web`: the dashboard Chat page, opened from the terminal (V5, owner request 21 Oct 2026).

- JARVES is running: the Core already serves the dashboard and the shared chat engine (chat_engine.WebChat on the
  Core's own Brain); the command asks it over the local link to open the Chat page in the browser.
- JARVES is stopped: a standalone dashboard starts here (like `jarvis execute`, no voice, camera or face), with the same
  chat engine on the text-only Brain, the same Skills, budget and approvals (the popup, plus the page itself), and the
  same history in store.db, so `jarvis chat` and the Windows voice session see these conversations too.

Paired phones and PCs use the same engine through the phone path: `jarvis web --mobile` (needs the mobile_access switch).
The pages a paired device may open are chosen per device: `jarvis web --device-pages` lists them,
`jarvis web --set-pages DEVICE chat,home` changes them (settings, secrets, files and devices are never remote).
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

from . import approvals as appr
from . import chat_engine
from . import logs

CHAT_FRAGMENT = "#/chat"


class TextCore:
    """The parts of a Core the chat engine uses, for a session without a running Core."""

    def __init__(self, cfg, home: Path, *, agent_factory=None, transport=None, popup: bool = True,
                 popup_test_invoke: str | None = None) -> None:
        from . import text_session
        self.cfg, self.home = cfg, Path(home)
        self.loop = asyncio.new_event_loop()
        self.live, self.stopping, self.curator = None, False, None
        self._thread = threading.Thread(target=self.loop.run_forever, name="jarves-web-chat-loop", daemon=True)
        self._thread.start()
        self.approvals = appr.attach(appr.ApprovalService(self.home))
        self.approvals.loop = self.loop
        if popup:
            self.approvals.channels.append(appr.PopupChannel(self.approvals, enabled=True, lang=cfg.ui.popup_language,
                                                             test_invoke=popup_test_invoke))
        self.session = text_session.build(cfg, self.home, self._ask, agent_factory=agent_factory, transport=transport,
                                          note=WEB_NOTE)
        self.brain, self.agent, self.registry = self.session.brain, self.session.agent, self.session.registry
        self.memory, self.workspace, self.settings = self.session.memory, self.session.workspace, self.session.settings
        from .store import Store
        self.store = Store(self.home / "store.db")

    async def _ask(self, prompt: str, timeout: float | None = None, **meta) -> bool:
        return await self.approvals.ask(prompt, timeout=timeout, **meta)
    _ask.accepts_meta = True

    def snapshot(self) -> dict:
        return {"core": "text session (JARVES itself is not running)", "components": {}}

    async def respond_remote(self, text: str, *, on_delta=None, fresh: bool = False, utterance: str | None = None):
        t0 = time.monotonic()
        reply = await (self.brain.respond(text, spoken=False, on_delta=on_delta) if on_delta is not None
                       else self.brain.respond(text, spoken=False))
        from .curator import _SENSITIVE
        private = logs.privacy()
        user = utterance if utterance is not None else (  # a UI-action turn: its item titles are never stored in the history
            f"<{len(text)} chars>" if private or _SENSITIVE.search(text) else text)
        said = f"<{len(reply.text)} chars>" if private else reply.text
        await asyncio.to_thread(self.store.record_typed, user, said, time.time(),
                                ms=round((time.monotonic() - t0) * 1000), fresh=fresh)
        return reply

    def close(self) -> None:
        from . import text_session
        self.stopping = True
        try:
            asyncio.run_coroutine_threadsafe(text_session.close_session(self.session), self.loop).result(10)
        except Exception:
            logs.warn("core", "CHAT_CLOSE_FAILED", "the text session did not close cleanly")
        self.approvals.close()
        appr.detach(self.approvals)
        self.store.close()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(5)


WEB_NOTE = ("\nThis is the JARVES web chat (the dashboard Chat page) in a text session: no microphone, camera capture, "
            "face or speech. Reply in text. Approvals are answered on the page or in the approval window. If details "
            "are missing, ask; never invent a title, time, recipient or permission.")


class Handle:
    """A running standalone web chat: the dashboard, the chat engine and the text core."""

    def __init__(self, dash, chat, core) -> None:
        self.dashboard, self.chat, self.core = dash, chat, core
        self.url = dash.url

    def stop(self) -> None:
        self.chat.close()
        self.dashboard.stop()
        self.core.close()


def start_standalone(cfg, home: Path, *, port: int = 0, mobile_on: bool = False, tls=None, agent_factory=None,
                     transport=None, popup: bool = True, popup_test_invoke: str | None = None,
                     lan_host: str | None = None) -> Handle:
    from .dashboard import Dashboard
    core = TextCore(cfg, home, agent_factory=agent_factory, transport=transport, popup=popup,
                    popup_test_invoke=popup_test_invoke)
    chat = chat_engine.WebChat(core, core.loop)
    try:
        dash = Dashboard(home, cfg, port, workspace=core.workspace, memory=core.memory, settings=core.settings,
                         mobile_on=mobile_on, tls=tls, chat=chat, lan_host=lan_host,
                         mobile_bridge=_text_bridge(core) if mobile_on else None).start()
    except Exception:
        chat.close()
        core.close()
        raise
    dash.mobile.gate = lambda: _switch(cfg, home, "mobile_access")
    return Handle(dash, chat, core)


def _switch(cfg, home, fid: str) -> bool:
    from .features import Features
    return Features(cfg, home).enabled(fid)


def _text_bridge(core: TextCore):
    """The older phone endpoints (/api/m/chat, /status) on the text core."""
    from .mobile import Bridge

    def run(coro):
        return asyncio.run_coroutine_threadsafe(coro, core.loop).result(150)

    async def respond(text: str) -> dict:
        r = await core.respond_remote(text)
        return {"reply": r.text, "ok": bool(r.ok)}
    return Bridge(chat=lambda text, device: run(respond(text)),
                  voice=lambda wav, device: {"transcript": "", "reply": "", "ok": False, "code": "NO_VOICE"},
                  status=lambda: {"core": "text session", "components": {}}, voice_available=lambda: False)


# ---------------------------------------------------------------------- CLI
def add_cli(sub) -> None:
    p = sub.add_parser("web", help="open the dashboard Chat page (streaming text chat with slash commands and inline "
                                   "approvals); works with JARVES stopped too")
    p.add_argument("--port", type=int, default=0, help="standalone dashboard port (default: a free port)")
    p.add_argument("--no-open", action="store_true", help="print the link, do not open the browser")
    p.add_argument("--mobile", action="store_true", help="also serve the phone/PC page for paired devices (needs: "
                                                         "jarvis features on mobile_access)")
    p.add_argument("--tls-selfsigned", action="store_true", help="with --mobile: self-signed certificate (phone microphone)")
    p.add_argument("--device-pages", action="store_true", help="list paired devices and the pages each may open")
    p.add_argument("--set-pages", nargs=2, metavar=("DEVICE", "PAGES"),
                   help="allowed pages of one device, e.g. chat,home,approvals,health (settings, secrets, files and "
                        "devices are computer-only)")


def cmd_web(a, cfg, home) -> int:
    from .features import Features
    feats = Features(cfg, home)
    if a.device_pages or a.set_pages:
        return _device_pages(a, home)
    for fid in ("dashboard", "web_chat"):
        if not feats.enabled(fid):
            print(f"The {fid} switch is off. Turn it on with: jarvis features on {fid}", file=sys.stderr)
            return 1
    from .cli import _ws_call
    page = {"page": CHAT_FRAGMENT, "open": not a.no_open}
    if not a.mobile:
        got = _ws_call(Path(home), cfg.link.port, {"type": "ui.open_dashboard", "payload": page}, "dashboard.opened", timeout=4)
        if got is not None:
            print(f"JARVES is running: the Chat page is open on {got.get('url')}{CHAT_FRAGMENT}" if got.get("browser")
                  else f"JARVES is running: the Chat page is at {got.get('url')}{CHAT_FRAGMENT} (open it on this computer)")
            return 0
    mobile_on, tls = False, None
    if a.mobile:
        if not feats.enabled("mobile_access"):
            print("Phone access is switched off. Turn it on with: jarvis features on mobile_access", file=sys.stderr)
            return 1
        mobile_on = True
        from . import mobile as _mobile
        cert, key = _mobile.tls_paths(home)
        if a.tls_selfsigned and not cert.is_file():
            from .dashboard_auth import lan_ip
            _mobile.make_selfsigned(home, lan_ip())
        if cert.is_file() and key.is_file():
            tls = (cert, key)
    try:
        h = start_standalone(cfg, Path(home), port=a.port, mobile_on=mobile_on, tls=tls)
    except OSError as e:
        print(f"cannot start the web chat: {e}", file=sys.stderr)
        return 1
    print(f"JARVES web chat (JARVES itself is not running; text only; the link holds a private key for THIS computer):\n"
          f"  {h.url}{CHAT_FRAGMENT}", flush=True)
    if mobile_on:
        print(f"Phone access is ON on {h.dashboard.mobile.host}:{h.dashboard.port}: pair from the dashboard Phone page.", flush=True)
    print("Ctrl+C to stop.", flush=True)
    if not a.no_open:
        import webbrowser
        webbrowser.open(h.url + CHAT_FRAGMENT)
    try:
        h.dashboard._thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        h.stop()
    return 0


def _device_pages(a, home) -> int:
    from . import mobile
    m = mobile.MobileAccess(Path(home))
    if a.set_pages:
        dev, pages = a.set_pages[0], [p for p in a.set_pages[1].split(",") if p]
        bad = [p for p in pages if p in mobile.PC_ONLY_PAGES]
        if bad:
            print(f"{', '.join(bad)}: settings, secrets, files and devices stay on the computer and cannot be given to a device.",
                  file=sys.stderr)
            return 1
        unknown = [p for p in pages if p not in mobile.PAGES]
        if unknown:
            print(f"unknown page: {', '.join(unknown)} (choose from {', '.join(mobile.PAGES)})", file=sys.stderr)
            return 1
        if not m.set_pages(dev, pages, by="cli"):
            print("no such active device (see: jarvis web --device-pages)", file=sys.stderr)
            return 1
    rows = [d for d in m.devices() if not d.get("revoked")]
    if not rows:
        print("No paired devices.")
    for d in rows:
        print(f"{d['id']}  {d['name']}: {', '.join(d['pages']) or '(no pages)'}")
    print(f"computer-only (never remote): {', '.join(mobile.PC_ONLY_PAGES)}")
    return 0


COMMANDS = {"web": cmd_web}
