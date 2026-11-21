"""#1516: an unbroken 16-char base32 seed next to a seed word (totp, seed, 2fa, otp, authenticator, Hebrew) is refused."""
import random

import pytest

from jarves.preferences import Preferences, looks_sensitive
from jarves.userdata import UserDB

B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

LEAKY = [
    "my totp seed is JBSWYDPEHPK3PXPQ",              # one digit group, 16 chars
    "2fa backup: jbswydpehpk3pxpq",
    "OTP=KRSXG5CTMVRXEZLU",
    "authenticator key NBSWY3DPFQQHO33S please remember",
    "JBSWYDPEHPK3PXPQ is my google authenticator seed",
    "הסיד של האימות הוא JBSWYDPEHPK3PXPQ",
    "מאמת: JBSWYDPEHPK3PXPQ",
]
SAFE = [
    "base64urlencoded sha256checksumfile",           # identifiers in a dev draft, no seed word
    "I prefer short answers in the morning",
    "use the authenticator app on my phone",         # seed word, no base32 run
    "seed the database with ipv4address rows",       # word run under 16 chars
    "my totp app shows 1234 digits",                 # digits only, not a 16-char run
    "the 2fa commit was 3ee96db4a1c2f0e9b8d7",       # hex SHA next to a seed word is not base32
    "JBSWYDPEHPK3PXPQ",                              # bare run with one digit group, no seed word (out of scope)
]


@pytest.mark.parametrize("text", LEAKY)
def test_seed_next_to_a_seed_word_is_sensitive(text):
    assert looks_sensitive(text)


@pytest.mark.parametrize("text", SAFE)
def test_prose_and_identifiers_without_a_seed_word_stay_safe(text):
    assert not looks_sensitive(text)


def test_known_fail_safe_identifier_next_to_a_seed_word_is_refused():
    # Intentional trade-off: a 16+ single-case identifier with a digit 2-7 within 20 chars of a seed word is refused.
    assert looks_sensitive("seed the database with base64urlencoded rows")


def test_seed_word_further_than_20_chars_does_not_count():
    assert not looks_sensitive("totp" + " and other unrelated words" + " JBSWYDPEHPK3PXPQ")


def test_word_adjacent_16_char_seed_rate_is_high():
    rng = random.Random(1516)
    seeds = ["".join(rng.choice(B32) for _ in range(16)) for _ in range(400)]
    seeds = [s for s in seeds if any(c in "234567" for c in s)]
    for case in (str.upper, str.lower):
        hits = sum(looks_sensitive(f"my totp seed: {case(s)}") for s in seeds)
        assert hits / len(seeds) >= 0.99


def test_suggest_refuses_a_word_adjacent_seed_and_stores_nothing(tmp_path):
    prefs = Preferences(UserDB(tmp_path / "memory.db"), clock=lambda: 1000.0)
    with pytest.raises(ValueError, match="sensitive"):
        prefs.suggest("remember my 2fa seed JBSWYDPEHPK3PXPQ", "my 2fa seed JBSWYDPEHPK3PXPQ")
    assert prefs.items() == []
