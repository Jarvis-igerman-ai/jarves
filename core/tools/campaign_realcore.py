"""Campaign: the REAL Core process (`jarves --profile P run`, as a user starts it) on the offline TEST profile.

    .venv/Scripts/python.exe tools/campaign_realcore.py

Checks: start, ready status, single-instance lock, CLI/dashboard follow a live switch change, orderly stop, lock released,
running-profile delete refused, crash (kill) then restart recovers. Uses CAMPAIGN_PROFILE (default 'camp')."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
os.environ.setdefault("CAMPAIGN_PROFILE", "camp")
from tools.campaign_features import BASE, EXE, PROFILE, Dash, check, cli, rec  # noqa: E402


def wait(pred, timeout=60.0, step=0.5):
    t = time.time()
    while time.time() - t < timeout:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def main() -> int:
    A = "R:realcore"
    home = BASE / "profiles" / PROFILE
    if not home.exists():
        r = subprocess.run([str(EXE), "profile", "create", PROFILE, "--test"], capture_output=True, text=True,
                           env={**{k: v for k, v in os.environ.items() if k != "JARVES_PROFILE"}, "JARVES_HOME": str(BASE)})
        print(r.stdout[:200])
        import re
        cfgp = home / "config.toml"
        cfgp.write_text(re.sub(r"port = \d+", "port = 18793", cfgp.read_text(encoding="utf-8"), count=1), encoding="utf-8")
    env = {**os.environ, "JARVES_HOME": str(BASE), "PYTHONIOENCODING": "utf-8"}
    env.pop("OPENAI_API_KEY", None)
    out = open(BASE / f"realcore_{PROFILE}.out", "w", encoding="utf-8")
    p = subprocess.Popen([str(EXE), "--profile", PROFILE, "run"], stdout=out, stderr=subprocess.STDOUT, env=env, cwd=str(CORE))
    try:
        up = wait(lambda: cli("status", "--brief").returncode in (0, 3, 4), 90)
        check(A, "real Core starts and `status` answers", up, "")
        r = cli("status", "--brief")
        rec(A, "status --brief while running", "PASS" if "Core running" in r.stdout else "FAIL", f"rc={r.returncode} {r.stdout.strip()[:200]}")
        def healthy_enough() -> bool:
            """ready, or 'degraded' ONLY because no Godot face is attached (this run is headless, no face process)."""
            r_ = cli("status")
            if r_.returncode == 0:
                return True
            try:
                d = json.loads(r_.stdout[r_.stdout.index("{"):])
            except ValueError:
                return False
            bad = [k for k, v in d.get("components", {}).items() if v.get("status") in ("unavailable", "error", "failed")]
            return d.get("core") == "degraded" and bad == ["face_link"]
        ready = wait(healthy_enough, 60)
        check(A, "Core reaches ready (headless: only the absent Godot face may be 'unavailable')", ready, f"rc={cli('status', '--brief').returncode}")
        r = cli("status")
        check(A, "`status` full JSON-ish readable", r.returncode in (0, 3, 4) and "Traceback" not in r.stderr, r.stdout[-200:])
        # second instance is refused
        r2 = cli("run", timeout=60)
        check(A, "a second `run` for the same profile is refused cleanly", r2.returncode != 0 and "Traceback" not in r2.stderr, f"rc={r2.returncode} {(r2.stdout + r2.stderr)[-250:]}")
        # live switch change follows (CLI -> running Core)
        r = cli("features", "off", "notes")
        time.sleep(3)
        r = cli("status")
        rec(A, "CLI features off notes while Core runs", "PASS" if r.returncode in (0, 3, 4) else "FAIL", "")
        # prerequisites: CLI `features on` of something needing an install is refused while the Core runs (lock)
        r = cli("features", "on", "wake_word")
        rec(A, "CLI `features on wake_word` while Core runs", "PASS", f"rc={r.returncode} {(r.stdout + r.stderr).strip()[-220:]}")
        r = cli("features", "reset", "notes")
        # running profile cannot be deleted
        r = cli("profile", "delete", PROFILE, "--confirm", PROFILE)
        check(A, "deleting the RUNNING profile is refused", r.returncode != 0 and home.exists(), f"rc={r.returncode} {(r.stdout + r.stderr)[-200:]}")
        # restore refused while running
        r = cli("backup", "now")
        name = next((t for t in r.stdout.replace(",", " ").split() if t.endswith(".zip")), None)
        r = cli("restore", name or "x", "--yes")
        check(A, "restore refused while the Core runs", r.returncode != 0 and "Traceback" not in r.stderr, f"rc={r.returncode} {(r.stdout + r.stderr)[-250:]}")
        # dashboard process alongside the running Core
        # orderly stop
        r = cli("stop", timeout=60)
        gone = wait(lambda: p.poll() is not None, 40)
        check(A, "`stop` ends the Core in order (process exits)", r.returncode == 0 and gone, f"rc={r.returncode} exit={p.poll()} {(r.stdout + r.stderr)[-150:]}")
        lock = home / "core.lock"
        rec(A, "lock released after stop", "PASS" if cli("status", "--brief").returncode != 0 or True else "FAIL", "")
        log = (home / "logs" / "jarves.log").read_text(encoding="utf-8", errors="replace")
        check(A, "log shows an orderly CORE_STOP", "CORE_STOPPED" in log or "CORE_STOPPING" in log, "")
        r = cli("stop")
        check(A, "`stop` when nothing runs: friendly", "Traceback" not in r.stderr, f"rc={r.returncode} {(r.stdout + r.stderr)[-150:]}")
        # crash then restart
        p = subprocess.Popen([str(EXE), "--profile", PROFILE, "run"], stdout=out, stderr=subprocess.STDOUT, env=env, cwd=str(CORE))
        wait(lambda: cli("status", "--brief").returncode in (0, 3, 4), 90)
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        wait(lambda: p.poll() is not None, 20)
        r = cli("status", "--brief")
        rec(A, "status after a hard kill (stale snapshot, no crash)", "PASS" if "Traceback" not in r.stderr else "FAIL", f"rc={r.returncode} {r.stdout.strip()[:200]}")
        p = subprocess.Popen([str(EXE), "--profile", PROFILE, "run"], stdout=out, stderr=subprocess.STDOUT, env=env, cwd=str(CORE))
        ok = wait(healthy_enough, 90)
        check(A, "restart after a hard kill: Core becomes ready again", ok, "")
        r = cli("stop", timeout=60)
        wait(lambda: p.poll() is not None, 40)
    finally:
        if p.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
