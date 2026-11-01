"""Blocked view: one classification (each cause, empty, PR fail-soft), GET /api/blocked, CLI text and the static tab."""
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from jarves.workflow import observability as obs
from jarves.workflow.server import STATIC, Server
from jarves.workflow.store import Store

T0 = 1_800_000_000.0


def task(ident, state="queued", **extra):
    return {"id": ident, "title": f"Task {ident}", "state": state, "execution_allowed": True, "depends_on": [], "resources": [],
            "blocker": "", "updated_at": T0 - 600, "agent_id": None, **extra}


def classify(tasks=(), agents=(), questions=(), locks=None, claims=(), prs=()):
    return obs.classify_blocked(list(tasks), list(agents), list(questions), dict(locks or {}), list(claims), prs, T0)


class ClassifyTests(unittest.TestCase):
    def test_empty_and_unblocked_work(self):
        self.assertEqual(classify(), [])
        # Ready, running, finished and not-authorised work is not "blocked".
        rows = classify([task("ready"), task("run", "running"), task("ok", "done"), task("later", execution_allowed=False)])
        self.assertEqual(rows, [])

    def test_owner_question(self):
        q = {"id": "q1", "task_id": "a", "status": "pending", "blocks": True, "created_at": T0 - 3600}
        rows = classify([task("a", "waiting_owner"), task("b")], questions=[q, {"id": "q2", "task_id": "b", "status": "answered"}])
        self.assertEqual([(r["cause"], r["id"], r["who"], r["question_id"], r["since"]) for r in rows],
                         [("owner", "a", "owner", "q1", T0 - 3600)])
        self.assertEqual(rows[0]["next_code"], "answer")

    def test_worker_gone_keeps_locks_and_offers_recover_command(self):
        rows = classify([task("w", "blocked", quarantined=True, blocker="worker_offline", agent_id="ag1"),
                         task("n", "blocked", blocker="Native session stopped before a ledger handoff; manager must inspect")],
                        agents=[{"id": "ag1", "status": "offline"}], locks={"windows-gui": "w", "pr:9": "w"})
        self.assertEqual([r["cause"] for r in rows], ["worker_gone", "worker_gone"])
        self.assertEqual(rows[0]["locks"], ["pr:9", "windows-gui"])
        self.assertEqual(rows[0]["commands"], ["workflow_console.py recover --task w"])
        self.assertEqual(rows[0]["who"], "manager")

    def test_gone_agent_with_claims_but_no_task(self):
        claims = [{"resource": "branch:x", "agent": "ghost", "stale": True, "task_id": "agent:ghost", "claimed_at": T0 - 9000}]
        rows = classify(agents=[{"id": "ghost", "status": "offline", "heartbeat_at": T0 - 8000, "label": "Ghost"}], claims=claims)
        self.assertEqual([(r["cause"], r["kind"], r["id"], r["since"]) for r in rows], [("worker_gone", "agent", "ghost", T0 - 8000)])
        self.assertEqual(rows[0]["commands"], ["workflow_console.py release --resource branch:x --force"])
        # A live agent with a fresh claim is normal work, not blocked.
        live = [{"resource": "branch:y", "agent": "busy", "stale": False, "task_id": "t", "claimed_at": T0}]
        self.assertEqual(classify(agents=[{"id": "busy", "status": "running"}], claims=live), [])

    def test_dependency_and_resource(self):
        rows = classify([task("dep", "running"), task("d", depends_on=["dep"]), task("holder", "running"),
                         task("r", resources=["windows-gui"]), task("fine", depends_on=["done1"]), task("done1", "verified")],
                        locks={"windows-gui": "holder"})
        by = {r["id"]: r for r in rows}
        self.assertEqual(set(by), {"d", "r"})
        self.assertEqual((by["d"]["cause"], by["d"]["who"], by["d"]["waiting_on"]), ("dependency", "task:dep", ["dep"]))
        self.assertEqual((by["r"]["cause"], by["r"]["who"], by["r"]["waiting_on"]), ("resource", "task:holder", ["windows-gui"]))

    def test_other_keeps_raw_blocker(self):
        rows = classify([task("o", "blocked", blocker="<b>phone sheet missing</b>")])
        self.assertEqual((rows[0]["cause"], rows[0]["blocker"], rows[0]["next_code"]), ("other", "<b>phone sheet missing</b>", "inspect"))

    def test_pr_rows_and_fail_soft(self):
        prs = [{"number": 1, "title": "Conflicts", "state": "open", "mergeable_state": "dirty", "updated_at": "2026-11-01T10:00:00Z",
                "url": "https://github.com/igalgerman/jarves/pull/1"},
               {"number": 2, "title": "Red", "state": "open", "ci_state": "failure", "url": "javascript:alert(1)"},
               {"number": 3, "title": "Tier R", "state": "open", "labels": ["no-automerge"]},
               {"number": 4, "title": "Owned", "state": "open"},
               {"number": 5, "title": "Fine", "state": "open", "labels": ["docs"]},
               {"number": 6, "title": "Draft", "state": "open", "draft": True, "mergeable_state": "dirty"},
               {"title": "no number"}, "garbage", None, {"number": "x7", "mergeable": False}]
        claims = [{"resource": "pr:4", "agent": "gone", "stale": True, "task_id": "agent:gone", "claimed_at": T0}]
        rows = classify(prs=prs, claims=claims)
        self.assertEqual([(r["id"], r["next_code"], r["who"]) for r in rows],
                         [("1", "pr_conflict", "manager"), ("2", "pr_red", "manager"), ("3", "pr_review", "review"), ("4", "pr_owned", "agent:gone")])
        self.assertEqual(rows[0]["url"], "https://github.com/igalgerman/jarves/pull/1")
        self.assertEqual(rows[1]["url"], "")                       # only GitHub links survive
        self.assertIsNotNone(rows[0]["since"])
        self.assertEqual(rows[3]["commands"], ["workflow_console.py release --resource pr:4 --force"])
        for bad in (None, "x", {"a": 1}, 7):
            self.assertEqual(classify(prs=bad), [])


class BlockedViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "workflow.db", clock=lambda: T0)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def put(self, ident, **changes):
        with self.store._tx() as db:
            item = self.store._get(db, "task", ident)
            item.update(changes)
            self.store._put(db, "task", item)

    def test_grouped_view_text_and_speed(self):
        self.assertEqual(obs.blocked_view(self.store)["total"], 0)
        self.assertEqual(obs.blocked_text(obs.blocked_view(self.store)), "אין חסומים. הכול זורם.")
        for i in range(300):
            self.store.create_task({"id": f"t{i}", "title": f"Work {i}", "lane": "bugs"})
        self.put("t1", state="blocked", quarantined=True, blocker="worker_stalled")
        self.put("t2", state="blocked", blocker="needs a decision from legal")
        self.put("t3", depends_on=["t4"])
        self.put("t4", state="running")
        with self.store._tx() as db:
            db.execute("INSERT INTO workflow_resources VALUES ('windows-gui','t1')")
        self.put("t5", resources=["windows-gui"])
        started = time.perf_counter()
        view = obs.blocked_view(self.store)
        self.assertLess(time.perf_counter() - started, 0.3)
        self.assertEqual([g["cause"] for g in view["groups"]], list(obs.BLOCKED_CAUSES))
        self.assertEqual(view["counts"], {"owner": 0, "worker_gone": 1, "dependency": 1, "resource": 1, "pr": 0, "other": 1})
        self.assertEqual((view["total"], view["tasks_in_blocked_state"], view["prs"]["state"]), (4, 2, "unavailable"))
        text = obs.blocked_text(view)
        self.assertTrue(text.startswith("4 חסומים: עובד אחד נעלם, תלות אחת, משאב תפוס אחד, אחד מסיבה אחרת"), text)
        self.assertIn("מי פועל: מחכה ל־t4", text)
        self.assertIn("$ workflow_console.py recover --task t1", text)
        self.assertIn("נעילות שנשארו: windows-gui", text)
        self.assertIn("needs a decision from legal", text)

    def test_pr_snapshot_used_only_when_valid(self):
        self.store.set_source({"mode": "github", "sha": "abc", "synced_at": T0, "unmapped": [], "error": "",
                               "open_prs": [{"number": 8, "title": "Dirty", "state": "open", "mergeable_state": "dirty"}]})
        view = obs.blocked_view(self.store)
        self.assertEqual((view["counts"]["pr"], view["prs"]["state"]), (1, "ok"))
        self.store.set_source({"mode": "github", "sha": "abc", "synced_at": T0, "unmapped": [], "error": "rate limited", "open_prs": "bad"})
        self.assertEqual(obs.blocked_view(self.store)["counts"]["pr"], 0)


class BlockedHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = Server(Path(self.temp.name), 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = ""

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, data=None):
        h = {"Cookie": self.cookie, "Origin": self.server.origin}
        if data is not None:
            h.update({"Content-Type": "application/json", "X-Workflow-Request": "1"})
        req = urllib.request.Request(self.server.origin + path, data=json.dumps(data).encode() if data is not None else None, headers=h)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            return response.code, dict(response.headers), json.loads(raw) if "json" in response.headers.get("Content-Type", "") else raw

    def test_endpoint_needs_session(self):
        self.assertEqual(self.request("/api/blocked")[0], 401)
        status, headers, _ = self.request("/api/session", {"token": self.server.token})
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, data = self.request("/api/blocked")
        self.assertEqual(status, 200)
        self.assertEqual((data["total"], len(data["groups"])), (0, 6))
        self.assertNotIn(self.server.token, json.dumps(data))

    def test_static_tab_contract(self):
        status, headers, _ = self.request("/blocked.js")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/javascript"))
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        css = (STATIC / "style.css").read_text(encoding="utf-8")
        js = (STATIC / "blocked.js").read_text(encoding="utf-8")
        self.assertNotIn("<script>", html)
        self.assertLess(html.index('src="/app.js"'), html.index('src="/blocked.js"'))
        self.assertIn('<a href="#blocked" data-view="blocked" class="nav-link">', html)
        self.assertIn('<span class="nav-count" id="blocked-count" hidden>0</span>', html)
        self.assertIn('<section id="view-blocked" class="view" hidden aria-label="חסומים"><div id="blocked-root"></div></section>', html)
        self.assertIn('views.blocked = ["חסומים"', app)
        self.assertIn(".bk-summary", css)
        for banned in ("style=", "onclick", "method:", "POST", "localStorage", "http://", "https://", "eval(", "X-Workflow-Request"):
            self.assertNotIn(banned, js)
        self.assertIn("/api/blocked", js)
        self.assertIn('const TZ = "Asia/Jerusalem";', js)
        self.assertIn('aria-live="polite"', js)
        self.assertEqual(js.count('aria-live="polite"'), 1)
        # Every server value goes through esc(); ids sit in <bdi>.
        self.assertIn('const id = value => `<bdi class="mono" dir="ltr">${esc(value)}</bdi>`;', js)
        self.assertIn('<span class="bk-title" dir="auto">${esc(r.title || r.id)}</span>', js)
        self.assertIn('<q class="bk-blocker" dir="auto">${esc(r.blocker)}</q>', js)
        self.assertIn('<code dir="ltr">${esc(c)}</code>', js)
        self.assertNotRegex(js, r"[>\"=]\$\{((r|g)\.[a-z_]+|c|w)\}")   # never a raw value straight into markup
        # Locale part: same keys in Hebrew and English; no run of three bare English words in Hebrew.
        block = js[js.index("const I18N = {"):js.index("const lang =")]
        he, en = block[block.index("he: {"):block.index("en: {")], block[block.index("en: {"):]
        keys = lambda part: set(re.findall(r"(?<![^\s{,])([A-Za-z_]+): ", part)) - {"he", "en"}
        self.assertEqual(keys(he), keys(en))
        for text in re.findall(r'"([^"]*)"|`([^`]*)`', he):
            words = " ".join(text)
            self.assertIsNone(re.search(r"\b[A-Za-z]+ [A-Za-z]+ [A-Za-z]+\b", words), words)
        for cause in obs.BLOCKED_CAUSES:
            self.assertIn(f'"{obs.BLOCKED_HE[cause][0]}"', js)
        node = shutil.which("node")
        if node:
            self.assertEqual(subprocess.run([node, "--check", str(STATIC / "blocked.js")], capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
