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
