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
    "my totp seed is JBSWYDPEHPKAPXPQ",              # review R1: upper case, no digit
    "my totp seed is OTP3JBSWYDPEHPKQ",              # review R2: the run starts with a seed word
    "key: OTP3JBSWYDPEHPKQ",                         # intended: `OTP` + digit inside the run is the only seed word
]
SAFE = [
    "base64urlencoded sha256checksumfile",           # identifiers in a dev draft, no seed word
    "I prefer short answers in the morning",
    "use the authenticator app on my phone",         # seed word, no base32 run
    "seed the database with ipv4address rows",       # word run under 16 chars
    "my totp app shows 1234 digits",                 # digits only, not a 16-char run
    "the 2fa commit was deadbeefcafe2345abcd",       # review R3: hex in base32 letters next to a seed word
    "THE 2FA COMMIT WAS DEADBEEFCAFEBABEFACE",       # upper-case digit-free hex
    "הקבוצה מפסידה JBSWYDPEHPK3PXPQ",                # review R4: סיד inside מפסידה is not a seed word
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
    upper = sum(looks_sensitive(f"my totp seed: {s}") for s in seeds)
    assert upper == len(seeds)                       # every upper-case seed, digit-free ones included
    with_digit = [s.lower() for s in seeds if any(c in "234567" for c in s)]
    assert all(looks_sensitive(f"my totp seed: {s}") for s in with_digit)
    assert len(with_digit) / len(seeds) >= 0.9       # rate over ALL lowercase seeds; misses are pinned below


@pytest.mark.xfail(strict=True, reason="#1482: bare base32 seed with no seed word nearby is not refused yet")
def test_known_miss_bare_seed_without_a_seed_word():
    # Known leak, pinned as a strict xfail: the full base32 rule belongs to #1482, which will turn this into a pass.
    assert looks_sensitive("JBSWYDPEHPK3PXPQ")


@pytest.mark.xfail(strict=True, reason="#1482: seeds shown in 4-char groups are not refused yet")
def test_known_miss_grouped_seed_next_to_a_seed_word():
    # #1516 covers unbroken runs only; spaced/grouped base32 seeds belong to #1482.
    assert looks_sensitive("totp seed JBSW YDPE HPK3 PXPQ")


def test_known_miss_digit_free_lowercase_seed():
    # Trade-off: a digit-free lowercase run is indistinguishable from a long lowercase word, so it passes.
    assert not looks_sensitive("my totp seed: jbswydpehpkapxpq")


def test_suggest_refuses_a_word_adjacent_seed_and_stores_nothing(tmp_path):
    prefs = Preferences(UserDB(tmp_path / "memory.db"), clock=lambda: 1000.0)
    with pytest.raises(ValueError, match="sensitive"):
        prefs.suggest("remember my 2fa seed JBSWYDPEHPK3PXPQ", "my 2fa seed JBSWYDPEHPK3PXPQ")
    assert prefs.items() == []
