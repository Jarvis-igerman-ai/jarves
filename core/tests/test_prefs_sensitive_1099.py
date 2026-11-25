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
    # #1099 review 4: R1 a bare "key" with ":"/"=" and a credential-shaped value; R2 base64 with "/" in it
    "my openai key: abc12345",
    "wifi key = Kx7mP2q9",
    "the tokens: 9xK2mQ7p",
    "remember Zx9f/Q2LmB7v/T4kRw8YpN3sHd+",
    # #1099 review 5 R1: a lowercase alphanumeric token with "/" is not a path
    "remember k3j9x2m8q7/p4z1w6v5b0n2",
    # #1099 review 6: R2 a strong word right before ":" still refuses any value; R3 hex or grouped key by a key word
    "my api key: hunter",
    "openai key 5f4dcc3b5aa765d61d8327deb882cf99",
    "wifi key AbC1-xYz9-Qr7T-mN3p-Kd8W",
    "the token for work 5f4dcc3b5aa765d61d8327deb882cf99",
    # #1099 review 7 R1: digit groups that are not a real date or time are a value, not planning text
    "wifi key = 1234-5678-9012",
    "door key: 4821-9933",
    "the key is 13/45/9999",
    "token: 99:99:99",
    # #1099 review 8: R2 the Hebrew bare "key" (מפתח) with a value; R3 a date glued to more text is no date
    "המפתח שלי הוא abc12345",
    "wifi key: 2026-12-01.hunter22",
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
    # #1099 review 4: R4 a weak word (token/key) with ":" needs a credential-shaped value; R2 paths are not keys
    "fix token refresh: done",
    "tokens: 5 left",
    "the key: under the mat",
    "see core/src/jarves/hebrew2parts/voice3module for it",
    # #1099 review 5 R3: OKR-style goal text is not a key
    "key result: Q4-2026 revenue",
    "key points: Q4-2026 plan",
    # #1099 review 6: R1 planning wording with a date, time or quarter; R2 dev tasks naming a strong word
    "key dates: 2026-12-01",
    "key milestone: Q4-2026",
    "the key meeting is 10:30am",
    "key dates: 31/12/2026",
    "key call: 9:15pm",
    "key dates: 2026-12-01.",
    # #1099 review 8 R1: keyboard shortcuts are not credentials
    "the key is ctrl+space",
    "shortcut key: ctrl+alt",
    "use the key ctrl+shift+k",
    "fix api key loader: done",
    "client secret rotation: done",
    "fix key loader in commit 9fceb02d0ae598e95dc970b74767f19372d61af8",   # SHA more than two words after "key"
    "remember 5f4dcc3b5aa765d61d8327deb882cf99",                    # bare hex, no key word: allowed (git SHA)
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


def test_goal_carrying_a_key_is_refused_and_stores_nothing(home):
    """#1099 review R3: goals use the same gate; a goal naming a token is kept, one carrying a key is refused."""
    from jarves import goals as goals_mod
    g = goals_mod.Goals(open_db(home))
    with pytest.raises(ValueError):
        g.add("my token is Zx9fQ2LmB7vT4kRw8YpN3sHd")
    with pytest.raises(ValueError):
        g.add("rotate keys", "wifi key = Kx7mP2q9")
    assert g.list() == []
    assert g.add("fix token refresh expiry")["title"] == "fix token refresh expiry"
