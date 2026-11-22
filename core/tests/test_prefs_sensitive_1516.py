"""#1516: unbroken base32 (TOTP/2FA) seeds are refused by looks_sensitive; development words are not."""
import random

import pytest

from jarves.preferences import looks_sensitive

_B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

LEAKY = [
    "my totp seed is JBSWYTDPEHPKXPXP3QAB",          # one digit group, seed word near
    "2fa: KRSXGZLBMNQW4ZDF",                          # one digit group, "2fa" before
    "authenticator key abcdefghijklmn4z",            # lowercase, one digit group
    "המאמת שלי JBSWYTDPEHPKXPX5",                     # Hebrew seed word
    "הסוד abcdefghijklmn4z",                          # Hebrew seed word with a prefix letter
    "otp codes: abcdefghijklmn4z",                   # plural / token next to punctuation
    "JBSWY3DPEHPK3PXP",                               # two digit groups, no word needed
    "keep jbswy3dpehpk3pxp for later",               # lowercase, two groups
]

SAFE = [
    "base64urlencoded sha256checksumfile",
    "use base64urlencoded strings for the payload",
    "run the sha256checksumfile step before release",
    "commit d8d6def4 merged",
    "fix a3f9c2e1b7d4f6a8c0e2b4d6f8a1c3e5b7d9f0a2",  # 40-char git SHA
    "I prefer short answers in the morning",
    "internationalization",                           # long word, no digits
    "call getOtpSecret4Value in the handler",        # seed word only inside the run itself (review R1)
    "footprint abcdefghijklmn4z",                    # "otp" inside an ordinary word (review 2, R1)
    "יסוד abcdefghijklmn4z",                          # "סוד" inside an ordinary Hebrew word (review 2, R1)
]


@pytest.mark.parametrize("text", LEAKY)
def test_base32_seed_shapes_are_refused(text):
    assert looks_sensitive(text)


@pytest.mark.parametrize("text", SAFE)
def test_development_words_and_shas_pass(text):
    assert not looks_sensitive(text)


def _seeds(n, length, rng):
    out = []
    while len(out) < n:
        s = "".join(rng.choice(_B32) for _ in range(length))
        if any(c.isdigit() for c in s):           # an all-letter run is not a base32 seed shape we can tell from a word
            out.append(s)
    return out


def test_word_adjacent_16_char_seeds_are_refused():
    rng = random.Random(1516)
    seeds = _seeds(500, 16, rng)
    rate = sum(looks_sensitive(f"totp seed {s}") for s in seeds) / len(seeds)
    assert rate == 1.0


def test_bare_seed_detection_rates():
    rng = random.Random(1516)
    r16 = sum(map(looks_sensitive, _seeds(500, 16, rng))) / 500
    r32 = sum(map(looks_sensitive, _seeds(500, 32, rng))) / 500
    assert r16 >= 0.78 and r32 >= 0.97
