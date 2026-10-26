"""Runners: one headless, one-shot agent process per task (fresh small context). Each runner only BUILDS argv/env and
PARSES output; `run()` does the (killable) subprocess work with the prompt on stdin where the tool supports it.

Never passed: any flag in orchestrator.policy.FORBIDDEN_FLAGS (checked on the flags, not on the prompt text).
Subscription sign-ins only: the child gets an allow-listed environment without API keys (OPENROUTER_API_KEY only for
the paid OpenRouter route, which is off unless `projects.allow_openrouter`).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..orchestrator import policy
from . import gitops

_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
OUT_CAP = 200_000
# Read-only git: exact commands or fixed-flag prefixes, never a bare `git diff:*` / `git log:*` prefix (F6, #205: `git log
# --output=<path>` writes a file anywhere). The `--output` and `-c` forms are also in DENY (defence in depth).
READ_GIT = ("Bash(git status)", "Bash(git status --short)", "Bash(git status -sb)", "Bash(git diff)",
            "Bash(git diff --stat)", "Bash(git diff --name-only)", "Bash(git diff --cached)", "Bash(git diff HEAD)",
            "Bash(git log --oneline:*)", "Bash(git log -n:*)", "Bash(git log --stat)")
SAFE_BASH = (*READ_GIT, "Bash(git add:*)", "Bash(git commit:*)")
DENY = ("Bash(git * --output*)", "Bash(git * -o *)", "Bash(git -c:*)", "Bash(git * -c *)", "Bash(git * --ext-diff*)",
        "Bash(git * --upload-pack*)", "Bash(git * --exec*)", "Bash(git push:*)", "Bash(git rebase:*)", "Bash(git reset:*)", "Bash(git checkout:*)", "Bash(git branch:*)",
        "Bash(rm:*)", "Bash(curl:*)", "Bash(wget:*)", "Bash(sudo:*)", "WebFetch",
        # agent and sub-agent definitions/settings: an agent must not widen its own (or its sub-agents') permissions
        "Edit(.claude/**)", "Write(.claude/**)", "Edit(.codex/**)", "Write(.codex/**)", "Edit(.opencode/**)",
        "Write(.opencode/**)", "Edit(opencode.json)", "Write(opencode.json)", "Edit(.mcp.json)", "Write(.mcp.json)")


@dataclass
class Result:
    ok: bool = False
    text: str = ""
    session_id: str = ""
    cost_usd: float = 0.0
    tokens: int = 0
    error: str = ""
    timed_out: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class Spec:
    argv: list[str]
    stdin: str | None
    env: dict[str, str]


def check_flags(argv: list[str]) -> None:
    for a in argv:
        if a.startswith("-") and any(f in a for f in policy.FORBIDDEN_FLAGS):
            raise ValueError(f"forbidden flag {a}")
    for i, a in enumerate(argv):
        if a == "--permission-mode" and i + 1 < len(argv) and argv[i + 1] in ("bypassPermissions", "auto"):
            raise ValueError("forbidden permission mode")


def _resolve(cfg_cmd: list[str], names: tuple[str, ...]) -> list[str]:
    if cfg_cmd:
        return list(cfg_cmd)
    for n in names:
        p = shutil.which(n)
        if p:
            return [p]
    return []


class Runner:
    name = ""
    install_hint = ""

    def __init__(self, cfg=None) -> None:
        self.cfg = cfg

    def command(self) -> list[str]:
        return []

    def available(self) -> bool:
        return bool(self.command())

    def build(self, prompt: str, cwd: str, *, model: str = "", resume: str = "", read_only: bool = False,
              check_argv: list[str] | None = None, team: bool = False) -> Spec:
        raise NotImplementedError

    def parse(self, stdout: str, rc: int) -> Result:
        raise NotImplementedError

    def run(self, prompt: str, cwd: str, *, model: str = "", resume: str = "", read_only: bool = False,
            check_argv: list[str] | None = None, timeout: float = 1800, on_start=None, cancel: threading.Event | None = None,
            team: bool = False) -> Result:
        try:
            spec = self.build(prompt, cwd, model=model, resume=resume, read_only=read_only, check_argv=check_argv,
                              team=team)
            check_flags(spec.argv)
        except ValueError as e:
            return Result(error=str(e))
        try:
            p = subprocess.Popen(spec.argv, cwd=cwd, stdin=subprocess.PIPE if spec.stdin is not None else subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=spec.env,
                                 creationflags=_NOWIN, encoding="utf-8", errors="replace",
                                 start_new_session=(os.name != "nt"))
        except OSError as e:
            return Result(error=f"could not start {self.name}: {e}")
        if on_start:
            on_start(p.pid)
        box: dict = {}

        def talk() -> None:
            try:
                box["out"], box["err"] = p.communicate(spec.stdin, timeout=timeout)
            except subprocess.TimeoutExpired:
                box["timeout"] = True
        t = threading.Thread(target=talk, daemon=True)
        t.start()
        while t.is_alive():
            t.join(0.2)
            if cancel is not None and cancel.is_set():
                kill_tree(p)
                t.join(5)
                return Result(error="cancelled")
        if box.get("timeout"):
            kill_tree(p)
            return Result(error=f"timed out after {int(timeout)} s", timed_out=True)
        out = (box.get("out") or "")[-OUT_CAP:]
        r = self.parse(out, p.returncode)
        if not r.ok and not r.error:
            r.error = (box.get("err") or "").strip()[-300:] or f"{self.name} exited with {p.returncode}"
        return r


def kill_tree(p: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, creationflags=_NOWIN)
        else:
            import signal
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError, subprocess.SubprocessError):
        try:
            p.kill()
        except OSError:
            pass
    try:
        p.wait(5)
    except subprocess.SubprocessError:
        pass


def _tools(read_only: bool, check_argv: list[str] | None, team: bool = False) -> list[str]:
    t = ["Read", "Glob", "Grep"]
    if read_only:
        return t + list(READ_GIT)
    t += ["Edit", "Write", *SAFE_BASH]
    if team:
        t += ["Task", "Agent"]                    # Claude Code's own sub-agents; their definitions under .claude/ are
                                                  # denied (DENY) and checked again after the run (PROTECTED); real
                                                  # sub-agent limits are UNVERIFIED against a live Claude Code
    if check_argv:
        t.append(f"Bash({' '.join(check_argv[:3])}:*)")
    return t


class ClaudeRunner(Runner):
    name = "claude"
    install_hint = "install Claude Code and sign in (claude)"

    def command(self) -> list[str]:
        return _resolve(list(getattr(self.cfg, "claude_command", []) or []), ("claude", "claude.cmd"))

    def build(self, prompt, cwd, *, model="", resume="", read_only=False, check_argv=None, team=False) -> Spec:
        argv = [*self.command(), "-p", "--output-format", "json", "--permission-mode", "dontAsk",
                "--permission-prompts", "none", "--setting-sources", "user", "--allowedTools",
                *_tools(read_only, check_argv, team),
                "--disallowedTools", *DENY]
        if model:
            argv += ["--model", model]
        if resume:
            argv += ["--resume", resume]
        return Spec(argv, prompt, gitops.child_env())

    def parse(self, stdout: str, rc: int) -> Result:
        d = _last_json(stdout)
        if not isinstance(d, dict):
            return Result(error="" if rc else "no result from Claude Code", text=stdout[-2000:])
        text = str(d.get("result") or "")
        bad = bool(d.get("is_error")) or rc != 0
        return Result(ok=not bad, text=text, session_id=str(d.get("session_id") or ""),
                      cost_usd=float(d.get("total_cost_usd") or 0), error=text[:300] if bad else "",
                      tokens=_tok(d.get("usage")), extra={"turns": d.get("num_turns")})


class CodexRunner(Runner):
    name = "codex"
    install_hint = "install the Codex CLI and sign in (codex login)"

    def command(self) -> list[str]:
        return _resolve(list(getattr(self.cfg, "codex_command", []) or []), ("codex", "codex.exe", "codex.cmd"))

    def build(self, prompt, cwd, *, model="", resume="", read_only=False, check_argv=None, team=False) -> Spec:
        sandbox = "read-only" if read_only else "workspace-write"
        argv = [*self.command(), "exec", "--json", "--sandbox", sandbox, "-C", cwd, "--skip-git-repo-check"]
        if model:
            argv += ["-m", model]
        if resume and not read_only:            # `exec resume` takes no --sandbox: a read-only question is a fresh run
            argv = [*self.command(), "exec", "resume", resume, "--json"] + (["-m", model] if model else [])
        argv.append("-")
        return Spec(argv, prompt, gitops.child_env())

    def parse(self, stdout: str, rc: int) -> Result:
        sid, text, tokens, err = "", "", 0, ""
        for line in stdout.splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            ty = str(e.get("type") or "")
            if ty == "thread.started":
                sid = str(e.get("thread_id") or sid)
            elif ty == "item.completed":
                it = e.get("item") or {}
                if it.get("type") in ("agent_message", "assistant_message"):
                    text = str(it.get("text") or text)
            elif ty == "turn.completed":
                u = e.get("usage") or {}
                tokens += int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)
            elif ty in ("error", "turn.failed"):
                err = str(e.get("message") or (e.get("error") or {}).get("message") or "failed")[:300]
        return Result(ok=rc == 0 and not err and bool(text), text=text, session_id=sid, tokens=tokens, error=err)


class OpenCodeRunner(Runner):
    """OpenCode, also the route to OpenRouter models (`--model openrouter/<id>`). Its output shape is read leniently;
    UNVERIFIED against a live install (status implemented_unverified)."""
    name = "opencode"
    install_hint = "install OpenCode (opencode) and connect a provider"

    def command(self) -> list[str]:
        return _resolve(list(getattr(self.cfg, "opencode_command", []) or []), ("opencode", "opencode.cmd", "opencode.exe"))

    def build(self, prompt, cwd, *, model="", resume="", read_only=False, check_argv=None, team=False) -> Spec:
        extra: dict[str, str] = {}
        if model.startswith("openrouter/"):
            if not getattr(self.cfg, "allow_openrouter", False):
                raise ValueError("OpenRouter is paid and off: enable projects.allow_openrouter first")
            key = os.environ.get("OPENROUTER_API_KEY", "")
            if not key:
                raise ValueError("OPENROUTER_API_KEY is not set in the environment")
            extra["OPENROUTER_API_KEY"] = key
        argv = [*self.command(), "run", "--format", "json"]
        if model:
            argv += ["-m", model]
        if resume:
            argv += ["-s", resume]
        argv += ["--", prompt]                      # opencode takes the message as an argument
        return Spec(argv, None, gitops.child_env(extra))

    def parse(self, stdout: str, rc: int) -> Result:
        sid, parts, err = "", [], ""
        for line in stdout.splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            sid = str(e.get("sessionID") or e.get("session_id") or sid)
            if e.get("type") == "text":
                parts.append(str((e.get("part") or {}).get("text") or e.get("text") or ""))
            elif e.get("type") == "error":
                err = str((e.get("error") or {}).get("message") or e.get("message") or "error")[:300]
        text = "\n".join(p for p in parts if p) or ("" if parts or rc else stdout[-2000:])
        return Result(ok=rc == 0 and not err and bool(text), text=text, session_id=sid, error=err)


def _last_json(s: str):
    s = (s or "").strip()
    for cand in (s, *reversed([x for x in s.splitlines() if x.strip().startswith("{")])):
        try:
            return json.loads(cand)
        except ValueError:
            continue
    return None


def _tok(u) -> int:
    return int((u or {}).get("input_tokens") or 0) + int((u or {}).get("output_tokens") or 0) if isinstance(u, dict) else 0


REGISTRY = {"claude": ClaudeRunner, "codex": CodexRunner, "opencode": OpenCodeRunner}


def make(name: str, cfg=None) -> Runner | None:
    cls = REGISTRY.get(name.lower())
    return cls(cfg) if cls else None
