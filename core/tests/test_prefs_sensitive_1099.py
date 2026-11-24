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
    # #1099 review R1: hex secrets (non-SHA length, or SHA length next to a key word)
    "remember 5f4dcc3b5aa765d61d8327deb882cf99",                        # 32 hex
    "api secret 9fceb02d0ae598e95dc970b74767f19372d61af8",              # 40 hex beside a key word
    "המפתח שלי 9fceb02d0ae598e95dc970b74767f19372d61af8",               # Hebrew key word beside 40 hex
    # #1099 review R2 decision: "keyword: <plain word>" fails closed (a dev task can drop the colon)
    "fix token: refresh expiry",
    "tokens: rotate weekly",
    # #1099 review 4 (R2, R3): a bare 4+ digit PIN after "is"; a key word up to five words before SHA-length hex
    "the key is 12345",
    "my token is 4821.",
    "api secret for the staging cluster is 9fceb02d0ae598e95dc970b74767f19372d61af8",
    # #1099 review 3: camelCase-shaped key with two number chunks; a long random ID inside a share link
    "remember AbcdEfgh12IjklMnop34Qrst",                               # camelCase-shaped, two number chunks
    "open https://docs.google.com/document/d/1aB2cD3eF4gH5iJ6kL7mN8oP9qR0sT/edit",
    # #1099 review 5 R1: camelCase-shaped random string with one number chunk (chunks without a vowel)
    "remember AbcdEfghIjklMnop1Qrst",
    # #1099 review 5 R3: a short spoken value with inner punctuation (dot, comma, colon)
    "my token is ab.cd12ef",
    "the api key is x1,y2z3w",
    "my token is ab:cd12ef",                                            # review 6 R1: the colon case
    # #1099 review 5 R2 decision: a SHA with six or more digits in a row is refused by the older long-number rule
    "revert 9fceb0212345678598e95dc970b74767f19372d61a",
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
    "the token is 5 minutes old",                                  # a short count is not a PIN
    # #1099 review R1: git SHA lengths without a key word, and low-entropy hex, stay allowed
    "use sha256 6b86b273ff34fce19d6b804eff5a3f5747ada4eaa22f1d49c01e52ddb7875b4b",
    "merged as 9fceb02d0ae598e95dc970b74767f19372d61af8 yesterday",
    "abababababababababababababab1a1a1a",                          # low-entropy hex filler
    # #1099 review 3: Hebrew key words are whole words ("יסודי" is no "סוד"); a typical URL is not a key
    "בדיקה יסודית של 9fceb02d0ae598e95dc970b74767f19372d61af8",
    "play https://www.youtube.com/watch?v=dQw4w9WgXcQ in the kitchen",  # typical URL with an 11-character ID is not a key
    # #1099 review 5 R2: "=" is no part of a key segment, so key=<git SHA> stays a git SHA
    "open https://example.com/commit?v=9fceb02d0ae598e95dc970b74767f19372d61af8",
    "lock the build to sha=9fceb02d0ae598e95dc970b74767f19372d61af8",
    "call parseHttp2ResponseHeaders on start",                     # review 5 R1: abbreviations in a real name
    # #1099 review 5 R3: a sentence-ending word or a short number after is/was is still no value
    "the token is valid. see the docs",
]


@pytest.mark.parametrize("text", KEYS)
def test_key_and_token_shaped_text_is_sensitive(text):
    assert prefs_mod.looks_sensitive(text)


@pytest.mark.parametrize("text", PLAIN)
def test_ordinary_preferences_are_not_sensitive(text):
    assert not prefs_mod.looks_sensitive(text)


def test_gate_relies_on_log_token_shapes():
    """#1099 review 6 R2: looks_sensitive reuses logs.has_secret; pin the shapes the gate expects from it."""
    for key in ("sk-abcDEFghiJKLmnoPQR", "ghp_abcdefghijklmnopqrstuvwxyz0123456789", "AKIAABCDEFGHIJKLMNOP"):
        assert prefs_mod.logs.has_secret(key) and prefs_mod.looks_sensitive(key)


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
