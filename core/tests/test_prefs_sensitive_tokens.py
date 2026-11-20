"""#1099: preferences.looks_sensitive must refuse key/token-shaped text, not only passwords and numbers."""
from __future__ import annotations

import pytest

from jarves.preferences import looks_sensitive

# Fake, made-up values shaped like real credentials (never real keys).
TOKENS = [
    "use sk-abcDEF123ghiJKL456mno for the model",
    "my github token is ghp_aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3z",
    "AKIAABCDEFGHIJKLMNOP is the aws id",
    "slack xoxb-1234-abcdefABCDEF",
    "AIzaSyA-bcdEFGhij_KLMnopQRStuvWXyz01234",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.sig",
    "the value Zq8Lm3Rt9Vx2Bk7Np4Hs6Wd1",
    "d41d8cd98f00b204e9800998ecf8427e",
    "my aws secret is wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",   # base64 with '/', runs split below 20
    "key Qx7Lm2+Rt8Vx3/Bk9Np4Hs6Wd1Zq8Lm3Rt9Vx2Bk7Np4Hs6Wd1==",      # base64 with '+', '/' and '=' padding
]
KEYWORDS = [
    "remember my api key please",
    "the API_KEY goes in the header",
    "keep my access token handy",
    "my secret key is blue",
    "use the private key from the drive",
    "send it as Bearer abcdefgh",
    "store both api keys and refresh tokens",
    "הטוקן שלי הוא כחול",
    "מפתח API של השירות",
]
SAFE = [
    "I prefer short answers in the morning",
    "אני מעדיף תשובות קצרות בבוקר",
    "internationalization matters to me",
    "play state-of-the-art jazz",
    "call me after the meeting_room_booking_flow ends",
    "use tokens wisely and keep answers short",
    "teach me a secret keyboard shortcut",
    "remind me about the private keynote",
    "the api keyboard layout is fine",
    "use the notes in docs/music/jazz/favourites/list.md",         # lower-case path: not a key
    "Morning/Evening/Weekend routines are fine",                    # mixed case but no digit
    "",
]


@pytest.mark.parametrize("text", TOKENS)
def test_token_shaped_text_is_sensitive(text):
    assert looks_sensitive(text)


@pytest.mark.parametrize("text", KEYWORDS)
def test_credential_keywords_are_sensitive(text):
    assert looks_sensitive(text)


@pytest.mark.parametrize("text", SAFE)
def test_ordinary_preferences_stay_allowed(text):
    assert not looks_sensitive(text)


@pytest.mark.parametrize("text", [
    "use sk-abcdefghijklmnop for the model",           # all lowercase: only the sk- prefix rule
    "ghp_abcdefghijklmnopqrstuvwxyz",                   # lowercase only: only the gh?_ prefix rule
    "AKIAABCDEFGHIJKLMNOP",                             # no lowercase: only the AKIA prefix rule
])
def test_prefix_rules_alone_catch_keys(text):
    assert looks_sensitive(text)


def test_generic_rule_alone_catches_an_unprefixed_key():
    # no known prefix and no keyword, fewer than 6 digits in a row: only the mixed-case token-run rule matches
    assert looks_sensitive("config Qx7Lm2Rt8Vx3Bk9Np4Hs")


def test_bearer_needs_a_token_after_it():
    assert looks_sensitive("Authorization: Bearer abcdefgh1234")
    assert not looks_sensitive("the bearer of bad news is late")


def test_none_and_empty_are_not_sensitive():
    assert not looks_sensitive(None)
    assert not looks_sensitive("")


def test_bytes_are_decoded_and_checked():
    assert looks_sensitive(b"use sk-abcdefghijklmnop")
    assert looks_sensitive(bytearray(b"my api key"))
    assert not looks_sensitive(b"short answers please")


@pytest.mark.parametrize("value", [12, 3.5, ["sk-abcdefghijklmnop"], {"k": "v"}, object()])
def test_other_types_fail_closed(value):
    assert looks_sensitive(value)


def test_aws_prefix_is_case_sensitive():
    assert looks_sensitive("AKIAABCDEFGHIJKLMNOP")
    assert not looks_sensitive("akiaabcdefghijklmnop")  # lower case is not an AWS key id (and not mixed-case)


def test_known_limitation_hyphenated_mixed_case_phrase_with_a_digit_is_refused():
    # Known false positive, on the safe side: a 20+ char run joined by '-' that mixes cases and has a digit
    # looks like a key. Revisit (e.g. require 2+ digits) only if the owner reports friction.
    assert looks_sensitive("Wake-me-at-7-on-Monday")


def test_base64_rule_alone_catches_a_slash_split_secret():
    # every '/'-separated run is under 20 chars and there is no keyword, prefix or 6+ digit run:
    # only the base64 run rule (30+ chars of [A-Za-z0-9+/], mixed case and digits) matches
    assert looks_sensitive("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    assert not looks_sensitive("wJalrXUtnFEMI/K7MDENG")  # under 30 chars: not key-shaped


def test_known_limitation_hashes_and_camelcase_with_a_digit_are_refused():
    # Known false positives, on the safe side (also for dev-manager drafts): a 32+ hex git SHA/checksum and a
    # 20+ char CamelCase identifier with a digit look like keys. Revisit only if the owner reports friction.
    assert looks_sensitive("fix after 24fd0a5990d2f3df1abbf4655836533d3b6fcce1")
    assert looks_sensitive("rename Base64EncoderFactoryHelper")


def test_goals_refuse_a_non_text_step_instead_of_crashing(tmp_path):
    # goals.Goals.add checks the raw step items, so a non-text step must be refused (fail closed), not raise TypeError
    from jarves.goals import Goals
    from jarves.userdata import open_db
    g = Goals(open_db(tmp_path))
    with pytest.raises(ValueError, match="sensitive"):
        g.add("learn guitar", steps=["buy strings", 123])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="sensitive"):
        g.add("learn guitar", steps=["buy strings", "keep my access token"])
