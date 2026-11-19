"""#1099: preferences.looks_sensitive refuses key/token-shaped text (keyword list + high-entropy rule)."""
import pytest

from jarves.preferences import looks_sensitive


@pytest.mark.parametrize("text", [
    "remember my api key",
    "my OpenAI API-key is in the drawer",
    "save the access token for later",
    "my client_secret is on the fridge",
    "keep my private key safe",
    "מפתח API שלי",
    "הטוקן שלי נמצא במגירה",
    "use sk-abcDEF1234567890xyz for requests",
    "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2",
    "the code is Zq8vX2mK9pLr4TnW7yBc3HdF",          # random-looking mixed string, no known prefix
    "AKIA" + "ABCDEFGHIJKLMNOP",
])
def test_key_or_token_shaped_text_is_sensitive(text):
    assert looks_sensitive(text)


@pytest.mark.parametrize("text", [
    "I prefer short answers in the morning",
    "אני אוהב מוזיקה שקטה בערב",
    "token refresh fails after the update",             # devmanager draft wording must still save
    "keep secrets out of the logs",
    "core/src/jarves/preferences_module_file.py",
    "internationalization_documentation_pages",          # long word, letters only
    "wake me at 7 on weekdays",
])
def test_ordinary_text_is_not_sensitive(text):
    assert not looks_sensitive(text)
