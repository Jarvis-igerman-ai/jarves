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
    "send it as Bearer abc",
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


def test_non_string_input_does_not_crash():
    assert not looks_sensitive(None)  # type: ignore[arg-type]
    assert not looks_sensitive(12)  # type: ignore[arg-type]
