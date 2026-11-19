"""#206 H3/H4: the guides state two known exposures plainly instead of hiding them."""
import tomllib
from pathlib import Path

from jarves import guides

DATA = Path(guides.__file__).parent / "data"


def _text(name: str) -> str:
    return (DATA / name).read_text(encoding="utf-8")


def test_h3_mobile_guide_says_the_status_permission_shows_when_the_pc_mic_is_in_use():
    g = tomllib.loads(_text("mobile_access.toml"))
    assert "whether someone is at the computer" in g["privacy_en"]
    assert "מישהו ליד המחשב" in g["privacy_he"]


def test_h4_project_guide_says_the_check_command_runs_without_a_sandbox():
    g = tomllib.loads(_text("project_manager.toml"))
    assert "not in a sandbox" in g["privacy_en"]
    assert "לא בארגז חול" in g["privacy_he"]
