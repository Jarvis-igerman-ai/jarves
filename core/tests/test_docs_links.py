"""Docs refresh (SW-V4 package N): offline checks that the user-facing documents link to files that exist, that the
English and Hebrew user guides have the same sections, and that no personal data or secret-looking text slipped in.
No network, no Core, no hardware."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HEBREW = re.compile(r"[֐-׿]")
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

DOCS = [
    "README.md", "README_HE.md", "CHANGELOG.md", "core/README.md", "docs/README.md", "docs/USER_GUIDE.md",
    "docs/USER_GUIDE.he.md", "docs/RELEASE_NOTES_4.0.md", "docs/CONTEXT_MAP.md", "docs/INSTALL_TEST.md",
    "docs/GOOGLE_SETUP.md", "docs/CAMERA_SETUP.md",
]


def _links(path: Path):
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    return LINK.findall(text)


@pytest.mark.parametrize("rel", DOCS)
def test_relative_links_resolve(rel):
    path = ROOT / rel
    assert path.is_file(), rel
    broken = []
    for target in _links(path):
        if re.match(r"[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
            continue
        file_part = target.split("#", 1)[0]
        if file_part and not (path.parent / file_part).resolve().exists():
            broken.append(target)
    assert not broken, f"{rel}: broken links {broken}"


def test_docs_index_lists_every_user_document():
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    for name in ("USER_GUIDE.md", "USER_GUIDE.he.md", "RELEASE_NOTES_4.0.md", "INSTALL_TEST.md", "GOOGLE_SETUP.md",
                 "CAMERA_SETUP.md", "NOW.md", "HANDOFF.md", "ROADMAP.md"):
        assert name in index, name


def _sections(path: Path):
    return [m.group(1) for m in re.finditer(r"^## (\d+)\.", path.read_text(encoding="utf-8"), re.M)]


def test_user_guides_have_the_same_sections_in_both_languages():
    en, he = ROOT / "docs" / "USER_GUIDE.md", ROOT / "docs" / "USER_GUIDE.he.md"
    assert _sections(en) == _sections(he) and len(_sections(en)) >= 16
    assert not HEBREW.search(en.read_text(encoding="utf-8")), "the English guide must not contain Hebrew"
    assert len(HEBREW.findall(he.read_text(encoding="utf-8"))) > 2000


def test_guides_cover_every_v4_feature():
    text = (ROOT / "docs" / "USER_GUIDE.md").read_text(encoding="utf-8").lower()
    for word in ("privacy", "approvals", "recall", "coding agents", "whatsapp", "dialing", "health", "wake",
                 "dashboard", "update", "rollback", "performance", "delegation"):
        assert word in text, word


def test_no_personal_data_or_secrets_in_the_new_docs():
    secret = re.compile(r"sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY")
    for rel in ("README_HE.md", "CHANGELOG.md", "docs/README.md", "docs/USER_GUIDE.md", "docs/USER_GUIDE.he.md",
                "docs/RELEASE_NOTES_4.0.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not secret.search(text), rel
        assert "gmail.com" not in text and "97254" not in text, rel
        assert "igalgerman/JARVIS" not in text, rel


def _resolved_links(path: Path):
    out = set()
    for target in _links(path):
        if re.match(r"[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
            continue
        out.add((path.parent / target.split("#", 1)[0]).resolve())
    return out


def test_every_doc_is_listed_in_doc_map():
    """docs/DOC_MAP.md is the single index: every Markdown file under docs/ must be linked from it."""
    doc_map = ROOT / "docs" / "DOC_MAP.md"
    listed = _resolved_links(doc_map)
    missing = [str(p.relative_to(ROOT)) for p in sorted((ROOT / "docs").rglob("*.md"))
               if p.resolve() != doc_map.resolve() and p.resolve() not in listed]
    assert not missing, f"add these to docs/DOC_MAP.md: {missing}"


def test_doc_map_is_the_entry_point():
    for rel in ("docs/README.md", "docs/CONTEXT_MAP.md", "CLAUDE.md"):
        assert "DOC_MAP.md" in (ROOT / rel).read_text(encoding="utf-8"), rel
    text = (ROOT / "docs" / "DOC_MAP.md").read_text(encoding="utf-8")
    for needle in ("Testing and manual testing", "V4", "V5", "V6", "V7"):
        assert needle in text, needle


def test_all_relative_links_under_docs_resolve():
    """Every relative link in every Markdown file under docs/ (not the evidence reports, which quote old paths)."""
    broken = []
    for path in sorted((ROOT / "docs").rglob("*.md")):
        if "reviews" in path.relative_to(ROOT / "docs").parts:
            continue
        for target in _links(path):
            if re.match(r"[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
                continue
            file_part = target.split("#", 1)[0]
            if file_part and not (path.parent / file_part).resolve().exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not broken, broken


def test_historical_docs_are_marked():
    for rel in ("docs/plans/SW_NEXT_STEPS.md", "docs/plans/FULL_TEST_PLAN.md", "docs/SW_V1_WORK_PACKAGE.md",
                "docs/JARVES_SW_V2_IMPLEMENTATION_SPEC.md", "docs/JARVES_SW_V3_WORK_PACKAGE.md", "docs/archive/ROADMAP.md"):
        assert "Historical, superseded by" in (ROOT / rel).read_text(encoding="utf-8")[:600], rel
