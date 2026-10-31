"""Autopilot self-update: keep the flow checkout on the newest code without a human restart.

Event driven: it runs only when the synced main SHA changed (or a previous attempt was blocked and is
retried on the next change). Fast-forward only, on a clean checkout, never reset or overwrite. When the
code changed it asks the supervisor to restart itself at a quiet moment (no worker start in flight); the
ledger, policy and running worker sessions are kept. A blocked update raises one dashboard alert and the
loop continues. The autopilot never merges pull requests, tags or pushes.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .autopilot_watch import _load, _save, raise_alert

REMOTE = "jarves"
STATE_KEY = "autopilot_update"


def run_git(repo: Path, args: list[str]) -> tuple[int, str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120, creationflags=flags)
    return p.returncode, (p.stdout or "").strip()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def start_in_flight(store) -> bool:
    return any(a.get("autopilot") and a.get("task_id") and not a.get("session_id") for a in store.snapshot()["agents"])


def update_step(store, policy: dict, *, repo: Path | None = None, git=run_git, notify=None) -> str:
    """Returns: skipped | current | updated | blocked_dirty | blocked_non_ff | blocked_error."""
    if not policy.get("enabled") or policy.get("kill"):
        return "skipped"
    repo = repo or repo_root()
    sha = str(store.snapshot().get("source", {}).get("sha", ""))
    state = _load(store, STATE_KEY, {"sha": ""})
    if not sha or sha == state.get("sha"):
        return "skipped"
    if start_in_flight(store):
        return "skipped"                      # retry on the next step; the sha is not recorded yet
    rc, _ = git(repo, ["fetch", REMOTE])
    if rc:
        return _blocked(store, state, sha, "blocked_error", "update fetch failed; will retry on the next main change", notify)
    rc, branch = git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    if rc or branch in {"", "HEAD"}:
        return _blocked(store, state, sha, "blocked_error", "update skipped: flow checkout is on a detached HEAD", notify)
    rc, dirty = git(repo, ["status", "--porcelain", "--untracked-files=no"])
    if rc or dirty:
        return _blocked(store, state, sha, "blocked_dirty",
                        "update skipped: flow checkout has local changes (commit or stash them)", notify)
    rc, old = git(repo, ["rev-parse", "HEAD"])
    rc2, new = git(repo, ["rev-parse", f"{REMOTE}/{branch}"])
    if rc or rc2:
        return _blocked(store, state, sha, "blocked_error", f"update skipped: no {REMOTE}/{branch}", notify)
    if old == new:
        _save(store, STATE_KEY, {"sha": sha})
        return "current"
    rc, _ = git(repo, ["merge", "--ff-only", f"{REMOTE}/{branch}"])
    if rc:
        return _blocked(store, state, sha, "blocked_non_ff",
                        f"update skipped: {branch} cannot fast-forward to {REMOTE}/{branch}", notify)
    _save(store, STATE_KEY, {"sha": sha})
    store.record_event("autopilot_update", f"Flow checkout updated {old[:8]} -> {new[:8]}; supervisor restarts")
    return "updated"


def _blocked(store, state, sha, name, text, notify) -> str:
    _save(store, STATE_KEY, {"sha": sha})     # one alert per main change, no retry loop
    raise_alert(store, "update_" + name, text, notify=notify)
    return name


def relaunch(home: Path, profile_home: Path, port: int, popen=subprocess.Popen) -> None:
    """Start a detached successor that waits for this supervisor's lock to be free, then supervises again."""
    entry = repo_root() / "core" / "tools" / "workflow_console.py"
    code = ("import sys,time,subprocess\n"
            "sys.path.insert(0, sys.argv[5])\n"
            "from jarves.workflow.control import exclusive\n"
            "from pathlib import Path\n"
            "for _ in range(120):\n"
            "    try:\n"
            "        with exclusive(Path(sys.argv[1]) / 'supervisor.lock'): break\n"
            "    except OSError: time.sleep(0.5)\n"
            "subprocess.Popen([sys.executable, sys.argv[4], 'supervise', '--home', sys.argv[1], '--profile-home', sys.argv[2],"
            " '--port', sys.argv[3]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n")
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)) if sys.platform == "win32" else 0
    popen([sys.executable, "-c", code, str(home), str(profile_home), str(port), str(entry), str(entry.parents[1] / "src")],
          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
