"""#764: deterministic routing of red checks, conflicts and review comments to the owning agent (mocked GitHub data)."""
import json
import tempfile
import unittest
from pathlib import Path

from jarves.workflow import messages, pr_routing
from jarves.workflow.store import Store


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


class Gh:
    """Fake public GitHub reads; counts calls."""

    def __init__(self):
        self.runs, self.state, self.comments, self.calls = [], "clean", 0, 0

    def __call__(self, path):
        self.calls += 1
        if "check-runs" in path:
            return {"check_runs": self.runs}
        return {"mergeable_state": self.state, "review_comments": self.comments}


def run(name, conclusion="failure", status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion}


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.store = Store(Path(self.temp.name) / "w.db", clock=self.clock)
        for ident, label in (("w1", "Claude · workflow-bugs"), ("m1", "Claude · manager")):
            self.store.register_agent({"id": ident, "label": label, "lane": "any"})
        messages.claim(self.store, "w1", "pr:7", "my fix")
        self.gh = Gh()
        self.prs = [{"number": 7, "head_sha": "abc123", "head_ref": "fix-7"}, {"number": 8, "head_sha": "def", "head_ref": "x"}]

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def route(self):
        return pr_routing.route(self.store, self.prs, self.gh)

    def inbox(self, who):
        return messages.inbox(self.store, who)["messages"]

    def test_red_check_names_only_to_owner_deduped(self):
        self.gh.runs = [run("tests (3.12)"), run("lint", "success"), run("docs", status="in_progress")]
        self.assertEqual(self.route(), [{"pr": 7, "kind": "ci_red", "to": "w1"}])
        msg = self.inbox("w1")[0]
        self.assertIn("tests (3.12)", msg["body"])
        self.assertNotIn("lint", msg["body"])
        self.assertEqual(msg["resource"], "pr:7")
        self.assertEqual(self.route(), [])                      # same head and checks: deduplicated
        self.assertEqual(self.inbox("m1"), [])                  # owner is alive: manager not bothered
        self.assertEqual(self.gh.calls, 4)                      # only the claimed PR (#7) was read, twice per pass

    def test_rate_limit_then_new_failure_after_gap(self):
        self.gh.runs = [run("a")]
        self.route()
        self.prs[0]["head_sha"] = "newsha"
        self.gh.runs = [run("b")]
        self.assertEqual(self.route(), [])                      # new head, but inside the gap
        self.clock.now += pr_routing.MIN_GAP_S + 1
        self.assertEqual(len(self.route()), 1)

    def test_conflict_and_review_comments(self):
        self.assertEqual(self.route(), [])                      # first look only records the comment count
        self.gh.state, self.gh.comments = "dirty", 2
        kinds = sorted(e["kind"] for e in self.route())
        self.assertEqual(kinds, ["conflict", "review"])
        self.gh.comments = 3
        self.clock.now += pr_routing.MIN_GAP_S + 1
        self.assertEqual([e["kind"] for e in self.route()], ["review"])   # conflict already sent for this head

    def test_branch_claim_and_manager_when_owner_gone(self):
        messages.release(self.store, "w1", "pr:7")
        messages.claim(self.store, "w1", "branch:fix-7", "branch")
        with self.store._tx() as db:
            agent = self.store._get(db, "agent", "w1")
            agent["status"] = "offline"
            self.store._put(db, "agent", agent)
        self.gh.runs = [run("tests")]
        self.assertEqual({e["to"] for e in self.route()}, {"w1", "manager"})
        self.assertEqual(len(self.inbox("m1")), 1)

    def test_stale_claim_goes_to_manager_and_outage_is_silent(self):
        self.clock.now += messages.CLAIM_TTL_S + 5
        self.gh.runs = [run("tests")]
        self.assertIn("manager", {e["to"] for e in self.route()})
        self.prs[0]["head_sha"] = "other"

        def boom(path):
            raise OSError("rate limit")
        self.assertEqual(pr_routing.route(self.store, self.prs, boom), [])

    def test_no_claims_no_reads_and_no_owner_question_power(self):
        messages.release(self.store, "w1", "pr:7")
        self.assertEqual(self.route(), [])
        self.assertEqual(self.gh.calls, 0)
        self.assertEqual(json.loads(json.dumps(self.store.snapshot()["questions"])), [])


if __name__ == "__main__":
    unittest.main()
