"""#1099: preferences.looks_sensitive must refuse key/token-shaped text (keywords, known token shapes, high-entropy runs)."""
import pytest

from jarves.preferences import looks_sensitive

LEAKY = [
    "my api key is abc",                                         # keyword: api key
    "remember my API_KEY for the weather service",
    "my github access token for the account",                    # keyword: access token
    "token = abc for the weather",                               # labelled value
    "keep this secret: hunter",                                  # labelled value
    "the OAuth client secret",
    "my private key lives in the drawer",
    "the access key for the cloud",
    "שמור את הטוקן שלי",                                          # Hebrew: token
    "מפתח API של מזג האוויר",                                     # Hebrew: API key
    "use sk-proj-AbCdEfGhIjKlMnOp for answers",                  # OpenAI-shaped key (known token shape)
    "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4",                     # GitHub token shape
    "AKIA" + "ABCDEFGHIJKLMNOP",                                 # AWS key id
    "prefer Zx9Qw3Er7Ty1Ui5Op2As8Df4Gh6Jk",                      # unlabelled high-entropy string
    "aGVsbG8td29ybGQtMTIzNDU2Nzg5MGFiY2RlZg==",                  # base64-looking blob
]
SAFE = [
    "short answers in the morning",
    "I prefer jazz while cooking",
    "תשובות קצרות בבוקר",
    "call me by my first name",
    "plan a secret birthday party",                            # ordinary word, not a credential
    "secret-ish title quokka",
    "internationalization-friendly formatting",               # long word, no digits: not a key
    "play the playlist morning-chill-2024",                    # short mixed run: not high entropy
    "open 2fa-backup-codes-for-office365 notes last",          # long dashed word slug: not a key
    "videos like youtube.com/watch?v=dQw4w9WgXcQ",             # short id in a URL
]


def test_suggest_refuses_token_text_and_stores_nothing(tmp_path):
    from jarves import preferences as prefs_mod
    from jarves.userdata import open_db
    p = prefs_mod.Preferences(open_db(tmp_path))
    with pytest.raises(ValueError):
        p.suggest("use Zx9Qw3Er7Ty1Ui5Op2As8Df4Gh6Jk for the weather", "owner said so")
    assert p.items("proposed") == []


@pytest.mark.parametrize("text", LEAKY)
def test_key_or_token_shaped_text_is_sensitive(text):
    assert looks_sensitive(text)


@pytest.mark.parametrize("text", SAFE)
def test_ordinary_preferences_are_not_sensitive(text):
    assert not looks_sensitive(text)
