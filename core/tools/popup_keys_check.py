"""Approval popup keyboard rules on the REAL Tk window (own process, like the real popup): invisible (alpha 0), bell
muted, the keyboard bindings of the real (non-test) mode. Prints one JSON object: case -> decision written (or null).

    python tools/popup_keys_check.py <out_dir>
"""
from __future__ import annotations

import json
import sys
import tkinter as tk
from pathlib import Path

from jarves import approval_popup as ap

CASES = (("medium-unarmed", "medium", "", "<Return>", False),
         ("medium-armed", "medium", "", "<Return>", True),
         ("high-armed-code", "high", "4821", "<Return>", True),
         ("high-armed-kp-enter", "high", "4821", "<KP_Enter>", True),
         ("high-escape", "high", "4821", "<Escape>", True),
         ("medium-escape", "medium", "", "<Escape>", True))


def main(out_dir: Path) -> dict:
    orig = tk.Tk.__init__

    def init(self, *a, **k):
        orig(self, *a, **k)
        self.attributes("-alpha", 0.0)            # never seen on the owner's desktop
    tk.Tk.__init__ = init
    tk.Misc.bell = lambda self, *a, **k: None    # never heard
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = {}
    for case, risk, code, key, arm in CASES:
        out = out_dir / f"{case}.json"
        out.unlink(missing_ok=True)
        req = {"id": case, "title": "Run a command", "prompt": "Run a command?", "risk": risk, "kind": "shell",
               "lang": "en", "timeout_s": 30, "typed_code": code}
        root = ap.build(req, out, None)
        if arm:
            root.jarves.arm()
        if code:
            root.jarves.typed.set(code)
        root.focus_force()                        # a key reaches the window that has the focus
        root.update()
        root.event_generate(key, when="now")
        try:
            root.update()
        except tk.TclError:
            pass
        rows[case] = json.loads(out.read_text(encoding="utf-8")).get("decision") if out.exists() else None
        try:
            root.destroy()
        except tk.TclError:
            pass
    return rows


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]))))
