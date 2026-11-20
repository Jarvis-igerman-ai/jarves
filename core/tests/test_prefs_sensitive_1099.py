"""#1099: preferences.looks_sensitive must refuse key/token-shaped text (keywords and high-entropy strings).

Fake secrets are assembled at run time so secret scanners do not flag this file; none is a real credential."""
import pytest

from jarves import preferences as prefs_mod
from jarves.userdata import open_db

_TAIL = "Q7xLm2Vb9KpR4tZw8NcH3yFd"
SECRET_TEXTS = [
    "my key is " + "sk" + "-proj-" + _TAIL,                     # OpenAI-style key
    "use " + "gh" + "p_" + _TAIL + "1234",                       # GitHub token
    "AK" + "IA" + "Q7XLM2VB9KPR4TZW",                            # AWS access key id
    "here: " + _TAIL + "a8e4",                                   # bare high-entropy string, no prefix
    "d41d8cd98f00b204e9800998ecf8427e",                          # 32-char hex secret
    "remember my api key please",
    "the access token for the router",
    "store my secret key",
    "-----BEGIN " + "PRIVATE KEY-----",
    "תזכור את הטוקן שלי",
]
SAFE_TEXTS = [
    "I prefer short answers in the morning",
    "internationalization documentation preferred",              # long word, no digits
    "play jazz after 8pm on weekdays",
    "תשובות קצרות בבוקר",
    "reduce token usage in long chats",                          # the word 'token' alone is not a secret
    "version 2026-11-20 notes",
]


@pytest.mark.parametrize("text", SECRET_TEXTS)
def test_key_or_token_shaped_text_is_sensitive(text):
    assert prefs_mod.looks_sensitive(text)


@pytest.mark.parametrize("text", SAFE_TEXTS)
def test_ordinary_preferences_are_not_sensitive(text):
    assert not prefs_mod.looks_sensitive(text)


def test_suggest_refuses_and_stores_nothing_for_a_bare_token(home):
    p = prefs_mod.Preferences(open_db(home))
    with pytest.raises(ValueError):
        p.suggest("remember " + _TAIL + "a8e4", "remember " + _TAIL + "a8e4", by="voice")
    assert p.items("proposed") == []
