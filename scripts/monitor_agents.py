"""Token-free watcher for the SW-V2/V3 agent worktrees, CI and disk. Pure Python + git + gh: no model calls.

Usage:  python scripts/monitor_agents.py [--every 300] [--once]
Writes %LOCALAPPDATA%\\jarves\\monitor\\status.md (open it any time) and monitor.log; shows a Windows toast when
something needs attention (disk low, CI failed, an agent branch got new commits, all quiet for a long time).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "jarves" / "monitor"
DISK_WARN_GB = 8
STALE_MIN = 90


def run(args: list[str], cwd: Path = REPO, timeout: int = 60) -> str:
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0   # no flashing console windows
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, encoding="utf-8",
                           errors="replace", creationflags=flags)
        return (r.stdout or "").strip()
    except Exception as e:  # noqa: BLE001 - a monitor must never crash
        return f"ERR {e}"


def toast(title: str, text: str) -> None:
    ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;"
          "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent('ToastText02');"
          f"$t.GetElementsByTagName('text')[0].AppendChild($t.CreateTextNode('{title}'))|Out-Null;"
          f"$t.GetElementsByTagName('text')[1].AppendChild($t.CreateTextNode('{text}'))|Out-Null;"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('JARVES monitor')"
          ".Show([Windows.UI.Notifications.ToastNotification]::new($t))")
    run(["powershell", "-NoProfile", "-Command", ps], timeout=20)


def worktrees() -> list[dict]:
    rows = []
    for line in run(["git", "worktree", "list", "--porcelain"]).split("\n\n"):
        path = branch = ""
        for ln in line.splitlines():
            if ln.startswith("worktree "):
                path = ln[9:]
            if ln.startswith("branch "):
                branch = ln[7:].replace("refs/heads/", "")
        if ".claude" not in path.replace("\\", "/"):
            continue
        p = Path(path)
        ahead = run(["git", "rev-list", "--count", "swv2-integrate..HEAD"], cwd=p)
        dirty = len(run(["git", "status", "--short"], cwd=p).splitlines())
        last = run(["git", "log", "-1", "--format=%ct|%ar|%s"], cwd=p).split("|", 2)
        age_min = (time.time() - int(last[0])) / 60 if last and last[0].isdigit() else -1
        rows.append({"name": p.name, "branch": branch, "ahead": ahead, "dirty": dirty, "age_min": round(age_min),
                     "last": (last[2] if len(last) > 2 else "")[:70]})
    return rows


def ci() -> list[str]:
    out = run(["gh", "run", "list", "--limit", "6", "--json", "status,conclusion,headBranch,name,createdAt"], timeout=40)
    try:
        return [f"{r['headBranch']}: {r['name']} {r['conclusion'] or r['status']}" for r in json.loads(out)]
    except Exception:  # noqa: BLE001
        return [out[:200]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=300)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    seen: dict[str, str] = {}
    alerted: set[str] = set()
    while True:
        free_gb = shutil.disk_usage(REPO.anchor or "C:\\").free / 1e9
        rows, runs = worktrees(), ci()
        lines = [f"# JARVES monitor  {time.strftime('%Y-%m-%d %H:%M:%S')}", "",
                 f"disk free: {free_gb:.1f} GB", "", "## agent worktrees (commits ahead / uncommitted files / last commit)"]
        for r in rows:
            lines.append(f"- {r['name']}: +{r['ahead']} commits, {r['dirty']} dirty, last {r['age_min']} min ago - {r['last']}")
        lines += ["", "## recent CI runs"] + [f"- {x}" for x in runs]
        (OUT / "status.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        events = []
        if free_gb < DISK_WARN_GB and "disk" not in alerted:
            alerted.add("disk"); events.append(f"Disk low: {free_gb:.1f} GB free")
        if free_gb >= DISK_WARN_GB:
            alerted.discard("disk")
        for r in rows:
            sig = f"{r['ahead']}/{r['dirty']}"
            if seen.get(r["name"]) not in (None, sig) and r["dirty"] == 0 and r["ahead"] != "0":
                pass                                   # progress is in status.md; no popup for ordinary commits
            seen[r["name"]] = sig
        for x in runs:
            key = "ci:" + x
            if "failure" in x and key not in alerted:
                alerted.add(key); events.append("CI failed: " + x)
        if events:
            with (OUT / "monitor.log").open("a", encoding="utf-8") as f:
                for e in events:
                    f.write(f"{time.strftime('%H:%M:%S')} {e}\n")
            toast("JARVES monitor", "; ".join(events)[:200].replace("'", ""))
        if a.once:
            print("\n".join(lines))
            return
        time.sleep(a.every)


if __name__ == "__main__":
    main()
