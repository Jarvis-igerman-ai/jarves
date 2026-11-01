"""Real loopback HTTP/auth/SSE and owner-wait integration, no external services."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from jarves.workflow.server import Server


class WorkflowHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = Server(Path(self.temp.name), 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = ""

    def tearDown(self):
        self.doCleanups()
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, data=None, headers=None):
        h = {"Cookie": self.cookie, "Origin": self.server.origin}
        if data is not None:
            h.update({"Content-Type": "application/json", "X-Workflow-Request": "1"})
        h.update(headers or {})
        req = urllib.request.Request(self.server.origin + path,
                                     data=json.dumps(data).encode() if data is not None else None, headers=h)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            return response.code, dict(response.headers), json.loads(raw) if "json" in response.headers.get("Content-Type", "") else raw

    def login(self):
        status, headers, data = self.request("/api/session", {"token": self.server.token})
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]

    def test_private_state_requires_private_session(self):
        self.assertEqual(self.request("/api/state")[0], 401)
        self.assertEqual(self.request("/api/session", {"token": "wrong"})[0], 401)
        self.login()
        status, _, data = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(data["agents"], [])
        self.assertEqual(data["autopilot"]["work"], [])
        self.assertFalse(data["autopilot"]["policy"]["enabled"])
        self.assertNotIn(self.server.token, json.dumps(data))

    def test_origin_host_and_request_marker_fail_closed(self):
        self.login()
        self.assertEqual(self.request("/api/owner", {"mode": "present"}, {"Origin": "https://attacker.invalid"})[0], 403)
        self.assertEqual(self.request("/api/owner", {"mode": "present"}, {"Host": "attacker.invalid"})[0], 403)
        self.assertEqual(self.request("/api/owner", {"mode": "present"}, {"X-Workflow-Request": ""})[0], 403)
        self.assertEqual(self.server.store.snapshot()["owner"]["effective_mode"], "away")

    def test_static_csp_and_no_path_traversal(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'lang="he"', body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.login()
        self.assertEqual(self.request("/../access.token")[0], 404)
        self.assertEqual(self.request("/api/state")[1]["Cache-Control"], "no-store")

    def test_unanswered_work_is_saved_while_worker_claims_independent_task(self):
        store = self.server.store
        for ident in ("needs-owner", "independent"):
            store.create_task({"id": ident, "title": ident, "lane": "bugs"})
        store.register_agent({"id": "bugs", "lane": "bugs"})
        store.claim("bugs", "needs-owner")
        q = store.ask({"task_id": "needs-owner", "title": "Which behavior?", "checkpoint": "commit A"})
        self.assertEqual(store.claim("bugs")["id"], "independent")
        self.login()
        self.assertEqual(self.request(f"/api/questions/{q['id']}/defer", {})[0], 200)
        waiting = next(t for t in store.snapshot()["tasks"] if t["id"] == "needs-owner")
        self.assertEqual(waiting["state"], "waiting_owner")
        self.assertEqual(self.request(f"/api/questions/{q['id']}/answer", {"answer": "Keep existing behavior"})[0], 200)
        state = self.request("/api/state")[2]
        resumed = next(t for t in state["tasks"] if t["id"] == "needs-owner")
        self.assertEqual(resumed["state"], "queued")
        self.assertEqual(state["questions"][0]["answer"], "Keep existing behavior")
        self.assertEqual(resumed["validation"], "not_run")

    def test_dispatch_uses_existing_gated_dispatcher_not_fake_heartbeat(self):
        self.login()
        with patch("jarves.workflow.control.dispatch", return_value=[{"status": "submitted"}]) as called:
            status, _, result = self.request("/api/dispatch", {})
            self.assertEqual(status, 200)
            called.assert_called_once()
            self.assertEqual(result["result"]["assignments"][0]["status"], "submitted")
        self.assertEqual(self.server.store.snapshot()["supervisor"]["status"], "offline")

    def test_web_cannot_authorize_imported_scope_or_recover_unknown_writer(self):
        self.server.store.create_task({"id": "scope", "title": "Imported", "execution_allowed": False})
        self.login()
        self.assertEqual(self.request("/api/tasks/scope/transition", {"execution_allowed": True})[0], 400)
        self.assertEqual(self.request("/api/tasks/scope/transition", {"state": "done", "delivery": "beta"})[0], 400)
        self.assertFalse(self.server.store.snapshot()["tasks"][0]["execution_allowed"])

    def test_sse_initial_revision_and_event_type(self):
        self.login()
        req = urllib.request.Request(self.server.origin + "/api/events", headers={"Cookie": self.cookie})
        with urllib.request.urlopen(req, timeout=5) as response:
            self.assertEqual(response.headers["Content-Type"], "text/event-stream; charset=utf-8")
            self.assertEqual(response.readline().strip(), b"event: change")
            self.assertTrue(response.readline().startswith(b"data: "))

    def test_action_history_and_retro_keep_existing_auth_and_csrf_boundaries(self):
        from jarves.workflow.trace import record
        self.assertEqual(self.request("/api/trace")[0], 401)
        self.login()
        actor = self.server.store.register_agent({"id": "worker"})
        record(self.server.store, actor, "PreToolUse", tool="Read", call_id="real-call")
        status, _, data = self.request("/api/trace?agent_id=worker")
        self.assertEqual(status, 200)
        self.assertEqual(data["events"][0]["call_id"], "real-call")
        self.assertEqual(self.request("/api/trace?since=nan")[0], 400)
        self.assertEqual(self.request("/api/trace/settings", {"mode": "details"}, {"X-Workflow-Request": ""})[0], 403)
        self.assertEqual(self.request("/api/trace/settings", {"mode": "details"})[0], 200)
        status, _, result = self.request("/api/trace/retro", {"agent_id": "worker"})
        self.assertEqual(status, 200)
        self.assertFalse(result["result"]["executed"])
        self.assertEqual(self.request("/api/trace/retro", {"output": "../../public.json"})[0], 400)

    def test_state_hides_archived_agents_but_counts_them(self):
        # #730: offline past the retention window and test leftovers leave the console list.
        from jarves.workflow.store import Store
        now = [1000.0]
        clocked = Store(Path(self.temp.name) / "workflow.db", clock=lambda: now[0])
        original = self.server.store
        self.addCleanup(lambda: (setattr(self.server, "store", original), clocked.close()))
        for ident in ("old-1", "old-2", "e2e-leftover", "mgr-restart-test-3", "recent", "live"):
            clocked.register_agent({"id": ident, "label": ident, "lane": "features"})
        for ident in ("old-1", "old-2", "recent", "e2e-leftover"):
            clocked.observe_agent(ident, "offline")
        now[0] += 86400 - 60
        clocked.observe_agent("recent", "offline")
        clocked.observe_agent("live", "running")
        now[0] += 120   # old-* are now past 24 h; recent is 60 s offline
        self.server.store = clocked
        self.login()
        state = self.request("/api/state")[2]
        self.assertEqual(sorted(a["id"] for a in state["agents"]), ["live", "recent"])
        self.assertEqual(state["agent_counts"], {"live": 1, "offline": 1, "archived": 4, "total": 6, "retention_s": 86400})
        (Path(self.temp.name) / "autopilot.json").write_text(json.dumps(
            {**__import__("jarves.workflow.autopilot", fromlist=["x"]).DEFAULTS,
             "watchdog": {"idle_s": 300, "stale_s": 120, "stuck_s": 1800, "max_restarts_per_task": 1,
                          "agent_retention_s": 30 * 86400}}), encoding="utf-8")
        state = self.request("/api/state")[2]
        self.assertEqual(sorted(a["id"] for a in state["agents"]), ["live", "old-1", "old-2", "recent"])
        self.assertEqual(state["agent_counts"]["archived"], 2)

    def test_console_session_ended_notice_static_contract(self):
        # #741: any 401/403 stops polling, drops stale data and shows one Hebrew notice with a copy button, never zeros.
        static = Path(__import__("jarves.workflow.server", fromlist=["x"]).STATIC)
        app = (static / "app.js").read_text(encoding="utf-8")
        css = (static / "style.css").read_text(encoding="utf-8")
        api = app[app.index("async function api("):app.index("function syncControls")]
        self.assertIn("response.status === 401 || response.status === 403", api)
        self.assertIn("sessionEnded();", api)
        body = app[app.index("function sessionEnded()"):app.index("async function api(")]
        for needle in ("data = null", "stream?.close()", 'id="session-ended-copy"', "navigator.clipboard.writeText(OPEN_COMMAND)", 'dir="auto"', "הסשן הפרטי הסתיים"):
            self.assertIn(needle, body)
        self.assertIn('const OPEN_COMMAND = "jarvis workflow open"', app)
        self.assertIn("if (ended) return;", app[app.index("async function refresh()"):app.index("function renderLiveStatus")])
        self.assertIn("!document.hidden && !ended", app)
        self.assertIn(".session-ended", css)
        self.assertNotIn("style=", body)  # strict CSP: no inline styles

    def test_console_agent_list_ui_static_contract(self):
        # #730: the toggle is keyboard reachable, remembered with guarded storage and not an aria-live region.
        static = Path(__import__("jarves.workflow.server", fromlist=["x"]).STATIC)
        app = (static / "app.js").read_text(encoding="utf-8")
        html = (static / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="agents-offline-wrap"', html)
        self.assertIn('aria-expanded="${showOffline}"', app)
        self.assertIn('aria-controls="agents-offline"', app)
        self.assertEqual(sum(1 for l in app.splitlines() if "localStorage." in l and "OFFLINE_KEY" in l), 2)  # drafts (#731) use storage too; all guarded below
        for line in app.splitlines():
            if "localStorage." in line:
                self.assertIn("try {", line)
        self.assertNotIn("aria-live", html[html.index("agents-offline-wrap") - 200:html.index("agents-offline-wrap") + 100])
        import shutil
        import subprocess
        node = shutil.which("node")
        if node:
            self.assertEqual(subprocess.run([node, "--check", str(static / "app.js")], capture_output=True).returncode, 0)
            # #730 owner order in the console: 5 running + 160 queued -> the list begins with the 5 running.
            start = app.index("  const stateRank =")
            block = app[app.index("  const versions ="):app.index("\n", app.index("  const versions ="))] + app[start:app.index("\n  }\n", start) + 4]
            script = block + """
const tasks = [];
for (let i = 0; i < 160; i++) tasks.push({id: "q" + String(i).padStart(3, "0"), state: "queued", priority: i % 4, version: ["V7", "V6", "V5", "V4.5.1"][Math.floor(i / 4) % 4]});
for (let i = 0; i < 5; i++) tasks.push({id: "r" + i, state: "running", priority: 3, version: "V7"});
tasks.push({id: "d", state: "done", priority: 0}, {id: "w", state: "waiting_owner", priority: 3}, {id: "b", state: "blocked"}, {id: "v", state: "review"});
tasks.sort(taskOrder);
console.log(JSON.stringify(tasks.slice(0, 9).map(t => t.id).concat([tasks[9].version, tasks[tasks.length - 1].id])));
"""
            out = subprocess.run([node, "-e", script], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(json.loads(out.stdout), ["r0", "r1", "r2", "r3", "r4", "w", "b", "v", "q012", "V4.5.1", "d"])

    def test_metrics_count_truth_contract(self):
        # #732 S1: running+claimed live workers, running and review separate, merged/verified/shipped split, no bare alerts number.
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        app = (Path(__import__("jarves.workflow.server", fromlist=["x"]).STATIC) / "app.js").read_text(encoding="utf-8")

        def cut(start, end):
            i = app.index(start)
            return app[i:app.index(end, i)]
        nl = chr(10)
        code = (cut("  const esc =", nl) + nl + cut("  const validationRank", nl) + nl + cut("  function complete(task)", "  // What is working now")
                + cut("  function agentStale", "  function renderMetrics") + cut("  function renderMetrics", "  // #730: live workers"))
        script = """
const $ = sel => ({set textContent(v) { out.q = v; }, set innerHTML(v) { out.html = v; }}); const out = {};
const seconds = v => v || 0;
let data;
""" + code + """
const now = Date.now() / 1000;
const ok = {task_id: null, heartbeat_at: now};
data = {
  agents: [{id: "a1", status: "running", heartbeat_at: now}, {id: "a2", status: "claimed", heartbeat_at: now}, {id: "a3", status: "running", heartbeat_at: now - 9999}, {id: "h1", status: "idle"}, {id: "a4", status: "offline"}],
  agent_counts: {live: 3},
  tasks: [{state: "running", agent_id: "h1"}, {state: "running", agent_id: "h1"}, {state: "review"}, {state: "review"}, {state: "review"}, {state: "verified", implementation: "merged"}, {state: "queued", implementation: "merged"}, {state: "queued"}],
  questions: [{status: "pending"}, {status: "answered"}, {status: "deferred"}],
  alerts: [],
};
renderMetrics();
const calm = out.html;
data.alerts = [{summary: "x"}, {summary: "y"}];
renderMetrics();
console.log(JSON.stringify({q: out.q, calm, noisy: out.html}));
"""
        res = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(res.returncode, 0, res.stderr)
        got = json.loads(res.stdout)
        self.assertEqual(got["q"], 2)
        html = got["calm"]
        self.assertIn('<div class="metric-value">3<small dir="auto">3 ', html)        # distinct task holders (h1) + live running/claimed agents without a task; stale excluded
        self.assertIn('<div class="metric-value">2<small dir="auto">3 ', html)        # 2 running tasks, 3 in review: not added together
        self.assertIn("מוזגו 2 · אומתו 1 · נשלחו 0", html)
        self.assertEqual(html.count("<progress"), 3)
        self.assertIn("הכול תקין", html)
        self.assertIn("2 בעיות פתוחות", got["noisy"])
        self.assertNotIn("alerts", html.lower())
    def test_today_first_screen_static_contract(self):
        # #732: Today is its own CSP-safe script, served by the static route, read-only, accessible.
        import re
        import shutil
        import subprocess
        static = Path(__import__("jarves.workflow.server", fromlist=["x"]).STATIC)
        html = (static / "index.html").read_text(encoding="utf-8")
        today = (static / "today.js").read_text(encoding="utf-8")
        app = (static / "app.js").read_text(encoding="utf-8")
        status, headers, body = self.request("/today.js")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/javascript"))
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertLess(html.index('src="/app.js"'), html.index('src="/today.js"'))
        self.assertEqual(html.count("<h1"), 1)
        self.assertNotIn('id="now-strip"', html)  # the Now strip is merged into "Running now"
        self.assertIn('<div id="today-needs" aria-live="polite">', html)
        self.assertEqual(len(re.findall(r'<section class="today-block[^"]*" aria-labelledby="today-[a-z]+-title">', html)), 4)
        self.assertIn('dispatchEvent(new CustomEvent("workflow:render"', app)
        # Strict CSP: no inline styles or handlers; read-only: no requests, no answer path.
        for banned in ("style=", "onclick", "fetch(", "/api/", "XMLHttpRequest", "localStorage"):
            self.assertNotIn(banned, today)
        self.assertIn('dir="auto">${esc(q.title)}', today)
        self.assertIn("שום דבר לא מחכה לך", today)
        node = shutil.which("node")
        if node:
            self.assertEqual(subprocess.run([node, "--check", str(static / "today.js")], capture_output=True).returncode, 0)
            start = today.index("  function blockedReason")
            script = today[today.index("  const unblockers"):today.index("\n", today.index("  const unblockers"))] \
                + today[start:today.index("\n  }\n", start) + 4] + """
const cases = [{blocker: "needs the phone and the car for a device check"}, {blocker: "CI check test_x fails on main"},
  {blocker: "autopilot: stalled twice, owner decision needed"}, {blocker: "worker_offline"}, {blocker: "something else"},
  {blocker: "", required_validation: "device"}];
console.log(JSON.stringify(cases.map(t => { const r = blockedReason(t); return [r.who, Boolean(r.reason), r.raw]; })));
"""
            out = subprocess.run([node, "-e", script], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(json.loads(out.stdout), [["hardware", False, "needs the phone and the car for a device check"],
                                                      ["ci", False, "CI check test_x fails on main"], ["you", True, ""],
                                                      ["team", True, ""], ["unknown", False, "something else"], ["hardware", False, ""]])

    def test_health_and_timeline_views_static_contract(self):
        # Health ("watch the watcher") and Timeline: separate CSP-safe read-only scripts, both locales, escaped text.
        import re
        import shutil
        import subprocess
        static = Path(__import__("jarves.workflow.server", fromlist=["x"]).STATIC)
        html = (static / "index.html").read_text(encoding="utf-8")
        app = (static / "app.js").read_text(encoding="utf-8")
        css = (static / "style.css").read_text(encoding="utf-8")
        for name in ("health.js", "timeline.js"):
            status, headers, _ = self.request("/" + name)
            self.assertEqual(status, 200, name)
            self.assertTrue(headers["Content-Type"].startswith("text/javascript"))
            self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
            self.assertLess(html.index('src="/app.js"'), html.index(f'src="/{name}"'))
        self.assertNotIn("<script>", html)
        for view, label in (("health", "בריאות"), ("timeline", "ציר זמן")):
            self.assertIn(f'<a href="#{view}" data-view="{view}" class="nav-link">', html)
            self.assertIn(f'<section id="view-{view}" class="view" hidden aria-label="{label}"><div id="{view}-root"></div></section>', html)
            self.assertIn(f'views.{view} = ["{label}"', app)
        health = (static / "health.js").read_text(encoding="utf-8")
        timeline = (static / "timeline.js").read_text(encoding="utf-8")
        for script in (health, timeline):
            # Strict CSP and read-only: no inline styles/handlers, no writes, no storage, no third-party code.
            for banned in ("style=", "onclick", "method:", "POST", "localStorage", "http://", "https://", "eval(", "X-Workflow-Request"):
                self.assertNotIn(banned, script)
            self.assertIn('timeZone: TZ', script)
            self.assertIn('const TZ = "Asia/Jerusalem";', script)
            self.assertIn('new URLSearchParams(location.search).get("demo") === "1"', script)
            self.assertRegex(script, r"response\.status [!=]== 404")  # no backend yet: "no data", not an error
            block = script[script.index("const I18N = {"):script.index("const lang =")]
            he, en = block[block.index("he: {"):block.index("en: {")], block[block.index("en: {"):]
            keys = lambda part: set(re.findall(r"(?<![^\s{,])([A-Za-z]+): ", part)) - {"he", "en"}
            self.assertEqual(keys(he), keys(en))
            for text in re.findall(r'"([^"]*)"|`([^`]*)`', he):
                words = " ".join(text)
                self.assertIsNone(re.search(r"\b[A-Za-z]+ [A-Za-z]+ [A-Za-z]+\b", words), words)
            for layer in ("מפקח", "טייס אוטומטי", "עוטף claude-j", "מנהל", "ליבה", "גשר סוכנים",
                          "Supervisor", "Autopilot", "claude-j wrapper", "Manager", "Core", "Agent bridge"):
                self.assertIn(f'"{layer}"', script)
        self.assertIn("/api/health", health)
        self.assertIn("/api/timeline?hours=${hours}", timeline)
        self.assertIn('stopped: (at, dur) => `המפעל עצר מ־${at} — ${dur}.`, ready: n => `עבודה ממתינה: ${n}`', health)
        # Every server-provided string goes through esc(); free text keeps its own direction.
        self.assertIn('<span class="hl-note" dir="auto">${esc(l.note)}</span>', health)
        self.assertIn('<span class="hl-incident-summary" dir="auto">${esc(i.summary || i.key)}</span>', health)
        self.assertIn('data-task="${esc(i.task_id)}"', health)
        self.assertIn('<span class="tl-move-title" dir="auto">${esc(p.title || p.task_id)}</span>', timeline)
        self.assertIn('aria-label="${esc(text)}"', timeline)
        self.assertIn('fill="url(#tl-hatch)"', timeline)
        self.assertNotIn('data-filter=', timeline)  # app.js owns [data-filter] (roadmap)
        for marker in ('data-tl-range="${h}" aria-pressed=', 'data-tl-lane="${esc(value)}" aria-pressed=', 'role="tooltip"', 'id="tl-copy"'):
            self.assertIn(marker, timeline)
        self.assertIn("@media(prefers-reduced-motion:reduce){.hl-banner-dot,.hl-dead .hl-dot{animation:none}}", css)
        node = shutil.which("node")
        if node:
            for name in ("health.js", "timeline.js"):
                self.assertEqual(subprocess.run([node, "--check", str(static / name)], capture_output=True).returncode, 0, name)
            # Copy-as-text: the CLI layout (English, Israel time, newest pipeline first).
            start = timeline.index("  const stamp = epoch")
            script = "\n".join(timeline[timeline.index(f"  const {n}"):timeline.index("\n", timeline.index(f"  const {n}"))]
                               for n in ("TZ", "LAYERS", "num", "list")) \
                + timeline[start:timeline.index("\n  }\n", timeline.index("function timelineText", start)) + 4] + """
const now = Date.UTC(2026, 10, 1, 18, 43) / 1000, stop = now - 13 * 3600;
const data = {segments: [{layer: "autopilot", state: "dead", start: stop, end: now}, {layer: "supervisor", state: "ok", start: now - 30 * 3600, end: now}],
  marks: [{ts: stop, kind: "dead", layer: "autopilot", summary: "heartbeat lost"}],
  gaps: [{layer: "autopilot", start: stop, end: now, seconds: 13 * 3600, ready_work: 41}],
  pipeline: [{ts: now - 600, task_id: "T-2", title: "B", lane: "bugs", from_state: "review", to_state: "merged", agent: "w"},
             {ts: now - 1200, task_id: "T-1", title: "A", lane: "ops", from_state: null, to_state: "queued", agent: ""}]};
console.log(timelineText(data, 24, now));
"""
            out = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.strip().splitlines(), [
                "JARVES timeline: last 24h, Asia/Jerusalem, generated 2026-11-01 20:43", "", "STOPS",
                "  2026-11-01 07:43 -> 2026-11-01 20:43  autopilot   stopped 13.0h  ready_work=41", "", "LAYERS",
                "  2026-10-31 20:43 -> 2026-11-01 20:43  supervisor  ok",
                "  2026-11-01 07:43 -> 2026-11-01 20:43  autopilot   dead", "", "MARKS",
                "  2026-11-01 07:43  autopilot   dead: heartbeat lost", "", "PIPELINE (newest first)",
                "  2026-11-01 20:33  T-2  review -> merged  [bugs]  w  B",
                "  2026-11-01 20:23  T-1  - -> queued  [ops]  -  A"])


if __name__ == "__main__":
    unittest.main()
