"""#1099: preferences.looks_sensitive must refuse key/token-shaped text, not only passwords and long numbers."""
import pytest

from jarves import preferences as prefs_mod
from jarves.userdata import open_db

KEYS = [
    "my key is sk-abcDEFghiJKLmnoPQR",                       # OpenAI-style key (shared token shapes)
    "use ghp_abcdefghijklmnopqrstuvwxyz0123456789 for github",           # GitHub token
    "aws AKIAABCDEFGHIJKLMNOP please",                             # AWS key id
    "remember Zx9fQ2LmB7vT4kRw8YpN3sHd",                           # unprefixed high-entropy string
    "my api key is hunter2-x",                                     # keyword + value: api key
    "the client secret: abc",                                      # keyword + value: client secret
    "store this access token = qwerty",                            # keyword + value: access token
    "my secret key is open_sesame",                                # keyword + value: secret key
    "private key: begin here",                                     # keyword + value: private key
    "access key was hello-w0rld",                                  # keyword + value: access key
    "my github token for work is abc12345",                             # up to two words between keyword and value
    "הטוקן שלי הוא abc12345",                                           # Hebrew: token
    "מפתח API הוא abc12345",                                            # Hebrew: API key
    "מפתח הגישה שלי: abc",                                         # Hebrew: access key
    "מפתח סוד זה abc12345",                                             # Hebrew: secret key
    # #1099 review 3: up to five words between keyword and a spoken value; a bare "key" with a value counts
    "the token for my work github account is abc12345",
    "my openai key is abc12345",
    "Remember aB3xY7qL9mN2pR5tV8wZ1kC4",                                # random mixed case, not camelCase words
    # #1099 review R1: a letters-only passphrase after "is", and a short value pasted right after the keyword
    "my api key is correcthorsebattery",
    "my api key abc123",
    "token x9y8z7w6",
    # #1099 review 2 R1: up to two words between the keyword and a pasted letters+digits value
    "the token for prod a1b2c3d4e5",
    "הטוקן שלי abc12345",
    # #1099 doctor R1: the model-name exemption needs short hyphen parts; a long part is still a pasted value
    "token abc-12345678",
]
PLAIN = [
    "I prefer short answers in the morning",
    "אני מעדיף תשובות קצרות בבוקר",
    "play jazz while I cook dinner",
    "call me by my first name",
    "supercalifragilisticexpialidocious songs",                    # long word, no digits: not a key
    "meeting reminders 15 minutes before",
    # #1099 review: naming a key/token without a value is not a secret (dev tasks and goals use these words)
    "fix token refresh expiry",
    "move the API key loader to settings",
    "rotate the client secret next week",
    "לתקן את רענון הטוקן",
    # #1099 review: identifiers, not keys (the new rules allow them; the older 6-digit rule still applies)
    "revert commit 9fceb02d0ae598e95dc970b74767f19372d61af8",     # 40-char git SHA (pure hex)
    "ticket 3f2a9c1e-7b4d-4e8a-9c2f-1a5b7d9e3c6f",                 # UUID
    "rename test_prefs_sensitive_1099 to test_keys_42",            # snake_case identifier with digits
    # #1099 review 2: Hebrew markers are whole words; a state description is not a value
    "לזהות טוקן חדש",
    "טוקן חדש לזהות",
    "the token is expired after an hour",
    "the access key was rotated.",
    "the api key is valid.",
    # #1099 review 3: a hyphenated state word after is/was is not a value; camelCase names are not keys
    "the token is well-known",
    "the api key is re-issued",
    "rename handleOAuth2CallbackResponse in the auth module",
    "call getUserPreferences2Async on start",
    "the key is under the mat",
    # #1099 review R1/R2: short state words, a short version tag, and a camelCase name with a year are not keys
    "the api key is compromised",
    "the token is automatically refreshed",
    "rename token v2 handler",
    "refactor parseReport2024QuarterlyTotals in reports",
    # #1099 review 2 R2: a count after "tokens:" is not a value
    "limit tokens: 4000 per day",
    # #1099 doctor R1: a hyphenated model or version name of short parts after a key/token word is not a value
    "raise the token limit for claude-3-opus",
    "set max tokens for gpt-4o",
    "the token budget for claude-opus-5-5",
]
# Accepted over-refusal (fails closed, review 2 R2/R3): a word after "token:" and a long upper-case letter+digit code
# are refused; the owner can rephrase ("fix the token refresh bug") and order numbers are not preferences.
OVER_REFUSED = ["fix token: refresh bug", "ORDER2024INVOICE12345"]


@pytest.mark.parametrize("text", KEYS)
def test_key_and_token_shaped_text_is_sensitive(text):
    assert prefs_mod.looks_sensitive(text)


@pytest.mark.parametrize("text", PLAIN)
def test_ordinary_preferences_are_not_sensitive(text):
    assert not prefs_mod.looks_sensitive(text)


@pytest.mark.parametrize("text", OVER_REFUSED)
def test_documented_over_refusal_stays_refused(text):
    assert prefs_mod.looks_sensitive(text)


def test_non_string_input_does_not_crash():
    assert prefs_mod.looks_sensitive(None) is False
    assert prefs_mod.looks_sensitive("my openai key is abc12345") is True   # a bool, not a match object
    assert prefs_mod.looks_sensitive(12345678) is True                  # a long number stays sensitive


def test_suggest_refuses_a_key_and_stores_nothing(home):
    p = prefs_mod.Preferences(open_db(home))
    with pytest.raises(ValueError):
        p.suggest("my token is Zx9fQ2LmB7vT4kRw8YpN3sHd", "I said Zx9fQ2LmB7vT4kRw8YpN3sHd")
    assert p.items("proposed") == []


def test_dev_task_naming_a_token_is_kept_but_one_carrying_a_key_is_refused(home):
    """#1099 review R1: the same gate guards dev drafts; naming a token is fine, pasting one is not."""
    from jarves import devmanager
    from jarves.diag import db as diag_db
    from jarves.store import Store
    st = Store(home / "store.db")
    diag_db.ensure(st)
    assert devmanager.suggest(st, "fix token refresh expiry", "move the API key loader to settings")["status"] == "draft"
    with pytest.raises(ValueError):
        devmanager.suggest(st, "token refresh", "my token is Zx9fQ2LmB7vT4kRw8YpN3sHd")
    assert len(devmanager.listing(st)) == 1
    st.close()
