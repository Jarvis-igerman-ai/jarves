"""SW-V4 G (#17) recall benchmark: the 40-question set (30 Hebrew, 10 English) over planted conversations plus
distractors, through the real Store.ingest and the real FTS5 index (tests/recall_corpus.py, deterministic, offline).
Targets: Recall@3 >= 36/40 and the right day in the spoken answer >= 34/40; search p95 <= 150 ms with 5000
conversations (10 000 turns); build time and index size reported. `python tools/recall_quality.py` prints the
same score with every answer."""
from __future__ import annotations

import time

import recall_corpus as rc
from jarves import recall
from jarves import recall_text as rt


def test_the_question_set_is_40_questions_30_hebrew_10_english():
    keys = [k for _q, k in rc.QUESTIONS]
    assert len(rc.QUESTIONS) == 40 and len(set(keys)) == 40 and set(keys) == {c.key for c in rc.PLANTED}
    assert sum(bool(rt.HEBREW.search(q)) for q, _ in rc.QUESTIONS) == 30
    assert {c.kind for c in rc.PLANTED} == {"voice", "typed"}
    convs = rc.conversations(400)
    assert len(convs) == 440 and rc.conversations(400)[300].turns == convs[300].turns      # deterministic


def test_recall_quality_meets_the_target(tmp_path):
    st, ids, convs = rc.build(tmp_path, 400)
    try:
        rec = recall.Recall(tmp_path, store=st, enabled=lambda: True)
        build = rec.rebuild()
        s = rc.score(rec, ids)
        print(f"\nrecall@3 {s['recall_at_k']}/40, right day {s['date_ok']}/40, p50 {s['p50_ms']} ms, p95 "
              f"{s['p95_ms']} ms, {len(convs)} conversations, build {build['build_s']} s")
        for r in s["rows"]:
            if not (r["hit"] and r["date_ok"]):
                print("  miss:", r["want"], r["q"], "->", r["answer"])
        assert s["recall_at_k"] >= 36, s["recall_at_k"]
        assert s["date_ok"] >= 34, s["date_ok"]
        he = [r for r in s["rows"] if r["lang"] == "he"]
        assert sum(r["hit"] for r in he) >= 27                   # Hebrew alone stays at 90 % too
        for r in s["rows"]:                                      # every spoken answer: at most two sentences
            assert r["answer"].count(". ") + r["answer"].count("? ") <= 1, r["answer"]
    finally:
        st.close()


def test_search_p95_under_150_ms_at_5000_conversations(tmp_path):
    from jarves.store import Store
    st = Store(tmp_path / "store.db")
    try:
        ids = rc.seed_store(st, rc.conversations(5000 - len(rc.PLANTED)))
        assert len(ids) == 5000 and st.rows("SELECT COUNT(*) n FROM turns")[0]["n"] == 10_000
        rec = recall.Recall(tmp_path, store=st, enabled=lambda: True)
        t0 = time.perf_counter()
        build = rec.rebuild()
        assert build["docs"] == 10_000 and time.perf_counter() - t0 < 60
        rt.forms.cache_clear()
        rt.keys.cache_clear()                                    # first searches see no cached word
        times = []
        for q, _k in rc.QUESTIONS * 2:
            with st._lock:
                times.append(recall.search(st._db, q, now=rc.NOW, limit=3)["took_ms"])
        times.sort()
        p95 = times[int(len(times) * 0.95) - 1]
        stats = rec.stats()
        print(f"\n10 000 turns: build {build['build_s']} s, index {stats['index_bytes']} bytes, store "
              f"{stats['store_bytes']} bytes, search p50 {times[len(times) // 2]} ms p95 {p95} ms")
        assert p95 <= 150, p95
        assert rc.score(rec, ids)["recall_at_k"] >= 36           # and the answers still hold at this size
    finally:
        st.close()
