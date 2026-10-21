"""SW-V4 L study: the restricted-token probe (core/tools/token_probe.py) on a real Windows process. It runs `cmd.exe` under a
restricted COPY of this test's own token against files in a throw-away folder under %PUBLIC%/%TEMP%, and removes them after.
It touches no registry, PATH, install or network. The product does not use restricted tokens yet (docs/reviews/
SWV4_L_INSTALLER.md section 8); these tests pin the findings the recommendation rests on."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


@pytest.fixture(scope="module")
def matrix():
    import token_probe as tp
    base = tp.new_dir(tp.os.environ.get("PUBLIC", r"C:\Users\Public"))
    try:
        return {r["variant"]: r for r in tp.probe(base)}
    finally:
        tp.cleanup(base)


@pytest.mark.windows_only
def test_every_variant_can_start_a_console_less_child(matrix):
    assert all(r["start"] for r in matrix.values()), matrix


@pytest.mark.windows_only
def test_the_baseline_reads_and_writes_everything_the_owner_can(matrix):
    r = matrix["plain"]
    assert r["read_owner_only"] and r["read_world"] and r["read_world_in_profile"] and r["write_owner_only"]


@pytest.mark.windows_only
def test_deny_only_user_sid_hides_owner_only_files_from_the_child_but_not_world_readable_ones(matrix):
    r = matrix["deny_user"]
    assert not r["read_owner_only"]                  # answer.key / secrets.env stand-in: unreadable
    assert not r["write_owner_only"]
    assert r["read_world"]                           # a program folder readable by Users / Everyone still works
    assert not r["read_world_in_profile"]            # ...but NOT when the path runs through the owner-only profile root


@pytest.mark.windows_only
def test_write_restricted_token_keeps_reads_and_blocks_writes(matrix):
    r = matrix["write_restricted"]
    assert r["read_owner_only"] and r["read_world"]
    assert not r["write_owner_only"]
