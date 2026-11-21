"""#206 H3/H4: the guides state two privacy limits plainly, in English and Hebrew, also in `jarvis help`."""
from jarves import guides


def test_206_h3_phone_guide_says_the_status_permission_shows_when_the_pc_mic_is_in_use():
    g = guides.find("mobile_access")
    assert "status" in g.privacy_en and "microphone" in g.privacy_en and "presence" in g.privacy_en
    assert "status" in g.privacy_he and "מיקרופון" in g.privacy_he
    assert g.privacy_en in guides.help_text(g)


def test_206_h4_project_guide_says_the_check_command_runs_without_a_sandbox():
    g = guides.find("project_manager")
    assert "check" in g.privacy_en and "sandbox" in g.privacy_en
    assert "ארגז חול" in g.privacy_he
    assert g.privacy_en in guides.help_text(g)
