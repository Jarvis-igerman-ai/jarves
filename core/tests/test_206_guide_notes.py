"""#206 H3/H4: the guides state two known exposures plainly. Read through the guides loader and the `jarvis help`
text the owner sees, checking key terms rather than whole sentences."""
from jarves import guides


def _guide(name: str):
    g = guides.find(name)
    assert g is not None, name
    return g


def test_h3_mobile_guide_says_the_status_permission_shows_when_the_pc_mic_is_in_use():
    g = _guide("mobile_access")
    for term in ("status", "microphone", "someone is at the computer"):
        assert term in g.privacy_en
    for term in ("מצב", "המיקרופון", "מישהו ליד המחשב"):
        assert term in g.privacy_he
    assert g.privacy_en in guides.help_text(g)


def test_h4_project_guide_says_the_check_command_runs_without_a_sandbox():
    g = _guide("project_manager")
    for term in ("check command", "not in a sandbox", "trust"):
        assert term in g.privacy_en
    for term in ("פקודת הבדיקה", "לא בארגז חול"):
        assert term in g.privacy_he
    assert g.privacy_en in guides.help_text(g)
