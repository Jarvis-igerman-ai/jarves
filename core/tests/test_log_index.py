"""SW-V4 O (#17): root-cause hints. tools/build_log_index.py scans logs.* calls (AST) and records file:line, function
and the last commit (git blame AT BUILD TIME) in a real throw-away git repo; 10 planted errors link to the right
file, line and commit; context lines carry no message text and no content fields."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import diag_replay as rp
from jarves.diag import db, logindex
from jarves.diag.anomaly import Detector
from jarves.store import Store

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_log_index as bli  # noqa: E402

CODES = [f"PLANTED_ERR_{i}" for i in range(10)]


def git(repo, *args):
    p = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    assert shutil.which("git"), "git is part of the test platform"
    r = tmp_path / "repo"
    pkg = r / "core" / "src" / "jarves"
    pkg.mkdir(parents=True)
    git(r, "init", "-q")
    git(r, "config", "user.email", "t@example.invalid")
    git(r, "config", "user.name", "t")
    first = ["from jarves import logs", "", "", "def handler():"]
    first += [f"    logs.error(\"mod\", \"{c}\", \"step {i} failed\", n={i})" for i, c in enumerate(CODES[:5])]
    (pkg / "mod.py").write_text("\n".join(first) + "\n", encoding="utf-8")
    second = ["from jarves import logs", "", "", "class Service:", "    async def run(self):"]
    second += [f"        logs.warn(\"svc\", \"{c}\", f\"retry {{k}} of {i}\", k=1)" for i, c in enumerate(CODES[5:])]
    (pkg / "svc.py").write_text("\n".join(second) + "\n", encoding="utf-8")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "one")
    c1 = git(r, "rev-parse", "HEAD")[:12]
    lines = (pkg / "svc.py").read_text(encoding="utf-8").splitlines()
    lines[5] = lines[5].replace("retry", "retrying")                 # the first warn call changes in commit two
    (pkg / "svc.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    git(r, "commit", "-q", "-am", "two")
    c2 = git(r, "rev-parse", "HEAD")[:12]
    return {"root": r, "src": pkg, "c1": c1, "c2": c2}


def test_index_links_ten_planted_errors_to_file_line_commit(repo, tmp_path):
    idx = bli.build(repo["src"], repo_root=repo["root"])
    want = {CODES[i]: ("jarves/mod.py", 5 + i, "handler", repo["c1"]) for i in range(5)}
    want.update({CODES[5 + i]: ("jarves/svc.py", 6 + i, "Service.run", repo["c2"] if i == 0 else repo["c1"])
                 for i in range(5)})
    li = logindex.LogIndex(idx)
    for code, (file, line, fn, commit) in want.items():
        h = li.hint(code)
        assert (h["file"], h["line"], h["function"], h["commit"]) == (file, line, fn, commit), (code, h)
        assert h["url"] == f"https://github.com/igalgerman/jarves/blob/{commit}/core/src/{file}#L{line}"
    assert li.template(CODES[0]) == "step 0 failed" and li.template(CODES[6]) == "retry {} of 1"
    # the detector attaches the hints to new-code findings
    st = Store(tmp_path / "s.db")
    try:
        rp.build(st, plant=())
        for i, c in enumerate(CODES):
            db.write(st, "INSERT INTO diag_codes (res, ts, code, level, component, n, app_version, git_commit) "
                         "VALUES ('h',?,?,?,?,?,?,?)", (int(rp.NOW // 3600 * 3600) - 3600 * (2 + i), c, "ERROR", "mod",
                                                        3, rp.V0, rp.C0))
        found = {f.fingerprint: f for f in Detector(st, index=li).run(2 * 86400, now=rp.NOW)}
        for code, (file, line, _fn, commit) in want.items():
            f = found[f"anomaly:newcode:{code}"]
            assert f.hints and f.hints[0]["file"] == file and f.hints[0]["line"] == line and f.hints[0]["commit"] == commit
    finally:
        st.close()


def test_real_source_index_without_blame():
    src = Path(__file__).resolve().parents[1] / "src" / "jarves"
    idx = bli.build(src, use_blame=False)
    sites = idx["codes"]["SELFCHECK_STARTED"]
    text = (src / "selfcheck.py").read_text(encoding="utf-8").splitlines()
    assert any(s["file"] == "jarves/selfcheck.py" and "SELFCHECK_STARTED" in text[s["line"] - 1] for s in sites)
    assert "PREFLIGHT_DONE" in idx["codes"] and len(idx["codes"]) > 200
    json.dumps(idx)                                                  # shippable as JSON


def test_missing_index_means_no_hints(tmp_path):
    li = logindex.LogIndex(path=tmp_path / "none.json")
    assert not li.built and li.hint("ANYTHING") is None


def test_context_lines_are_templates_and_numbers_only(tmp_path):
    import datetime as dt
    (tmp_path / "logs").mkdir()
    recs = []
    for i in range(14):
        recs.append({"ts": dt.datetime.fromtimestamp(rp.NOW - 100 + i, dt.timezone.utc).isoformat(), "level": "INFO",
                     "component": "c", "code": "PLANTED_ERR_0" if i == 7 else "OTHER", "msg": f"user said שלום {i}",
                     "text": "private words", "name": "Yossi", "token": "tok-123", "ms": i, "ok": True})
    (tmp_path / "logs" / "jarves.log").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs),
                                                   encoding="utf-8")
    li = logindex.LogIndex({"codes": {"PLANTED_ERR_0": [{"file": "jarves/x.py", "line": 3, "msg": "step 0 failed"}]}})
    lines = logindex.context_lines(tmp_path, "PLANTED_ERR_0", since=rp.NOW - 1000, until=rp.NOW, index=li)
    assert len(lines) == 10 and any("PLANTED_ERR_0: step 0 failed [ms=7 ok=True]" in x for x in lines)
    blob = "\n".join(lines)
    for leak in ("user said", "שלום", "private words", "Yossi", "tok-123"):
        assert leak not in blob
