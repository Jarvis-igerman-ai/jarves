"""#456: no user agent or link may name the old repo igalgerman/jarves."""
from jarves import journey, phone_location


def test_user_agents_name_the_org_repo():
    for ua in (journey.USER_AGENT, phone_location.USER_AGENT):
        assert "Jarvis-igerman-ai/jarves" in ua and "igalgerman/jarves" not in ua
