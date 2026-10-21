"""Live check of the orchestrator against the REAL Claude Code and Codex on this PC (SW-V4 E, agent row AE-*).

    python tools/agents_live_check.py [--claude] [--codex] [--max-usd 0.10]

Uses a temporary JARVES home and a scratch git repo (nothing of the owner's projects is touched; the owner's own
sessions are only COUNTED, as they are outside the temporary allow-list). Prints shapes, states, costs - never
tokens, prompts of other sessions or e-mails. Claude runs on haiku; both run on the owner's subscriptions
(apiKeySource none / chatgpt): the reported cost is Claude Code's client-side estimate, not money.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def wait(pred, timeout=240):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(0.5)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--claude", action="store_true")
    ap.add_argument("--codex", action="store_true")
    ap.add_argument("--max-usd", type=float, default=0.10)
    a = ap.parse_args()
    if not (a.claude or a.codex):
        a.claude = a.codex = True
    from jarves.config import Config
    from jarves.orchestrator.service import Orchestrator
    home = Path(tempfile.mkdtemp(prefix="jarves_live_home_"))
    repo = Path(tempfile.mkdtemp(prefix="jarves_live_repo_"))
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=l@l", "-c", "user.name=l", "commit", "-q",
                    "--allow-empty", "-m", "init"], check=True)
    cfg = Config()
    cfg.agents.max_cost_usd_per_day = a.max_usd
    o = Orchestrator(home, cfg).start()
    out: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        o.projects.add(str(repo), "scratch")
        out["doctor"] = {c["check"]: (c["ok"], c["detail"]) for c in o.doctor()["checks"]}
        lst = o.list(force=True)
        out["owner_sessions_counted_not_shown"] = lst["hidden_outside_allowlist"]
        if a.claude:
            t0 = time.monotonic()
            r = o.start_session("claude", "scratch", "Reply with exactly the word OK. Do not use any tools.",
                                approved=True, model="haiku")
            out["claude_start"] = {"ok": r["ok"], "code": r.get("code"), "attach": (r.get("session") or {}).get("attach"),
                                   "auth": (r.get("session") or {}).get("auth")}
            if r["ok"]:
                sid = r["session"]["id"]
                s = wait(lambda: o.db.session(sid)["turns"] >= 1 and o.db.session(sid))
                out["claude_turn1"] = {"state": s["state"], "summary": s["last_summary"], "cost_usd": s["cost_usd"],
                                       "s": round(time.monotonic() - t0, 1)}
                o.tell(sid, "Now reply with exactly the word DONE. Do not use any tools.")
                s = wait(lambda: o.db.session(sid)["turns"] >= 2 and o.db.session(sid))
                out["claude_turn2"] = {"summary": s["last_summary"], "cost_usd": s["cost_usd"]}
                out["claude_status_text"] = o.status(sid)["text"]
                out["claude_stop"] = o.stop_session(sid, approved=True)["code"]
        if a.codex:
            t0 = time.monotonic()
            r = o.start_session("codex", "scratch", "Reply with exactly the word OK. Do not run any commands.",
                                approved=True)
            out["codex_start"] = {"ok": r["ok"], "code": r.get("code"), "auth": (r.get("session") or {}).get("auth")}
            if r["ok"]:
                sid = r["session"]["id"]
                s = wait(lambda: o.db.session(sid)["turns"] >= 1 and o.db.session(sid))
                out["codex_turn1"] = {"state": s["state"], "summary": s["last_summary"], "tokens": s["tokens_total"],
                                      "events": [e["kind"] for e in o.db.events(sid)],
                                      "s": round(time.monotonic() - t0, 1)}
            seen = [x for x in o.list(force=True)["sessions"] if x["provider"] == "codex"]
            out["codex_listed"] = [(x["state"], x["attach"]) for x in seen]
    finally:
        o.stop()
    print(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
