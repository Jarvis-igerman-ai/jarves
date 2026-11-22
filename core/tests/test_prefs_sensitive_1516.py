"""#1516: an unbroken 16-char base32 seed with a digit next to a seed word (totp, otp, 2fa, seed, Hebrew) is refused."""
import random

import pytest

from jarves import devmanager, goal_plans, goals
from jarves.diag import db as diag_db
from jarves.preferences import Preferences, looks_sensitive
from jarves.store import Store
from jarves.userdata import open_db

B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
SEED = "JBSWYDPEHPK3PXPQ"                            # 16 chars, one digit group

LEAKY = [
    f"my totp seed is {SEED}",
    "2fa backup: jbswydpehpk3pxpq",
    "OTP=KRSXG5CTMVRXEZLU",
    "authenticator key NBSWY3DPFQQHO33S please remember",
    f"{SEED} is my google authenticator seed",
    f"הסיד של האימות הוא {SEED}",
    f"מאמת: {SEED}",
    "key: OTP3JBSWYDPEHPKQ",
    f"backup seed {SEED} for my 2fa app",            # weak word near the run, strong word elsewhere                         # `OTP` + digit inside the run counts as the seed word
    "otpauth://totp/Example:alice@example.com?secret=JBSWY3DPEHPK3PXP&issuer=Example",  # review R1 (#1555)
    "otpauth://hotp/AcmeCorporation:a.long.user.name@example.com?counter=1&secret=JBSWY3DPEHPK3PXP",
]
SAFE = [
    "base64urlencoded sha256checksumfile",           # identifiers in a dev draft, no seed word
    "I prefer short answers in the morning",
    "use the authenticator app on my phone",         # seed word, no base32 run
    "seed the database with ipv4address rows",       # run under 16 chars
    "the 2fa commit was deadbeefcafe2345abcd",       # plain hex next to a seed word
    f"הקבוצה מפסידה {SEED}",                         # סיד inside מפסידה is not a seed word
    "OTP SETUP: INTERNATIONALIZATION",               # review R1: digit-free upper-case word next to a seed word
    "TOTP IS GREAT FOR ACCOUNTABILITYCHECKS",        # review R1: shouted prose
    "my totp app: internationalization",             # digit-free lower-case word
    "the secret to good coffee is fresh beans",      # `secret` alone, no base32 run
    "seed the database with base64urlencoded rows",  # review R2 (#1555): weak word `seed` with no strong word
    "set client_secret to user2authorization",       # review R2 (#1555): weak word `secret` with no strong word
]


@pytest.mark.parametrize("text", LEAKY)
def test_seed_next_to_a_seed_word_is_sensitive(text):
    assert looks_sensitive(text)


@pytest.mark.parametrize("text", SAFE)
def test_prose_and_identifiers_stay_safe(text):
    assert not looks_sensitive(text)


def test_seed_word_further_than_20_chars_does_not_count():
    assert not looks_sensitive("totp and other unrelated words " + SEED)


def test_word_adjacent_16_char_seed_rate_is_high():
    rng = random.Random(1516)
    seeds = ["".join(rng.choice(B32) for _ in range(16)) for _ in range(400)]
    for case in (str.upper, str.lower):
        hits = sum(looks_sensitive(f"my totp seed: {case(s)}") for s in seeds)
        assert hits / len(seeds) >= 0.9              # only digit-free seeds (~4%) pass; see the known miss below


def test_known_miss_digit_free_seed():
    # Follow-up #1559. Trade-off (review R1): a digit-free run is indistinguishable from a long word, so it passes in either case.
    assert not looks_sensitive("my totp seed: JBSWYDPEHPKAPXPQ")


def test_known_miss_spaced_group_seed():
    # Follow-up #1559: groups shorter than 16 chars each are not joined; flip this pin when #1559 lands.
    assert not looks_sensitive("my totp seed: JBSW Y3DP EHPK 3PXP")


def test_known_fail_safe_identifier_with_a_digit_next_to_a_strong_seed_word_is_refused():
    assert looks_sensitive("totp: seed the database with base64urlencoded rows")


# ---------------------------------------------------------------- write paths (review R2)
def test_preferences_refuse_a_seed_and_store_nothing(tmp_path):
    prefs = Preferences(open_db(tmp_path), clock=lambda: 1000.0)
    with pytest.raises(ValueError, match="sensitive"):
        prefs.suggest(f"remember my 2fa seed {SEED}", f"my 2fa seed {SEED}")
    assert prefs.items() == []


def test_goals_refuse_a_seed_and_store_nothing(tmp_path):
    g = goals.Goals(open_db(tmp_path), clock=lambda: 1000.0)
    with pytest.raises(ValueError, match="sensitive"):
        g.add("set up the router", details=f"totp seed {SEED}")
    with pytest.raises(ValueError, match="sensitive"):
        g.add("set up the router", steps=[f"type otp {SEED}"])
    assert g.list() == []


def test_goal_plans_refuse_a_seed_and_store_nothing(tmp_path):
    st = goal_plans.PlanStore(open_db(tmp_path))
    with pytest.raises(ValueError, match="sensitive"):
        st.propose(f"2fa seed {SEED}", [{"tool": "noop"}])
    with pytest.raises(ValueError, match="sensitive"):
        st.propose("login", [{"tool": "noop", "why": f"authenticator {SEED}"}])
    assert st.list() == []


def test_development_draft_refuses_a_seed_and_stores_nothing(tmp_path):
    st = Store(tmp_path / "store.db")
    diag_db.ensure(st)
    with pytest.raises(ValueError, match="sensitive"):
        devmanager.suggest(st, "Fix login", f"my totp seed is {SEED}")
    assert devmanager.listing(st) == []
