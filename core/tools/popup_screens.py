"""Real screenshots of the approval popup (jarves.approval_popup) in its hard cases, with geometry checks.

    cd core && .venv\\Scripts\\python tools\\popup_screens.py [--out ../docs/reviews/swv3_popup] [--only NAME]

Each case builds the REAL window (visible, `show` mode: it never answers and nothing runs), waits until it is drawn
and Approve is armed, asserts that BOTH buttons' boxes (winfo_rootx/rooty + size) lie fully inside the window and
inside the screen, that the whole window is inside the work area (taskbar-safe) and at most 60% of the screen high,
then captures the window region (PIL.ImageGrab) to a PNG. Demo text only. 150% / 200% are simulated with Tk
scaling (fonts + the 8 px grid), the real display scaling is used for the 100% cases. Exit code 1 on any failure.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from jarves import approval_popup as ap  # noqa: E402

LONG = ("Run a PowerShell maintenance script that cleans the temporary folders of every user profile, rebuilds the "
        "search index and then restarts the Windows Search service so that the start menu search works again")
LONG_WHY = ("You said yesterday evening that the start menu search is slow and sometimes shows nothing at all. "
            "Rebuilding the index usually fixes that. " * 6)
BASE = {"id": "demo", "kind": "shell", "risk": "medium", "timeout_s": 60,
        "title": "whoami in the sandbox folder", "prompt": "JARVES wants to run a command on this PC.",
        "why": "Demo only - nothing will be executed.", "details": {"command": "whoami", "cwd": "sandbox"}}


def case(name, theme="dark", scale=None, open_details=False, **req):
    return {"name": name, "theme": theme, "scale": scale, "open": open_details, "req": {**BASE, **req}}


CASES = [
    case("short_dark"),
    case("short_light", theme="light"),
    case("low_risk_feature_light", theme="light", kind="feature", risk="low", title="Turn on the morning briefing",
         prompt="", why="", details={}),
    case("long_dark", title=LONG, prompt=LONG + " " + LONG, why=LONG_WHY,
         details={"command": "powershell -NoProfile -File C:\\Users\\Public\\jarves\\maintenance\\" + "x" * 160 +
                  ".ps1 -All -Force", "cwd": "C:\\Users\\Public\\jarves\\sandbox\\" + "deep\\" * 30,
                  "reasons": ["changes system services", "touches every user profile", "needs admin rights"]}),
    case("long_details_open_dark", open_details=True, title=LONG, prompt=LONG + " " + LONG, why=LONG_WHY * 3,
         details={"command": "powershell -NoProfile -File C:\\maintenance\\" + "y" * 400 + ".ps1",
                  "cwd": "C:\\Users\\Public\\jarves\\sandbox", "purpose": LONG_WHY}),
    case("long_details_open_light", theme="light", open_details=True, title=LONG, prompt=LONG, why=LONG_WHY * 2,
         details={"command": "dir /s C:\\Users\\Public", "cwd": "C:\\Users\\Public"}),
    case("dangerous_code_dark", risk="high", typed_code="4821", title="Delete the folder C:\\Data\\old-backups",
         details={"command": "rmdir /s /q C:\\Data\\old-backups", "cwd": "C:\\Data",
                  "reasons": ["deletes files permanently"]}),
    case("dangerous_code_long_light", theme="light", risk="high", typed_code="7395", title=LONG, why=LONG_WHY,
         details={"command": "reg delete HKCU\\Software\\Demo /f"}),
    case("hebrew_dark", lang="he", title="להריץ את הפקודה whoami בתיקיית ארגז החול",
         prompt="JARVES רוצה להריץ פקודה במחשב הזה.", why="הדגמה בלבד, שום דבר לא ירוץ."),
    case("hebrew_dangerous_open_light", theme="light", lang="he", risk="high", typed_code="2468", open_details=True,
         title="למחוק את התיקייה C:\\Data\\old-backups לצמיתות, כולל כל הקבצים והתיקיות שבתוכה ובלי אפשרות שחזור",
         why="ביקשת לפנות מקום בדיסק. " * 4, details={"command": "rmdir /s /q C:\\Data\\old-backups"}),
    case("scale150_long_dark", scale=1.5, title=LONG, why=LONG_WHY, details={"command": "whoami"}),
    case("scale150_details_open_light", theme="light", scale=1.5, open_details=True, title=LONG, why=LONG_WHY * 3),
    case("scale200_dangerous_dark", scale=2.0, risk="high", typed_code="1357", title=LONG, why=LONG_WHY),
    case("scale200_details_open_light", theme="light", scale=2.0, open_details=True, title=LONG, why=LONG_WHY * 3,
         details={"command": "x" * 300}),
]


def pump(root, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.02)


def shoot(c: dict, out_dir: Path, tmp: Path) -> dict:
    from PIL import ImageGrab
    root = ap.build(c["req"], tmp / f"{c['name']}.out.json", "show", theme=c["theme"], scale=c["scale"])
    try:
        if c["open"] and root.jarves.toggle is not None:
            root.jarves.toggle_details()
        pump(root, 1.3)                                     # drawn, fonts settled, Approve armed (900 ms)
        rep = ap.layout_report(root)
        x, y, w, h = rep["outer"]
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
        path = out_dir / f"{c['name']}.png"
        img.save(path)
        rep["png"] = path.name
        rep["name"] = c["name"]
        return rep
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(HERE.parents[1] / "docs" / "reviews" / "swv3_popup"))
    p.add_argument("--only")
    a = p.parse_args(argv)
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    reps, bad = [], []
    with tempfile.TemporaryDirectory() as t:
        for c in CASES:
            if a.only and a.only not in c["name"]:
                continue
            rep = shoot(c, out_dir, Path(t))
            reps.append(rep)
            fails = [k for k, v in rep["ok"].items() if not v]
            print(f"{'OK  ' if not fails else 'FAIL'} {c['name']:32s} outer={rep['outer']} approve={rep['approve']} "
                  f"deny={rep['deny']} {fails or ''}")
            if fails:
                bad.append((c["name"], fails))
    (out_dir / "layout_report.json").write_text(json.dumps(reps, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(reps)} screenshots in {out_dir}; failures: {bad or 'none'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
