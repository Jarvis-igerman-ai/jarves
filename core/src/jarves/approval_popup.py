"""The approval popup: a small always-on-top native window (own process, started by approvals.PopupChannel).

    python -m jarves.approval_popup --in request.json --out result.json [--test-invoke approve|deny|wrong-code|hold]

Shows exactly what is requested, why (JARVES's words, marked as such), the risk, the details (command, folder,
package...) and a countdown. Deny has the keyboard focus; Enter / Escape / closing the window = Deny; Approve is
enabled only after a short delay (no accidental click as it appears) and, for a typed-confirmation request, only
when the shown code was typed. The result file is written once, on a click; at the countdown's end the window just
closes (the Core records a timeout = deny).

Library: tkinter / Tk 8.6 from the Python standard library (no extra install, works under pythonw, Windows and the
Pi desktop). A WinRT toast with buttons was not used: an unpackaged app needs a registered AppUserModelID + COM
activator for button clicks, and Focus Assist / Do-not-disturb hides toasts silently - a topmost window is always
seen. `--test-invoke` presses a button itself on an invisible (alpha 0) window: the real window code runs in tests.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

RISK_COLORS = {"low": "#2e7d32", "medium": "#b26a00", "high": "#c62828"}
RISK_HE = {"low": "נמוך", "medium": "בינוני", "high": "גבוה"}
KIND_HE = {"tool": "פעולה", "feature": "הפעלת יכולת", "camera": "מצלמה", "message": "שליחת הודעה",
           "shell": "הרצת פקודה במחשב", "install": "התקנת תוכנה", "schedule": "משימה מתוזמנת", "other": "בקשה"}
DETAIL_HE = {"command": "פקודה", "cwd": "תיקייה", "purpose": "מה זה עושה (לדברי JARVES)", "manager": "מנהל חבילות",
             "package": "חבילה", "version": "גרסה", "source": "מקור", "timeout_s": "זמן מקסימלי (שניות)",
             "classification": "סיווג", "reasons": "למה", "runs_as": "רץ בתור", "log": "יומן פלט"}
APPROVE_DELAY_MS = 900


def write_result(path: Path, d: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def build(req: dict, out: Path, test_invoke: str | None = None):
    import tkinter as tk
    from tkinter import font as tkfont

    root = tk.Tk()
    root.title("JARVES - אישור נדרש / Approval needed")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    if test_invoke:
        root.attributes("-alpha", 0.0)            # tests: a real, mapped window nobody sees
    risk = req.get("risk") if req.get("risk") in RISK_COLORS else "high"
    code = str(req.get("typed_code") or "")
    state = {"done": False, "left": int(req.get("timeout_s") or 60)}
    base = tkfont.nametofont("TkDefaultFont")
    bold = base.copy()
    bold.configure(weight="bold", size=base.cget("size") + 1)
    mono = tkfont.nametofont("TkFixedFont")
    wrap = 440

    def finish(decision: str) -> None:
        if state["done"]:
            return
        state["done"] = True
        write_result(out, {"id": req.get("id"), "decision": decision, "typed": typed.get() if code else ""})
        root.destroy()

    head = tk.Frame(root, bg=RISK_COLORS[risk])
    head.pack(fill="x")
    tk.Label(head, text=f"סיכון {RISK_HE[risk]} · {KIND_HE.get(req.get('kind'), 'בקשה')}   |   Risk: {risk}",
             bg=RISK_COLORS[risk], fg="white", font=bold, anchor="e", justify="right").pack(fill="x", padx=10, pady=6)
    body = tk.Frame(root, padx=12, pady=8)
    body.pack(fill="both")
    tk.Label(body, text="JARVES מבקש את האישור שלך / JARVES asks for your approval", anchor="e",
             justify="right").pack(fill="x")
    tk.Label(body, text=str(req.get("title") or "")[:200], font=bold, wraplength=wrap, anchor="e",
             justify="right").pack(fill="x", pady=(6, 2))
    prompt = str(req.get("prompt") or "")
    if prompt and prompt != req.get("title"):
        tk.Label(body, text=prompt[:900], wraplength=wrap, anchor="e", justify="right").pack(fill="x")
    if req.get("why"):
        tk.Label(body, text="למה (במילים של JARVES, לא נבדק) / Why (JARVES's words, not verified):", fg="#555",
                 anchor="e", justify="right").pack(fill="x", pady=(8, 0))
        tk.Label(body, text=str(req["why"])[:600], wraplength=wrap, anchor="e", justify="right").pack(fill="x")
    details = req.get("details") or {}
    if details:
        box = tk.Text(body, height=min(10, 2 + sum(1 + len(str(v)) // 60 for v in details.values())), width=64,
                      font=mono, wrap="char", relief="solid", borderwidth=1)
        for k, v in details.items():
            box.insert("end", f"{DETAIL_HE.get(k, k)} / {k}:\n", "k")
            box.insert("end", f"  {v}\n")
        box.tag_configure("k", foreground="#555")
        box.configure(state="disabled")             # read-only but selectable (copy the exact command)
        box.pack(fill="x", pady=(8, 0))
    typed = tk.StringVar()
    approve = tk.Button(body, text="אשר / Approve", width=14, state="disabled", command=lambda: finish("approve"))
    deny = tk.Button(body, text="דחה / Deny", width=14, command=lambda: finish("deny"))
    ready = {"delay": False}

    def update_approve(*_):
        ok = ready["delay"] and (not code or typed.get().strip() == code)
        approve.configure(state="normal" if ok else "disabled")
    if code:
        tk.Label(body, text=f"פעולה מסוכנת: כדי לאשר הקלד את הקוד {code}\nDangerous: type {code} to approve",
                 fg=RISK_COLORS["high"], font=bold, anchor="e", justify="right").pack(fill="x", pady=(10, 2))
        entry = tk.Entry(body, textvariable=typed, width=10, justify="center", font=bold)
        entry.pack(pady=2)
        typed.trace_add("write", update_approve)
    countdown = tk.Label(body, fg="#555", anchor="e", justify="right")
    countdown.pack(fill="x", pady=(8, 2))
    row = tk.Frame(body)
    row.pack(fill="x", pady=(4, 2))
    deny.pack(in_=row, side="right", padx=4)
    approve.pack(in_=row, side="right", padx=4)

    def tick():
        if state["done"]:
            return
        if state["left"] <= 0:
            state["done"] = True                   # no result file: the Core records the timeout (= deny)
            root.destroy()
            return
        countdown.configure(text=f"אם לא תענה: נדחה בעוד {state['left']} שנ׳ / No answer = denied in {state['left']} s")
        state["left"] -= 1
        root.after(1000, tick)

    def arm():
        ready["delay"] = True
        update_approve()
    if not test_invoke:
        # Keyboard / close = Deny. Not in tests: the invisible test window could otherwise take a stray key press
        # from whoever is typing on this desktop and answer instead of the scripted button.
        root.protocol("WM_DELETE_WINDOW", lambda: finish("deny"))
        root.bind("<Escape>", lambda _e: finish("deny"))
        deny.bind("<Return>", lambda _e: finish("deny"))
        approve.bind("<Return>", lambda _e: approve.invoke())
    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    root.geometry(f"+{max(0, root.winfo_screenwidth() - w - 24)}+{max(0, root.winfo_screenheight() - h - 72)}")
    if not test_invoke:
        deny.focus_set()
    root.after(APPROVE_DELAY_MS, arm)
    tick()
    if not test_invoke:
        root.bell()
    elif test_invoke == "hold":
        pass                                      # silent voice tests: an invisible real window that never answers
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jarves.approval_popup")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--test-invoke", choices=["approve", "deny", "wrong-code", "hold"])
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
