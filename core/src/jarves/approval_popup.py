"""The approval popup: a small always-on-top native window (own process, started by approvals.PopupChannel).

    python -m jarves.approval_popup --in request.json --out result.json [--test-invoke approve|deny|wrong-code|hold|show]

A compact, calm card (owner feedback 20 Oct: "where is the button for approve?", "too much text", "not beautiful"):

    [risk chip] JARVES asks                  Auto-deny 58s
    Run a command?                         <- ONE bold line (from the request kind)
    whoami in the sandbox folder           <- ONE short sentence (title, max ~90 chars, ellipsised)
    Details v                              <- command / folder / why / ... collapsed by default
    [type the code 4821]                   <- only for dangerous (typed-confirmation) requests
    ----------------- thin countdown bar
    [   Deny   ]  [  Approve  ]            <- ALWAYS visible: packed first, anchored at the bottom

The two buttons are packed before anything else (side=bottom) and the window is never taller than 60% of the screen
and always placed inside the work area (taskbar-safe), so long text can only shrink the collapsible details, never
push the buttons out. Enter = Approve (only once it is armed: a short delay after the window appears, and for a
typed-confirmation request only once the code matches), Escape / closing the window = Deny. The result file is
written once, on a click; at the countdown's end the window just closes (the Core records a timeout = deny).

Language: the request's "lang" (approvals.PopupChannel passes `ui.popup_language`, default English); "he" shows
the whole card in Hebrew, right-aligned. Never both languages at once. Theme follows Windows (AppsUseLightTheme),
DPI-aware (per-monitor), Segoe UI, a consistent 8 px grid scaled with the screen DPI.

Library: tkinter / Tk 8.6 from the Python standard library (no extra install, works under pythonw, Windows and the
Pi desktop). A WinRT toast with buttons was not used: an unpackaged app needs a registered AppUserModelID + COM
activator for button clicks, and Focus Assist / Do-not-disturb hides toasts silently - a topmost window is always
seen. `--test-invoke` presses a button itself on an invisible (alpha 0) window: the real window code runs in tests;
`show` keeps a visible window that never answers (screenshots, core/tools/popup_screens.py).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

APPROVE_DELAY_MS = 900
CARD_WIDTH = 420                 # logical px at 100% scaling
MAX_SCREEN_FRACTION = 0.60       # the whole window is never taller than this part of the screen
SENTENCE_MAX = 90                # chars of the one plain sentence; the rest is in Details
TEST_INVOKES = ("approve", "deny", "wrong-code", "hold", "show")

STRINGS = {
    "en": {
        "window": "JARVES - approval",
        "app": "JARVES asks",
        "risk": {"low": "Low risk", "medium": "Medium risk", "high": "High risk"},
        "ask": {"tool": "Allow this action?", "feature": "Turn on a feature?", "camera": "Use the camera?",
                "message": "Send a message?", "shell": "Run a command?", "install": "Install software?",
                "schedule": "Schedule a task?", "other": "Allow this?"},
        "approve": "Approve", "deny": "Deny",
        "details": "Details", "hide": "Hide details",
        "request": "Request", "why": "Why (JARVES's words, not verified)",
        "code": "Dangerous action. Type {code} to approve.",
        "left": "Auto-deny {s}s",
        "keys": {"command": "Command", "cwd": "Folder", "folder": "Folder", "purpose": "Purpose (JARVES's words)",
                 "manager": "Package manager", "package": "Package", "version": "Version", "source": "Source",
                 "timeout_s": "Time limit (s)", "classification": "Classification", "reasons": "Why it needs approval",
                 "runs_as": "Runs as", "log": "Output log", "to": "To", "text": "Text", "app": "App"},
    },
    "he": {
        "window": "JARVES - אישור",
        "app": "JARVES מבקש",
        "risk": {"low": "סיכון נמוך", "medium": "סיכון בינוני", "high": "סיכון גבוה"},
        "ask": {"tool": "לאשר את הפעולה?", "feature": "להפעיל יכולת?", "camera": "להשתמש במצלמה?",
                "message": "לשלוח הודעה?", "shell": "להריץ פקודה?", "install": "להתקין תוכנה?",
                "schedule": "לתזמן משימה?", "other": "לאשר?"},
        "approve": "אשר", "deny": "דחה",
        "details": "פרטים", "hide": "הסתר פרטים",
        "request": "הבקשה", "why": "למה (במילים של JARVES, לא נבדק)",
        "code": "פעולה מסוכנת. כדי לאשר הקלד {code}",
        "left": "נדחה בעוד {s} שנ׳",
        "keys": {"command": "פקודה", "cwd": "תיקייה", "folder": "תיקייה", "purpose": "מה זה עושה (לדברי JARVES)",
                 "manager": "מנהל חבילות", "package": "חבילה", "version": "גרסה", "source": "מקור",
                 "timeout_s": "זמן מקסימלי (שניות)", "classification": "סיווג", "reasons": "למה צריך אישור",
                 "runs_as": "רץ בתור", "log": "יומן פלט", "to": "אל", "text": "טקסט", "app": "אפליקציה"},
    },
}

# flat palettes; chips are a tinted background with a strong text colour (calm, readable in both themes)
PALETTES = {
    "light": {"bg": "#e9ebee", "surface": "#dcdfe4", "text": "#1f2329", "muted": "#4c5663", "border": "#bfc4cc",
              "primary": "#3c5a99", "primary_hover": "#324d87", "primary_off": "#a9b6d0", "on_primary": "#ffffff",
              "danger": "#8f3535", "danger_hover": "#7a2c2c", "danger_off": "#d3b3b3",
              "secondary": "#dadde2", "secondary_hover": "#cdd1d7", "on_secondary": "#1f2329",
              "focus": "#1f2329", "track": "#d3d7dd", "link": "#2f4f8f",
              "chip": {"low": ("#d5e3d9", "#2a5c3a"), "medium": ("#eadfc9", "#6e4a12"), "high": ("#ebd8d8", "#8c3030")}},
    "dark": {"bg": "#1b1d21", "surface": "#25282d", "text": "#e4e7eb", "muted": "#9aa2ad", "border": "#34383f",
             "primary": "#4a6fb0", "primary_hover": "#41639f", "primary_off": "#2a3850", "on_primary": "#ffffff",
             "danger": "#b04a4e", "danger_hover": "#9e4044", "danger_off": "#4a2a2c",
             "secondary": "#2f3238", "secondary_hover": "#3a3e45", "on_secondary": "#e4e7eb",
             "focus": "#e4e7eb", "track": "#2f3238", "link": "#8db0e8",
             "chip": {"low": ("#22362a", "#8fc7a0"), "medium": ("#3a2f1c", "#e0b574"), "high": ("#3e2423", "#efa19b")}},
}
_RTL = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿]")


def write_result(path: Path, d: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------- pure helpers (no Tk, unit-tested anywhere)
def pick_lang(req: dict) -> str:
    v = str(req.get("lang") or "en").lower()
    return "he" if v.startswith("he") else "en"


def is_rtl(text: str) -> bool:
    return bool(_RTL.search(text or ""))


def shorten(text: str, limit: int = SENTENCE_MAX) -> str:
    """One line of plain text: whitespace collapsed, cut at a word boundary, an ellipsis when cut."""
    t = " ".join(str(text or "").split())
    if len(t) <= limit:
        return t
    cut = t[:limit - 1]
    sp = cut.rfind(" ")
    if sp >= limit * 0.6:
        cut = cut[:sp]
    return cut.rstrip(" ,.;:-") + "…"


def headline(req: dict, lang: str) -> str:
    asks = STRINGS[lang]["ask"]
    return asks.get(str(req.get("kind") or "other"), asks["other"])


def sentence(req: dict) -> str:
    return shorten(req.get("title") or req.get("prompt") or "")


def detail_rows(req: dict, lang: str) -> list[tuple[str, str, str]]:
    """(label, value, style) rows behind the Details toggle; style 'mono' for commands / paths."""
    s = STRINGS[lang]
    rows: list[tuple[str, str, str]] = []
    title = " ".join(str(req.get("title") or "").split())
    prompt = " ".join(str(req.get("prompt") or "").split())
    if title and len(title) > SENTENCE_MAX:
        rows.append((s["request"], title[:2000], "text"))
    if prompt and prompt != title:
        rows.append((s["request"] if not rows else "", prompt[:2000], "text"))
    for k, v in (req.get("details") or {}).items():
        if isinstance(v, (list, tuple)):
            v = ", ".join(str(x) for x in v)
        elif isinstance(v, dict):
            v = json.dumps(v, ensure_ascii=False)
        rows.append((s["keys"].get(k, k), str(v)[:2000], "mono" if k in ("command", "cwd", "folder", "log") else "text"))
    if req.get("why"):
        rows.append((s["why"], " ".join(str(req["why"]).split())[:2000], "text"))
    return rows


def place(work: tuple[int, int, int, int], size: tuple[int, int], frame: tuple[int, int, int, int] = (0, 0, 0, 0),
          margin: int = 16, where: str = "bottom-right") -> tuple[int, int]:
    """Top-left (outer) position for a client area `size`, with window decorations `frame` = (left, top, right,
    bottom), so the WHOLE window lies inside the work area `work` = (left, top, right, bottom)."""
    wl, wt, wr, wb = work
    fl, ft, fr, fb = frame
    ow, oh = size[0] + fl + fr, size[1] + ft + fb
    if where == "center":
        x, y = wl + (wr - wl - ow) // 2, wt + (wb - wt - oh) // 2
    else:
        x, y = wr - ow - margin, wb - oh - margin
    x = max(wl, min(x, wr - ow))
    y = max(wt, min(y, wb - oh))
    return x, y


def inside(inner: tuple[int, int, int, int], outer: tuple[int, int, int, int]) -> bool:
    """Boxes as (x, y, w, h): `inner` lies fully inside `outer`."""
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    return iw > 0 and ih > 0 and ix >= ox and iy >= oy and ix + iw <= ox + ow and iy + ih <= oy + oh


# ---------------------------------------------------------------------- platform bits
def _dpi_aware() -> None:
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)        # per-monitor aware: crisp text, real pixels
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def system_theme() -> str:
    if sys.platform != "win32":
        return "light"
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
    except OSError:
        return "light"


def work_area(root) -> tuple[int, int, int, int]:
    """The usable desktop (without the taskbar) of the primary screen, in the same pixels as Tk."""
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        try:
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):   # SPI_GETWORKAREA
                if r.right > r.left and r.bottom > r.top:
                    return r.left, r.top, r.right, r.bottom
        except Exception:
            pass
    return 0, 0, sw, max(200, sh - 48)


def _hwnd(root) -> int:
    try:
        return int(root.wm_frame(), 16)
    except (ValueError, Exception):
        return 0


def outer_rect(root) -> tuple[int, int, int, int]:
    """The window as seen on screen (x, y, w, h), title bar included, without the invisible resize borders."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        h, r = _hwnd(root), wintypes.RECT()
        if h:
            try:
                if ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(h), 9, ctypes.byref(r),
                                                              ctypes.sizeof(r)) == 0:      # EXTENDED_FRAME_BOUNDS
                    return r.left, r.top, r.right - r.left, r.bottom - r.top
            except Exception:
                pass
    x, y = root.winfo_rootx(), root.winfo_rooty()
    return root.winfo_x(), root.winfo_y(), root.winfo_width() + 2 * (x - root.winfo_x()), \
        root.winfo_height() + (y - root.winfo_y()) + (x - root.winfo_x())


def _style_frame(root, theme: str, bg: str) -> None:
    """Windows 11: dark title bar in dark mode, rounded corners, caption in the card colour (one seamless card)."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes
    h = _hwnd(root)
    if not h:
        return
    rgb = bg.lstrip("#")
    colorref = int(rgb[4:6] + rgb[2:4] + rgb[0:2], 16)
    for attr, val in ((20, 1 if theme == "dark" else 0), (33, 2), (35, colorref)):
        v = ctypes.c_int(val)
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(h), attr, ctypes.byref(v), ctypes.sizeof(v))
        except Exception:
            pass


# ---------------------------------------------------------------------- widgets
class FlatButton:
    """A flat, large-target button (tk.Label based: full colour control on every platform) with hover, pressed,
    disabled and a clear keyboard focus ring. `invoke()` runs the command only while enabled."""

    def __init__(self, parent, text: str, command, *, font, colors: tuple[str, str, str, str], ring: str,
                 page_bg: str, pad: tuple[int, int], ring_px: int) -> None:
        import tkinter as tk
        self.command = command
        self.normal, self.hover, self.off, self.fg = colors
        self.enabled = True
        self.w = tk.Label(parent, text=text, font=font, bg=self.normal, fg=self.fg, padx=pad[0], pady=pad[1],
                          cursor="hand2", takefocus=1, highlightthickness=ring_px, highlightcolor=ring,
                          highlightbackground=page_bg)
        self._inside = False
        self.w.bind("<Enter>", lambda _e: self._hover(True))
        self.w.bind("<Leave>", lambda _e: self._hover(False))
        self.w.bind("<ButtonRelease-1>", lambda _e: self.invoke() if self._inside else None)
        self.w.bind("<space>", lambda _e: self.invoke())

    def _hover(self, on: bool) -> None:
        self._inside = on
        if self.enabled:
            self.w.configure(bg=self.hover if on else self.normal)

    def set_enabled(self, on: bool) -> None:
        self.enabled = on
        self.w.configure(bg=(self.hover if self._inside else self.normal) if on else self.off,
                         cursor="hand2" if on else "arrow", takefocus=1 if on else 0)

    def invoke(self) -> None:
        if self.enabled:
            self.command()


# ---------------------------------------------------------------------- the window
def build(req: dict, out: Path, test_invoke: str | None = None, *, theme: str | None = None,
          scale: float | None = None, where: str = "bottom-right"):
    import tkinter as tk
    from tkinter import font as tkfont

    _dpi_aware()
    for attempt in range(3):
        try:
            root = tk.Tk()
            break
        except tk.TclError as e:      # Windows: Tcl's init script read now and then fails ("couldn't read file
            if attempt == 2 or "couldn't read file" not in str(e) and "tk.tcl" not in str(e):   # ...auto.tcl")
                raise
            time.sleep(0.2)
    lang = pick_lang(req)
    S = STRINGS[lang]
    rtl = lang == "he"
    theme = theme or os.environ.get("JARVES_POPUP_THEME") or system_theme()
    theme = theme if theme in PALETTES else "light"
    P = PALETTES[theme]
    if scale is None and os.environ.get("JARVES_POPUP_SCALE"):
        scale = float(os.environ["JARVES_POPUP_SCALE"])
    if scale:
        root.tk.call("tk", "scaling", scale * 96 / 72)         # fonts in points follow; px() below follows `s`
        s = float(scale)
    else:
        s = max(1.0, root.winfo_fpixels("1i") / 96.0)

    def px(n: float) -> int:
        return int(round(n * s))

    root.title(S["window"])
    root.attributes("-topmost", True)
    root.resizable(False, False)
    root.configure(bg=P["bg"])
    if test_invoke and test_invoke != "show":
        root.attributes("-alpha", 0.0)            # tests: a real, mapped window nobody sees
    risk = req.get("risk") if req.get("risk") in ("low", "medium", "high") else "high"
    code = str(req.get("typed_code") or "")
    timeout = int(req.get("timeout_s") or 60)
    state = {"done": False, "deadline": time.monotonic() + timeout, "left": timeout, "open": False}

    fams = set(tkfont.families(root))
    fam = next((f for f in ("Segoe UI", "Noto Sans", "DejaVu Sans") if f in fams),
               tkfont.nametofont("TkDefaultFont").cget("family"))
    mono_fam = next((f for f in ("Cascadia Mono", "Consolas", "DejaVu Sans Mono") if f in fams),
                    tkfont.nametofont("TkFixedFont").cget("family"))
    f_title = tkfont.Font(root, family=fam, size=13, weight="bold")
    f_body = tkfont.Font(root, family=fam, size=10)
    f_small = tkfont.Font(root, family=fam, size=9)
    f_chip = tkfont.Font(root, family=fam, size=8, weight="bold")
    f_btn = tkfont.Font(root, family=fam, size=10, weight="bold")
    f_mono = tkfont.Font(root, family=mono_fam, size=9)
    f_code = tkfont.Font(root, family=mono_fam, size=14, weight="bold")

    W = px(CARD_WIDTH)
    pad = px(24)
    inner = W - 2 * pad
    side_a, side_b = ("right", "left") if rtl else ("left", "right")
    anchor, justify = ("e", "right") if rtl else ("w", "left")
    typed = tk.StringVar()

    def finish(decision: str) -> None:
        if state["done"]:
            return
        state["done"] = True
        write_result(out, {"id": req.get("id"), "decision": decision, "typed": typed.get() if code else ""})
        root.destroy()

    card = tk.Frame(root, bg=P["bg"], padx=pad, pady=pad, width=W)
    card.pack(fill="both", expand=True)

    # 4) the buttons FIRST, anchored at the bottom: nothing packed later can push them out of the window
    btnrow = tk.Frame(card, bg=P["bg"])
    btnrow.pack(side="bottom", fill="x")
    danger = risk == "high"
    approve = FlatButton(btnrow, S["approve"], lambda: finish("approve"), font=f_btn,
                         colors=(P["danger"], P["danger_hover"], P["danger_off"], P["on_primary"]) if danger
                         else (P["primary"], P["primary_hover"], P["primary_off"], P["on_primary"]),
                         ring=P["focus"], page_bg=P["bg"], pad=(px(16), px(10)), ring_px=px(2))
    deny = FlatButton(btnrow, S["deny"], lambda: finish("deny"), font=f_btn,
                      colors=(P["secondary"], P["secondary_hover"], P["secondary"], P["on_secondary"]),
                      ring=P["focus"], page_bg=P["bg"], pad=(px(16), px(10)), ring_px=px(2))
    btnrow.columnconfigure(0, weight=1, uniform="b")
    btnrow.columnconfigure(1, weight=1, uniform="b")
    first, second = (approve, deny) if rtl else (deny, approve)     # primary on the reading-end side
    first.w.grid(row=0, column=0, sticky="nsew", padx=(0, px(4)))
    second.w.grid(row=0, column=1, sticky="nsew", padx=(px(4), 0))
    approve.set_enabled(False)

    # 3) thin countdown bar
    bar_h = max(2, px(3))
    bar = tk.Canvas(card, height=bar_h, bg=P["track"], highlightthickness=0, bd=0)
    bar.pack(side="bottom", fill="x", pady=(px(16), px(16)))
    fill = bar.create_rectangle(0, 0, 0, bar_h, width=0, fill=P["danger"] if danger else P["primary"])

    # typed code (dangerous requests only), just above the bar
    entry = None
    if code:
        cbox = tk.Frame(card, bg=P["bg"])
        cbox.pack(side="bottom", fill="x", pady=(px(16), 0))
        tk.Label(cbox, text=S["code"].format(code=code), font=f_small, bg=P["bg"], fg=P["chip"]["high"][1], anchor=anchor,
                 justify=justify, wraplength=inner).pack(fill="x")
        entry = tk.Entry(cbox, textvariable=typed, width=8, justify="center", font=f_code, relief="flat",
                         bg=P["surface"], fg=P["text"], insertbackground=P["text"], highlightthickness=px(2),
                         highlightcolor=P["primary"], highlightbackground=P["border"])
        entry.pack(side=side_a, pady=(px(8), 0), ipady=px(4))

    # 1) chip + app name, countdown text on the other side
    head = tk.Frame(card, bg=P["bg"])
    head.pack(side="top", fill="x")
    chip_bg, chip_fg = P["chip"][risk]
    tk.Label(head, text=S["risk"][risk], font=f_chip, bg=chip_bg, fg=chip_fg, padx=px(8), pady=px(2)).pack(side=side_a)
    tk.Label(head, text=S["app"], font=f_small, bg=P["bg"], fg=P["muted"]).pack(side=side_a, padx=px(8))
    left_lbl = tk.Label(head, text="", font=f_small, bg=P["bg"], fg=P["muted"])
    left_lbl.pack(side=side_b)

    # 2) ONE bold line + ONE short sentence
    tk.Label(card, text=headline(req, lang), font=f_title, bg=P["bg"], fg=P["text"], anchor=anchor,
             justify=justify).pack(side="top", fill="x", pady=(px(16), 0))
    sent = sentence(req)
    s_rtl = is_rtl(sent) if not rtl else True
    if sent:
        tk.Label(card, text=sent, font=f_body, bg=P["bg"], fg=P["text"], anchor="e" if s_rtl else "w",
                 justify="right" if s_rtl else "left", wraplength=inner).pack(side="top", fill="x", pady=(px(8), 0))

    # Details: collapsed by default
    rows = detail_rows(req, lang)
    toggle = None
    dframe = tk.Frame(card, bg=P["surface"])
    text = tk.Text(dframe, wrap="word", font=f_body, bg=P["surface"], fg=P["text"], relief="flat", bd=0,
                   highlightthickness=0, padx=px(12), pady=px(8), width=1, height=1, cursor="arrow",
                   selectbackground=P["primary"], selectforeground=P["on_primary"])
    from tkinter import ttk
    st = ttk.Style(root)
    st.theme_use("clam")                              # the native Windows scrollbar ignores colours (white in dark)
    st.configure("J.Vertical.TScrollbar", background=P["border"], troughcolor=P["surface"], bordercolor=P["surface"],
                 lightcolor=P["border"], darkcolor=P["border"], arrowcolor=P["muted"], gripcount=0,
                 arrowsize=px(10), width=px(10))
    st.map("J.Vertical.TScrollbar", background=[("active", P["muted"])])
    sb = ttk.Scrollbar(dframe, orient="vertical", command=text.yview, style="J.Vertical.TScrollbar")
    text.configure(yscrollcommand=sb.set)
    for i, (k, v, style) in enumerate(rows):
        if k:
            text.insert("end", ("\n" if i else "") + k + "\n", ("k",))
        text.insert("end", v + "\n", ("mono" if style == "mono" else "v",))
    text.tag_configure("k", font=f_small, foreground=P["muted"], spacing1=px(4))
    text.tag_configure("mono", font=f_mono)
    text.tag_configure("v", font=f_body)
    if rtl:
        text.tag_add("rtl", "1.0", "end")
        text.tag_configure("rtl", justify="right")
    text.configure(state="disabled")                  # read-only but selectable (copy the exact command)
    text.pack(side=side_a, fill="both", expand=True)

    def detail_lines() -> int:
        avail = max(40, inner - 2 * px(12) - px(12))
        n = 0
        for i, (k, v, style) in enumerate(rows):
            n += (2 if i and k else 1 if k else 0)
            f = f_mono if style == "mono" else f_body
            for part in v.split("\n"):
                n += max(1, math.ceil(f.measure(part) * 1.08 / avail))
        return max(1, n)

    def layout() -> None:
        """Size: natural height, but the whole window <= 60% of the screen; place it inside the work area."""
        root.update_idletasks()
        work = work_area(root)
        fr = frame_px()
        cap = int(root.winfo_screenheight() * MAX_SCREEN_FRACTION) - fr[1] - fr[3]
        cap = min(cap, work[3] - work[1] - fr[1] - fr[3] - 2 * px(16))
        if state["open"]:
            line = max(f_body.metrics("linespace"), f_mono.metrics("linespace"))
            want = detail_lines()
            text.configure(height=want)
            root.update_idletasks()
            over = root.winfo_reqheight() - cap
            if over > 0:
                text.configure(height=max(2, want - math.ceil(over / line)))
                sb.pack(side=side_b, fill="y")
            else:
                sb.pack_forget()
            root.update_idletasks()
        h = min(root.winfo_reqheight(), cap)
        x, y = place(work, (W, h), fr, margin=px(16), where=where)
        root.geometry(f"{W}x{h}+{x}+{y}")
        j.work, j.cap = work, cap

    def frame_px() -> tuple[int, int, int, int]:
        """Window decorations (left, top, right, bottom) in px; measured once the window is mapped."""
        if j.frame is None:
            if root.winfo_ismapped():
                o = outer_rect(root)
                cx, cy = root.winfo_rootx(), root.winfo_rooty()
                j.frame = (max(0, cx - o[0]), max(0, cy - o[1]),
                           max(0, o[0] + o[2] - cx - root.winfo_width()),
                           max(0, o[1] + o[3] - cy - root.winfo_height()))
                return j.frame
            return (px(8), px(32), px(8), px(8))          # a safe guess until mapped
        return j.frame

    def toggle_details(*_e) -> None:
        state["open"] = not state["open"]
        if state["open"]:
            dframe.pack(side="top", fill="both", expand=True, pady=(px(8), 0))
        else:
            dframe.pack_forget()
        toggle.configure(text=(S["hide"] + "  ▴") if state["open"] else (S["details"] + "  ▾"))
        layout()

    if rows:
        toggle = tk.Label(card, text=S["details"] + "  ▾", font=f_small, bg=P["bg"], fg=P["link"],
                          cursor="hand2", takefocus=1, highlightthickness=px(1), highlightcolor=P["focus"],
                          highlightbackground=P["bg"])
        toggle.pack(side="top", anchor=anchor, pady=(px(8), 0))
        toggle.bind("<Button-1>", toggle_details)
        toggle.bind("<Return>", toggle_details)
        toggle.bind("<space>", toggle_details)

    ready = {"delay": False}

    def update_approve(*_):
        approve.set_enabled(ready["delay"] and (not code or typed.get().strip() == code))
    if code:
        typed.trace_add("write", update_approve)

    def tick():
        if state["done"]:
            return
        left = state["deadline"] - time.monotonic()
        if left <= 0:
            state["done"] = True                   # no result file: the Core records the timeout (= deny)
            root.destroy()
            return
        state["left"] = math.ceil(left)
        left_lbl.configure(text=S["left"].format(s=state["left"]))
        wbar = bar.winfo_width()
        frac = max(0.0, min(1.0, left / max(1, timeout)))
        x0, x1 = (wbar * (1 - frac), wbar) if rtl else (0, wbar * frac)
        bar.coords(fill, x0, 0, x1, bar_h)
        state["tick"] = root.after(250, tick)

    def arm():
        ready["delay"] = True
        update_approve()

    def on_return(_e=None):
        if root.focus_get() is deny.w:
            deny.invoke()
        elif toggle is not None and root.focus_get() is toggle:
            toggle_details()
        elif risk != "high":
            approve.invoke()                       # no-op until armed. High risk is never approved by a key press: click it

    if not test_invoke:
        # Keyboard / close = Deny. Not in tests: the invisible test window could otherwise take a stray key press
        # from whoever is typing on this desktop and answer instead of the scripted button.
        root.protocol("WM_DELETE_WINDOW", lambda: finish("deny"))
        root.bind("<Escape>", lambda _e: finish("deny"))
        root.bind("<Return>", on_return)
        root.bind("<KP_Enter>", on_return)
    elif test_invoke == "show":
        root.protocol("WM_DELETE_WINDOW", root.destroy)

    j = SimpleNamespace(approve=approve, deny=deny, card=card, buttons=btnrow, bar=bar, toggle=toggle,
                        details=dframe, text=text, entry=entry, typed=typed, lang=lang, theme=theme, scale=s,
                        toggle_details=toggle_details, layout=layout, frame=None, work=None, cap=None, arm=arm,
                        state=state)
    root.jarves = j
    layout()
    root.update()                                  # mapped: measure the real title bar / borders, place again
    layout()
    _style_frame(root, theme, P["bg"])
    if not test_invoke or test_invoke == "show":
        (entry or approve.w).focus_set()
    state["arm"] = root.after(APPROVE_DELAY_MS, arm)

    def on_destroy(e) -> None:
        if e.widget is root:                       # no stray after() callbacks into a destroyed interpreter
            state["done"] = True
            for k in ("tick", "arm"):
                if state.get(k):
                    try:
                        root.after_cancel(state[k])
                    except Exception:
                        pass
    root.bind("<Destroy>", on_destroy, add="+")
    tick()
    if not test_invoke:
        root.bell()
    elif test_invoke in ("hold", "show"):
        pass                                      # silent voice tests / screenshots: a real window that never answers
    else:
        def press():
            if test_invoke == "approve":
                if code:
                    typed.set(code)
                arm()
                approve.invoke()
            elif test_invoke == "wrong-code":
                typed.set("0000" if code != "0000" else "1111")
                arm()
                approve.invoke()                  # disabled: nothing happens
                root.after(50, lambda: finish("deny"))
            else:
                deny.invoke()
        root.after(200, press)
    return root


def layout_report(root) -> dict:
    """Screen boxes (x, y, w, h) of the window client area, the outer window, both buttons, screen and work area,
    plus the checks the UX tests and the screenshot tool assert."""
    root.update_idletasks()
    j = root.jarves

    def box(w) -> tuple[int, int, int, int]:
        return w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()
    win = box(root)
    outer = outer_rect(root)
    wl, wt, wr, wb = j.work or work_area(root)
    screen = (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
    work = (wl, wt, wr - wl, wb - wt)
    rep = {"window": win, "outer": outer, "screen": screen, "work": work,
           "approve": box(j.approve.w), "deny": box(j.deny.w), "scale": j.scale, "lang": j.lang, "theme": j.theme}
    rep["ok"] = {
        "approve_in_window": inside(rep["approve"], win), "deny_in_window": inside(rep["deny"], win),
        "approve_on_screen": inside(rep["approve"], screen), "deny_on_screen": inside(rep["deny"], screen),
        "window_in_work_area": inside(outer, work) or inside(win, work),
        "height_cap": outer[3] <= int(screen[3] * MAX_SCREEN_FRACTION) + 2,
        "buttons_viewable": bool(j.approve.w.winfo_viewable() and j.deny.w.winfo_viewable()),
        "big_targets": min(rep["approve"][3], rep["deny"][3]) >= int(32 * j.scale),
    }
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jarves.approval_popup")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--test-invoke", choices=list(TEST_INVOKES))
    a = ap.parse_args(argv)
    try:
        req = json.loads(Path(a.inp).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 2
    try:
        root = build(req, Path(a.out), a.test_invoke)
    except Exception as e:                           # no display / no Tk: the Core uses the other channels
        print(f"popup unavailable: {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
