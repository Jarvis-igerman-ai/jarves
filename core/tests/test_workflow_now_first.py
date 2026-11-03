"""#733/#732: the console shows what is running NOW first, a one-line count banner, and hides unknown states.

Hermetic: the static scripts are read from disk and small slices run under node with a fixed clock; no service."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from jarves.workflow.server import STATIC

NODE = shutil.which("node")


def _read(name):
    return (Path(STATIC) / name).read_text(encoding="utf-8")


def _cut(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i)]


def _node(script):
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


class NowFirstStaticTests(unittest.TestCase):
    def test_working_now_block_comes_first_and_banner_is_on_top(self):
        html = _read("index.html")
        self.assertLess(html.index('class="today-block today-running"'), html.index('class="today-block today-needs"'))
        self.assertLess(html.index('id="ov-banner"'), html.index('id="ov-status"'))
        css = _read("style.css")
        self.assertIn(".today-running{grid-column:1/-1}", css)
        self.assertIn(".now-strip{", css)
        # Tokens only, so light and dark both follow; no fixed colours in the new rules.
        rules = css[css.index("/* #733"):]
        self.assertNotRegex(rules[rules.index("*/"):], r"#[0-9a-fA-F]{3,6}\b")

    def test_hebrew_strings_have_no_three_word_english_runs(self):
        app, today, ov = _read("app.js"), _read("today.js"), _read("overview.js")
        he_parts = [_cut(app, '      nowTitle: "עובד עכשיו"', "    en: {"),
                    _cut(today, "    runFor: d => `רץ", "\n"),
                    _cut(ov, '    banner: {workers: n => n === 1 ? "עובד אחד רץ"', "    v: {")]
        for part in he_parts:
            for text in re.findall(r'"([^"]*)"|`([^`]*)`', re.sub(r"\$\{[^}]*\}", "", part)):
                self.assertIsNone(re.search(r"\b[A-Za-z]+ [A-Za-z]+ [A-Za-z]+\b", " ".join(text)), text)
        for key in ("nowTitle", "runningFor", "lastActivity", "noWorker", "hiddenStates"):
            self.assertEqual(app.count(f"{key}: "), 2, key)
        for key in ("runFor", "lastBeat", "noAgent"):
            self.assertEqual(today.count(f"{key}: "), 2, key)
        self.assertEqual(ov.count("banner: {workers:"), 2)

    @unittest.skipUnless(NODE, "node not available")
    def test_today_lists_every_running_task_stalled_and_orphaned_first(self):
        today = _read("today.js")
        code = _cut(today, "  const seconds =", "\n  // Clock time") + "\n" + _cut(today, "  const runMinutes", "\n  function running(data)")
        got = _node(code + """
const now = 1000000;
const data = {
  agents: [{id: "a", status: "running", task_id: "t1", heartbeat_at: now - 5, progress_at: now - 5},
           {id: "b", status: "running", task_id: "t2", heartbeat_at: now - 900, progress_at: now - 900},
           {id: "c", status: "offline", task_id: "t3", heartbeat_at: now - 50}],
  tasks: [{id: "t1", state: "running", agent_id: "a"}, {id: "t2", state: "running", agent_id: "b"},
          {id: "t3", state: "running", agent_id: "c"}, {id: "t4", state: "running"}, {id: "q", state: "queued"},
          {id: "x", state: "cancelled"}],
};
console.log(JSON.stringify(runningEntries(data, now).map(e => [e.task.id, e.fresh, e.agent ? e.agent.id : null])));
""")
        self.assertEqual(sorted(row[0] for row in got), ["t1", "t2", "t3", "t4"])  # no queued/cancelled; orphans kept
        self.assertEqual(got[-1], ["t1", True, "a"])                               # healthy after those needing attention
        self.assertEqual({row[0] for row in got if not row[1]}, {"t2", "t3", "t4"})

    @unittest.skipUnless(NODE, "node not available")
    def test_unknown_or_cancelled_states_are_hidden_and_order_is_running_first(self):
        app = _read("app.js")
        code = _cut(app, "  const versions =", "\n") + "\n" + _cut(app, "  const stateRank =", "\n  // #733: how long")
        got = _node(code + """
const tasks = [{id: "q", state: "queued"}, {id: "c", state: "cancelled"}, {id: "r", state: "running"}, {id: "z", state: "zombie"},
  {id: "v", state: "review"}, {id: "w", state: "waiting_owner"}, {id: "b", state: "blocked"}, {id: "d", state: "done"}];
console.log(JSON.stringify(tasks.filter(listed).sort(taskOrder).map(t => t.id)));
""")
        self.assertEqual(got, ["r", "w", "b", "v", "q", "d"])

    @unittest.skipUnless(NODE, "node not available")
    def test_banner_reuses_the_verdict_numbers(self):
        ov = _read("overview.js")
        code = "const EN = true;\n" + _cut(ov, "  const T = EN ?", "\n  const signatures") \
            + "\nlet remote = null, remoteAt = 0, remotePrs = null;\n" + _cut(ov, "  function bannerText(", "\n  function renderStatus")
        got = _node(code + """
const now = 5000;
const f = {active: 1, questions: 4};
const local = bannerText(f, {source: {}}, now);
const snap = bannerText(f, {source: {open_prs: [1, 2]}}, now);
remote = {detail: {active: 3, questions: 2}}; remoteAt = now - 10; remotePrs = 7;
const fresh = bannerText(f, {source: {open_prs: [1]}}, now);
remoteAt = now - 999;
const stale = bannerText(f, {source: {open_prs: [1]}}, now);
console.log(JSON.stringify({local, snap, fresh, stale}));
""")
        self.assertEqual(got["local"], "1 worker running · 4 need you")         # an unknown PR count is left out
        self.assertEqual(got["snap"], "1 worker running · 2 PRs waiting · 4 need you")
        self.assertEqual(got["fresh"], "3 workers running · 7 PRs waiting · 2 need you")  # the /api/health verdict wins
        self.assertEqual(got["stale"], "1 worker running · 1 PR waiting · 4 need you")


if __name__ == "__main__":
    unittest.main()
