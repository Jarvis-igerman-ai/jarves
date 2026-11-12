"""Visual demo of a pull request (JAR-251): before/after screenshots, a short screen recording and terminal images.

Owner, 12 Nov 2026: "the demos are not good, I expect videos or images, something easy to understand". For a PR that
changes something a user sees (dashboard pages, guides, CLI output) CI produces:

- ``<page>-before.png`` / ``<page>-after.png`` and ``<page>-compare.png`` (both side by side, labelled) for every
  affected dashboard page, rendered by the same hermetic DEMO dashboard the page audit uses (tools/all_pages_check.py:
  throw-away home, synthetic data, 127.0.0.1 only, network blocked, no secrets);
- ``phone-before.png`` / ``phone-after.png`` (+ compare) when the phone site changed: a paired DEMO phone session;
- ``walkthrough.webm``: a 10-20 s recording that opens each affected page of the PR head and scrolls through it;
- ``cli-<n>-before.svg`` / ``-after.svg`` (+ ``.png``): rendered terminal output of the affected read-only CLI help;
- ``demo.json``: title, a one-line "what changed for you", the files, the checks (JS errors on the PR side fail ``ok``).

"before" is the PR's merge base (main), "after" the PR head. Each side is rendered by a child Python process whose
PYTHONPATH points at that checkout's ``core/src`` and ``core/tools``; the child verifies which ``jarves`` it imported.

    python tools/pr_demo.py plan --base SHA [--files-from FILE]          # JSON: is the change user-facing, which pages
    python tools/pr_demo.py ci --base SHA --base-core ../../base/core --pr 12 --out ../demo
    python tools/pr_demo.py capture --core DIR --pages home,settings --label after --out DIR [--video]   (child)
    python tools/pr_demo.py cli --core DIR --cmds "--help|phone --help" --label after --out DIR          (child)

The .github/workflows/pr-demo.yml job uploads ``--out`` as the artifact ``demo-<PR>``. A demo never blocks a merge.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # core/
REPO = ROOT.parent
PKG = "core/src/jarves/"
STATIC = PKG + "dashboard_static/"
MAX_PAGES = 4
MAX_CLI = 3
SHARED = {"app.js", "shell.js", "shell.css", "style.css", "i18n.js", "index.html", "strip.js", "strip.css"}
OVERVIEW = ["home", "settings"]                      # what a change to the shared shell is shown on
DESKTOP = {"width": 1280, "height": 800}
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SECRET = re.compile(r"(?i)((?:token|secret|password|passwd|api[_-]?key|authorization)\S*\s*[=:]\s*)\S+")
STORY = re.compile(r"(?im)^\s*(?:\*\*)?(?:what changed for you|now you can)(?:\*\*)?\s*:\s*(?:\*\*)?\s*(.+?)\s*$")
CHILD_TIMEOUT = 300                                  # s per child; at most 4 children (20 min) + setup stay under the job's 30 min
PHONE = {"width": 390, "height": 844}
CLI_FILE = re.compile(r"^core/src/jarves/(?:([a-z0-9]+)_cli|cli|clilang|cli_parity)\.py$")


# ---------------------------------------------------------------- plan (pure: paths -> what to show)
def registered_pages(js_text: str) -> list[str]:
    """Page ids a dashboard script registers (``PAGES.x =`` / ``PAGES["x"] =``)."""
    found = re.findall(r"PAGES(?:\.([a-z0-9_]+)|\[['\"]([a-z0-9_-]+)['\"]\])\s*=", js_text)
    return list(dict.fromkeys(a or b for a, b in found))


def plan(files: list[str], read=lambda p: (REPO / p).read_text(encoding="utf-8", errors="replace"),
         known_pages: list[str] | None = None) -> dict:
    """Which user-visible surfaces a set of changed repo paths touches. ``read`` reads a repo path of the PR head."""
    pages: list[str] = []
    cli: list[str] = []
    phone = False
    reasons: list[str] = []

    def add(ps, why, force=False):
        for p in ps:                                     # modules (pages.d) and guides are not in the static nav
            if p not in pages and (force or known_pages is None or p in known_pages or p.startswith("guide/")):
                pages.append(p)
        reasons.append(why)

    for f in files:
        f = f.replace("\\", "/")
        if f.startswith(STATIC + "mobile/"):
            phone = True
            reasons.append(f"{f}: phone site")
        elif f.startswith(STATIC + "locales/") or (f.startswith(STATIC) and f[len(STATIC):] in SHARED):
            add(OVERVIEW, f"{f}: shared dashboard shell")
        elif f.startswith(STATIC) and f.endswith(".js"):
            try:
                ids = registered_pages(read(f))
            except OSError:                              # deleted in the PR
                ids = []
            add(ids or OVERVIEW, f"{f}: page script")
        elif f.startswith(STATIC) and f.endswith((".css", ".html")):
            stem = Path(f).stem
            add([stem] if known_pages and stem in known_pages else OVERVIEW, f"{f}: page style")
        elif f.startswith(PKG + "pages.d/") and f.endswith(".toml"):
            try:
                m = re.search(r'(?m)^page\s*=\s*"([a-z0-9_-]+)"', read(f))
            except OSError:
                m = None
            add([m.group(1)] if m else [], f"{f}: page module", force=True)
        elif f.startswith(PKG + "guides/data/") and f.endswith(".toml"):
            add(["guide/" + Path(f).stem], f"{f}: guide")
        elif CLI_FILE.match(f):
            m = CLI_FILE.match(f)
            cmd = f"{m.group(1)} --help" if m.group(1) else "--help"
            if cmd not in cli:
                cli.append(cmd)
            reasons.append(f"{f}: command line")
    if cli and "--help" not in cli and len(cli) < MAX_CLI:
        cli.append("--help")
    return {"user_facing": bool(pages or cli or phone), "pages": pages[:MAX_PAGES], "more_pages": pages[MAX_PAGES:],
            "phone": phone, "cli": cli[:MAX_CLI], "reasons": reasons}


def changed_files(base: str) -> list[str]:
    mb = subprocess.run(["git", "merge-base", base, "HEAD"], cwd=REPO, capture_output=True, text=True, check=True)
    out = subprocess.run(["git", "diff", "--name-only", mb.stdout.strip(), "HEAD"], cwd=REPO, capture_output=True,
                         text=True, check=True)
    return [x for x in out.stdout.splitlines() if x.strip()]


def known_pages_of(core: Path) -> list[str]:
    sys.path.insert(0, str(core / "tools"))
    try:
        import all_pages_check as apc
        return apc.nav_pages()
    except Exception:                                    # an old checkout: no filter
        return []
    finally:
        sys.path.pop(0)


def what_changed(title: str, body: str) -> str:
    m = STORY.search(body or "")
    text = m.group(1) if m else re.sub(r"^\s*(?:JAR-\d+|[a-z]+(?:\([^)]*\))?)\s*:\s*", "", title or "").strip()
    return scrub(text)[:200]


def scrub(text: str) -> str:
    return SECRET.sub(r"\1[redacted]", ANSI.sub("", text))


# ---------------------------------------------------------------- terminal image
def terminal_svg(title: str, text: str, max_lines: int = 40, cols: int = 100) -> str:
    """A terminal window as SVG (no dependencies). Long output is cut with a visible marker."""
    lines = scrub(text).expandtabs(4).splitlines() or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [f"... ({len(lines) - max_lines + 1} more lines)"]
    lines = [ln if len(ln) <= cols else ln[:cols - 1] + "…" for ln in lines]
    w, lh, top = 16 + cols * 8 + 16, 18, 44
    h = top + lh * len(lines) + 16
    rows = "\n".join(f'<text x="16" y="{top + lh * (i + 1) - 4}" xml:space="preserve">{html.escape(ln)}</text>'
                     for i, ln in enumerate(lines))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<rect width="{w}" height="{h}" rx="8" fill="#1e1e1e"/>'
            f'<circle cx="18" cy="16" r="5" fill="#ff5f56"/><circle cx="36" cy="16" r="5" fill="#ffbd2e"/>'
            f'<circle cx="54" cy="16" r="5" fill="#27c93f"/>'
            f'<text x="72" y="21" fill="#9a9a9a" font-family="monospace" font-size="13">{html.escape(title)}</text>'
            f'<g fill="#e6e6e6" font-family="DejaVu Sans Mono, Consolas, monospace" font-size="13">\n{rows}\n</g></svg>')


# ---------------------------------------------------------------- child: render one side
def _assert_core(core: Path) -> None:
    import jarves
    got = Path(jarves.__file__).resolve()
    if core.resolve() not in got.parents:
        raise SystemExit(f"imported jarves from {got}, expected under {core}")


def _launch(pw):
    try:
        import all_pages_check as apc
        return pw.chromium.launch(headless=True, **apc.launch_kwargs("msedge" if sys.platform == "win32" else ""))
    except SystemExit:
        return pw.chromium.launch(headless=True)


def _settle(p) -> None:
    try:
        p.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    p.wait_for_timeout(600)


def local_only(url: str) -> bool:
    """Requests a demo page may make: this machine's DEMO dashboard and local files, never another host."""
    from urllib.parse import urlsplit
    u = urlsplit(url)
    return u.scheme in ("file", "data", "blob", "about") or (u.scheme in ("http", "https", "ws", "wss")
                                                             and u.hostname in ("127.0.0.1", "localhost", "::1"))


def guard(ctx):
    """Abort every browser request to another host (PR code may try to fetch anything from the runner)."""
    ctx.route("**/*", lambda route: route.continue_() if local_only(route.request.url) else route.abort())
    return ctx


def phone_shot(b, d, label: str, out: Path) -> tuple[str, list[str]]:
    """The phone site (/m/) as a phone sees it: pair a DEMO phone with a one-time code, screenshot the app view."""
    errs: list[str] = []
    d.mobile.enabled = True                              # the throw-away DEMO dashboard only; 127.0.0.1, test data
    code = d.mobile.new_code(by="demo")["code"]
    ctx = guard(b.new_context(viewport=PHONE, is_mobile=True, has_touch=True, locale="en-US", color_scheme="light"))
    p = ctx.new_page()
    p.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    try:
        p.goto(d.url.split("/?")[0].rstrip("/") + f"/m/#c={code}")
        p.wait_for_selector("#pairView:not([hidden])", timeout=20000)
        p.fill("#name", "Demo phone")
        p.tap("#pairForm button[type=submit]")
        p.wait_for_selector("#appView:not([hidden])", timeout=20000)
        _settle(p)
        f = out / f"phone-{label}.png"
        p.screenshot(path=str(f))
        return f.name, errs
    except Exception as e:
        errs.append(f"{type(e).__name__}: {str(e).splitlines()[0][:200]}")
        return "", errs
    finally:
        ctx.close()


def capture(core: Path, pages: list[str], label: str, out: Path, video: bool, lang: str, phone: bool = False) -> dict:
    """Screenshots of each page (and the phone site) from the DEMO dashboard of ``core``; optional walkthrough video."""
    import all_pages_check as apc
    from playwright.sync_api import sync_playwright
    _assert_core(core)
    out.mkdir(parents=True, exist_ok=True)
    res: dict = {"label": label, "shots": {}, "errors": {}, "video": ""}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        d, _sid = apc.start_dashboard(Path(tmp))
        try:
            with sync_playwright() as pw:
                b = _launch(pw)
                try:
                    targets = [(pg, d.url + "#/" + pg, DESKTOP) for pg in pages]
                    for name, url, vp in targets:
                        ctx = guard(b.new_context(viewport=vp, locale="en-US", color_scheme="light"))
                        ctx.add_init_script(f"try{{localStorage.setItem('jarves-lang','{lang}')}}catch(e){{}}")
                        p = ctx.new_page()
                        errs: list[str] = []
                        p.on("pageerror", lambda e, errs=errs: errs.append(f"pageerror: {e}"))
                        p.on("console", lambda m, errs=errs: errs.append(f"console.error: {m.text}")
                             if m.type == "error" else None)
                        try:
                            resp = p.goto(url)
                            if resp is not None and resp.status >= 400:
                                errs.append(f"HTTP {resp.status}")
                            _settle(p)
                            f = out / f"{safe(name)}-{label}.png"
                            p.screenshot(path=str(f))
                            res["shots"][name] = f.name
                        except Exception as e:           # a page that does not exist on main yet, a timeout
                            errs.append(f"{type(e).__name__}: {str(e).splitlines()[0][:200]}")
                        res["errors"][name] = [scrub(x)[:300] for x in errs]
                        ctx.close()
                    if phone:
                        shot, errs = phone_shot(b, d, label, out)
                        if shot:
                            res["shots"]["phone"] = shot
                        res["errors"]["phone"] = [scrub(x)[:300] for x in errs] or ([] if shot else ["no screenshot"])
                    if video and targets:
                        record_video(res, b, targets, out, lang)
                finally:
                    b.close()
        finally:
            d.stop()
    return res


def record_video(res: dict, b, targets, out: Path, lang: str) -> None:
    """The walkthrough is extra: no ffmpeg or a broken recording is reported, the screenshots and checks are kept."""
    try:
        res["video"] = walkthrough(b, targets, out, lang)
    except Exception as e:
        res["video_error"] = scrub(f"{type(e).__name__}: {(str(e).splitlines() or [''])[0]}")[:200]


def walkthrough(b, targets, out: Path, lang: str) -> str:
    """Open each page and scroll through it slowly; about 4 s per page, 20 s at most."""
    vdir = out / "_video"
    ctx = guard(b.new_context(viewport=DESKTOP, record_video_dir=str(vdir), record_video_size=DESKTOP))
    ctx.add_init_script(f"try{{localStorage.setItem('jarves-lang','{lang}')}}catch(e){{}}")
    p = ctx.new_page()
    per = max(2500, min(5000, 18000 // len(targets)))
    for name, url, vp in targets:
        try:
            p.set_viewport_size(vp)
            p.goto(url)
            _settle(p)
            steps = 6
            for i in range(steps):
                p.evaluate(f"window.scrollTo({{top: (document.body.scrollHeight - innerHeight) * {(i + 1) / steps}, "
                           "behavior: 'smooth'})")
                p.wait_for_timeout(per // (steps + 2))
            p.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
            p.wait_for_timeout(per // (steps + 2))
        except Exception:
            continue
    vid = p.video
    ctx.close()                                          # the video file is complete only after the context closes
    dest = out / "walkthrough.webm"
    if vid and Path(vid.path()).exists():               # no ffmpeg: no file, the screenshots still count
        Path(vid.path()).replace(dest)
    for f in vdir.glob("*"):
        f.unlink()
    if vdir.exists():
        vdir.rmdir()
    return dest.name if dest.exists() else ""


def cli_capture(core: Path, cmds: list[str], label: str, out: Path) -> dict:
    """Run read-only ``jarvis <cmd>`` (help output only) against a throw-away home; write terminal SVGs."""
    _assert_core(core)
    out.mkdir(parents=True, exist_ok=True)
    res: dict = {"label": label, "cli": []}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as home:
        env = {**os.environ, "JARVES_HOME": home, "JARVES_TEST_OFFLINE": "1", "NO_COLOR": "1", "COLUMNS": "100",
               "PYTHONPATH": os.pathsep.join([str(core / "src"), str(core / "tools")]), "PYTHONIOENCODING": "utf-8"}
        env.pop("OPENAI_API_KEY", None)
        for i, cmd in enumerate(cmds, 1):
            args = cmd.split()
            if not args or args[-1] != "--help":         # only help output: no side effects in CI
                continue
            code = "import sys; from jarves.cli import main; sys.argv = ['jarvis'] + sys.argv[1:]; sys.exit(main())"
            try:
                r = subprocess.run([sys.executable, "-c", code, *args], env=env, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=60)
                text, rc = (r.stdout + r.stderr), r.returncode
            except subprocess.TimeoutExpired:
                text, rc = "(timed out)", -1
            f = out / f"cli-{i}-{label}.svg"
            f.write_text(terminal_svg(f"$ jarvis {cmd}", text), encoding="utf-8")
            res["cli"].append({"cmd": cmd, "rc": rc, "file": f.name})
    return res


def svg_to_png(out: Path) -> None:
    """PNG copies of the terminal SVGs (Telegram and some viewers do not show SVG)."""
    svgs = sorted(out.glob("cli-*.svg"))
    if not svgs:
        return
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = _launch(pw)
        try:
            for s in svgs:
                p = b.new_page()
                p.goto(s.resolve().as_uri())
                p.locator("svg").screenshot(path=str(s.with_suffix(".png")))
                p.close()
        finally:
            b.close()


def compare(out: Path, pages: list[str]) -> list[str]:
    """``<page>-compare.png``: before and after side by side with labels (rendered by the browser, no Pillow).
    Returns the pages whose before and after screenshots are identical."""
    same = []
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = _launch(pw)
        try:
            for name in pages:
                a, z = out / f"{safe(name)}-before.png", out / f"{safe(name)}-after.png"
                if not z.exists():
                    continue
                if a.exists() and a.read_bytes() == z.read_bytes():
                    same.append(name)
                left = (f'<img src="{a.resolve().as_uri()}">' if a.exists()
                        else '<div class="none">This page did not exist before this change.</div>')
                doc = ("<!doctype html><meta charset=utf-8><style>body{margin:0;font:600 20px system-ui;background:#f4f4f5}"
                       ".row{display:flex;gap:16px;padding:16px}.col{flex:1}.col p{margin:0 0 8px}"
                       "img{width:100%;border:1px solid #ccc;border-radius:6px}.none{padding:80px 20px;color:#666}"
                       f"</style><div class=row><div class=col><p>Before (main)</p>{left}</div>"
                       f'<div class=col><p>After (this change){" - no visible difference" if name in same else ""}</p><img src="{z.resolve().as_uri()}"></div></div>')
                h = out / f"_{safe(name)}.html"
                h.write_text(doc, encoding="utf-8")
                p = b.new_page(viewport={"width": 1600, "height": 300})
                p.goto(h.resolve().as_uri())
                p.wait_for_load_state("load")
                f = out / f"{safe(name)}-compare.png"
                p.screenshot(path=str(f), full_page=True)
                p.close()
                h.unlink()
        finally:
            b.close()
    return same


def safe(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or "page"


# ---------------------------------------------------------------- ci: both sides + demo.json
def _child(core: Path, args: list[str]) -> dict:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(core / "src"), str(core / "tools")])}
    try:
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), *args, "--core", str(core)], env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=CHILD_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"failed": True, "rc": -1, "log": f"timed out after {CHILD_TIMEOUT} s"}
    last = (r.stdout.strip().splitlines() or [""])[-1]
    try:
        return json.loads(last)
    except ValueError:
        return {"failed": True, "rc": r.returncode, "log": scrub((r.stderr or r.stdout)[-1500:])}


def ci(base: str, base_core: Path | None, pr: str, out: Path, title: str, body: str, files: list[str] | None,
       lang: str = "en") -> dict:
    files = changed_files(base) if files is None else files
    pl = plan(files, known_pages=known_pages_of(ROOT) or None)
    out.mkdir(parents=True, exist_ok=True)
    demo = {"pr": pr, "title": scrub(title)[:200], "what_changed": what_changed(title, body), "plan": pl,
            "files": [], "checks": [], "notes": [], "ok": True, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if not pl["user_facing"]:
        demo["notes"].append("No user-visible change found (no dashboard page, phone site, guide or CLI file).")
    sides = [("after", ROOT)] + ([("before", base_core)] if base_core and (base_core / "src").is_dir() else [])
    if len(sides) == 1:
        demo["notes"].append("No base checkout: only the 'after' side was rendered.")
    for label, core in sides:
        if pl["pages"] or pl["phone"]:
            args = ["capture", "--label", label, "--out", str(out), "--pages=" + ",".join(pl["pages"]), "--lang", lang]
            r = _child(core, args + (["--video"] if label == "after" and pl["pages"] else [])
                       + (["--phone"] if pl["phone"] else []))
            if r.get("failed"):
                demo["notes"].append(f"{label}: page capture failed: {r.get('log', '')}")
                if label == "after":
                    demo["ok"] = False
            if label == "after" and pl["pages"] and not r.get("failed") and not r.get("video"):
                demo["notes"].append("after: no walkthrough video was recorded. " + r.get("video_error", ""))
            for name, errs in (r.get("errors") or {}).items():
                if label == "after":
                    demo["checks"].append({"name": f"{name}: no browser errors", "ok": not errs, "detail": errs[:5]})
        if pl["cli"]:
            r = _child(core, ["cli", "--label", label, "--out", str(out), "--cmds=" + "|".join(pl["cli"])])
            if r.get("failed"):
                demo["notes"].append(f"{label}: CLI capture failed: {r.get('log', '')}")
            elif label == "after":
                if not r.get("cli"):
                    demo["checks"].append({"name": "terminal images rendered", "ok": False, "detail": []})
                for c in r.get("cli", []):
                    demo["checks"].append({"name": f"jarvis {c['cmd']} exits 0", "ok": c["rc"] == 0, "detail": []})
    shown = pl["pages"] + (["phone"] if pl["phone"] else [])
    if shown:
        try:
            for name in compare(out, shown):
                demo["notes"].append(f"{name}: looks the same before and after (the change is not visible here).")
        except Exception as e:
            demo["notes"].append(f"side-by-side images failed: {type(e).__name__}")
    try:
        svg_to_png(out)
    except Exception as e:
        demo["notes"].append(f"terminal PNGs failed: {type(e).__name__}")
    demo["ok"] = demo["ok"] and all(c["ok"] for c in demo["checks"])
    demo["files"] = sorted(f.name for f in out.iterdir() if f.is_file() and f.name != "demo.json")
    (out / "demo.json").write_text(json.dumps(demo, indent=1, ensure_ascii=False), encoding="utf-8")
    return demo


def summary_md(demo: dict) -> str:
    """Markdown for the job summary (GitHub cannot inline artifact images, so it lists them)."""
    lines = [f"### Demo for PR #{demo['pr']}: {demo['what_changed'] or demo['title']}",
             f"Result: {'OK' if demo['ok'] else 'PROBLEMS FOUND'}. Download the artifact `demo-{demo['pr']}`.", ""]
    lines += [f"- {'pass' if c['ok'] else 'FAIL'}: {c['name']}" for c in demo["checks"]]
    lines += [f"- note: {n}" for n in demo["notes"]]
    lines += ["", "Files: " + ", ".join(demo["files"])]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("plan")
    s.add_argument("--base")
    s.add_argument("--files-from")
    s = sub.add_parser("ci")
    s.add_argument("--base", required=True)
    s.add_argument("--base-core")
    s.add_argument("--pr", default="0")
    s.add_argument("--out", required=True)
    s.add_argument("--files-from")
    s.add_argument("--lang", default="en", choices=["en", "he"])
    s.add_argument("--summary", help="append the markdown summary to this file (GITHUB_STEP_SUMMARY)")
    s = sub.add_parser("capture")
    s.add_argument("--core", required=True)
    s.add_argument("--pages", default="")
    s.add_argument("--video", action="store_true")
    s.add_argument("--phone", action="store_true", help="also the phone site (/m/) in a paired DEMO phone session")
    s.add_argument("--label", required=True, choices=["before", "after"])
    s.add_argument("--out", required=True)
    s.add_argument("--lang", default="en", choices=["en", "he"])
    s = sub.add_parser("cli")
    s.add_argument("--core", required=True)
    s.add_argument("--cmds", required=True)
    s.add_argument("--label", required=True, choices=["before", "after"])
    s.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def files_arg():
        return Path(a.files_from).read_text(encoding="utf-8").split() if a.files_from else None

    if a.cmd == "plan":
        files = files_arg() if a.files_from else changed_files(a.base or "origin/main")
        print(json.dumps(plan(files, known_pages=known_pages_of(ROOT) or None), indent=1))
        return 0
    if a.cmd == "ci":
        demo = ci(a.base, Path(a.base_core) if a.base_core else None, a.pr, Path(a.out),
                  os.environ.get("PR_TITLE", ""), os.environ.get("PR_BODY", ""), files_arg(), a.lang)
        text = summary_md(demo)
        print(text)
        if a.summary:
            with open(a.summary, "a", encoding="utf-8") as fh:
                fh.write(text)
        return 0                                         # a demo informs the owner; it never blocks a merge
    core = Path(a.core).resolve()
    here = Path(__file__).resolve().parent             # the script dir comes first on sys.path: use the side's own tools
    sys.path[:] = [str(core / "tools"), str(core / "src")] + [x for x in sys.path if x and Path(x).resolve() != here]
    if a.cmd == "capture":
        r = capture(core, [x for x in a.pages.split(",") if x], a.label, Path(a.out), a.video, a.lang, a.phone)
    else:
        r = cli_capture(core, [x.strip() for x in a.cmds.split("|") if x.strip()], a.label, Path(a.out))
    print(json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
