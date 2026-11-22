"""#1099: preferences.looks_sensitive must refuse key/token-shaped text, not only passwords and long numbers."""
import pytest

from jarves import preferences as prefs_mod
from jarves.userdata import open_db

KEYS = [
    "my key is sk-abcDEFghiJKLmnoPQR",                       # OpenAI-style key (shared token shapes)
    "use ghp_abcdefghijklmnopqrstuvwxyz0123 for github",           # GitHub token
    "aws AKIAABCDEFGHIJKLMNOP please",                             # AWS key id
    "remember Zx9fQ2LmB7vT4kRw8YpN3sHd",                           # unprefixed high-entropy string
    "remember my api key",                                         # keyword: api key
    "the client secret for the app",                               # keyword: client secret
    "store this access token for me",                              # keyword: access token
    "תזכור את הטוקן שלי",                                          # Hebrew keyword: token
]
PLAIN = [
    "I prefer short answers in the morning",
    "אני מעדיף תשובות קצרות בבוקר",
    "play jazz while I cook dinner",
    "call me by my first name",
    "supercalifragilisticexpialidocious songs",                    # long word, no digits: not a key
    "meeting reminders 15 minutes before",
]


@pytest.mark.parametrize("text", KEYS)
def test_key_and_token_shaped_text_is_sensitive(text):
    assert prefs_mod.looks_sensitive(text)


@pytest.mark.parametrize("text", PLAIN)
def test_ordinary_preferences_are_not_sensitive(text):
    assert not prefs_mod.looks_sensitive(text)


def test_non_string_input_does_not_crash():
    assert prefs_mod.looks_sensitive(None) is False
    assert prefs_mod.looks_sensitive(12345678) is True                  # a long number stays sensitive


def test_suggest_refuses_a_key_and_stores_nothing(home):
    p = prefs_mod.Preferences(open_db(home))
    with pytest.raises(ValueError):
        p.suggest("my token is Zx9fQ2LmB7vT4kRw8YpN3sHd", "I said Zx9fQ2LmB7vT4kRw8YpN3sHd")
    assert p.items("proposed") == []
