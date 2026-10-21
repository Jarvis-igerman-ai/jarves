"""SW-V3 test campaign, PASS B (feature by feature): drives the REAL Core (in this process, offline TEST profile),
the real dashboard over HTTP, the real CLI (subprocess) and the Core tool registry.

    .venv/Scripts/python.exe tools/campaign_features.py STAGE [STAGE...]

State lives in JARVES_HOME (a temp dir, profile "test"); the real profile is never touched. Every check is appended to
RESULTS (JSON lines) so the review document can be built from it. Stages are small so one failure does not hide others.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "src"))
sys.path.insert(0, str(CORE))
BASE = Path(os.environ.get("JARVES_HOME") or (Path(os.environ["TEMP"]) / "jv_b_home"))
os.environ["JARVES_HOME"] = str(BASE)
PROFILE = os.environ.get("CAMPAIGN_PROFILE", "test")
os.environ["JARVES_PROFILE"] = PROFILE
os.environ.pop("OPENAI_API_KEY", None)
RESULTS = Path(os.environ.get("CAMPAIGN_RESULTS") or (BASE / "campaign_results.jsonl"))
EXE = CORE / ".venv" / "Scripts" / "jarves.exe"


def rec(area: str, name: str, verdict: str, evidence: str = "") -> None:
    row = {"area": area, "name": name, "verdict": verdict, "evidence": evidence[:900]}
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"[{verdict}] {area} :: {name}" + (f"  -- {evidence[:300]}" if evidence and verdict != "PASS" else ""), flush=True)


def check(area: str, name: str, ok: bool, evidence: str = "") -> bool:
    rec(area, name, "PASS" if ok else "FAIL", evidence)
    return ok


def cli(*args: str, timeout: int = 120, stdin: str | None = None, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "JARVES_HOME": str(BASE), "PYTHONPATH": str(CORE / "src"), **(env_extra or {})}
    return subprocess.run([sys.executable, "-m", "jarves", "--profile", PROFILE, *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env, input=stdin, cwd=str(CORE))


class Dash:
    """The real dashboard over HTTP, as the browser uses it (one-time token -> cookie, X-Jarves header)."""

    def __init__(self, url: str) -> None:
        import httpx
        from urllib.parse import urlsplit
        u = urlsplit(url)
        self.c = httpx.Client(base_url=f"{u.scheme}://{u.netloc}", timeout=20, follow_redirects=False)
        r = self.c.get("/?" + u.query)
        if r.status_code != 302:
            raise RuntimeError(f"dashboard token exchange: HTTP {r.status_code}")

    def get(self, path: str):
        r = self.c.get(path)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text

    def req(self, method: str, path: str, body: dict | None = None):
        r = self.c.request(method, path, json=body, headers={"X-Jarves": "1"})
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text

    def post(self, path: str, body: dict | None = None):
        return self.req("POST", path, body or {})


class FakeAutostart:
    """Never touch the real HKCU Run key from a campaign run (the first toggles run did, for about a second)."""
    platform = "fake"

    def __init__(self) -> None:
        self.on = False

    def enabled(self) -> bool:
        return self.on

    def enable(self) -> str:
        self.on = True
        return "fake"

    def disable(self) -> bool:
        self.on = False
        return True

    def describe(self) -> dict:
        return {"platform": "fake", "mechanism": "campaign fake", "enabled": self.on, "entry": None}


class _Policy:
    def __init__(self, face) -> None:
        self.face = face

    def __setitem__(self, k, v) -> None:
        self.face.confirm_policy = "approve" if v else "deny"

    def __getitem__(self, k):
        return self.face.confirm_policy == "approve"


class _Answers:
    """The prompts the face was asked to approve since the last clear()."""

    def __init__(self, face) -> None:
        self.face, self.mark = face, 0

    def _all(self) -> list[str]:
        return [str(p.get("prompt")) for ts, ty, p in self.face.msgs[self.mark:] if ty == "confirm.request"]

    def clear(self) -> None:
        self.mark = len(self.face.msgs)

    def __bool__(self) -> bool:
        return bool(self._all())

    def __len__(self) -> int:
        return len(self._all())

    def __iter__(self):
        return iter(self._all())

    def __getitem__(self, i):
        return self._all()[i]


class H:
    """Harness: a booted Core + its dashboard."""

    async def boot(self, confirm=None):
        from jarves import profiles
        profiles.select(PROFILE)
        from jarves.app import Core
        from jarves.config import load_config
        cfg, cp = load_config()
        self.home = cp.parent
        self.cfg = cfg
        self.core = Core(cfg, self.home, console_logs=False, use_real_audio=False, autostart=FakeAutostart())
        self.core.open_url = None
        try:                                    # many scripted approvals in a minute: the dedicated stage restores the real limits
            from jarves import approvals as appr
            appr.RATE_LIMIT = (10_000, 60.0)
            appr.DENY_COOLDOWN_S = 0.0
        except ImportError:
            pass
        await self.core.start()
        if getattr(self.core, "approvals", None) is not None:
            self.core.approvals.rate_limit = (10_000, 60.0)        # instance value (set from the module default at creation)
        self.reg = self.core.registry
        self.feat = self.core.features
        # approvals take the REAL path: Core.ask_confirm -> link -> a stand-in for the Godot face that answers by policy
        from tools.suite_voice import FaceClient
        self.face = FaceClient(self.home, cfg.link.port)
        await self.face.start()
        await until(lambda: self.core.link.face_client_id() is not None, 5)
        self.policy = _Policy(self.face)
        self.answers = _Answers(self.face)
        self.url = await self.core.open_dashboard()
        self.dash = Dash(self.url)
        return self

    async def stop(self):
        try:
            await self.face.stop()
        except Exception:
            pass
        await self.core.stop()

    async def call(self, tool: str, args: dict | None = None, origin: str = "model"):
        return await self.reg.call(tool, args or {}, origin=origin)

    async def acli(self, *a, **k):
        return await asyncio.to_thread(cli, *a, **k)

    async def aget(self, p):
        return await asyncio.to_thread(self.dash.get, p)

    async def apost(self, p, b=None):
        return await asyncio.to_thread(self.dash.post, p, b)

    async def areq(self, m, p, b=None):
        return await asyncio.to_thread(self.dash.req, m, p, b)


async def until(pred, timeout: float = 4.0, step: float = 0.1) -> bool:
    t = time.time()
    while time.time() - t < timeout:
        try:
            if pred():
                return True
        except Exception:
            pass
        await asyncio.sleep(step)
    return False


async def log_review(h, stage_name: str, logp: Path, off: int) -> None:
    """After EACH stage: `jarvis logs -n 300 --log-level WARNING` must work, and every WARNING/ERROR the stage wrote is
    grouped (component, code, message shape) and stored as LOG:<stage> rows for manual classification."""
    import collections
    import re
    r = await h.acli("logs", "-n", "300", "--log-level", "WARNING")
    ok = r.returncode == 0 and "Traceback" not in r.stderr
    rec("LOG:" + stage_name, "`jarvis logs -n 300 --log-level WARNING` works", "PASS" if ok else "FAIL",
        f"rc={r.returncode} lines={len(r.stdout.splitlines())}")
    try:
        with logp.open("rb") as f:
            f.seek(off)
            lines = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return
    groups: dict[tuple, list] = collections.OrderedDict()
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("level") in ("WARNING", "ERROR", "CRITICAL"):
            shape = re.sub(r"\d+", "N", d.get("msg", ""))[:70]
            extra = [str(d[k]) for k in ("tool", "result_code", "reason", "err_code", "feature", "job", "kind") if d.get(k) not in (None, "")]
            if extra:
                shape += " [" + " ".join(extra)[:70] + "]"
            groups.setdefault((d["level"], d["component"], d["code"], shape), []).append(d)
    for (lvl, comp, code, shape), items in groups.items():
        rec("LOG:" + stage_name, f"{lvl} {comp}/{code}: {shape}", "INFO", f"x{len(items)} first={items[0].get('ts', '')[11:19]}")


STAGES: dict[str, callable] = {}


def stage(fn):
    STAGES[fn.__name__] = fn
    return fn


async def main(names: list[str]) -> int:
    import importlib
    for mod in ("campaign_stages_a", "campaign_stages_b", "campaign_stages_c", "campaign_stages_d", "campaign_stages_e", "campaign_stages_f", "campaign_stages_g", "campaign_stages_h", "campaign_stages_v4"):
        try:
            importlib.import_module(f"tools.{mod}")
        except ModuleNotFoundError as e:
            if mod not in str(e):
                raise
    import tools.campaign_features as cf2          # this file runs as __main__; the stage modules import the other copy
    STAGES.update(cf2.STAGES)
    h = await H().boot()
    rc = 0
    try:
        for n in names:
            if n not in STAGES:
                print("unknown stage", n, "known:", ", ".join(STAGES))
                rc = 2
                continue
            print(f"=== stage {n}", flush=True)
            logp = h.home / "logs" / "jarves.log"
            off = logp.stat().st_size if logp.exists() else 0
            try:
                await STAGES[n](h)
            except Exception as e:
                import traceback
                traceback.print_exc()
                rec(n, "stage crashed", "FAIL", f"{type(e).__name__}: {e}")
                rc = 1
            await log_review(h, n, logp, off)
    finally:
        await h.stop()
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
