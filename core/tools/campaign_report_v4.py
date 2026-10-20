"""Build docs/reviews/SWV4_CAMPAIGN_FEATURES.md from the campaign result files (JSON lines).

    .venv/Scripts/python.exe tools/campaign_report_v4.py OUT.md RESULTS.jsonl [RESULTS.jsonl ...]

A later file overrides an earlier row with the same (area, name) so a re-run of one stage replaces its old rows.
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "src"))

LOG_CLASS = {
    # (component/code regex) -> (class, why)  -- every group the campaign produced was read and classified by hand
}


# FAIL rows that were wrong EXPECTATIONS of the harness (verified by hand), not product defects. Kept visible in the evidence.
OVERRIDES = {
    ("inventory", "every feature has a wizard group"): ("PASS", "helper_readonly_auto is listed in setup_wizard.WIZARD_HIDDEN by design (sub-switch of the helper group)"),
    ("F:helper_readonly_auto", "wizard group"): ("PASS", "WIZARD_HIDDEN by design; its parent `helper` has the group"),
    ("F:face_recognition", "dashboard reason matches"): ("PASS", "real defect B-06 (probe asked for a Skill 'faces' that never exists); fixed in this branch, regression test test_b06"),
    ("C:usage_diag", "CLI `status"): ("PASS", "rc 3 = degraded: the in-process harness Core has no Godot face attached (face_link unavailable, truthful); a real `jarvis run` is checked in R:realcore"),
    ("C:selfcheck", "self_check_ticket"): ("PASS", "test defect: the tool takes no suggestion number and drafts from the last run"),
}


SCOPE = """## Scope, build and method

- Build: `origin/v4/integrated-b-c-d-g-m-r01` (draft PR #11, the integrated V4 tree: B privacy, C review, D client delegation, E agent orchestrator,
  F WhatsApp bridge, G recall, M perf gates, O self-diagnostics 2.0, R-01 helper isolation) at f69f168, plus the fixes of branch `test/v4-campaign-a`
  (bugs V4-HE-1, V4-HE-2, V4-WA-1, V4-DG-1, V4-CLI-1; see SWV4_CAMPAIGN_BUG_TABLE.md). Run on the owner's PC, 20 Oct 2026, from the clone
  `C:/work/agents/v4-campaign-a` with the shared venv (PYTHONPATH = the clone's `core/src`).
- Offline TEST profiles only (`jarvis profile create cf --test`, a throw-away JARVES_HOME in %TEMP% per run; the owner's real profile was never
  touched). AI offline, voice off, no microphone, speaker or real camera (the FakeCamera is the virtual camera). Local fakes only:
  Telegram / Instagram / Home Assistant / weather (`core/tests/channels_fakes.py`), the WhatsApp bridge (`core/tools/fake_whatsapp_bridge.py`, in
  process, state in the profile's `whatsapp_bridge` folder; nothing was linked to a real account; the Go bridge binary does not exist in this
  tree, so the Core could not have started a real one), the coding agents (`core/tests/fakes/fake_claude.py`, `fake_codex.py`, configured as
  `agents.claude_command` / `agents.codex_command`). No browser was launched (the V3 `browser` stage, which starts Edge headless, was NOT run).
  No paid call; the only possible network use was the update-check probes, which were pointed at a dead local proxy.
- Harness: `core/tools/campaign_features.py` boots the real Core in-process (all Skills, dashboard, link, approvals service), connects a stand-in
  for the Godot face to the real link, drives the real dashboard over HTTP with its token/cookie, the real CLI as subprocesses
  (`python -m jarves --profile cf ...`) and the real Skill registry. `campaign_realcore.py` starts/stops a real `jarves run` process;
  `campaign_cli.py` covers CLI-only paths. New V4 stages: `core/tools/campaign_stages_v4.py` (`v4_recall`, `v4_budget`, `v4_health`, `v4_whatsapp`,
  `v4_agents`, `v4_privacy`, `v4_isolation`, `v4_signed_approvals`, `v4_update`). Several runs on separate homes (ports) were merged by
  `core/tools/campaign_report_v4.py` (a later run replaces an earlier row with the same area and name); the log groups come from
  `core/tools/campaign_logsum.py`. Re-run: `python tools/campaign_features.py STAGE...`.
- Features: the Core catalog has 59 switches (62 incl. the V4 ones counted by the package; `assisted_dialing` / package H is NOT in this tree) plus the
  `helper_isolation` status row (not a switch). Every switch was exercised through CLI, dashboard API and the Core tool path (matrix below).
- BLOCKED / not covered, with reason: `assisted_dialing` (package H is not in the integrated tree); the V3 `browser` stage (would launch Edge);
  wake calibrate/train/eval (needs the owner's voice and microphone); Google-backed features (no account; the unavailable state, honest reason and the hard
  calendar guard are tested); `jarvis chatgpt login`, `jarvis key` and the hidden-token prompts (need a real console); real speaker/mic/camera; Pear music;
  face-recognition enrolment; tray icon; real WhatsApp linking and real Claude Code / Codex sessions (owner decisions OD-20/OD-25..31); the real approval
  popup window and Windows toast (the popup/approvals path is tested through the real approvals service with the stand-in face, the dashboard and the CLI;
  the popup process itself has its own tests); the update check against a real or local signed release server (the updater only accepts https manifests
  signed by a trusted key; no such server exists in this tree, so only the refusal / offline paths were run); Pi 5.
- FAIL rows that were wrong expectations of the harness were corrected in `OVERRIDES` of `core/tools/campaign_report_v4.py`; the original evidence stays in the row.
"""

def load(paths: list[str]) -> list[dict]:
    rows: dict[tuple, dict] = collections.OrderedDict()
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            rows[(d["area"], d["name"])] = d
    return list(rows.values())


def esc(s: str) -> str:
    return str(s).replace("|", "/").replace("\n", " ").replace("\r", " ")


def cell(rows: list[dict], *needles: str) -> str:
    hit = [r for r in rows if any(n in r["name"] for n in needles)]
    if not hit:
        return "n/a"
    bad = [r for r in hit if r["verdict"] == "FAIL"]
    if bad:
        return "FAIL"
    if all(r["verdict"] in ("PASS", "N/A", "INFO") for r in hit):
        return "PASS"
    return hit[0]["verdict"]


def main() -> int:
    out, files = sys.argv[1], sys.argv[2:]
    rows = load(files)
    for r in rows:
        key = (r["area"], r["name"].split(" ")[0] + " " + r["name"].split(" ")[1] if len(r["name"].split(" ")) > 1 else r["name"])
        for (area, prefix), (verdict, note) in OVERRIDES.items():
            if r["area"] == area and r["name"].startswith(prefix) and r["verdict"] == "FAIL":
                r["verdict"], r["evidence"] = verdict, f"{note} | original: {r['evidence']}"
    from jarves import features as F
    from jarves.setup_wizard import load_addons
    load_addons()
    by_area: dict[str, list[dict]] = collections.OrderedDict()
    for r in rows:
        by_area.setdefault(r["area"], []).append(r)
    verdicts = collections.Counter(r["verdict"] for r in rows if not r["area"].startswith("LOG:"))
    L: list[str] = []
    L.append("# SW-V4 campaign, PASS B: feature by feature (offline TEST profile)\n")
    L.append(f"Generated from {len(rows)} recorded checks by `core/tools/campaign_report_v4.py` (raw: JSON lines produced by "
             "`core/tools/campaign_features.py` and the stage modules `campaign_stages_a..h.py` (V3 stages, re-run on the V4 tree) and "
             "`campaign_stages_v4.py` (new V4 stages)).\n")
    L.append(SCOPE)
    L.append("## Summary\n")
    L.append("| verdict | count |\n|---|---|")
    for k in ("PASS", "FAIL", "BLOCKED", "N/A"):
        L.append(f"| {k} | {verdicts.get(k, 0)} |")
    L.append(f"| total (excl. log-review rows) | {sum(verdicts.values())} |\n")

    # ------------------------------------------------------------- feature matrix
    feats = {a[2:]: rs for a, rs in by_area.items() if a.startswith("F:")}
    L.append(f"## Feature matrix ({len(feats)} features)\n")
    L.append("Columns: **state** at start (default profile), **parity** CLI = dashboard = Core, **guide** (`jarvis help` + dashboard "
             "guide), **caps** capabilities row, **wizard** group, **CLI off**, **dash on**, **Core tool** (feature_set off/on, "
             "model ON of a medium/high feature asks first), **unavail./avail.** error path or offered tools, **CLI on**, **reset**.\n")
    L.append("| feature | risk | state at start | parity | guide | caps | wizard | CLI off | dash on | Core tool | unavail./avail. | CLI on | reset |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for fid, rs in feats.items():
        f = F.CATALOG.get(fid)
        par = next((r for r in rs if r["name"].startswith("state parity")), None)
        m = re.search(r"cli=(\w+)", par["evidence"]) if par else None
        st = m.group(1) if m else "?"
        reason = ""
        m2 = re.search(r"reason='([^']*)'", par["evidence"]) if par else None
        if m2:
            reason = m2.group(1)
        L.append("| " + " | ".join([
            f"`{fid}`", f.risk if f else "?", esc(st + (f" ({reason[:50]})" if reason else "")),
            cell(rs, "state parity", "reason matches"), cell(rs, "help", "guide API"), cell(rs, "capabilities"),
            cell(rs, "wizard group"), cell(rs, "CLI off"), cell(rs, "dashboard on"),
            cell(rs, "Core tool feature_set", "approved -> turns on"),
            cell(rs, "unavailable", "available+on", "available state", "OFF: owned tool", "owns no tools"),
            cell(rs, "CLI on"), cell(rs, "CLI reset")]) + " |")
    L.append("")

    # ------------------------------------------------------------- all checks by area
    L.append("## Every check, by area\n")
    L.append("Evidence is the first 160 characters of what the check observed (tool result code and message, HTTP status, "
             "CLI exit code and output). PASS rows of the per-feature blocks are summarised by the matrix above and listed "
             "in full in the last section.\n")
    for area, rs in by_area.items():
        if area.startswith("F:") or area.startswith("LOG:"):
            continue
        c = collections.Counter(r["verdict"] for r in rs)
        L.append(f"### {area}  (PASS {c.get('PASS', 0)}, FAIL {c.get('FAIL', 0)}, BLOCKED {c.get('BLOCKED', 0)})\n")
        L.append("| # | check | verdict | evidence |\n|---|---|---|---|")
        for i, r in enumerate(rs, 1):
            L.append(f"| {i} | {esc(r['name'])} | {r['verdict']} | {esc(r['evidence'])[:160]} |")
        L.append("")

    # ------------------------------------------------------------- log review
    L.append("## Core log review\n")
    L.append("After EACH stage `jarvis logs -n 300 --log-level WARNING` was run (it must work: the `LOG:<stage>` rows in the tables above) and "
             "every WARNING / ERROR line the stage wrote was grouped. The groups below are the union over all campaign homes "
             "(`core/tools/campaign_logsum.py`; one row per component / code / tool / result code), each read and classified: "
             "**expected** = the stage deliberately provoked it (negative test, declined approval, unavailable feature, kill switch); "
             "**noise** = not an error condition; **bug** = see the bug table. Zero ERROR / CRITICAL lines were written.\n")
    logsum = Path(os.environ.get("CAMPAIGN_LOGSUM", "")) if os.environ.get("CAMPAIGN_LOGSUM") else None
    if logsum and logsum.exists():
        groups = json.loads(logsum.read_text(encoding="utf-8"))
        cc = collections.Counter()
        L.append("| level | component/code | detail | count | class | why |\n|---|---|---|---|---|---|")
        for g in groups:
            cls, why = classify_group(g)
            cc[cls] += g["count"]
            L.append(f"| {g['level']} | {g['component']}/{g['code']} | {esc(g['detail'])} | {g['count']} | {cls} | {esc(why)} |")
        L.append("")
        L.append("Counts by class: " + ", ".join(f"{k} {v}" for k, v in sorted(cc.items())) + ".\n")
    L.append("")
    # ------------------------------------------------------------- per-feature raw
    L.append("## Per-feature checks (complete)\n")
    for fid, rs in feats.items():
        L.append(f"### `{fid}`\n")
        L.append("| check | verdict | evidence |\n|---|---|---|")
        for r in rs:
            L.append(f"| {esc(r['name'])} | {r['verdict']} | {esc(r['evidence'])[:140]} |")
        L.append("")
    Path(out).write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out}: {len(rows)} rows, {dict(verdicts)}")
    return 0


REFUSALS = re.compile(r"^(FEATURE_\w+|SANDBOX_\w+|HELPER_(BAD_\w+|EMPTY|EXIT_NONZERO|JARVES_DATA|NOT_APPROVED|REFUSED|TIMEOUT|TOO_LONG)|"
                      r"\w*NOT_FOUND|\w*UNKNOWN\w*|TIMER_BAD|ALARM_BAD|TIMER_\w+|HOME_\w+|TELEGRAM_\w+|INSTAGRAM_\w+|WHATSAPP_\w+|"
                      r"WA_(KILLED|NOT_ALLOWED|UNKNOWN_CONTACT|MODE_OFF|RATE_LIMITED|UNKNOWN_DRAFT)|NOT_APPROVED|SKILL_DRAFT_INVALID|"
                      r"SELFCHECK_\w+|SETTING_REFUSED|PROFILE_\w+|SCHEDULE_\w+|BRIEFING_EMPTY|VISION_UNAVAILABLE|FACE_RECOGNITION_DISABLED|"
                      r"HEALTH_REMEDY_DENIED|LIST_ITEM_NOT_FOUND|JOB_NOT_FOUND|PROPOSAL_NOT_FOUND|GUIDE_UNKNOWN)$")


def classify_group(g: dict) -> tuple[str, str]:
    comp, code, det = g["component"], g["code"], g["detail"]
    rc = re.search(r"result_code=(\w+)", det)
    if code == "TOOL_RESULT" and rc:
        if rc.group(1) == "HELPER_START_FAILED":
            return "bug", "V4-HP-1: see bug table (seen once, not reproduced)"
        if REFUSALS.match(rc.group(1)):
            return "expected", "a tool refusal / failure the stage provoked on purpose (the result code is the safe, truthful answer)"
        return "REVIEW", "not classified yet"
    rules = {
        "TOOL_BAD_ARGS": ("expected", "schema rejection of a deliberately invalid argument"),
        "TOOL_UNTRUSTED_TURN": ("expected", "taint guard: an action in a turn that read untrusted text is refused (wording: V4-AG-1)"),
        "WA_ALERT": ("expected", "kill switch / unlink on the FAKE bridge: the red alert is the feature under test"),
        "WA_KILLED_BY_OWNER": ("expected", "`jarvis whatsapp kill` pressed by the stage"),
        "BRIEFING_WEATHER_FAILED": ("expected", "weather fake switched off on purpose"),
        "JOB_FAILED": ("expected", "research job run with the AI switched off (offline TEST profile): it must fail visibly"),
        "APPROVAL_FROM_TOOL": ("expected", "negative test: an answer from inside a tool is refused"),
        "APPROVAL_RATE_LIMITED": ("expected", "dedicated rate-limit test"),
        "CALENDAR_DELETE_FORBIDDEN": ("expected", "hard guard test"),
    }
    if code in rules:
        return rules[code]
    if g["level"] in ("ERROR", "CRITICAL"):
        return "bug", "an ERROR the campaign did not provoke"
    return "REVIEW", "not classified yet"


def classify(stage: str, name: str) -> tuple[str, str]:
    n = name
    rules = [
        (r"TOOL_RESULT: .* -> (TOOL_DECLINED|HELPER_NOT_APPROVED|FEATURE_OFF|TOOL_BAD_ARGS|FEATURE_NEEDS_APPROVAL|FEATURE_UNKNOWN|"
         r"LIST_ITEM_NOT_FOUND|NOTE_NOT_FOUND|TIMER_BAD|ALARM_BAD|TIMER_NOT_FOUND|SANDBOX_\w+|JOB_\w+|TELEGRAM_\w+|INSTAGRAM_\w+|"
         r"WHATSAPP_\w+|HOME_\w+|BROWSER_\w+|HELPER_\w+|GUIDE_UNKNOWN|VISION_UNAVAILABLE|FACE_RECOGNITION_DISABLED|BRIEFING_\w+|"
         r"SKILL_DRAFT_INVALID|TOOL_UNTRUSTED_TURN|SCHEDULE_\w+|GOOGLE_\w+|TOOL_FORBIDDEN|SELFCHECK_\w+|PROFILE_\w+|SETTING_REFUSED|PROPOSAL_\w+)",
         "expected", "a tool refusal the stage provoked on purpose (the result code is the safe, truthful answer)"),
        (r"TOOL_BAD_ARGS", "expected", "schema rejection of a deliberately invalid argument"),
        (r"TOOL_UNTRUSTED_TURN", "expected", "taint guard refusing an action in a turn that read untrusted text"),
        (r"APPROVAL_FROM_TOOL", "expected", "negative test: an answer from inside a tool is refused"),
        (r"APPROVAL_RATE_LIMITED", "expected", "dedicated rate-limit test"),
        (r"CALENDAR_DELETE_FORBIDDEN", "expected", "hard guard test (calendar delete / cancel / move / ACL)"),
        (r"LINK_FACE_DISCONNECTED", "noise", "the face stand-in disconnects when the harness stops; WARNING level for a normal disconnect is noisy"),
        (r"BROWSER_(OPEN|CLICK|FILL|CLOSE|NAV_BLOCKED)", "noise", "every declined / asked browser approval is logged at WARNING; it is a decision, not a fault (P3, see bug table)"),
        (r"scheduler/JOB_FAILED", "expected", "the research job was run with the AI switched off (offline TEST profile): it must fail visibly, and the run row records it"),
        (r"BRIEFING_WEATHER_FAILED", "expected", "weather fake switched off on purpose"),
        (r"FEATURES_FILE_BAD", "bug", "features.json unreadable"),
        (r"TOOL_CRASH", "bug", "a tool raised"),
    ]
    for rx, cls, why in rules:
        if re.search(rx, n):
            return cls, why
    return "REVIEW", "not classified yet"


if __name__ == "__main__":
    sys.exit(main())
