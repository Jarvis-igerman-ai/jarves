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
    "the client secret: abc123xyz",                                # keyword + value: client secret
    "store this access token = qwerty12",                          # keyword + value: access token
    "my secret key is open_sesame",                                # keyword + value: secret key
    "private key: MIIEvQIBAD4N",                                   # keyword + value: private key
    "access key was hello-w0rld",                                  # keyword + value: access key
    "my github token for work is abc12345",                             # up to two words between keyword and value
    "הטוקן שלי הוא abc12345",                                           # Hebrew: token
    "מפתח API הוא abc12345",                                            # Hebrew: API key
    "מפתח הגישה שלי: abc123",                                      # Hebrew: access key
    "מפתח סוד זה abc12345",                                             # Hebrew: secret key
    # #1099 review 3: up to five words between keyword and a spoken value; a bare "key" with a value counts
    "the token for my work github account is abc12345",
    "my openai key is abc12345",
    "Remember aB3xY7qL9mN2pR5tV8wZ1kC4",                                # random mixed case, not camelCase words
    # #1099 review 4 R2: a base64url/slash key split into short pieces is still one key
    "Zx9fQ2LmB7-vT4kRw8YpN_3sHdQ2LmB7vT",
    "keep aB3xY7qL9m/N2pR5tV8wZ/1kC4dE6fG",
    # #1630 review: a dotted value (R1), a bare 32-hex key (R2), an alternating lowercase key (R3)
    "my api key is ab.cd1234ef",
    "my api key is ab.cd1234ef.",
    "remember 3f9a1c7e5b2d4f6a8c0e1b3d5f7a9c2e",
    "remember ab12cd34ef56gh78ij90kl",
    "token: $abc123XYZ",                                              # "$" plus a random value is no env name
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
    # #1099 review 4 R1: a colon after a token word is not a value unless the value looks like a credential
    "token refresh: fix it",
    "Tokens: rotate weekly",
    "the token at https://example.com/docs/a1 explains it",
    "api key: see https://example.com/k1",
    # #1099 review 4 R2: identifiers and paths with separators stay allowed
    "edit changelog.d/2026-11-22-intake-1099.md",
    "open github.com/Jarvis-igerman-ai/jarves/pull/1586",
    "run SOME_CONSTANT_NAME_V2 and my-feature-branch-2026",
    # #1630 review R4: env-var references and encoding/hash names are not values
    "the api key is base64-encoded",
    "the token is SHA256",
    "set token: $GITHUB_TOKEN in CI",
    "set token: ${GITHUB_TOKEN} in CI",
    "the access key is utf-8.",
    # #1630 review R2/R3: canonical UUIDs, 64-hex SHAs and camelCase names stay allowed
    "id 3F2A9C1E-7B4D-4E8A-9C2F-1A5B7D9E3C6F",
    "sha256 9f86d0a1b8c4c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f0a0e",
    "call getUserPrefsFromCache42Fast here",
    "see github.com/x/jarves/commit/9fceb02d0ae598e95dc970b74767f19372d61af8",
]


@pytest.mark.parametrize("text", KEYS)
def test_key_and_token_shaped_text_is_sensitive(text):
    assert prefs_mod.looks_sensitive(text)


@pytest.mark.parametrize("text", PLAIN)
def test_ordinary_preferences_are_not_sensitive(text):
    assert not prefs_mod.looks_sensitive(text)


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
