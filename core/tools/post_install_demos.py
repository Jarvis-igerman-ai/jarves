"""Post-install demo step (JAR-368): after a successful beta install, run the scenario of every merged user-facing ticket
that has no demo yet, record PASS/FAIL and save a screenshot, a short video and a one-line caption to <data>/demos.

    python core/tools/post_install_demos.py run --tickets FILE [--demos DIR] [--jarvis EXE] [--dashboard-url URL]
    python core/tools/post_install_demos.py metrics [--demos DIR] [--required JAR-1,JAR-2]

FILE is a JSON list of {"ticket", "merged", "user_facing", "argv", "page", "caption"}. `argv` is passed to the installed
`jarvis` program without a shell and only when it equals one of the whole argv lists in ALLOWED (else refused, never
run). `--jarvis` is resolved to an absolute path (never the current folder). The browser may only load the checked loopback
origin: requests elsewhere are aborted, redirects are followed here hop by hop and aborted when any hop leaves the origin,
WebSockets to another origin are closed, service workers are blocked, WebRTC UDP and DNS prefetch are switched off at
launch; a page that still ends up elsewhere is a FAIL. Not covered: preconnect hints (UNVERIFIED, not exercised). A ticket stays pending (retried next pass) until it has a PASS demo.
Each demo is <demos>/<ticket>/demo.json (written atomically) plus demo.png and demo.webm (Playwright, headless).
`metrics` prints demos_24h, with_image, with_video, scenario_pass_pct (null = unknown) and the --required tickets missing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

REQUIRED = (  # default for --required (this ticket's acceptance list)
    "JAR-188", "JAR-189", "JAR-191", "JAR-193", "JAR-210", "JAR-214")
ALLOWED = {("status",), ("doctor",), ("selftest",), ("version",), ("features",), ("guides",), ("help",)}
# whole-URL allowlist: no userinfo, backslash, whitespace, path, query or fragment (Chromium parses those differently)
URL_RE = re.compile(r"(https?)://(127\.0\.0\.1|localhost|\[::1\])(?::(\d{1,5}))?/?")
PAGE_RE = re.compile(r"/(?!/)[A-Za-z0-9/_.\-?=&#]*")
TICKET_RE = re.compile(r"[A-Z]+-\d+")
DAY_S = 86400
DEFAULT_PORT = {"http": 80, "https": 443}
WS_SCHEME = {"ws": "http", "wss": "https"}
MAX_HOPS = 5
# traffic Playwright routing cannot see: WebRTC ICE/STUN over UDP and DNS prefetch (preconnect hints are not covered)
LAUNCH_ARGS = ("--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--dns-prefetch-disable")


def load_tickets(path: Path) -> list[dict]:
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"DEMO_TICKETS_INVALID: {e}") from e
    if not isinstance(rows, list):
        raise ValueError("DEMO_TICKETS_INVALID: expected a JSON list")
    return [r for r in rows if isinstance(r, dict) and TICKET_RE.fullmatch(str(r.get("ticket", "")))]


def _check_argv(argv) -> list[str]:
    if not (isinstance(argv, list) and all(isinstance(a, str) for a in argv) and tuple(argv) in ALLOWED):
        raise ValueError(f"DEMO_SCENARIO_REFUSED: {argv!r} is not an allowed read-only jarvis command")
    return argv


def check_url(url: str) -> str:
    """Returns the base URL rebuilt from the checked parts only (never the raw string)."""
    m = URL_RE.fullmatch(str(url))
    if not m or (m.group(3) and not 0 < int(m.group(3)) < 65536):
        raise ValueError(f"DEMO_URL_REFUSED: {url!r} is not a loopback http(s) dashboard URL")
    scheme, host, port = m.groups()
    return f"{scheme}://{host}" + (f":{int(port)}" if port else "")


def same_origin(url: str, base: str) -> bool:
    """True only when url has the scheme, host and port of the checked base and no userinfo."""
    try:
        u, b = urlsplit(str(url)), urlsplit(base)
        return (not u.username and u.scheme == b.scheme and u.hostname == b.hostname
                and (u.port or DEFAULT_PORT.get(u.scheme)) == (b.port or DEFAULT_PORT[b.scheme]))
    except ValueError:
        return False


def guarded_route(route, base: str) -> None:
    """Fetch a request ourselves without automatic redirects; every hop must stay on the checked origin."""
    url, method, override = route.request.url, str(route.request.method).upper(), {}
    for _ in range(MAX_HOPS + 1):
        if not same_origin(url, base):
            return route.abort()
        try:
            resp = route.fetch(url=url, max_redirects=0, **override)
        except Exception:  # noqa: BLE001 - dashboard down/reset: abort now; goto then fails and the ticket records FAIL
            return route.abort()
        loc = resp.headers.get("location")
        if not (300 <= resp.status < 400 and loc):
            return route.fulfill(response=resp)
        if resp.status == 303 or (resp.status in (301, 302) and method != "GET"):
            method, override = "GET", {"method": "GET", "post_data": ""}   # like a browser: next hop is a bodiless GET
        url = urljoin(url, loc)
    return route.abort()   # too many redirects


def guarded_websocket(ws, base: str) -> None:
    """Connect a WebSocket to the server only when it targets the checked origin; otherwise close it unconnected."""
    scheme, sep, rest = str(ws.url).partition("://")
    if sep and scheme in WS_SCHEME and same_origin(f"{WS_SCHEME[scheme]}://{rest}", base):
        ws.connect_to_server()
    else:
        ws.close()


def context_options(video_dir: str) -> dict:
    return {"record_video_dir": video_dir, "viewport": {"width": 1280, "height": 720}, "service_workers": "block"}


def redact(text: str) -> str:
    """Hide the home folder and user name in saved output and errors (data/demos may be shared as evidence)."""
    home = str(Path.home())
    if len(home) > 1:
        for form in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
            text = re.sub(re.escape(form), "~", text, flags=re.IGNORECASE)
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    return re.sub(rf"\b{re.escape(user)}\b", "<user>", text) if len(user) > 2 else text


def _write_json(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".demo-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def has_demo(demos: Path, ticket: str) -> bool:
    """True only for a PASS record with image and video; FAIL or corrupt records are retried."""
    try:
        r = json.loads((Path(demos) / ticket / "demo.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (isinstance(r, dict) and r.get("result") == "PASS"
            and all(r.get(k) == n and (Path(demos) / ticket / n).is_file()
                    for k, n in (("image", "demo.png"), ("video", "demo.webm"))))


def record(row: dict, demos: Path, run_scenario, capture, clock=time.time) -> dict:
    ticket = row["ticket"]
    d = Path(demos) / ticket
    d.mkdir(parents=True, exist_ok=True)
    rec = {"ticket": ticket, "caption": str(row.get("caption") or f"{ticket} scenario on the installed beta")[:200],
           "at": clock(), "result": "FAIL", "image": None, "video": None, "error": None}
    try:
        code, out = run_scenario(_check_argv(row.get("argv")))
        rec["exit_code"], rec["output_tail"] = code, redact(str(out))[-400:]
        page = str(row.get("page") or "/")
        if not PAGE_RE.fullmatch(page):
            raise ValueError(f"DEMO_PAGE_REFUSED: {page!r}")
        img, vid = d / "demo.png", d / "demo.webm"
        for f in (img, vid):
            f.unlink(missing_ok=True)   # never count media left over from an earlier failed pass
        capture(page, img, vid)
        rec["image"] = img.name if img.is_file() else None
        rec["video"] = vid.name if vid.is_file() else None
        rec["result"] = "PASS" if code == 0 and rec["image"] and rec["video"] else "FAIL"
    except Exception as e:  # noqa: BLE001 - any scenario/browser error (ImportError, Playwright Error) is a FAIL record
        rec["error"] = redact(f"{type(e).__name__}: {e}")[:300]
    _write_json(d / "demo.json", rec)
    return rec


def run(rows: list[dict], demos: Path, run_scenario, capture, clock=time.time) -> list[str]:
    """Record a demo for every merged user-facing ticket without a PASS demo; returns the tickets handled."""
    done = []
    for row in rows:
        if row.get("merged") is True and row.get("user_facing") is True and not has_demo(demos, row["ticket"]):
            record(row, demos, run_scenario, capture, clock)
            done.append(row["ticket"])
    return done


def metrics(demos: Path, now: float | None = None, required=REQUIRED) -> dict:
    now = time.time() if now is None else now
    recs = []
    for p in sorted(Path(demos).glob("*/demo.json")):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(r, dict) and isinstance(r.get("at"), (int, float)) and now - r["at"] <= DAY_S:
            recs.append(r)
    passed = sum(r.get("result") == "PASS" for r in recs)
    return {"demos_24h": len(recs), "with_image": sum(bool(r.get("image")) for r in recs),
            "with_video": sum(bool(r.get("video")) for r in recs),
            "scenario_pass_pct": round(passed / len(recs) * 100, 1) if recs else None,
            "missing": [t for t in required if not has_demo(demos, t)]}


def resolve_jarvis(exe: str) -> str | None:
    """Absolute path of the jarvis program, or None. A relative hit (the Windows current folder) is refused."""
    found = shutil.which(exe)
    return found if found and os.path.isabs(found) else None


def subprocess_runner(exe: str | None):
    def go(argv: list[str]) -> tuple[int, str]:
        if not exe:
            raise ValueError("DEMO_JARVIS_NOT_FOUND: no absolute jarvis program on PATH or --jarvis")
        p = subprocess.run([exe, *argv], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180, check=False)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    return go


def playwright_capture(base_url: str):  # pragma: no cover - needs a browser and the installed beta
    base = check_url(base_url)

    def go(page_path: str, img: Path, vid: Path) -> None:
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
            b = pw.chromium.launch(args=list(LAUNCH_ARGS))
            try:
                ctx = b.new_context(**context_options(tmp))
                try:
                    ctx.route("**/*", lambda r: guarded_route(r, base))
                    ctx.route_web_socket(re.compile(".*"), lambda ws: guarded_websocket(ws, base))
                    page = ctx.new_page()
                    page.goto(base + page_path, wait_until="networkidle", timeout=30000)
                    if not same_origin(page.url, base):
                        raise ValueError(f"DEMO_URL_REFUSED: navigated off {base}")
                    page.wait_for_timeout(3000)
                    page.screenshot(path=str(img))
                    video = page.video
                finally:
                    ctx.close()   # the video file is complete only after the context closes
            finally:
                b.close()
            shutil.move(video.path(), vid)   # works across drives, unlike os.replace
    return go


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=("run", "metrics"))
    ap.add_argument("--tickets", type=Path)
    ap.add_argument("--demos", type=Path, default=Path("data") / "demos")
    ap.add_argument("--jarvis", default="jarvis")
    ap.add_argument("--dashboard-url", default="http://127.0.0.1:8765")
    ap.add_argument("--required", default=",".join(REQUIRED), help="comma-separated tickets that must have a demo")
    a = ap.parse_args(argv)
    required = [t for t in a.required.split(",") if TICKET_RE.fullmatch(t.strip())]
    if a.cmd == "run":
        if not a.tickets:
            ap.error("run needs --tickets")
        try:
            rows = load_tickets(a.tickets)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        try:
            capture = playwright_capture(a.dashboard_url)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        a.demos.mkdir(parents=True, exist_ok=True)
        exe = resolve_jarvis(a.jarvis)
        print(f"jarvis: {redact(exe) if exe else 'NOT FOUND'}", file=sys.stderr)
        done = run(rows, a.demos, subprocess_runner(exe), capture)
        print(f"recorded {len(done)} demo(s): {', '.join(done) or '-'}")
    m = metrics(a.demos, required=[t.strip() for t in required])
    print(json.dumps(m))
    return 0 if not m["missing"] else 1   # PASS state of the listed tickets, not the 24 h window


if __name__ == "__main__":
    sys.exit(main())
