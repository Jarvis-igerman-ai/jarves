"""#1099: preferences.looks_sensitive must refuse key/token-shaped text, not only passwords and numbers."""
from __future__ import annotations

import pytest

from jarves.preferences import looks_sensitive

# Fake, made-up values shaped like real credentials (never real keys).
TOKENS = [
    "use sk-abcDEF123ghiJKL456mno for the model",
    "use sk-abcdefghijklmnop for the model",               # all lower case: only the sk- prefix rule
    "stripe sk_live_abcdefghijklmnop",                       # lower case: only the sk_live_ rule
    "stripe pk_test_abcdefghijklmnop",
    "my github token is ghp_abcdefghijklmnopqrstuvwxyz",    # lower case: only the gh?_ prefix rule
    "AKIAABCDEFGHIJKLMNOP is the aws id",                    # no lower case: only the AKIA rule
    "slack xoxb-1234-abcdefABCDEF",
    "AIzaSyA-bcdEFGhij_KLMnopQRStuvWXyz01234",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.sig",
    "config Qx7Lm2Rt8Vx3Bk9Np4Hs",                           # no prefix/keyword: only the mixed-case run rule
    "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",                      # 32 hex, no 6-digit run: only the hex rule
    "github_pat_abcdefghijklmnopqrstuvwxyz",                 # lower case: only the github_pat_ rule
    "gitlab glpat-abcdefghijklmnopqrstuv",                   # lower case: only the glpat- rule
    "ASIAABCDEFGHIJKLMNOP is a temporary aws id",            # no lower case: only the ASIA rule
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",              # '/' splits runs below 20: only the base64 rule
    "Authorization: Bearer abcdefgh1234",
    "Authorization: Bearer abcdEFGHijkl",
]
KEYWORDS = [
    "remember my api key please",
    "the API_KEY goes in the header",
    "keep my access token handy",
    "my secret key is blue",
    "use the private key from the drive",
    "store both api keys and refresh tokens",
    "הטוקן שלי הוא כחול",
    "מפתח API של השירות",
]
SAFE = [
    "I prefer short answers in the morning",
    "אני מעדיף תשובות קצרות בבוקר",
    "internationalization matters to me",
    "call me after the meeting_room_booking_flow ends",
    "use tokens wisely and keep answers short",
    "teach me a secret keyboard shortcut",
    "remind me about the private keynote",
    "the bearer of bad news is late",
    "a bearer instruments shop",                             # 8+ letter word after bearer, no digit/mixed case
    "use the notes in docs/music/jazz/favourites/list.md",   # lower-case path
    "Morning/Evening/Weekend routines are fine",             # mixed case, no digit
    "wJalrXUtnFEMI/K7MDENG",                                 # under 30 chars
    "akiaabcdefghijklmnop",                                  # AKIA is case-sensitive
    "",
]


@pytest.mark.parametrize("text", TOKENS + KEYWORDS)
def test_key_and_token_shaped_text_is_sensitive(text):
    assert looks_sensitive(text)


@pytest.mark.parametrize("text", SAFE)
def test_ordinary_preferences_stay_allowed(text):
    assert not looks_sensitive(text)


def test_none_bytes_and_other_types():
    for empty in (None, "", b"", 0, False, [], {}):
        assert not looks_sensitive(empty)  # empty values stay "not sensitive", as before the change
    assert looks_sensitive(b"use sk-abcdefghijklmnop")
    assert not looks_sensitive(b"short answers please")
    for value in (12, ["sk-abcdefghijklmnop"], object()):
        assert looks_sensitive(value)  # fail closed instead of raising TypeError


@pytest.mark.parametrize("text", [
    "Wake-me-at-7-on-Monday",                                # hyphen-joined mixed case with a digit
    "fix after a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",    # git SHA (32+ hex, no 6-digit run)
    "rename Base64EncoderFactoryHelper",                     # CamelCase identifier with a digit
    "Users/Name/Music/Playlist2024/Favourites",              # mixed-case path with a digit (base64 rule)
])
def test_known_limitation_safe_side_false_positives(text):
    # Refused on the safe side (never leaked). Revisit only if the owner reports friction.
    assert looks_sensitive(text)


def test_goals_refuse_a_non_text_step_instead_of_crashing(tmp_path):
    from jarves.goals import Goals
    from jarves.userdata import open_db
    g = Goals(open_db(tmp_path))
    with pytest.raises(ValueError, match="sensitive"):
        g.add("learn guitar", steps=["buy strings", 123])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="sensitive"):
        g.add("learn guitar", steps=["buy strings", "keep my access token"])


def test_preferences_suggest_refuses_a_token_and_stores_nothing(tmp_path):
    from jarves.preferences import Preferences
    from jarves.userdata import open_db
    p = Preferences(open_db(tmp_path))
    with pytest.raises(ValueError, match="sensitive"):
        p.suggest("use sk_live_abcdefghijklmnop for payments", "the owner said so")
    with pytest.raises(ValueError, match="sensitive"):
        p.add_confirmed("my github token is ghp_abcdefghijklmnopqrstuvwxyz")
    assert p.items("proposed") == [] and p.items("confirmed") == []
