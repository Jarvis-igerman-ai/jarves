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
]
KEYWORDS = [
    "remember my api key please",
    "the API_KEY goes in the header",
    "keep my access token handy",
    "my secret key is blue",
    "use the private key from the drive",
    "send it as Bearer abcdefgh",
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
    assert not looks_sensitive(None)  # type: ignore[arg-type]
    assert not looks_sensitive("")


def test_bytes_are_decoded_and_checked():
    assert looks_sensitive(b"use sk-abcdefghijklmnop")  # type: ignore[arg-type]
    assert looks_sensitive(bytearray(b"my api key"))  # type: ignore[arg-type]
    assert not looks_sensitive(b"short answers please")  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [12, 3.5, ["sk-abcdefghijklmnop"], {"k": "v"}, object()])
def test_other_types_fail_closed(value):
    assert looks_sensitive(value)
