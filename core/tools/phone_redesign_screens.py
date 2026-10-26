"""Phone app redesign (#511): screenshots at 390x844 (EN + HE, light + dark) and a test-persona server for the real phone.

A temporary JARVES home with a FAKE model (no network, no owner data) runs the real dashboard server with phone access on,
exactly like core/tests/test_mobile_phone_e2e.py. The fake model answers with streamed markdown so the chat can be judged.

  python tools/phone_redesign_screens.py                 # writes docs/reviews/PHONE_REDESIGN_SCREENSHOTS/browser/*.png
  python tools/phone_redesign_screens.py --serve 8777    # keeps the server up; prints a pairing URL for the test phone
                                                         # (adb reverse tcp:8777 tcp:8777, then open http://localhost:8777/m/)
"""
from __future__ import annotations

import argparse
import os
import asyncio
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("JARVES_BETA_FEATURES", "1")         # as the tests: the owner install has the opt-in on
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(ROOT / "tools")]
OUT = ROOT.parent / "docs" / "reviews" / "PHONE_REDESIGN_SCREENSHOTS" / "browser"

ANSWERS = {
    "plan": ("Here is a plan for **Saturday**:\n\n1. Groceries at 10:00\n2. Call the garage about the service\n3. Gym at 17:30\n\n"
             "Want me to add these as tasks?"),
    "code": ("A small Python example:\n\n```python\nfrom datetime import date\n\ndef days_left(d: date) -> int:\n"
             "    return (d - date.today()).days\n```\n\nIt returns the number of days until `d`."),
    "שבת": "התוכנית לשבת:\n\n- קניות בעשר\n- שיחה למוסך על הטיפול\n- חדר כושר ב-17:30\n\nלהוסיף את אלה כמשימות?",
}


def answer_for(text: str) -> str:
    for k, v in ANSWERS.items():
        if k in text.lower():
            return v
    return "Sure. I am the **test persona**: this answer comes from a fake model on a temporary home."


def make_phone(home: Path, tls: bool, host: str | None = None):
    import test_mobile_phone_e2e as e2e

    class DemoCore(e2e._ChatCore):
        async def respond_remote(self, text, *, on_delta=None, fresh=False, utterance=None):
            self.fake.chats.append(text)
            full = answer_for(text)
            if on_delta is not None:                       # streamed in pieces, like a real model
                for i in range(0, len(full), 12):
                    on_delta(full[i:i + 12])
                    await asyncio.sleep(0.05)
            return SimpleNamespace(text=full, ok=True, code="", source="")

    e2e._ChatCore = DemoCore
    if host:
        e2e.HOST = host
    return e2e.Phone(home, tls), e2e


def shoot(page, name: str):
    OUT.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(400)
    page.screenshot(path=str(OUT / f"{name}.png"))


def screens() -> None:
    from playwright.sync_api import sync_playwright
    import os
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td, sync_playwright() as pw:
        os.environ["JARVES_HOME"] = td
        p, e2e = make_phone(Path(td), tls=True)
        import all_pages_check as apc
        b = pw.chromium.launch(headless=True, args=e2e.ARGS, **apc.launch_kwargs("msedge" if sys.platform == "win32" else ""))
        try:
            p.owner("POST", "/api/mobile/settings", {"phone_approvals": True})
            for lang, locale in (("en", "en-US"), ("he", "he-IL")):
                for scheme in ("light", "dark"):
                    tag = f"{lang}-{scheme}"
                    page = e2e.new_page(b, locale=locale, scheme=scheme)
                    page.goto(f"{p.base}/m/")
                    page.wait_for_selector("#pairView:not([hidden])")
                    shoot(page, f"01-pair-{tag}")
                    page.goto("about:blank")
                    e2e.ui_pair(page, p)
                    page.wait_for_selector(".jc-in")
                    with p.chat._lock:                     # a fresh, empty conversation for each run
                        p.chat.messages.clear()
                    p.chat.events.add("reset")
                    page.wait_for_timeout(800)
                    shoot(page, f"02-chat-empty-{tag}")
                    msgs = ["Plan my Saturday", "Show me a code example"] if lang == "en" else ["תכנן לי את שבת", "show me code"]
                    for m in msgs:
                        page.fill(".jc-in", m)
                        page.tap(".jc-send")
                        e2e.until(page, "document.querySelectorAll('.jc-acts').length >= 1 && !document.querySelector('.streaming')", 20000)
                        page.wait_for_timeout(600)
                    shoot(page, f"03-chat-{tag}")
                    page.tap("#menuBtn")
                    page.wait_for_selector("#drawer:not([hidden])")
                    shoot(page, f"04-drawer-{tag}")
                    page.tap("#drawerClose")
                    fut = p.ask("Send the test message" if lang == "en" else "שליחת הודעת בדיקה")
                    page.wait_for_selector(".jc-card", timeout=15000)
                    shoot(page, f"05-chat-approval-{tag}")
                    for tab in ("approvals", "voice", "health", "home"):
                        e2e.go(page, tab)
                        page.wait_for_timeout(900)
                        shoot(page, f"06-{tab}-{tag}")
                    from jarves import approvals as appr
                    for r in appr.read_pending_file(p.home, reveal=False):
                        appr.submit_answer(p.home, r["id"], False, channel="phone")
                    fut.result(15)
                    page.context.close()
        finally:
            b.close()
            p.close()
    print("screenshots:", OUT)


def serve(port: int, tls: bool) -> None:
    import os
    import conftest
    conftest.free_port = lambda: port
    td = tempfile.mkdtemp(prefix="jarves-phone-demo-")
    os.environ["JARVES_HOME"] = td
    p, _ = make_phone(Path(td), tls=tls, host="localhost")
    p.owner("POST", "/api/mobile/settings", {"phone_approvals": True})
    c = p.code()
    print("home:", td)
    print("pair:", c["url"].replace(f"localhost:{p.d.port}", f"localhost:{port}"), "code:", c["code"], flush=True)
    try:
        while True:
            cmd = Path(td, "ask.txt")
            if cmd.exists():                                # drop a file to put a test approval in front of the phone
                title = cmd.read_text(encoding="utf-8").strip() or "Send the test message"
                cmd.unlink()
                p.ask(title)
            time.sleep(1)
    except KeyboardInterrupt:
        p.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", type=int, default=0)
    ap.add_argument("--tls", action="store_true")
    a = ap.parse_args()
    serve(a.serve, a.tls) if a.serve else screens()
