"""#1099: preferences.looks_sensitive must refuse key/token-shaped text, not only passwords and long numbers."""
import pytest

from jarves import devmanager as dev_mod, goals as goals_mod, logs, preferences as prefs_mod
from jarves.store import Store
from jarves.userdata import open_db

# Built at runtime so the repo's own secret scanners do not flag fake fixtures.
OPENAI = "sk-" + "proj" + "A1b2C3d4E5f6G7h8J9k0"
GITHUB = "ghp" + "_" + "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg0Fe"
RANDOM = "Qm7vX2pL9sK4tR8wZ1nB6yH3cJ5dF0gA"       # no vendor prefix: caught by the high-entropy rule
# vendor shapes known to logs.has_secret(); JWT and AWS match neither the keywords nor the high-entropy rule
JWT = "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiJvd25lciJ9" + ".c2lnbmF0dXJlLXZhbHVl"
AWS = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
PEM = "-----BEGIN " + "RSA PRIVATE" + " KEY-----\nabc\n-----END RSA PRIVATE KEY-----"

LEAKY = [
    f"use the key {OPENAI} for my scripts",
    f"my github is {GITHUB}",
    f"remember {RANDOM}",
    f"the router login is {JWT}",
    f"use {AWS} for backups",
    f"keep this: {PEM}",
    "my api key is in the drawer",
    "save my access token for the router",
    "the client secret goes in the config",
    "my private key is on the usb stick",
    "תזכור את ה-API key שלי",
    "תשמור את הטוקן שלי",
    "שמור את מפתח ה-API שלי",                 # Hebrew only: no English "api key" to lean on
    "המפתח הפרטי שלי נמצא בכונן",
    "send the bearer token with every call",
    "send bearer Ab12Cd34Ef56Gh78 with every call",   # bearer value branch (review of #1489 @ ca5c6b0, R2)
    "Authorization: Bearer Ab12Cd34Ef56Gh78",
    "export OPENAI_API_KEY_FILE before running",   # word end is not "_": variable names stay caught
    "rotate the refresh tokens weekly",
    "send the bearer tokens with every call",      # plural bearer (review of #1489 @ 412fc49, R1)
]
SAFE = [
    "I prefer short answers in the morning",
    "אני מעדיף תשובות קצרות בבוקר",
    "play jazz while I cook dinner",
    "call me Sam, not mister",
    "use metric units and 24h time",
    "summarise news from https://example.com/world/europe",
    "open make-the-coffee-strong-2024 and MyFavouritePlaylist2024",
    "the file release_notes_v4_0_1_final is fine",
    "the bearer of the news was late",
    "my private keyboard layout is Dvorak",      # keyword must end the word (review R1)
    "the secret keyboard shortcut opens notes",
    "access keyboard settings by voice",
    "the laws keys chapter",                     # vendor name must start a word (review of #1489, R1)
]
# review of #1479, R1: vendor names before key/token, and a bare key/token/secret followed by a value
VENDOR_OR_VALUE = [
    "my openai key is in the drawer",            # "openai" ends in "ai", not "api"
    "my github token is on the fridge",
    "export ANTHROPIC_API_KEY before running",
    "the token is Abcdefghijkl1",                # one digit run: the entropy rule does not fire
    "secret: hunter2isGreat",
    "my key = qwertyuiop9",
    "my key is hunter2",                         # 7 chars: the value minimum is 6 (review of #1489, R3)
    "the token: v4.0.1x9Qz",                     # version prefix glued to more value is still refused
    "the token: v4.0.1.Ab9",
]
SAFE_WORDING = [
    "the key is to stay calm",
    "the secret is patience",
    "the secret to good coffee is fresh beans",
    "a token of thanks after dinner",
    "the key is 2024",
    "the key is ab12c",                          # 5 chars: below the value minimum (boundary pin)
    "the token: abcdefghij",                       # value without a digit: not refused (boundary pin)
    "the token: v4.0.1",                         # version string is not a value (review @ 412fc49, R3)
    "the key is 2.0.1-rc2 now",
    "the token: v4.0.1.",
]
SHA = "ab17fe59578af69d56815eb69b4dbcb72a864efe"   # a real 40-hex commit id of this branch
SAFE_DRAFTS = [
    (f"revert commit {SHA}", f"it broke the build; see {SHA[:7]}"),
    ("bump jarves to v4.0.1-rc2", "edit core/src/jarves/goals.py and pyproject.toml"),
    ("crash in release_notes_v4_0_1_final", "trace in C:/work/jarves/core/src/jarves/devmanager.py line 50"),
    # mixed-case path with 3+ digit groups: '/' splits the run before the entropy check (review @ ca5c6b0, R1)
    ("crash in /Users/Owner/Documents/Project2024/v3/file1.py", "seen in /Users/Owner/Builds/Release7x9/run2/Log5"),
]


@pytest.mark.parametrize("text", LEAKY)
def test_key_and_token_shaped_text_is_sensitive(text):
    assert prefs_mod.looks_sensitive(text)


@pytest.mark.parametrize("text", VENDOR_OR_VALUE)
def test_vendor_key_and_bare_key_value_are_sensitive(text):
    assert prefs_mod.looks_sensitive(text)


@pytest.mark.parametrize("text", SAFE_WORDING)
def test_key_and_secret_as_ordinary_words_are_not_sensitive(text):
    assert not prefs_mod.looks_sensitive(text)


@pytest.mark.parametrize("text", SAFE)
def test_ordinary_preferences_are_not_sensitive(text):
    assert not prefs_mod.looks_sensitive(text)


@pytest.mark.parametrize("token", [JWT, AWS])
def test_vendor_shapes_are_caught_through_has_secret_only(token):
    # no entropy assertion here: that would pin _ENTROPY_BITS, not has_secret (review @ ca5c6b0, R3)
    assert not prefs_mod._SENSITIVE.search(token)
    assert logs.has_secret(token)
    assert prefs_mod.looks_sensitive(f"remember {token}")


def test_token_shaped_suggestion_is_refused_and_not_stored(home, monkeypatch):
    seen = []
    monkeypatch.setattr(prefs_mod.logs, "info", lambda *a, **f: seen.append((a, f)))
    p = prefs_mod.Preferences(open_db(home))
    with pytest.raises(ValueError, match="sensitive") as err:
        p.suggest(f"remember {RANDOM}", "the owner said it", by="voice")
    assert p.items() == []
    # the refusal log and error carry only the reason code, never the refused text (review R3)
    assert [a[1] for a, _ in seen] == ["PREF_REFUSED_SENSITIVE"]
    assert RANDOM not in repr(seen) and RANDOM not in str(err.value)


def test_sha_with_six_digit_run_is_still_refused_known_gap_1482():
    # KNOWN GAP (#1482), pinned so it stays visible (the repo forbids xfail): the old \\d{6,} rule refuses a commit
    # SHA containing a 6-digit run, so SAFE_DRAFTS above only proves SHAs without one. Flip this when #1482 lands.
    assert prefs_mod.looks_sensitive("revert commit 3f2a1b9c0d123456ef7890abcdef1234567890ab")


def test_lowercase_hex_key_is_not_refused_known_gap_1482():
    # KNOWN GAP (#1482), pinned on purpose: the entropy rule needs upper AND lower case so commit SHAs stay allowed
    # in dev drafts; a 32-char lowercase hex key without a 6-digit run therefore passes. Flip when #1482 lands.
    hexkey = "9f3a" + "c2e8" + "b71d" + "4e05" + "a6f9" + "d3c1" + "8b2e" + "7f4a"
    assert len(hexkey) == 32 and not prefs_mod.looks_sensitive(f"remember {hexkey}")


def test_goals_share_the_refusal(home):
    # intended: goals use the same check, so a credential mention is refused there too (#1099)
    g = goals_mod.Goals(open_db(home))
    with pytest.raises(ValueError, match="sensitive"):
        g.add("rotate things", steps=["paste my api key into the router"])
    assert g.add("run ten kilometres")["title"] == "run ten kilometres"


def test_dev_drafts_share_the_refusal(home):
    st = Store(home / "store.db")
    with pytest.raises(ValueError, match="sensitive"):
        dev_mod.suggest(st, "store the client secret in the settings page")
    assert dev_mod.listing(st) == []
    st.close()


@pytest.mark.parametrize("title,body", SAFE_DRAFTS)
def test_technical_dev_drafts_are_still_accepted(home, title, body):
    # commit SHAs, versions and paths go through every rule, logs.has_secret included, without a refusal
    assert not logs.has_secret(f"{title} {body}")
    st = Store(home / "store.db")
    assert dev_mod.suggest(st, title, body)["title"].endswith(title)
    st.close()
