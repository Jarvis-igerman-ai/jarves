"""#1099: preferences.looks_sensitive must refuse key/token-shaped text, not only passwords and long numbers."""
import pytest

from jarves import preferences as prefs_mod
from jarves.userdata import open_db

# Built at runtime so the repo's own secret scanners do not flag fake fixtures.
OPENAI = "sk-" + "proj" + "A1b2C3d4E5f6G7h8J9k0"
GITHUB = "ghp" + "_" + "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg0Fe"
RANDOM = "Qm7vX2pL9sK4tR8wZ1nB6yH3cJ5dF0gA"       # no vendor prefix: caught by the high-entropy rule

LEAKY = [
    f"use the key {OPENAI} for my scripts",
    f"my github is {GITHUB}",
    f"remember {RANDOM}",
    "my api key is in the drawer",
    "save my access token for the router",
    "the client secret goes in the config",
    "my private key is on the usb stick",
    "תזכור את ה-API key שלי",
    "תשמור את הטוקן שלי",
]
SAFE = [
    "I prefer short answers in the morning",
    "אני מעדיף תשובות קצרות בבוקר",
    "play jazz while I cook dinner",
    "call me Igal, not mister",
    "use metric units and 24h time",
    "summarise news from https://example.com/world/europe",
]


@pytest.mark.parametrize("text", LEAKY)
def test_key_and_token_shaped_text_is_sensitive(text):
    assert prefs_mod.looks_sensitive(text)


@pytest.mark.parametrize("text", SAFE)
def test_ordinary_preferences_are_not_sensitive(text):
    assert not prefs_mod.looks_sensitive(text)


def test_token_shaped_suggestion_is_refused_and_not_stored(home):
    p = prefs_mod.Preferences(open_db(home))
    with pytest.raises(ValueError, match="sensitive"):
        p.suggest(f"remember {RANDOM}", "the owner said it", by="voice")
    assert p.items() == []
