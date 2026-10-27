"""Browser Skill backend (SW-V3 P1.2, Issue #17; owner decision 20 Oct 2026):

- FULL CONTROL (open, read, click, fill) only on sites in the owner's allow-list. The list lives in
  <state_dir>/browser_sites.json, starts EMPTY and is edited by the owner only: dashboard Browser page
  (GET/POST /api/browser...), `jarvis browser allow|remove <site>`. The model has no tool that changes it.
- Any other site: READ-ONLY, and only after the owner approved opening it on the screen (registry.confirm; asked
  once per host per browser session). Clicking or filling there is refused.
- Purchases / payments / credential entry ALWAYS ask on the screen first, also on allow-listed sites: decided from
  the real element in the page (password / card / one-time-code fields, buy / pay / checkout buttons, payment
  forms, card-number-like values), never from what the model says about it.
- Page text is UNTRUSTED (like mail and camera images): results carry it under `untrusted_*` with a note, and the
  camera taint guard (camera/wire.install_taint_guard) makes every OTHER side-effect tool the model calls in the
  next 60 s ask the user first.
- Isolation: a fresh in-memory browser context per session (no personal profile, cookies or saved passwords are
  ever read; nothing persists after close), downloads refused, service workers blocked, no permissions granted,
  only http/https, AI-provider account pages (ChatGPT / Claude quota) blocked outright, and every top-level
  navigation (links, scripts, popups) checked against the allow-list / approvals by a request route.
- Engine: Playwright (async). Windows drives the INSTALLED Edge or Chrome (no download); Linux / Pi uses
  Playwright's Chromium or the distro chromium. Headless; launched on first use, closed after IDLE_CLOSE_S.
  Activity is visible: health component "browser" (dashboard / `jarvis status`) + log lines with the host only
  (never full URLs, typed values or page text).
"""
from __future__ import annotations

import asyncio
import importlib.util
import ipaddress
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from . import features as feat
from . import logs
from .capabilities import PI_ROADMAP

FEATURE = "browser"
SITES_FILE = "browser_sites.json"
MAX_SITES = 200
MAX_TEXT_CHARS = 6000
MAX_ELEMENTS = 60
NAV_TIMEOUT_MS = 20_000
ACTION_TIMEOUT_MS = 8_000
IDLE_CLOSE_S = 300.0
# AI-provider account / quota pages: never scraped (SW-V3 work package P1.2), even if someone allow-lists them.
BLOCKED_HOSTS = ("chatgpt.com", "chat.openai.com", "platform.openai.com", "claude.ai", "console.anthropic.com")
_HOST_RX = re.compile(r"^(localhost|[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+)$")

# ---------------------------------------------------------------------- what always needs the owner's approval
PURCHASE_RX = re.compile(
    r"\b(buy|purchase|pay|payment|pay now|checkout|check out|place order|order now|confirm order|complete order|"
    r"submit order|subscribe|donate|billing|add card|upgrade plan)\b|"
    r"קנ[הויי]|קנייה|לקנות|רכיש|לרכוש|תשלום|לשלם|שלם עכשיו|הזמנ|להזמין|הזמן עכשיו|לקופה|מעבר לקופה|מנוי|תרומ",
    re.I)
CRED_RX = re.compile(
    r"pass(word)?|passwd|pwd|(?<![a-z])pin(?![a-z])|otp|one.?time|2fa|totp|cvv|cvc|csc|card|credit|iban|"
    r"(?<![a-z])ssn(?![a-z])|security.?code|"
    r"סיסמ|קוד אימות|קוד סודי|כרטיס|אשראי|תעודת זהות|ת\.ז", re.I)
CRED_AUTOCOMPLETE = re.compile(r"^(current-password|new-password|one-time-code|cc-[a-z-]+)$")


def luhn_like(value: str) -> bool:
    """A 13-19 digit number passing the Luhn check (a payment card number typed into any field)."""
    digits = re.sub(r"[\s-]", "", value or "")
    if not digits.isdigit() or not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def click_sensitive(meta: dict) -> str | None:
    """Why pressing this element is a purchase / payment (None = not)."""
    text = " ".join(str(meta.get(k) or "") for k in ("label", "name", "id", "href"))
    if meta.get("submits") and meta.get("in_form"):
        text += " " + str(meta.get("form_text") or "")
        if meta.get("form_payment"):
            return "payment form"
    if PURCHASE_RX.search(text):
        return "purchase or payment"
    return None


def fill_sensitive(meta: dict, value: str) -> str | None:
    """Why typing into this field is credential / payment entry (None = not)."""
    if (meta.get("type") or "") == "password":
        return "password"
    if CRED_AUTOCOMPLETE.match(meta.get("autocomplete") or ""):
        return "credential or payment field"
    if CRED_RX.search(" ".join(str(meta.get(k) or "") for k in ("name", "id", "placeholder", "label"))):
        return "credential or payment field"
    if luhn_like(value):
        return "card number"
    return None


# ---------------------------------------------------------------------- sites and URLs
def normalize_site(entry: str) -> str:
    """'https://www.Example.com/x' -> 'example.com'. Raises ValueError for anything that is not a host name."""
    s = (entry or "").strip().lower()
    if not s:
        raise ValueError("empty site")
    if "://" not in s:
        s = "http://" + s
    try:
        host = urlsplit(s).hostname or ""
    except ValueError:
        raise ValueError(f"not a web site: {entry!r}") from None
    if host.startswith("www."):
        host = host[4:]
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError(f"not a web site: {entry!r}") from None
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    if not _HOST_RX.match(host):
        raise ValueError(f"not a web site: {entry!r} (write it like example.com)")
    return host


def host_matches(host: str, site: str) -> bool:
    return host == site or host.endswith("." + site)


def blocked(host: str) -> bool:
    return any(host_matches(host, b) for b in BLOCKED_HOSTS)


def url_host(url: str) -> str:
    """Host of an http(s) URL ('' for anything else: file:, data:, javascript:, chrome:...)."""
    try:
        u = urlsplit((url or "").strip())
    except ValueError:
        return ""
    if u.scheme not in ("http", "https") or not u.hostname:
        return ""
    return u.hostname.lower()


_OTHER_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d+(?:[/?#]|$))")


def with_scheme(url: str) -> str:
    """'shop.example.com/x' -> 'https://shop.example.com/x'. Anything that starts with another scheme and has no '//'
    (javascript:, data:, mailto:...) is NOT a web address: it must not be 'completed' into a fake host and then asked
    about (campaign 20 Oct, B-02); 'host:8080/x' stays a host with a port."""
    url = (url or "").strip()
    if url and "://" not in url:
        if _OTHER_SCHEME.match(url):
            raise BrowserError("BROWSER_BAD_URL", "only http/https web addresses can be opened")
        url = "https://" + url
    return url


class Sites:
    """The owner's allow-list (one JSON file; re-read on every check, so a dashboard edit applies at once)."""

    def __init__(self, home: Path | None) -> None:
        self.path = Path(home) / SITES_FILE if home is not None else None

    def list(self) -> list[str]:
        if self.path is None:
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out = []
        for s in raw.get("sites", []) if isinstance(raw, dict) else []:
            try:
                n = normalize_site(str(s))
            except ValueError:
                continue
            if n not in out:
                out.append(n)
        return out

    def _save(self, sites: list[str], by: str) -> None:
        if self.path is None:
            raise ValueError("no data folder")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "sites": sites, "changed_by": by, "changed_at": time.time()},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        logs.info("browser", "BROWSER_SITES_CHANGED", "browser allow-list changed", n=len(sites), by=by)

    def add(self, entry: str, by: str = "user") -> list[str]:
        site = normalize_site(entry)
        if blocked(site):
            raise ValueError(f"{site} is an AI account page; JARVES never browses it")
        sites = self.list()
        if site not in sites:
            if len(sites) >= MAX_SITES:
                raise ValueError(f"at most {MAX_SITES} sites")
            sites.append(site)
            self._save(sites, by)
        return sites

    def remove(self, entry: str, by: str = "user") -> list[str]:
        site = normalize_site(entry)
        sites = [s for s in self.list() if s != site]
        self._save(sites, by)
        return sites

    def allowed(self, host: str) -> bool:
        return bool(host) and not blocked(host) and any(host_matches(host, s) for s in self.list())


# ---------------------------------------------------------------------- engine discovery (cheap, no launch)
def _win_paths(*rel: str) -> list[Path]:
    roots = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    return [Path(r) / p for r in roots if r for p in rel]


def _bundled_chromium() -> bool:
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    roots = [Path(base)] if base and base != "0" else []
    roots += [Path(os.environ.get("LOCALAPPDATA", "~")) / "ms-playwright", Path.home() / ".cache" / "ms-playwright"]
    return any(r.is_dir() and any(r.glob("chromium*")) for r in roots)


def candidates() -> list[tuple[str, dict]]:
    """Browsers to try, best first: (name, chromium.launch kwargs)."""
    out: list[tuple[str, dict]] = []
    if sys.platform == "win32":
        if any(p.is_file() for p in _win_paths(r"Microsoft\Edge\Application\msedge.exe")):
            out.append(("Microsoft Edge", {"channel": "msedge"}))
        if any(p.is_file() for p in _win_paths(r"Google\Chrome\Application\chrome.exe")):
            out.append(("Google Chrome", {"channel": "chrome"}))
    if _bundled_chromium():
        out.append(("Playwright Chromium", {}))
    if sys.platform != "win32":
        for exe in ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/snap/bin/chromium"):
            if Path(exe).is_file():
                out.append((f"Chromium ({exe})", {"executable_path": exe}))
    return out


def _playwright_present() -> bool:
    try:
        return importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):
        return False


def _probe(_ctx) -> tuple[bool, str]:
    if not _playwright_present():
        return False, "Playwright is not installed (jarvis features on browser installs it)"
    if not candidates():
        return False, ("no browser found: install Microsoft Edge or Chrome (Windows) / `playwright install chromium` "
                       "or apt chromium (Pi)")
    return True, ""


feat.register(FEATURE, "דפדפן", "Browser", False, probe=_probe, slow=True, needs=["playwright", "edge_or_chromium"],
              risk="high", skills=["browser"],
              about="opens web pages for you: full control only on your allow-list (empty at first), other sites "
                    "read-only after you approve; purchases, payments and passwords always ask on the screen",
              targets={"windows": {"status": "implemented_unverified",
                                   "evidence": "core/tests/test_browser.py drives the installed Edge (headless) "
                                               "against a local test page: allow-list, read-only ask, confirmations",
                                   "blocker": "not yet used in a real spoken conversation"},
                       "linux_arm64_pi": {"status": "implemented_unverified", "roadmap_issue": PI_ROADMAP,
                                          "blocker": "Playwright linux-aarch64 wheel + Chromium never run on a Pi 5; "
                                                     "RAM/CPU of a headless page not measured there"}},
              permissions=["read", "action (allow-listed sites only)",
                           "always confirmed: purchases, payments, credentials"],
              settings="dashboard Browser page / jarvis browser allow <site>")


# ---------------------------------------------------------------------- page scripts (run inside the page)
ELEMENTS_JS = """([attr, max]) => {
  document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=link], ' +
              '[role=checkbox], [role=tab], [contenteditable=true]';
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim().slice(0, 80);
  const out = [];
  for (const e of document.querySelectorAll(sel)) {
    if (out.length >= max) break;
    const r = e.getBoundingClientRect(), st = getComputedStyle(e);
    if (r.width < 1 || r.height < 1 || st.visibility === 'hidden' || st.display === 'none') continue;
    const ref = String(out.length + 1);
    e.setAttribute(attr, ref);
    const t = (e.getAttribute('type') || '').toLowerCase();
    const btn = ['submit', 'button', 'reset'].includes(t);
    const label = clean(e.getAttribute('aria-label') || (e.labels && e.labels[0] && e.labels[0].innerText) ||
                        (btn ? e.value : '') || (e.tagName === 'INPUT' ? '' : e.innerText) || e.placeholder ||
                        e.getAttribute('name') || e.title);
    const item = {ref, tag: e.tagName.toLowerCase(), label};
    if (t) item.type = t;
    if (e.tagName === 'A') item.href = e.href.slice(0, 200);
    out.push(item);
  }
  return out;
}"""

META_JS = """(e) => {
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim().slice(0, 200);
  const t = (e.getAttribute('type') || '').toLowerCase();
  const tag = e.tagName.toLowerCase();
  const form = e.form || e.closest('form');
  const btn = ['submit', 'button', 'reset', 'image'].includes(t);
  const submits = !!form && ((tag === 'button' && (t === '' || t === 'submit')) || (tag === 'input' && (t === 'submit' || t === 'image')));
  const meta = {tag, type: t, name: clean(e.getAttribute('name')), id: clean(e.id),
    autocomplete: clean(e.getAttribute('autocomplete')).toLowerCase(), placeholder: clean(e.getAttribute('placeholder')),
    label: clean(e.getAttribute('aria-label') || (e.labels && e.labels[0] && e.labels[0].innerText) ||
                 (btn ? e.value : '') || (tag === 'input' ? '' : e.innerText) || e.placeholder || e.title),
    href: tag === 'a' ? e.href.slice(0, 300) : '', in_form: !!form, submits, editable: !!e.isContentEditable,
    form_text: '', form_payment: false};
  if (form) {
    meta.form_payment = !!form.querySelector('[autocomplete^="cc-"], input[name*="card" i], input[name*="cvv" i], ' +
                                             'input[name*="cvc" i], input[id*="card" i]');
    meta.form_text = clean([form.getAttribute('action') || '', form.id, form.getAttribute('name') || '',
      ...Array.from(form.querySelectorAll('button, input[type=submit]')).map(b => b.innerText || b.value)].join(' '));
  }
  return meta;
}"""


class BrowserError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


ConfirmFn = Callable[[str], Awaitable[bool]]


class BrowserService:
    """One headless browser, one isolated context, one current page. All methods are serialized (asyncio.Lock)."""

    def __init__(self, home: Path | None, *, confirm: ConfirmFn | None = None, health=None,
                 headless: bool = True, idle_close_s: float = IDLE_CLOSE_S) -> None:
        self.sites = Sites(home)
        self.confirm = confirm
        self.health = health
        self.headless = headless
        self.idle_close_s = idle_close_s
        self.engine = ""
        self._pw = self._browser = self._context = self._page = None
        self._lock = asyncio.Lock()
        self._approved: set[str] = set()        # read-only hosts the owner approved in THIS browser session
        self._blocked: list[str] = []           # top-level navigations refused since the last tool call
        self._attr = ""
        self._idle: asyncio.TimerHandle | None = None
        self._idle_task: asyncio.Task | None = None
        self.activity: list[dict] = []          # recent actions (host + action + result; no URLs, values or text)

    # --- policy views
    def mode(self, host: str) -> str:
        """control (allow-listed) | read_only (approved this session) | ask (needs approval) | blocked."""
        if not host or blocked(host):
            return "blocked"
        if self.sites.allowed(host):
            return "control"
        return "read_only" if host in self._approved else "ask"

    def current_host(self) -> str:
        return url_host(self._page.url) if self._page is not None else ""

    def status(self) -> dict:
        host = self.current_host()
        return {"open": self._page is not None, "engine": self.engine, "host": host,
                "mode": self.mode(host) if host else "", "approved_read_only": sorted(self._approved),
                "sites": self.sites.list(), "recent": self.activity[-10:]}

    def _note(self, action: str, host: str, result: str) -> None:
        self.activity.append({"ts": time.time(), "action": action, "host": host, "result": result})
        del self.activity[:-20]
        # blocked / declined are normal policy decisions (INFO); only a real failure is a warning
        log = logs.warn if result == "failed" else logs.info
        log("browser", "BROWSER_" + action.upper(), "browser action", action=action, host=host, outcome=result)
        if self.health is not None:
            if self._page is None:
                self.health.set("browser", "ok", "idle (no page open)")
            else:
                cur = self.current_host()
                self.health.set("browser", "ok", f"page open: {cur or 'blank'} ({self.mode(cur) if cur else '-'})")

    async def _ask(self, prompt: str) -> bool:
        if self.confirm is None:
            return False
        try:
            return bool(await self.confirm(prompt))
        except Exception:
            logs.error("browser", "BROWSER_CONFIRM_FAILED", "the on-screen confirmation failed: treated as NO", exc=True)
            return False

    # --- lifecycle
    async def _ensure(self) -> None:
        if self._page is not None and not self._page.is_closed():
            return
        if self._context is None:
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                raise BrowserError("BROWSER_UNAVAILABLE", "Playwright is not installed "
                                                          "(jarvis features on browser)") from None
            if self._pw is None:
                self._pw = await async_playwright().start()
            errors = []
            for name, kw in candidates():
                try:
                    self._browser = await self._pw.chromium.launch(headless=self.headless, **kw)
                    self.engine = name
                    break
                except Exception as e:          # wrong channel / missing build: try the next one
                    errors.append(f"{name}: {type(e).__name__}")
            if self._browser is None:
                await self._stop_pw()
                raise BrowserError("BROWSER_UNAVAILABLE", "no browser could start (" + ("; ".join(errors) or
                                   "none installed: Edge / Chrome / Chromium") + ")")
            self._context = await self._browser.new_context(accept_downloads=False, service_workers="block",
                                                            locale="he-IL", permissions=[])
            self._context.set_default_timeout(ACTION_TIMEOUT_MS)
            self._context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
            await self._context.route("**/*", self._route)
            self._context.on("page", self._on_page)
            logs.info("browser", "BROWSER_STARTED", "browser started (headless, isolated profile)",
                      engine=self.engine)
        self._page = await self._context.new_page()

    def _on_page(self, page) -> None:             # a popup / new tab becomes the current page
        self._page = page

    async def _route(self, route) -> None:
        req = route.request
        try:
            top = req.is_navigation_request() and req.frame.parent_frame is None
        except Exception:
            top = False
        if top:
            host = url_host(req.url)
            if self.mode(host) not in ("control", "read_only"):
                self._blocked.append(host or "non-web address")
                # a policy decision, not a fault (O-04): INFO keeps the WARNING view for real problems
                logs.info("browser", "BROWSER_NAV_BLOCKED", "navigation refused",
                          host=host)
                # "aborted" (net::ERR_ABORTED): Chromium cancels the navigation and keeps the current document.
                # "blockedbyclient" made it commit an empty error page (chrome-error://) AFTER the click had
                # returned on a loaded runner, so the page we promised to keep was gone (nightly 39740226891).
                await route.abort("aborted")
                return
        await route.continue_()

    async def _stop_pw(self) -> None:
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None

    async def close(self, reason: str = "closed") -> None:
        async with self._lock:
            await self._close(reason)

    async def _close(self, reason: str) -> None:
        if self._idle is not None:
            self._idle.cancel()
            self._idle = None
        was_open = self._browser is not None
        for obj in (self._context, self._browser):
            if obj is not None:
                try:
                    await obj.close()
                except Exception:
                    pass
        self._context = self._browser = self._page = None
        await self._stop_pw()
        self._approved.clear()
        if was_open:
            self._note("close", "", reason)

    def _touch(self) -> None:
        if self.idle_close_s <= 0:
            return
        loop = asyncio.get_running_loop()
        if self._idle is not None:
            self._idle.cancel()
        def fire() -> None:
            self._idle = None
            self._idle_task = loop.create_task(self.close("idle"))
        self._idle = loop.call_later(self.idle_close_s, fire)

    # --- helpers
    def _take_blocked(self) -> list[str]:
        out, self._blocked = list(dict.fromkeys(self._blocked)), []
        return out

    async def _settle(self) -> None:
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass

    async def _check_landed(self, refused: list[str] | None = None) -> str:
        """After a navigation: the page must be on a host this session may see (redirects bypass the route). A
        navigation the route refused is aborted and the current page stays; should an engine still show an error
        page (no host), go back to the page we were on."""
        host = self.current_host()
        if refused and not host:
            try:
                await self._page.go_back(wait_until="domcontentloaded", timeout=5000)
            except Exception:
                pass
            host = self.current_host()
        if host and self.mode(host) in ("control", "read_only"):
            return host
        await self._page.goto("about:blank")
        raise BrowserError("BROWSER_NOT_APPROVED", f"the page went to {host or 'a non-web address'}, which you did "
                           "not approve; I closed it. Ask me to open that site if you want it.")

    async def _element(self, ref: str):
        if not self._attr or not ref.isdigit():
            raise BrowserError("BROWSER_NO_ELEMENT", "read the page first (browser_read) and use an element number")
        loc = self._page.locator(f'[{self._attr}="{ref}"]')
        if await loc.count() != 1:
            raise BrowserError("BROWSER_NO_ELEMENT", f"element {ref} is not on the page any more; read it again")
        return loc

    def _require_control(self, action: str) -> str:
        if self._page is None:
            raise BrowserError("BROWSER_NO_PAGE", "no page is open; open one first")
        host = self.current_host()
        m = self.mode(host)
        if m != "control":
            raise BrowserError("BROWSER_READ_ONLY", f"{host or 'this page'} is read-only: I may not {action} there. "
                               "The owner can add it to the allow-list (dashboard Browser page or "
                               "`jarvis browser allow <site>`).")
        return host

    # --- operations (each returns plain dicts; the Skill turns them into ToolResults)
    async def open(self, url: str) -> dict:
        url = with_scheme(url)
        host = url_host(url)
        if not host:
            raise BrowserError("BROWSER_BAD_URL", "only http/https web addresses can be opened")
        async with self._lock:
            m = self.mode(host)
            if m == "blocked":
                self._note("open", host, "blocked")
                raise BrowserError("BROWSER_BLOCKED", f"{host} is an AI account page; I never browse it")
            if m == "ask":
                ok = await self._ask(f"לפתוח את {host} לקריאה בלבד? (האתר לא ברשימת האתרים המאושרים) / "
                                     f"Open {host} read-only? (not on your allow-list)")
                if not ok:
                    self._note("open", host, "declined")
                    raise BrowserError("TOOL_DECLINED", f"not opened: {host} was not approved")
                self._approved.add(host)
                m = "read_only"
            await self._ensure()
            self._blocked = []
            try:
                await self._page.goto(url, wait_until="domcontentloaded")
            except Exception as e:
                if not self._take_blocked():
                    self._note("open", host, "failed")
                    raise BrowserError("BROWSER_NAV_FAILED", f"could not open {host} ({type(e).__name__})") from None
                self._note("open", host, "blocked")
                raise BrowserError("BROWSER_NOT_APPROVED", f"{host} was not approved") from None
            landed = await self._check_landed()
            self._attr = ""
            title = (await self._page.title())[:200]
            self._note("open", landed, "ok")
            self._touch()
            return {"host": landed, "mode": self.mode(landed), "title": title}

    async def read(self) -> dict:
        async with self._lock:
            if self._page is None:
                raise BrowserError("BROWSER_NO_PAGE", "no page is open; open one first")
            host = self.current_host()
            if self.mode(host) not in ("control", "read_only"):
                raise BrowserError("BROWSER_NOT_APPROVED", "this page is not approved for reading")
            text = await self._page.evaluate("() => document.body ? document.body.innerText : ''")
            text = re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t ]+", " ", text or "")).strip()
            self._attr = "data-jarves-" + secrets.token_hex(4)
            elements = await self._page.evaluate(ELEMENTS_JS, [self._attr, MAX_ELEMENTS])
            title = (await self._page.title())[:200]
            self._note("read", host, "ok")
            self._touch()
            return {"host": host, "mode": self.mode(host), "title": title, "text": text[:MAX_TEXT_CHARS],
                    "truncated": len(text) > MAX_TEXT_CHARS, "elements": elements}

    async def click(self, ref: str) -> dict:
        async with self._lock:
            host = self._require_control("click")
            loc = await self._element(ref)
            meta = await loc.evaluate(META_JS)
            why = click_sensitive(meta)
            if why:
                label = (meta.get("label") or meta.get("name") or "?")[:60]
                if not await self._ask(f"לאשר לחיצה על \"{label}\" ב-{host}? זו נראית כמו רכישה או תשלום. / "
                                       f"Press \"{label}\" on {host}? It looks like a {why}."):
                    self._note("click", host, "declined")
                    raise BrowserError("TOOL_DECLINED", f"not pressed: the owner did not approve the {why}")
            self._blocked, before = [], self._page.url
            await loc.click()
            await self._settle()
            refused = self._take_blocked()
            landed = await self._check_landed(refused)
            if refused or self._page.url != before:     # a new document: the element numbers are gone
                self._attr = ""
            self._note("click", landed, "ok")
            self._touch()
            return {"host": landed, "mode": self.mode(landed), "title": (await self._page.title())[:200],
                    "confirmed": bool(why), "navigation_refused": refused}

    async def fill(self, ref: str, value: str, press_enter: bool = False) -> dict:
        async with self._lock:
            host = self._require_control("type")
            loc = await self._element(ref)
            meta = await loc.evaluate(META_JS)
            if meta.get("tag") not in ("input", "textarea", "select") and not meta.get("editable"):
                raise BrowserError("BROWSER_NOT_A_FIELD", f"element {ref} is not a text field or list")
            why = fill_sensitive(meta, value)
            if not why and press_enter and meta.get("in_form") and meta.get("form_payment"):
                why = "payment form"
            if not why and press_enter and PURCHASE_RX.search(meta.get("form_text") or ""):
                why = "purchase or payment"
            if why:
                label = (meta.get("label") or meta.get("name") or "?")[:60]
                if not await self._ask(f"להזין {why} בשדה \"{label}\" ב-{host}? (הערך לא מוצג) / "
                                       f"Enter a {why} into \"{label}\" on {host}? (value not shown)"):
                    self._note("fill", host, "declined")
                    raise BrowserError("TOOL_DECLINED", f"not typed: the owner did not approve the {why}")
            if meta.get("tag") == "select":
                try:
                    await loc.select_option(label=value)
                except Exception:
                    await loc.select_option(value=value)
            else:
                await loc.fill(value)
            refused: list[str] = []
            landed = host
            if press_enter:
                self._blocked, before = [], self._page.url
                await loc.press("Enter")
                await self._settle()
                refused = self._take_blocked()
                landed = await self._check_landed(refused)
                if refused or self._page.url != before:
                    self._attr = ""
            self._note("fill", landed, "ok")
            self._touch()
            return {"host": landed, "mode": self.mode(landed), "confirmed": bool(why), "submitted": press_enter,
                    "navigation_refused": refused}


# ---------------------------------------------------------------------- Core wiring
def attach(core) -> BrowserService:
    """Build the service, register the Skill (tools hidden while the `browser` switch is off) and the page-text
    taint guard. Nothing starts a browser until the first tool call."""
    from .camera.wire import install_taint_guard
    from .skills.browser import BrowserSkill
    svc = BrowserService(core.home, confirm=core.ask_confirm, health=core.health)
    taint = install_taint_guard(core.registry, exempt_prefix="browser_", module="browser",
                                label=("תוכן מדף אינטרנט", "web page content"))
    # the policy (allow-list, read-only, always-confirm) lives in the service, like feature_set's in features.py
    core.registry.allowed_actions |= {"browser_open", "browser_click", "browser_fill", "browser_close"}
    core.registry.register(BrowserSkill(svc, on_untrusted=taint))
    core.health.set("browser", "ok" if core.features.active(FEATURE) else "disabled",
                    "idle (no page open)" if core.features.active(FEATURE) else core.features.reason(FEATURE))
    return svc


# ---------------------------------------------------------------------- dashboard
def _view(ctx) -> dict:
    from .health import read_snapshot
    f = feat._ctx_features(ctx)
    snap = read_snapshot(ctx.home / "health.json") or {}
    comp = ((snap.get("components") or {}).get("browser") or {}) if snap.get("age_s", 1e9) < 120 else {}
    try:
        live = bool(f.status("browser_liveview")["active"])        # B2: shown only when the beta switch is on
    except Exception:                                              # noqa: BLE001 - an unregistered switch = off
        live = False
    return {"sites": Sites(ctx.home).list(), "feature": f.status(FEATURE), "activity": comp, "live_view": live,
            "blocked_hosts": list(BLOCKED_HOSTS),
            "rules": ["Full control (open, read, click, type) only on the sites listed here.",
                      "Any other site: read-only, and only after you approve it on the screen.",
                      "Purchases, payments and passwords / card numbers always ask on the screen first.",
                      "Fresh private browser every time: no personal profile, cookies or saved passwords; "
                      "downloads are refused."]}


def api_browser(ctx, m, body):
    return 200, _view(ctx)


def api_browser_sites(ctx, m, body):
    body = body or {}
    action, site = body.get("action"), body.get("site")
    if action not in ("add", "remove") or not isinstance(site, str):
        return 400, {"error": "body needs {\"action\": \"add\"|\"remove\", \"site\": \"example.com\"}"}
    try:
        (Sites(ctx.home).add if action == "add" else Sites(ctx.home).remove)(site, by="dashboard")
    except ValueError as e:
        return 400, {"error": str(e)}
    return 200, _view(ctx)


def dashboard_routes(register) -> None:
    register("GET", r"/api/browser", api_browser)
    register("POST", r"/api/browser/sites", api_browser_sites)


# ---------------------------------------------------------------------- CLI
def add_cli(sub) -> None:
    b = sub.add_parser("browser", help="Browser Skill: the allow-list of sites JARVES may fully control "
                                       "(status / sites / allow SITE / remove SITE)")
    b.add_argument("action", nargs="?", default="status", choices=["status", "sites", "allow", "remove"])
    b.add_argument("site", nargs="?", help="e.g. example.com (subdomains included)")


def cmd_browser(a, cfg, home) -> int:
    sites = Sites(home)
    if a.action in ("allow", "remove"):
        if not a.site:
            print(f"usage: jarvis browser {a.action} <site>", file=sys.stderr)
            return 2
        try:
            now = sites.add(a.site, by="cli") if a.action == "allow" else sites.remove(a.site, by="cli")
        except ValueError as e:
            print(f"not changed: {e}", file=sys.stderr)
            return 2
        print(f"allow-list ({len(now)}): " + (", ".join(now) or "empty"))
        return 0
    listed = sites.list()
    if a.action == "status":
        from .cli import _cli_features
        s = _cli_features(cfg, home).status(FEATURE)
        state = "ACTIVE" if s["active"] else ("off" if not s["enabled"] else "unavailable")
        print(f"browser: {state}" + (f" - {s['reason']}" if s["reason"] and not s["active"] else ""))
        print("engines found: " + (", ".join(n for n, _ in candidates()) or "none"))
    print(f"full-control sites ({len(listed)}): " + (", ".join(listed) or "none (every site is read-only + ask)"))
    print("change: jarvis browser allow|remove <site>   (or the dashboard Browser page)")
    return 0
