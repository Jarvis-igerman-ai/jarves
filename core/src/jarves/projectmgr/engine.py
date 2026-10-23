"""The stage machine: runs a task's pipeline (pipeline.py) stage by stage. Mixed into service.Manager.

One dispatch runs consecutive stages in one thread until the task finishes, parks (`waiting` for the owner), goes back
(re-queued: a new dispatch) or stops. The default pipeline `work test merge` behaves like the original single
pipeline: the check runs on the result merged with the integration branch, the merge is a fast-forward under a
per-project lock (re-checked when the branch moved since the test), a failed agent run or check gives a fresh attempt
while `max_attempts` allows.

Per-task run state lives in the `job` JSON column: tested_base, approved {stage, tree}, feedback (untrusted agent
text for the next card), repro {files, cmd}, kept (branch of work done without a merge), stage_runs.
"""
from __future__ import annotations

import threading
from pathlib import Path

from .. import logs
from ..orchestrator.envelope import clean, summary
from . import cards, debate as debatemod, gitops, pipeline as pl

STAGE_RUNS_PER_STAGE = 3                  # a task may run at most 3 x (active stages) stages in total (no ping-pong)


class StageEngine:
    # ------------------------------------------------------------------ pipeline and run state
    def _stages(self, p: dict, t: dict) -> list[dict]:
        if t.get("pipeline"):
            return t["pipeline"]
        try:
            return pl.parse(self._project_flow(p) or pl.DEFAULT_TEMPLATE, trusted=True)
        except pl.FlowError:
            return pl.parse(pl.DEFAULT_TEMPLATE)

    def _job(self, t: dict) -> dict:
        j = t.get("job")
        return dict(j) if isinstance(j, dict) else {}

    def _set_job(self, t: dict, **kw) -> dict:
        j = self._job(self.db.task(t["id"]) or t)
        for k, v in kw.items():
            if v is None:
                j.pop(k, None)
            else:
                j[k] = v
        self.db.update_task(t["id"], job=j)
        return j

    def _feedback(self, t: dict, text: str) -> None:
        j = self._job(self.db.task(t["id"]) or t)
        j["feedback"] = (list(j.get("feedback") or []) + [clean(text, cards.NOTE_MAX)])[-4:]
        self.db.update_task(t["id"], job=j)

    def _occupied(self, t: dict) -> bool:
        """A task holds its scope while it runs or while it is parked with an open worktree."""
        return t["status"] in ("running", "review") or (t["status"] in ("waiting", "todo", "ready") and bool(t["worktree"]))

    # ------------------------------------------------------------------ the dispatch thread
    def _execute(self, pid: int, tid: int, cancel: threading.Event) -> None:
        p = self.db.project(pid)
        try:
            self._drive(p, self.db.task(tid), cancel)
        except Exception as e:                                     # a task thread never dies silently
            logs.error("projects", "TASK_CRASHED", "task pipeline crashed", **logs.error_fields(e))
            self._fail(self.db.task(tid), "failed", f"internal error: {clean(e, 150)}", p)
        finally:
            if tid in self._prepaid:                                # no agent ran in this dispatch: give it back
                self._prepaid.discard(tid)
                self.db.bump("runs", -1)
            self.db.promote(pid)
            self._maybe_done(p)

    def _count_run(self, t: dict) -> None:
        """One agent run against the daily cap (the dispatch's reservation first)."""
        if t["id"] in self._prepaid:
            self._prepaid.discard(t["id"])
        else:
            self.db.bump("runs")

    def _drive(self, p: dict, t: dict, cancel: threading.Event) -> None:
        stages = self._stages(p, t)
        i = t["stage"]
        if i < len(stages) and stages[i].get("skip"):
            i = pl.next_index(stages, i)
        limit = STAGE_RUNS_PER_STAGE * max(1, len([s for s in stages if not s.get("skip")]))
        while i < len(stages):
            if self._stopped(t, cancel):
                return self._cleanup_stopped(p, self.db.task(t["id"]))
            runs = int(self._job(t).get("stage_runs") or 0) + 1
            if runs > limit:
                return self._fail(t, "blocked", f"too many stage runs ({runs - 1}): the flow goes round in circles", p)
            self._set_job(t, stage_runs=runs)
            st = stages[i]
            busy = "review" if st["kind"] in ("test", "verify", "merge", "cmd") else "running"
            if not self.db.claim_stage(t["id"], i, busy):          # a concurrent stop/cancel won the race
                return self._cleanup_stopped(p, self.db.task(t["id"]))
            out = self._run_stage(p, self.db.task(t["id"]), st, i, stages, cancel)
            t = self.db.task(t["id"])
            what = out[0]
            if what == "next":
                i = pl.next_index(stages, i)
                continue
            if what == "end":                                       # stopped / handled by the stage itself
                return
            if what == "requeue":                                   # e.g. the daily cap: try again later
                self.db.update_task(t["id"], status="todo", reason=clean(out[1], 200), pid=0)
                return
            if what == "park":
                self.db.update_task(t["id"], status="waiting", waiting=clean(out[1], 400), pid=0)
                self.db.event(t["id"], "manager", "waiting", clean(out[1], 300))
                self._signal(p, f"task #{t['seq']} waits for you: {summary(out[1], 80)}")
                return
            if what == "fail":
                return self._on_fail(p, t, st, i, stages, reason=out[1])
            if what == "stop":
                return self._fail(t, out[1], out[2], p)
        self._finish(p, self.db.task(t["id"]))

    def _finish(self, p: dict, t: dict) -> None:
        if t["worktree"]:
            # no merge stage: the work stays on its branch (named in later cards and in ship)
            if t["branch"]:
                self._set_job(t, kept=t["branch"])
                self.db.update_task(t["id"], result=clean(f"kept on branch {t['branch']} (not merged)", 200))
            gitops.remove_worktree(p["path"], Path(t["worktree"]))
            self.db.update_task(t["id"], worktree="")
        self.db.update_task(t["id"], status="done", finished=self.clock(), reason="", waiting="", pid=0)
        self.db.event(t["id"], "manager", "done", "all stages done")
        self._signal(p, f"task #{t['seq']} done")

    def _cleanup_stopped(self, p: dict, t: dict) -> None:
        if t and t["worktree"] and t["status"] == "cancelled":
            gitops.remove_worktree(p["path"], Path(t["worktree"]), t["branch"])
            self.db.update_task(t["id"], worktree="", branch="")

    def _on_fail(self, p, t, st, i, stages, *, reason: str) -> None:
        """Route a failed stage by its on_fail: retry (fresh attempt), back:<kind> (same branch, bounded), skip, stop."""
        if self._stopped(t, self._cancel.get(t["id"]) or threading.Event()):
            return self._cleanup_stopped(p, t)
        rule = "retry" if st["kind"] in pl.CODE_KINDS else (st.get("on_fail") or "stop")
        if rule == "skip":
            self.db.event(t["id"], "manager", "skipped", f"{st['kind']} failed and is skipped: {clean(reason, 200)}")
            self.db.update_task(t["id"], stage=pl.next_index(stages, i), status="todo", reason="")
            return
        if rule == "retry":
            target = i if st["kind"] in pl.CODE_KINDS else pl.find(stages, pl.CODE_KINDS, before=i)
            if target is None:
                return self._fail(t, "blocked", f"{st['kind']} failed: {reason}", p)
            if t["attempts"] >= self.cfg.max_attempts:
                return self._fail(t, "failed", reason, p)
            if t["worktree"]:
                gitops.remove_worktree(p["path"], Path(t["worktree"]), t["branch"])
            self.db.update_task(t["id"], worktree="", branch="", stage=target, status="todo", reason=clean(reason, 300),
                                pid=0)
            self._set_job(t, tested_base=None, approved=None)
            self.db.event(t["id"], "manager", "retry", clean(reason, 300))
            return
        if rule.startswith("back:"):
            target = pl.find(stages, (rule[5:],), before=i)
            if target is not None:
                if t["rounds"] >= self.cfg.max_review_rounds:
                    self.db.update_task(t["id"], status="waiting", pid=0, waiting=clean(
                        f"{st['kind']} still asks for changes after {t['rounds']} rounds: approve to continue anyway, "
                        f"or reject with what to do ({reason})", 400))
                    self._signal(p, f"task #{t['seq']} waits for you after {t['rounds']} review rounds")
                    return
                self.db.update_task(t["id"], rounds=t["rounds"] + 1, stage=target, status="todo",
                                    reason=clean(reason, 300), pid=0)
                self._set_job(t, tested_base=None, approved=None)
                self.db.event(t["id"], "manager", "back", clean(reason, 300))
                return
        self._fail(t, "blocked", f"{st['kind']} failed: {reason}", p)

    # ------------------------------------------------------------------ one stage
    def _run_stage(self, p, t, st, i, stages, cancel):
        kind = st["kind"]
        acts = pl.actors(st["by"])
        self.db.event(t["id"], "manager", "stage", f"{kind} by {st['by']}")
        if acts == ["owner"]:
            return self._owner_stage(p, t, st, i, stages)
        if kind in ("test", "verify", "cmd"):
            return self._st_check(p, t, st, cancel)
        if kind == "merge":
            return self._st_merge(p, t, stages, cancel)
        if kind == "release":
            return self._st_release(p, t)
        if kind == "debate":
            return self._st_debate(p, t, st, acts)
        if kind == "review":
            return self._st_review(p, t, st, acts)
        if kind in ("plan", "diagnose"):
            return self._st_note(p, t, kind, acts[0])
        if len(acts) > 1:
            return self._st_best_of(p, t, kind, acts, stages, cancel)
        return self._st_work(p, t, kind, acts[0], cancel)

    # ------------------------------------------------------------------ agents
    def _agent(self, p, t, spec: str, *, avoid: str = ""):
        """('jarves' | agent | team:agent) -> (runner, name, model, team) or (None, why, '', False). avoid: for
        'jarves', prefer another agent than this one (a reviewer is not the author)."""
        team = spec.startswith("team:")
        spec = spec[5:] if team else spec
        if spec == "jarves":
            r, name, model = None, "", ""
            if avoid:
                for n in self._runner_names(p, None):
                    rr, nn, mm = self._spec_runner(n)
                    if rr is not None and nn != avoid:
                        r, name, model = rr, nn, mm
                        break
            if r is None:
                r, name, model = self._pick_runner(p, t)
        else:
            r, name, model = self._spec_runner(spec)
            if r is not None and t.get("model") and not model:
                model = t["model"]
        if r is None:
            return None, f"{spec if spec != 'jarves' else 'no coding agent'} is not installed", "", False
        return r, name, model, team or t.get("mode") == "team"

    def _ensure_worktree(self, p, t):
        if t["worktree"] and Path(t["worktree"]).exists():
            return Path(t["worktree"]), t["branch"]
        att = max(1, t["attempts"])
        wt, br = gitops.new_task_worktree(self.home, p["path"], p["id"], t["seq"], att)
        self.db.update_task(t["id"], worktree=str(wt), branch=br)
        return wt, br

    def _card(self, p, t, failure: str = "") -> str:
        deps = [d for d in (self.db.task(x) for x in t["deps"]) if d]
        merged = [d for d in deps if not self._job(d).get("kept")]
        kept = [f"task #{d['seq']}'s work is on branch {self._job(d)['kept']}" for d in deps if self._job(d).get("kept")]
        base = gitops.integration_branch(p["id"])
        return cards.build_card(p, t, deps, cap=self.cfg.context_chars, failure=failure, notes=t["notes"],
                                integration=base if merged else "", feedback=self._job(t).get("feedback"), kept=kept)

    def _last_failure(self, t) -> str:
        last = [e for e in self.db.events(t["id"], 30) if e["kind"] == "check_failed"]
        return last[-1]["text"] if last and t["attempts"] >= 1 else ""

    def _st_work(self, p, t, kind, spec, cancel):
        r, name, model, team = self._agent(p, t, spec)
        if r is None:
            return ("stop", "blocked", name)
        if t["id"] not in self._prepaid and self._day_cap():
            return ("requeue", self._day_cap())
        self.db.update_task(t["id"], attempts=t["attempts"] + 1)
        t = self.db.task(t["id"])
        try:
            wt, br = self._ensure_worktree(p, t)
        except (gitops.GitError, OSError) as e:
            return ("stop", "failed", f"worktree failed: {clean(e, 200)}")
        self.db.update_task(t["id"], used_runner=name, model=model or t["model"])
        self.db.event(t["id"], "manager", "started", f"{kind} attempt {t['attempts']} with {name}")
        self._count_run(t)
        if kind == "reproduce":
            card = cards.REPRODUCE.format(card=cards.stage_card(p, t, [], cap=self.cfg.context_chars, notes=t["notes"],
                                                                feedback=self._job(t).get("feedback"))) + "\n\n" + cards.CONTRACT
        else:
            card = self._card(p, t, self._last_failure(t))
        res = self._run_agent(p, t, r, card, wt, model, team, cancel)
        if res is None:
            return ("end",)
        if not res.ok:
            return ("fail", f"{name}: {res.error or 'failed'}")
        return self._after_code(p, self.db.task(t["id"]), wt, br, res.text, kind)

    def _run_agent(self, p, t, r, card, wt, model, team, cancel, read_only=False):
        def on_start(pid_: int) -> None:
            self.db.update_task(t["id"], pid=pid_)
        res = r.run(card, str(wt), model=model, check_argv=p["check_cmd"], on_start=on_start, cancel=cancel,
                    timeout=min(14400, self.cfg.task_timeout_s * (3 if team else 1)), team=team, read_only=read_only)
        self._cost(res)
        cur = self.db.task(t["id"])
        self.db.update_task(t["id"], pid=0, session_ref=res.session_id or cur["session_ref"],
                            cost_usd=cur["cost_usd"] + res.cost_usd, tokens=cur["tokens"] + res.tokens)
        if self._stopped(t, cancel):
            self._cleanup_stopped(p, self.db.task(t["id"]))
            return None
        return res

    def _after_code(self, p, t, wt, br, text: str, kind: str, *, owner: bool = False):
        """Commit, enforce the scope and protected paths (the owner's own edits: scope is only a warning), keep the
        reproduction test untouched by the fix, record the handoff."""
        base = gitops.integration_branch(p["id"])
        h = cards.parse_handoff(text)
        try:
            gitops.commit_all(wt, f"task {t['seq']}: {t['title']}"[:120])
            files = gitops.changed_files(wt, base)
        except gitops.GitError as e:
            return ("stop", "failed", f"git failed: {clean(e, 200)}")
        scope = t["scope"]
        if kind == "reproduce" and not scope:
            scope = ["test", "tests", "spec", "*test*", "*/test*", "*spec*"]
        prot, outside = gitops.violations(files, scope)
        if prot:
            return ("stop", "blocked", "changed protected files: " + ", ".join(prot[:5]) + f" (not merged; on {br})")
        if outside and not owner:
            self.db.update_task(t["id"], handoff=cards.auto_handoff(text, gitops.diffstat(wt, base)), handoff_kind="auto")
            return ("stop", "blocked", "changed files outside the scope: " + ", ".join(outside[:5]) +
                    f" (not merged; the work is on branch {br})")
        if outside and owner:
            self.db.event(t["id"], "manager", "warning", "your edit touches files outside the scope: " +
                          ", ".join(outside[:5]))
        repro = self._job(t).get("repro") or {}
        if kind == "work" and repro.get("files") and repro.get("commit"):
            touched = gitops.git(wt, "diff", "--name-only", repro["commit"], "HEAD", "--", *repro["files"],
                                 check=False).stdout.split()
            if touched:
                return ("stop", "blocked", "the fix changed the reproduction test " + ", ".join(touched[:3]) +
                        " (it must stay as written)")
        if h and h["status"] == "BLOCKED":
            self.db.update_task(t["id"], handoff=cards.handoff_text(h), handoff_kind="agent")
            return ("stop", "blocked", h["summary"] or "the agent reported it is blocked")
        stat = gitops.diffstat(wt, base)
        hand = cards.handoff_text(h) if h else cards.auto_handoff(text, stat)
        self.db.update_task(t["id"], handoff=hand, handoff_kind="owner" if owner else ("agent" if h else "auto"),
                            result=stat)
        if kind == "reproduce":
            if not files:
                return ("fail", "no reproduction test was written")
            head = gitops.git(wt, "rev-parse", "HEAD", check=False).stdout.strip()
            self._set_job(t, repro={"files": files[:6], "commit": head, "cmd": repro.get("cmd") or []})
            return self._confirm_repro(p, self.db.task(t["id"]), wt)
        return ("next",)

    def _confirm_repro(self, p, t, wt):
        """The new test must FAIL now (it shows the bug). With a targeted repro command that is exact; with only the
        project check, the check must have passed before (else the failure proves nothing)."""
        repro = self._job(t).get("repro") or {}
        argv = repro.get("cmd") or []
        if not argv:
            argv = p["check_cmd"]
            if not argv:
                return ("next",)                                    # nothing to run: the agent's test is trusted
            ok_base, _ = gitops.run_check(self._integration(p), argv, timeout=min(900, self.cfg.task_timeout_s))
            if not ok_base:
                return ("stop", "blocked", "the check already fails before the reproduction: give a targeted "
                                           "command (jarvis project bug ... --repro-cmd)")
        ok, out = gitops.run_check(wt, argv, timeout=min(900, self.cfg.task_timeout_s))
        if ok:
            return ("fail", "the new test passes: the bug is not reproduced")
        self.db.event(t["id"], "manager", "reproduced", clean(out, 300))
        return ("next",)

    def _st_best_of(self, p, t, kind, acts, stages, cancel):
        """Several agents do the same work, each in its own worktree. Winner: passes the check (when the flow has
        one), then the smallest change, then your order. The others are removed at once."""
        if self._day_cap():
            return ("requeue", self._day_cap())
        base = gitops.integration_branch(p["id"])
        self.db.update_task(t["id"], attempts=t["attempts"] + 1)
        t = self.db.task(t["id"])
        card = self._card(p, t, self._last_failure(t))
        results: dict[int, tuple] = {}

        def one(k: int, spec: str) -> None:
            r, name, model, team = self._agent(p, t, spec)
            if r is None:
                results[k] = (spec, None, None, None, name)
                return
            try:
                wt, br = gitops.new_task_worktree(self.home, p["path"], p["id"], t["seq"], t["attempts"] * 10 + k)
            except (gitops.GitError, OSError) as e:
                results[k] = (spec, None, None, None, clean(e, 120))
                return
            self._count_run(t)
            res = r.run(card, str(wt), model=model, check_argv=p["check_cmd"], cancel=cancel, team=team,
                        timeout=min(14400, self.cfg.task_timeout_s * (3 if team else 1)))
            self._cost(res)
            results[k] = (name, wt, br, res, "")
        threads = [threading.Thread(target=one, args=(k, a), daemon=True) for k, a in enumerate(acts)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        if self._stopped(t, cancel):
            for _n, wt, br, _r, _e in results.values():
                if wt:
                    gitops.remove_worktree(p["path"], wt, br)
            return ("end",)
        has_check = bool(p["check_cmd"]) and any(s["kind"] in ("test", "verify") and not s.get("skip") for s in stages)
        good, notes = [], []
        for k in sorted(results):
            name, wt, br, res, err = results[k]
            if wt is None or not getattr(res, "ok", False):
                notes.append(f"{name}: {err or getattr(res, 'error', '') or 'failed'}")
                continue
            try:
                gitops.commit_all(wt, f"task {t['seq']} ({name}): {t['title']}"[:120])
                files = gitops.changed_files(wt, base)
                prot, outside = gitops.violations(files, t["scope"])
            except gitops.GitError as e:
                notes.append(f"{name}: git failed {clean(e, 80)}")
                continue
            if prot or outside:
                notes.append(f"{name}: changed files it may not")
                continue
            if has_check:
                if gitops.merge_in(wt, base):
                    notes.append(f"{name}: does not merge with earlier tasks")
                    continue
                ok, _out = gitops.run_check(wt, p["check_cmd"], timeout=min(900, self.cfg.task_timeout_s))
                if not ok:
                    notes.append(f"{name}: the check failed")
                    continue
            size = _diff_size(wt, base)
            good.append((size, k, name, wt, br, res))
        good.sort(key=lambda x: (x[0], x[1]))
        winner = good[0] if good else None
        for k in sorted(results):
            _name, wt, br, _res, _e = results[k]
            if wt is not None and (winner is None or wt != winner[3]):
                gitops.remove_worktree(p["path"], wt, br)
        if winner is None:
            return ("fail", "no agent produced a usable result: " + "; ".join(notes)[:250])
        _size, _k, name, wt, br, res = winner
        if t["worktree"] and Path(t["worktree"]).exists() and t["worktree"] != str(wt):
            gitops.remove_worktree(p["path"], Path(t["worktree"]), t["branch"])
        self.db.update_task(t["id"], worktree=str(wt), branch=br, used_runner=name)
        self.db.event(t["id"], "manager", "best_of", f"{name} won among {', '.join(acts)}" + (
            f" ({'; '.join(notes)[:200]})" if notes else ""))
        return self._after_code(p, self.db.task(t["id"]), wt, br, res.text, kind)

    # ------------------------------------------------------------------ read-only agent stages
    def _readonly_dir(self, p, t) -> Path:
        return Path(t["worktree"]) if t["worktree"] and Path(t["worktree"]).exists() else self._integration(p)

    def _ask_agent(self, p, t, spec: str, prompt: str, cwd: Path, *, avoid: str = "", timeout: float = 900):
        r, name, model, _team = self._agent(p, t, spec, avoid=avoid)
        if r is None:
            return None, name
        if t["id"] not in self._prepaid and self._day_cap():
            return None, self._day_cap()
        self._count_run(t)
        res = r.run(prompt, str(cwd), model=model, read_only=True, timeout=min(timeout, self.cfg.task_timeout_s))
        self._cost(res)
        return (res, name) if res.ok else (None, f"{name}: {clean(res.error, 160)}")

    def _st_note(self, p, t, kind, spec):
        if self._day_cap():
            return ("requeue", self._day_cap())
        card = cards.stage_card(p, t, [d for d in (self.db.task(x) for x in t["deps"]) if d], cap=self.cfg.context_chars,
                                notes=t["notes"], feedback=self._job(t).get("feedback"))
        prompt = (cards.PLAN_TASK if kind == "plan" else cards.DIAGNOSE).format(card=card)
        res, name = self._ask_agent(p, t, spec, prompt, self._readonly_dir(p, t))
        if res is None:
            return ("fail", name)
        label = "Plan" if kind == "plan" else "Diagnosis"
        self._feedback(t, f"{label} by {name}: {res.text}")
        self.db.event(t["id"], name, kind, clean(res.text, 400))
        return ("next",)

    def _st_review(self, p, t, st, acts):
        if not t["worktree"] or not Path(t["worktree"]).exists():
            return ("next",)                                        # nothing changed yet: nothing to review
        base = gitops.integration_branch(p["id"])
        diff = gitops.git(t["worktree"], "diff", f"{base}...HEAD", check=False).stdout
        if not diff.strip():
            return ("next",)
        if self._day_cap():
            return ("requeue", self._day_cap())
        card = cards.stage_card(p, t, [], cap=self.cfg.context_chars)
        prompt = cards.review_prompt(card, diff)
        verdicts, abstained = [], []
        for spec in acts:
            res, name = self._ask_agent(p, t, spec, prompt, Path(t["worktree"]), avoid=t["used_runner"])
            v = cards.parse_review(res.text) if res is not None else None
            if v is None:                                           # a crash or no verdict abstains, never approves
                abstained.append(name if res is None else f"{name}: no usable verdict")
                continue
            verdicts.append((name, v))
            self.db.event(t["id"], name, "review", f"{v['verdict']}: " + "; ".join(f["text"] for f in v["findings"][:3]))
        if not verdicts:
            return ("stop", "blocked", "no reviewer gave a verdict: " + "; ".join(abstained)[:200])
        ok = [n for n, v in verdicts if v["verdict"] == "approve"]
        req = st.get("require", "all")
        passed = (len(ok) == len(verdicts)) if req == "all" else (len(ok) >= 1) if req == "any" else \
            (len(ok) * 2 > len(verdicts))
        if passed:
            return ("next",)
        for n, v in verdicts:
            if v["verdict"] == "changes":
                self._feedback(t, cards.findings_note(n, v["findings"]))
        return ("fail", "review asks for changes")

    def _st_debate(self, p, t, st, acts):
        names = [a for a in acts if a not in ("jarves", "owner")]
        if len(names) < 2:
            avail = [n for n in self._runner_names(p, t) if self._spec_runner(n)[0] is not None]
            names = (names + [a for a in avail if a not in names])[:2]
        q = st.get("question") or f"How should this task be done? {t['title']}: {t['spec']}"
        r = self.debate(project=p["name"], question=q, agents=names, judge=st.get("judge") or "", task=t["seq"],
                        rounds=st.get("rounds", 2))
        if not r.get("ok"):
            return ("fail", r.get("text") or "the debate failed")
        if r.get("needs_owner"):
            return ("park", f"decide the debate: {r.get('text')} (answer with your decision)")
        self._feedback(t, f"Debate decision: {r['verdict']['decision']} ({r['verdict']['reasons']})")
        return ("next",)

    # ------------------------------------------------------------------ JARVES's own stages
    def _st_check(self, p, t, st, cancel):
        kind = st["kind"]
        repro = self._job(t).get("repro") or {}
        argv = st.get("cmd") if kind == "cmd" else p["check_cmd"]
        has_wt = bool(t["worktree"]) and Path(t["worktree"]).exists()
        wt = Path(t["worktree"]) if has_wt else self._integration(p)
        base = gitops.integration_branch(p["id"])
        with self._merge_locks.setdefault(p["id"], threading.Lock()):
            if self._stopped(t, cancel):
                return ("end",)
            if has_wt:
                try:
                    gitops.commit_all(wt, f"task {t['seq']}: {t['title']}"[:120])
                    conflicts = gitops.merge_in(wt, base)
                except gitops.GitError as e:
                    return ("stop", "failed", f"merge failed: {clean(e, 200)}")
                if conflicts:
                    return ("stop", "blocked", "merge conflict with earlier tasks in: " + ", ".join(conflicts[:5]))
            outs = []
            ok = True
            if kind == "verify" and repro.get("cmd"):
                ok, out = gitops.run_check(wt, repro["cmd"], timeout=min(900, self.cfg.task_timeout_s))
                outs.append(out)
                if not ok:
                    outs.insert(0, "the reproduction still fails:")
            if ok:
                ok, out = gitops.run_check(wt, argv or [], timeout=min(900, self.cfg.task_timeout_s))
                outs.append(out)
            if ok and kind == "verify" and repro.get("files") and repro.get("commit") and has_wt:
                touched = gitops.git(wt, "diff", "--name-only", repro["commit"], "HEAD", "--", *repro["files"],
                                     check=False).stdout.split()
                if touched:
                    ok = False
                    outs = [f"the reproduction test was changed: {', '.join(touched[:3])}"]
            tested = gitops.git(self._integration(p), "rev-parse", "HEAD", check=False).stdout.strip()
        if self._stopped(t, cancel):
            return ("end",)
        if not ok:
            text = " ".join(outs)
            self.db.event(t["id"], "manager", "check_failed", clean(text, cards.FAIL_MAX))
            return ("fail", f"the {kind} failed: " + clean(text, 150))
        self._set_job(t, tested_base=tested)
        return ("next",)

    def _st_merge(self, p, t, stages, cancel):
        if not t["worktree"] or not Path(t["worktree"]).exists():
            return ("next",)                                        # nothing to merge
        wt, br = Path(t["worktree"]), t["branch"]
        base = gitops.integration_branch(p["id"])
        has_check = any(s["kind"] in ("test", "verify") and not s.get("skip") for s in stages) and p["check_cmd"]
        with self._merge_locks.setdefault(p["id"], threading.Lock()):
            if self._stopped(t, cancel):
                return ("end",)
            try:
                gitops.commit_all(wt, f"task {t['seq']}: {t['title']}"[:120])
                head = gitops.git(self._integration(p), "rev-parse", "HEAD", check=False).stdout.strip()
                if head != self._job(t).get("tested_base"):
                    conflicts = gitops.merge_in(wt, base)            # the integration branch moved since the check
                    if conflicts:
                        return ("stop", "blocked", "merge conflict with earlier tasks in: " + ", ".join(conflicts[:5]))
                    if has_check:
                        ok, out = gitops.run_check(wt, p["check_cmd"], timeout=min(900, self.cfg.task_timeout_s))
                        if not ok:
                            self.db.event(t["id"], "manager", "check_failed", clean(out, cards.FAIL_MAX))
                            test_i = pl.find(stages, ("test", "verify"), before=len(stages))
                            st = stages[test_i] if test_i is not None else {"kind": "test", "on_fail": "retry"}
                            self._on_fail(p, t, st, test_i if test_i is not None else 0, stages,
                                          reason="the check failed after merging newer work: " + clean(out, 120))
                            return ("end",)
                gitops.fast_forward(self._integration(p), br)
            except gitops.GitError as e:
                return ("stop", "failed", f"merge failed: {clean(e, 200)}")
        stat = t["result"]
        gitops.remove_worktree(p["path"], wt, br)
        self.db.update_task(t["id"], branch="", worktree="", reason="")
        self._set_job(t, kept=None)
        self.db.event(t["id"], "manager", "merged", f"merged into {base}: {stat}")
        return ("next",)

    def _st_release(self, p, t):
        """The owner's release command (set on the PC only), at the integration branch's commit, in a detached
        worktree, one release at a time per project, after an approval each time. Never retried."""
        argv = p.get("release_cmd") or []
        if not argv:
            return ("park", "no release command is set for this project (jarvis project config --release "
                            "\"...\" on the PC): release it yourself, then say done")
        sha = gitops.git(self._integration(p), "rev-parse", "HEAD", check=False).stdout.strip()
        if not self._approve(f"Release {p['name']} at {sha[:10]}: {' '.join(argv)[:300]}?", "project_release",
                             {"project": p["name"], "commit": sha[:12]}):
            return ("park", "the release was not approved: say done when you released it yourself, or approve it "
                            "again by answering approve")
        lock = self._merge_locks.setdefault(("release", p["id"]), threading.Lock())
        if not lock.acquire(blocking=False):
            return ("requeue", "another release of this project is running")
        try:
            wt = gitops.detached_worktree(self.home, p["path"], p["id"], sha)
            try:
                ok, out = gitops.run_check(wt, argv, timeout=3600, env_keep=list(self.cfg.release_env))
            finally:
                gitops.remove_worktree(p["path"], wt)
        finally:
            lock.release()
        self.db.event(t["id"], "manager", "release", clean(out, 400))
        return ("next",) if ok else ("stop", "blocked", "the release command failed: " + clean(out, 150))

    # ------------------------------------------------------------------ the owner's stages
    def _tree(self, t) -> tuple[str, bool]:
        """(HEAD commit, is the worktree dirty) — an approval is void if either changes, including an uncommitted
        edit after the approval (the merge stage would otherwise fold it in silently)."""
        if not t["worktree"] or not Path(t["worktree"]).exists():
            return "", False
        wt = t["worktree"]
        head = gitops.git(wt, "rev-parse", "HEAD", check=False).stdout.strip()
        dirty = bool(gitops.git(wt, "status", "--porcelain", check=False).stdout.strip())
        return head, dirty

    def _owner_stage(self, p, t, st, i, stages):
        kind = st["kind"]
        appr = self._job(t).get("approved") or {}
        if appr.get("stage") == i:                                  # you approved; JARVES does the part it owns
            self._set_job(t, approved=None)
            if kind == "merge":
                head, dirty = self._tree(t)
                if dirty or (appr.get("tree") and appr["tree"] != head):
                    return ("park", f"the code changed after your approval: approve merging task #{t['seq']} again")
                return self._st_merge(p, self.db.task(t["id"]), stages, threading.Event())
            if kind == "work":
                wt, br = Path(t["worktree"]), t["branch"]
                head = gitops.git(wt, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()
                if head != br:
                    return ("park", f"the folder {wt} is not on the task branch {br} any more: switch back, then say done")
                base = gitops.integration_branch(p["id"])
                if gitops.git(wt, "merge-base", "--is-ancestor", base, "HEAD", check=False).returncode != 0 and \
                        gitops.git(wt, "merge-base", "--is-ancestor", f"{base}@{{1}}", "HEAD", check=False).returncode != 0:
                    pass                                            # rebased by hand: the merge stage re-merges
                return self._after_code(p, t, wt, br, "", "work", owner=True)
            return ("next",)
        base = gitops.integration_branch(p["id"])
        if kind in ("work", "reproduce"):
            try:
                wt, br = self._ensure_worktree(p, t)
            except (gitops.GitError, OSError) as e:
                return ("stop", "failed", f"worktree failed: {clean(e, 200)}")
            what = "write a failing test for the bug" if kind == "reproduce" else "edit the code"
            return ("park", f"your turn: {what} in {wt} (branch {br}), then say done")
        where = t["worktree"] or str(self._integration(p))
        text = {
            "plan": f"plan task #{t['seq']} yourself: say done with your plan as the text",
            "test": f"test task #{t['seq']} yourself (code in {where}), then say pass or fail",
            "verify": f"check that the bug of task #{t['seq']} is fixed (code in {where}), then say pass or fail",
            "review": f"review task #{t['seq']}: git -C \"{where}\" diff {base}...HEAD, then approve or reject with what "
                      "to change",
            "merge": f"approve merging task #{t['seq']} into {base}",
            "release": f"release {p['name']} yourself, then say done",
            "gate": st.get("question") or f"approve to continue task #{t['seq']}",
            "debate": "decide this question yourself and answer with your decision",
            "diagnose": f"find the cause of the bug of task #{t['seq']} and say done with what you found",
            "cmd": f"run the command for task #{t['seq']} yourself, then say pass or fail",
        }.get(kind, f"your turn on task #{t['seq']} ({kind}), then say done")
        return ("park", text)

    def answer(self, project: str = "", task="", answer: str = "approve", text: str = "", **_) -> dict:
        """The owner answers a waiting task: approve / done / pass -> go on; reject / fail -> the stage's on_fail
        with your text as feedback. A debate waiting for you takes your text as the decision."""
        p, err = self._need_project(project)
        if err:
            return err
        t = self._task(p, task)
        if t is None:
            return self._res(False, "NO_TASK", "which task? give its number")
        if t["status"] != "waiting":
            return self._res(False, "STATE", f"task #{t['seq']} is {t['status']}, not waiting for you")
        stages = self._stages(p, t)
        i = min(t["stage"], len(stages) - 1)
        st = stages[i]
        a = (answer or "").strip().lower()
        note = clean(text, cards.NOTE_MAX)
        positive = a in ("approve", "done", "pass", "yes", "ok", "go", "כן", "אישור", "מאשר", "עבר", "סיימתי")
        self.db.event(t["id"], "owner", "answer", f"{a}: {note}" if note else a)
        if note:
            self.db.update_task(t["id"], notes=(t["notes"] + [note])[-5:])      # the owner's own words
        if positive:
            if st["kind"] == "debate" and note:
                self.db.add_decision(p["id"], st.get("question") or t["title"], [], "owner", note, "", [],
                                     task_id=t["id"])
            if st["by"] == "owner" and st["kind"] in ("merge", "work"):
                if st["kind"] == "merge" and t["worktree"] and Path(t["worktree"]).exists():
                    try:
                        gitops.commit_all(Path(t["worktree"]), f"task {t['seq']}: owner edit before merge"[:120])
                    except gitops.GitError:
                        pass
                    t = self.db.task(t["id"])
                self._set_job(t, approved={"stage": i, "tree": self._tree(t)[0]})
                self.db.update_task(t["id"], status="todo", waiting="")
            else:
                self.db.update_task(t["id"], stage=pl.next_index(stages, i), status="todo", waiting="",
                                    rounds=0 if st["kind"] == "review" else t["rounds"])
            self.db.promote(p["id"])
            return self._res(True, "ok", f"Task #{t['seq']} goes on.")
        self.db.update_task(t["id"], status="running", waiting="")
        if note:
            self._feedback(t, f"The owner says: {note}")
        self._on_fail(p, self.db.task(t["id"]), st, i, stages, reason=note or "you said no")
        self.db.promote(p["id"])
        t2 = self.db.task(t["id"])
        return self._res(True, "ok", f"Task #{t['seq']}: {t2['status']}" + (f" ({t2['reason']})" if t2["reason"] else ""))

    # ------------------------------------------------------------------ debates
    def debate(self, project: str = "", question: str = "", agents: list[str] | None = None, judge: str = "",
               rounds: int = 2, task="", options: list[str] | None = None, **_) -> dict:
        """Two or more agents argue a question (read-only); the judge (never a participant) decides, or you do."""
        p, err = self._need_project(project)
        if err:
            return err
        try:
            names = [pl.actor(a) for a in (agents or []) if str(a).strip()]
            judge = pl.actor(judge) if judge else ""
        except pl.FlowError as e:
            return self._res(False, "BAD_AGENT", str(e))
        names = list(dict.fromkeys(n for n in names if n not in ("jarves", "owner")))
        if len(names) < 2:
            avail = [n for n in self._runner_names(p, None) if self._spec_runner(n)[0] is not None]
            names = (names + [a for a in avail if a not in names])[:2]
        if len(names) < 2:
            return self._res(False, "NO_RUNNER", "a debate needs two installed agents")
        if not judge:
            avail = [n for n in self._runner_names(p, None) if self._spec_runner(n)[0] is not None and n not in names]
            judge = avail[0] if avail else "owner"
        need = len(names) * max(1, min(3, int(rounds or 2))) + (0 if judge == "owner" else 1)
        if self.cfg.max_runs_per_day and self.db.counter("runs") + need > self.cfg.max_runs_per_day:
            return self._res(False, "CAP", f"a debate needs {need} agent runs; the daily limit leaves fewer")
        t = self._task(p, task) if task not in ("", None) else None
        cwd = self._readonly_dir(p, t) if t else self._integration(p)

        def run_fn(agent: str, prompt: str):
            r, name, model, _team = self._agent(p, t or {"model": "", "mode": "solo", "runner": ""}, agent)
            if r is None:
                return False, "", name
            self.db.bump("runs")
            res = r.run(prompt, str(cwd), model=model, read_only=True, timeout=180)
            self._cost(res)
            return res.ok, res.text, res.error
        ctx = f"{t['title']}: {t['spec']}" if t else (p.get("goal") or "")
        r = debatemod.run(question, names, run_fn, judge=judge, rounds=rounds, context=ctx, options=options)
        if not r.get("ok"):
            return self._res(False, "DEBATE_FAILED", r.get("text") or "the debate failed")
        v = r.get("verdict") or {}
        did = self.db.add_decision(p["id"], r["question"], names, judge, v.get("decision", "") if not r["needs_owner"]
                                   else "", v.get("reasons", ""), r["rounds"],
                                   state="waiting" if r["needs_owner"] else "decided", task_id=t["id"] if t else None)
        if t:
            self.db.event(t["id"], "manager", "debate", clean(r["text"], 300))
        return self._res(True, "ok", r["text"], decision=v, needs_owner=r["needs_owner"], id=did, rounds=r["rounds"],
                         participants=names, judge=judge, untrusted=self.UNTRUSTED)


def _diff_size(wt: Path, base: str) -> int:
    out = gitops.git(wt, "diff", "--numstat", f"{base}...HEAD", check=False).stdout
    n = 0
    for line in out.splitlines():
        a, _, rest = line.partition("\t")
        d, _, _ = rest.partition("\t")
        n += (int(a) if a.isdigit() else 0) + (int(d) if d.isdigit() else 0)
    return n
