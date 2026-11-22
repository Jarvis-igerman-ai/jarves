"""#1559: looks_sensitive refuses an authenticator (TOTP) seed next to a strong seed word, also when it is shown in
spaced 4-character groups or has no digit, while ordinary words near those words still pass."""
import pytest

from jarves.preferences import looks_sensitive

LEAKY = [
    "my totp seed: JBSW Y3DP EHPK 3PXP",          # spaced groups, as authenticator apps show it
    "my totp seed: jbsw y3dp ehpk 3pxp",          # lower case
    "my totp seed jbsw y3dp ehpk 3pxp",           # no colon: the seed word joins the run
    "my totp seed: JBSWYDPEHPKAPXPQ",             # no digit
    "2fa key JBSWY3DPEHPK3PXP please keep",       # unbroken with a digit
    "האוטנטיקטור שלי JBSW Y3DP EHPK 3PXP",
]
SAFE = [
    "OTP SETUP: INTERNATIONALIZATION",            # a long real word
    "my 2fa with this must work every day",       # four-letter words in a row
    "I prefer short answers in the morning",
    "JBSW Y3DP EHPK 3PXP",                        # no seed word near it
    "authenticator app is fine, call me at noon",
]


@pytest.mark.parametrize("text", LEAKY)
def test_seed_near_a_seed_word_is_sensitive(text):
    assert looks_sensitive(text)


@pytest.mark.parametrize("text", SAFE)
def test_ordinary_text_is_not_sensitive(text):
    assert not looks_sensitive(text)


def _random_seeds(n=2000, size=16):
    import random
    r = random.Random(1559)                       # fixed seed: the same sample on every run
    return ["".join(r.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(size)) for _ in range(n)]


def test_most_digit_free_seeds_are_refused_unbroken_and_spaced():
    seeds = _random_seeds()
    unbroken = sum(looks_sensitive(f"my totp seed: {s}") for s in seeds) / len(seeds)
    spaced = sum(looks_sensitive("my totp seed: " + " ".join(s[i:i + 4] for i in range(0, 16, 4)))
                 for s in seeds) / len(seeds)
    assert unbroken >= 0.8 and spaced >= 0.8, (unbroken, spaced)
