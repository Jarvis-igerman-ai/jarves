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
]
SHA = "ab17fe59578af69d56815eb69b4dbcb72a864efe"   # a real 40-hex commit id of this branch
SAFE_DRAFTS = [
    (f"revert commit {SHA}", f"it broke the build; see {SHA[:7]}"),
    ("bump jarves to v4.0.1-rc2", "edit core/src/jarves/goals.py and pyproject.toml"),
    ("crash in release_notes_v4_0_1_final", "trace in C:/work/jarves/core/src/jarves/devmanager.py line 50"),
]


@pytest.mark.parametrize("text", LEAKY)
def test_key_and_token_shaped_text_is_sensitive(text):
    assert prefs_mod.looks_sensitive(text)


@pytest.mark.parametrize("text", SAFE)
def test_ordinary_preferences_are_not_sensitive(text):
    assert not prefs_mod.looks_sensitive(text)


@pytest.mark.parametrize("token", [JWT, AWS])
def test_vendor_shapes_are_caught_through_has_secret_only(token):
    assert not prefs_mod._SENSITIVE.search(token)
    assert not any(prefs_mod._high_entropy(m) for m in prefs_mod._TOKENISH.findall(token))
    assert prefs_mod.looks_sensitive(f"remember {token}")


def test_token_shaped_suggestion_is_refused_and_not_stored(home):
    p = prefs_mod.Preferences(open_db(home))
    with pytest.raises(ValueError, match="sensitive"):
        p.suggest(f"remember {RANDOM}", "the owner said it", by="voice")
    assert p.items() == []


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
