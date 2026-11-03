"""V5-REPO-MOVE: source code names the current org repo, never the old igalgerman/jarves source repo."""
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "jarves"
OLD = "igalgerman/" + "jarves"          # assembled: not a literal here; "igalgerman/jarvis-releases" stays valid


def test_no_old_source_repo_in_src():
    hits = []
    for p in SRC.rglob("*"):
        if p.is_file() and p.suffix in {".py", ".js", ".toml", ".html"}:
            for n, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                low = line.lower()
                if OLD in low.replace("jarvis-releases", "") and "moved from" not in low:
                    hits.append(f"{p.relative_to(SRC)}:{n}")
    assert not hits, hits
