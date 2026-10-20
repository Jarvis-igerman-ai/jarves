"""SW-V4 G recall quality and speed (prints the score). Offline, deterministic, free.

    python tools/recall_quality.py                  # 40-question set: Recall@3, right day in the spoken answer
    python tools/recall_quality.py --baselines      # + the measured alternatives (plain unicode61, FTS5 trigram)
    python tools/recall_quality.py --perf 5000      # + index build time / size and search p50/p95 at 5000 conversations
    python tools/recall_quality.py --json           # machine-readable

The corpus (tests/recall_corpus.py): 40 planted conversations, distractors on the same topics on other days, 30 Hebrew
and 10 English questions. Everything runs on a fresh store.db in a temporary folder through the real Store.ingest and
the real recall index; nothing touches a profile.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import recall_corpus as rc  # noqa: E402
from jarves import recall  # noqa: E402
from jarves import recall_text as rt  # noqa: E402


def quality(n_distractors: int) -> dict:
    with tempfile.TemporaryDirectory() as d:
        t0 = time.perf_counter()
        st, ids, convs = rc.build(Path(d), n_distractors)
        ingest = time.perf_counter() - t0
        rec = recall.Recall(Path(d), store=st, enabled=lambda: True)
        b = rec.rebuild()
        s = rc.score(rec, ids)
        s.update({"conversations": len(convs), "ingest_s": round(ingest, 3), "build_s": b["build_s"],
                  "docs": rec.stats()["docs"], "index_bytes": rec.stats()["index_bytes"]})
        st.close()
    return s


# ---------------------------------------------------------------------- measured alternatives (research note)
def _baseline(n_distractors: int, tokenizer: str, strip: bool) -> dict:
    """Same corpus, same date filter, same per-conversation grouping; only the lexical part differs."""
    with tempfile.TemporaryDirectory() as d:
        st, ids, _ = rc.build(Path(d), n_distractors)
        db = sqlite3.connect(":memory:")
        db.execute(f"CREATE VIRTUAL TABLE f USING fts5(body, sid UNINDEXED, ts UNINDEXED, tokenize = '{tokenizer}')")
        for r in st.rows("SELECT session_id, ts, text FROM turns WHERE text IS NOT NULL"):
            db.execute("INSERT INTO f (body, sid, ts) VALUES (?,?,?)", (rt.soft(r["text"]), r["session_id"], r["ts"]))
        st.close()
        hits = 0
        for q, key in rc.QUESTIONS:
            when, rest = rt.parse_when(q, rc.NOW)
            words = []
            for raw in rt.raw_tokens(rest):
                tok = raw.replace("'", "").replace('"', "")
                if len(tok) < 2 or rt._stop(rt._fold(raw)):
                    continue
                if strip and rt.is_hebrew(tok):
                    tok = ([tok] + rt.prefix_forms(tok))[-1]
                if "trigram" in tokenizer and len(tok) < 3:
                    continue
                words.append('"' + tok + '"')
            if not words:
                continue
            lo, hi = (when.start, when.end) if when else (-1e18, 1e18)
            top: list[str] = []
            for sid, ts, _s in db.execute("SELECT sid, ts, bm25(f) FROM f WHERE f MATCH ? ORDER BY rank LIMIT 2000",
                                          (" OR ".join(words),)):
                if lo <= ts < hi and sid not in top:
                    top.append(sid)
                if len(top) == 3:
                    break
            hits += ids[key] in top
        db.close()
    return {"tokenizer": tokenizer, "prefix_strip": strip, "recall_at_3": hits, "n": len(rc.QUESTIONS)}


def baselines(n_distractors: int) -> list[dict]:
    return [_baseline(n_distractors, "unicode61 remove_diacritics 2", False),
            _baseline(n_distractors, "trigram", False),
            _baseline(n_distractors, "trigram", True)]


# ---------------------------------------------------------------------- speed at scale
def perf(n_conversations: int, reps: int = 5) -> dict:
    with tempfile.TemporaryDirectory() as d:
        from jarves.store import Store
        st = Store(Path(d) / "store.db")
        convs = rc.conversations(n_conversations - len(rc.PLANTED))
        t0 = time.perf_counter()
        ids = rc.seed_store(st, convs)
        ingest = time.perf_counter() - t0
        turns = st.rows("SELECT COUNT(*) n FROM turns")[0]["n"]
        rec = recall.Recall(Path(d), store=st, enabled=lambda: True)
        b = rec.rebuild()
        st.indexer = recall.Indexer()                                  # incremental: one more conversation
        t1 = time.perf_counter()
        for r in rc.records(rc.PLANTED[0], 999):
            st.ingest(r)
        st.commit()
        incr_ms = (time.perf_counter() - t1) * 1000
        times, cold = [], []
        for _ in range(reps):
            for q, _key in rc.QUESTIONS:
                with st._lock:
                    times.append(recall.search(st._db, q, now=rc.NOW, limit=3)["took_ms"])
        for q, _key in rc.QUESTIONS:                     # worst case: no word seen before (caches emptied)
            rt.forms.cache_clear()
            rt.keys.cache_clear()
            with st._lock:
                cold.append(recall.search(st._db, q, now=rc.NOW, limit=3)["took_ms"])
        cold.sort()
        s = rec.stats()
        st.close()
    times.sort()
    return {"conversations": len(ids), "turns": turns, "ingest_s": round(ingest, 2), "build_s": b["build_s"],
            "incremental_commit_ms": round(incr_ms, 2), "docs": s["docs"], "index_bytes": s["index_bytes"],
            "store_bytes": s["store_bytes"], "searches": len(times), "p50_ms": round(statistics.median(times), 2),
            "p95_ms": round(times[int(len(times) * 0.95) - 1], 2), "max_ms": round(times[-1], 2),
            "cold_p95_ms": round(cold[int(len(cold) * 0.95) - 1], 2), "cold_max_ms": round(cold[-1], 2)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--distractors", type=int, default=400)
    ap.add_argument("--baselines", action="store_true")
    ap.add_argument("--perf", type=int, default=0, metavar="CONVERSATIONS")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    out = {"quality": quality(a.distractors)}
    if a.baselines:
        out["baselines"] = baselines(a.distractors)
    if a.perf:
        out["perf"] = perf(a.perf)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0 if out["quality"]["recall_at_k"] >= 36 else 1
    q = out["quality"]
    for r in q["rows"]:
        print(f"{'OK ' if r['hit'] else 'MISS'} {'date' if r['date_ok'] else '----'} rank={r['rank'] or '-':<2} "
              f"{r['ms']:>6.1f} ms  [{r['want']}] {r['q']}\n      -> {r['answer']}")
    print(f"\nRecall@{q['k']}: {q['recall_at_k']}/{q['n']} ({100 * q['recall_at_k'] / q['n']:.0f} %)   right day in the "
          f"spoken answer: {q['date_ok']}/{q['n']}   search p50 {q['p50_ms']} ms, p95 {q['p95_ms']} ms   "
          f"({q['conversations']} conversations, {q['docs']} index rows, build {q['build_s']} s)")
    for b in out.get("baselines", []):
        print(f"baseline {b['tokenizer']:<32} prefix strip={b['prefix_strip']!s:<5} Recall@3 {b['recall_at_3']}/{b['n']}")
    if "perf" in out:
        p = out["perf"]
        print(f"perf: {p['conversations']} conversations / {p['turns']} turns: build {p['build_s']} s, incremental "
              f"commit {p['incremental_commit_ms']} ms, index {(p['index_bytes'] or 0) / 1048576:.1f} MB of "
              f"{(p['store_bytes'] or 0) / 1048576:.1f} MB store.db, search p50 {p['p50_ms']} ms p95 {p['p95_ms']} ms "
              f"max {p['max_ms']} ms ({p['searches']} searches); cold caches p95 {p['cold_p95_ms']} ms max {p['cold_max_ms']} ms")
    print("SCORE: " + ("PASS" if q["recall_at_k"] >= 36 and q["date_ok"] >= 34 else "FAIL") + " (target 36/40 and "
          "34/40 dates)")
    return 0 if q["recall_at_k"] >= 36 and q["date_ok"] >= 34 else 1


if __name__ == "__main__":
    sys.exit(main())
