"""SW-V4 O (#17), owner addition: the scheduled, SELECTIVE AI review. Real store.db / memory.db in tmp dirs, the
replay harness, a fake OpenAI client (captures the serialized request), fixed clocks.

- planted anomalies are selected: precision and recall 100% on the fixture
- boring 2-day windows are skipped at $0 (the client is never called)
- planted secrets / personal data never reach the reviewer (searched in several encodings); with the owner's
  AI-review path on, only the flagged conversation's excerpt appears, still without secrets
- the per-run and monthly caps are never exceeded (randomised property test with a fixed seed)
- nothing is applied without an approval (accepting a settings item only creates a proposal)
"""
from __future__ import annotations

import base64
import json
import random
import urllib.parse
from types import SimpleNamespace

import pytest

import diag_replay as rp
from jarves.config import Config, ModelPrice
from jarves.diag import ai_review, db
from jarves.diag.ai_review import SliceItem
from jarves.store import Store
from jarves.userdata import open_db


class FakeClient:
    """Stands in for openai.OpenAI(): records each request; usage stays within the API's own bounds."""

    def __init__(self, items=None, usage=None):
        self.calls: list[dict] = []
        self.items = items or []
        self.usage = usage
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        n_in = self.usage[0] if self.usage else len(kw["input"]) // 4
        n_out = self.usage[1] if self.usage else 300
        return SimpleNamespace(model=kw["model"], output_text=json.dumps({"items": self.items}),
                               usage=SimpleNamespace(input_tokens=n_in, output_tokens=n_out,
                                                     input_tokens_details=SimpleNamespace(cached_tokens=0)))


class Budget:
    def __init__(self):
        self.reserved = 0

    def reserve(self, kind="ai_requests", n=1):
        self.reserved += n
        return _Res()


class _Res:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None


@pytest.fixture
def env(home):
    st = Store(home / "store.db")
    dbs = open_db(home)
    yield SimpleNamespace(home=home, store=st, dbs=dbs, cfg=Config())
    dbs.close()
    st.close()


def slice_ids(env, now=rp.NOW, since=rp.NOW - 2 * 86400):
    return [i.id for i in ai_review.select(env.store, home=env.home, cfg=env.cfg, since=since, now=now)]


# ---------------------------------------------------------------------- selection
def test_planted_anomalies_are_selected_precision_and_recall_100(env):
    rp.build(env.store, plant=rp.ALL)
    got = set(slice_ids(env))
    want = rp.expected_slice(rp.ALL)
    allowed = set().union(*want.values())
    assert got <= allowed, f"precision: unexpected slice items {sorted(got - allowed)}"
    missing = {p: sorted(fps - got) for p, fps in want.items() if not fps <= got}
    assert not missing, f"recall: {missing}"


def test_boring_two_day_windows_are_skipped_at_zero_cost(env):
    rp.build(env.store, days=46, plant=())
    client = FakeClient()
    for w in range(15):                                         # 15 boring 2-day windows of the 30-day replay
        now = rp.NOW - w * 2 * 86400
        env.dbs.set_setting("health.ai_review_last", now - 2 * 86400, actor="test")
        v = ai_review.run(env.store, home=env.home, cfg=env.cfg, dbs=env.dbs, client=client, budget=Budget(),
                          now=now, trigger="schedule")
        assert v["status"] == "skipped" and v["reason"] == "nothing new" and v["cost_usd"] == 0.0, v
    assert client.calls == []                                   # nothing was sent, nothing billed
    assert ai_review.month_spent(env.store, rp.NOW) == 0.0


def test_not_due_and_off(env):
    rp.build(env.store, plant=rp.ALL)
    env.dbs.set_setting("health.ai_review_last", rp.NOW - 3600, actor="test")
    assert ai_review.run(env.store, home=env.home, cfg=env.cfg, dbs=env.dbs, client=FakeClient(), now=rp.NOW)["status"] \
        == "not_due"
    env.dbs.set_setting("health.ai_review_enabled", False, actor="test")
    env.dbs.set_setting("health.ai_review_last", 0.0, actor="test")
    v = ai_review.run(env.store, home=env.home, cfg=env.cfg, dbs=env.dbs, client=FakeClient(), now=rp.NOW)
    assert v["status"] == "not_due"                              # off = never due
    v = ai_review.run(env.store, home=env.home, cfg=env.cfg, dbs=env.dbs, client=FakeClient(), now=rp.NOW, force=True)
    assert v["status"] == "skipped" and v["reason"].startswith("off")


def test_selected_run_shows_cost_before_and_after_and_does_not_reraise(env):
    rp.build(env.store, plant=rp.ALL)
    env.dbs.set_setting("health.ai_review_last", rp.NOW - 2 * 86400, actor="test")
    pv = ai_review.preview(env.store, home=env.home, cfg=env.cfg, dbs=env.dbs, now=rp.NOW)
    assert not pv["would_skip"] and pv["est_usd"] > 0 and pv["cap_usd"] == pytest.approx(0.10)
    client = FakeClient(items=[{"ref": "trend:ram_mb", "severity": "medium", "title": "memory leak in the store "
                                "writer", "evidence": "ram +2 MB/h", "hypothesis": "queue grows", "fix": "bound it",
                                "effort": "hour", "action": None}])
    v = ai_review.run(env.store, home=env.home, cfg=env.cfg, dbs=env.dbs, client=client, budget=Budget(), now=rp.NOW)
    assert v["status"] == "done" and 0 < v["cost_usd"] <= v["est_usd"] <= 0.10
    assert v["model"] == "gpt-6.1-sol" and client.calls[0]["model"] == "gpt-6.1-sol"
    assert v["ready"] and v["items_list"][0]["title"].startswith("memory leak")
    en, he = ai_review.summary(v)
    assert "1 item" in en and "Nothing was changed" in en and "הבודק מצא 1" in he
    # the next run (2 days later, nothing new): the reviewed items are not raised again -> skipped, $0
    later = rp.NOW + 60
    env.dbs.set_setting("health.ai_review_last", rp.NOW, actor="test")
    v2 = ai_review.run(env.store, home=env.home, cfg=env.cfg, dbs=env.dbs, client=client, budget=Budget(), now=later,
                       force=True)
    assert v2["status"] == "skipped" and v2["reason"] == "nothing new" and len(client.calls) == 1


# ---------------------------------------------------------------------- the boundary
SECRETS = {
    "openai_key": "sk-proj-PLANTEDabcdefghijklmnopqrstuv0123456789",
    "google_refresh": "1//0gPLANTEDREFRESHTOKENabcdefghijklmnopqrstuvwxyz0123",
    "whatsapp_body": "PLANTED whatsapp body: meet me at the cafe",
    "contact_name": "Yossi Plantedman",
    "contact_number": "+972-54-123-4567",
    "calendar_title": "Dentist PLANTED appointment",
    "recalled": "PLANTED recalled sentence about the trip",
}
HEBREW = "שלום זה משפט שתול מהשיחה"


def encodings(s: str) -> list[str]:
    return [s, json.dumps(s)[1:-1], json.dumps(s, ensure_ascii=False)[1:-1], urllib.parse.quote(s),
            urllib.parse.quote_plus(s), base64.b64encode(s.encode()).decode(), s.lower(),
            s.encode("utf-16-le").decode("latin-1")]


def plant_sources(env):
    """Planted data in every place the selection reads: log lines around the codes, config drift, a flagged
    conversation in store.db (with the Hebrew sentence and a key in its text), the Google token file."""
    import datetime as dt
    logs_dir = env.home / "logs"
    logs_dir.mkdir()
    lines = []
    for k, t in enumerate((rp.NOW - 19 * 3600, rp.NOW - 18 * 3600, rp.NOW - 17 * 3600)):
        ts = dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()
        lines.append({"ts": ts, "level": "INFO", "component": "whatsapp", "code": "WA_MESSAGE",
                      "msg": f"message from {SECRETS['contact_name']} {SECRETS['contact_number']}: {SECRETS['whatsapp_body']}",
                      "body": SECRETS["whatsapp_body"], "name": SECRETS["contact_name"], "phone": SECRETS["contact_number"],
                      "session": "s1"})
        lines.append({"ts": ts, "level": "ERROR", "component": "test", "code": "PLANTED_NEW_ERROR",
                      "msg": f"failed with key {SECRETS['openai_key']} for {SECRETS['calendar_title']} {HEBREW}",
                      "title": SECRETS["calendar_title"], "refresh_token": SECRETS["google_refresh"],
                      "text": SECRETS["recalled"], "ms": 12 + k, "ok": False, "session": "s1"})
    (logs_dir / "jarves.log").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
                                         encoding="utf-8")
    db.write(env.store, "INSERT INTO diag_config (ts, digest, snapshot) VALUES (?,?,?)",
             (rp.NOW - 5 * 3600, "d2", db.dumps({"voice.wake_threshold": 0.6, "ai.voice_service_tier": "priority",
                                                 "voice.mode": "live", "identity.display_name": SECRETS["contact_name"]})))
    st = env.store
    st.x("INSERT INTO sessions (id, kind, started, ended) VALUES (?,?,?,?)", ("conv1", "live", rp.NOW - 7200, rp.NOW - 7000))
    for i, (role, text) in enumerate((("user", HEBREW), ("assistant", "בסדר"),
                                      ("user", f"המפתח שלי {SECRETS['openai_key']} ו-{SECRETS['google_refresh']}"),
                                      ("assistant", "אוקיי"))):
        st.x("INSERT INTO turns (session_id, ts, role, text, chars) VALUES (?,?,?,?,?)",
             ("conv1", rp.NOW - 7200 + i, role, text, len(text)))
    st.x("INSERT INTO issues (fingerprint, kind, severity, title, detail, first_seen, last_seen, count, last_session) "
         "VALUES (?,?,?,?,?,?,?,?,?)", ("dead:conv1", "dead_air", "medium", f"long silence after {HEBREW}",
                                         SECRETS["recalled"], rp.NOW - 7000, rp.NOW - 7000, 1, "conv1"))
    st.x("INSERT INTO issue_hits (fingerprint, session_id) VALUES (?,?)", ("dead:conv1", "conv1"))
    st.commit()
    (env.home / "google").mkdir()
    (env.home / "google" / "token.json").write_text(json.dumps({"refresh_token": SECRETS["google_refresh"]}),
                                                     encoding="utf-8")


def run_capture(env, *, excerpts: bool) -> str:
    rp.build(env.store, plant=rp.ALL)
    plant_sources(env)
    if excerpts:
        env.dbs.set_setting("self_check.ai_pass", True, actor="owner")    # the owner's explicit choice
    env.dbs.set_setting("health.ai_review_last", rp.NOW - 2 * 86400, actor="test")
    env.dbs.set_setting("health.ai_review_max_items", 40, actor="test")
    client = FakeClient()
    v = ai_review.run(env.store, home=env.home, cfg=env.cfg, dbs=env.dbs, client=client, budget=Budget(), now=rp.NOW)
    assert v["status"] == "done", v
    kw = client.calls[0]
    return kw["instructions"] + "\n" + kw["input"]


def test_planted_secrets_and_personal_data_never_reach_the_reviewer(env):
    sent = run_capture(env, excerpts=False)
    for name, s in SECRETS.items():
        for enc in encodings(s):
            assert enc not in sent, (name, enc)
    for enc in encodings(HEBREW):
        assert enc not in sent, "conversation text"
    assert "PLANTED_NEW_ERROR" in sent and "flagged_conversation" in sent    # the metadata IS there
    assert "excerpt of flagged conversation" not in sent


def test_with_the_owner_ai_path_only_flagged_excerpts_appear_still_no_secrets(env):
    sent = run_capture(env, excerpts=True)
    assert "excerpt of flagged conversation issue:dead:conv1" in sent and HEBREW in sent
    for name in ("openai_key", "google_refresh", "whatsapp_body", "contact_number", "calendar_title"):
        for enc in encodings(SECRETS[name]):
            assert enc not in sent, (name, enc)


def test_privacy_mode_blocks_excerpts_even_when_enabled(env):
    env.cfg.log.privacy = True
    env.dbs.set_setting("self_check.ai_pass", True, actor="owner")
    assert not ai_review.excerpts_allowed(env.store, env.cfg, env.dbs)
    env.cfg.log.privacy = False
    assert ai_review.excerpts_allowed(env.store, env.cfg, env.dbs)


def test_default_setting_is_not_an_owner_choice(env):
    assert env.dbs.setting("self_check.ai_pass") is True                   # the V3 default
    assert not ai_review.excerpts_allowed(env.store, env.cfg, env.dbs)     # but nobody chose it: no excerpts


def test_never_shown_list_is_complete_and_bilingual():
    text = " ".join(ai_review.NEVER_SHOWN).lower()
    for word in ("secrets", "message bodies", "contact", "calendar", "recalled", "wake audio", "agents' projects",
                 "conversation text"):
        assert word in text
    assert len(ai_review.NEVER_SHOWN_HE) == len(ai_review.NEVER_SHOWN)


# ---------------------------------------------------------------------- the caps (property test, fixed seed)
def test_cost_caps_are_never_exceeded_property():
    rng = random.Random(20261020)
    for case in range(200):
        cfg = Config()
        model = rng.choice(["gpt-6.1-sol", "gpt-6-luna", "gpt-6-astra", "unknown-model"])
        if model != "unknown-model":
            cfg.pricing.models[model] = ModelPrice(input=rng.uniform(0.05, 20), cached_input=0.0,
                                                   output=rng.uniform(0.2, 80))
        items = [SliceItem(f"x{i}", "error_cluster", rng.choice(["low", "medium", "high"]), rng.choice([1, 3, 5]),
                           "t" * rng.randint(1, 150), {"evidence": ["e" * rng.randint(0, 400)] * rng.randint(0, 5)})
                 for i in range(rng.randint(0, 40))]
        run_cap, month_cap = rng.uniform(0, 0.3), rng.uniform(0, 2)
        spent = rng.uniform(0, month_cap * 1.2)
        p = ai_review.plan(items, cfg=cfg, model=model, run_cap=run_cap, month_cap=month_cap, spent=spent,
                           max_items=rng.randint(1, 40))
        cap = max(0.0, min(run_cap, month_cap - spent))
        assert p["est_usd"] <= cap + 1e-12, case
        if p["items"]:
            # whatever the API reports stays within its bounds (input tokens <= bytes, output <= max_output_tokens)
            usage = {"in": rng.randint(0, len(p["request"].encode())), "out": rng.randint(0, ai_review.MAX_OUTPUT_TOKENS),
                     "cached": 0}
            assert ai_review.actual_usd(usage, cfg, model) <= p["est_usd"] + 1e-12, case
            assert spent + ai_review.actual_usd(usage, cfg, model) <= month_cap + 1e-12, case


def test_monthly_cap_over_many_runs(env):
    """Back-to-back forced runs with worst-case usage: the month total never passes the monthly cap."""
    env.dbs.set_setting("health.ai_review_month_cap_usd", 0.05, actor="test")
    env.dbs.set_setting("health.ai_review_run_cap_usd", 0.03, actor="test")
    total = 0.0
    for k in range(6):
        db.write(env.store, "DELETE FROM diag_review_items")                 # let the same items count as new
        rp.build(env.store, plant=("false_action_claim", "ram_creep"), seed=k)
        env.dbs.set_setting("health.ai_review_last", rp.NOW - 2 * 86400, actor="test")

        class WorstCase(FakeClient):
            def _create(self, **kw):
                self.usage = (len(kw["input"].encode()), ai_review.MAX_OUTPUT_TOKENS)
                return super()._create(**kw)
        v = ai_review.run(env.store, home=env.home, cfg=env.cfg, dbs=env.dbs, client=WorstCase(), budget=Budget(),
                          now=rp.NOW + k, force=True)
        total = ai_review.month_spent(env.store, rp.NOW + k)
        assert total <= 0.05 + 1e-12 and v["cost_usd"] <= 0.03 + 1e-12, (k, v)
    assert total > 0


# ---------------------------------------------------------------------- nothing is applied automatically
def test_accepting_a_settings_item_only_proposes_never_applies(env):
    from jarves.approvals import read_history
    rp.build(env.store, plant=("wake_false_positive_burst",))
    env.dbs.set_setting("health.ai_review_last", rp.NOW - 2 * 86400, actor="test")
    env.dbs.set_setting("health.ai_review_min_score", 1.0, actor="test")
    item = {"ref": "anomaly:wake_false", "severity": "medium", "title": "raise the wake threshold",
            "evidence": "13 false wakes/day", "hypothesis": "TV", "fix": "voice.wake_threshold 0.6", "effort": "minutes",
            "action": {"type": "setting", "key": "voice.wake_threshold", "value": 0.6}}
    v = ai_review.run(env.store, home=env.home, cfg=env.cfg, dbs=env.dbs, client=FakeClient(items=[item]),
                      budget=Budget(), now=rp.NOW)
    it = v["items_list"][0]
    assert it["action"]["key"] == "voice.wake_threshold"
    before = env.cfg.voice.wake_threshold
    r = ai_review.decide(env.store, it["id"], "accept", by="test", home=env.home, cfg=env.cfg, dbs=env.dbs)
    assert r["state"] == "accepted" and r["needs_approval"] and r["applied"] is False
    p = env.dbs.row("SELECT * FROM proposals WHERE id=?", (r["proposal_id"],))
    assert p["status"] == "proposed"                                         # waits for the approvals flow
    assert env.cfg.voice.wake_threshold == before and not (env.home / "identity.json").exists()
    assert not [h for h in read_history(env.home) if h.get("decision") == "approved"]   # no approval, no change
    for op in ("dismiss", "snooze", "reopen"):
        assert ai_review.decide(env.store, it["id"], op, by="test")["state"] in ("dismissed", "snoozed", "open")
    stale = ai_review.run_view(env.store, v["id"], now=rp.NOW + 8 * 86400)
    assert stale["items_list"][0]["state"] == "stale"                        # undecided for 7 days
