"""#1099: preferences.looks_sensitive must refuse key/token-shaped text, not only passwords and long numbers."""
import pytest

from jarves import logs
from jarves import preferences as prefs_mod
from jarves.userdata import open_db

KEYS = [
    "my key is sk-abcDEFghiJKLmnoPQR",                                  # OpenAI-style key (logs.has_secret shape)
    "use ghp_abcdefghijklmnopqrstuvwxyz0123456789 for github",          # GitHub token
    "aws AKIAABCDEFGHIJKLMNOP please",                                  # AWS key id
    "remember Zx9fQ2LmB7vT4kRw8YpN3sHd",                                # unprefixed high-entropy string
    "Remember aB3xY7qL9mN2pR5tV8wZ1kC4",                                # random mixed case, not camelCase words
    "remember aB3x-Kq9Z_7mPw2-Lr8T/xY4v",                               # base64url key scored whole
    "remember AbCdEfGhIjKl-mnopQRst-wxyz12",                            # mixed-case parts are not words
    "my api key is hunter2-x",
    "the client secret: abc",
    "store this access token = qwerty",
    "my secret key is open_sesame",
    "private key: begin here",
    "access key was hello-w0rld",
    "api keys: abc",
    "set openai_api_key: abc",
    "set my_token: abc",
    "github_token=abc",
    "my github token for work is abc12345",
    "the token for my work github account is abc12345",
    "my openai key is abc12345",
    "הטוקן שלי הוא abc12345",
    "מפתח API הוא abc12345",
    "מפתח הגישה שלי: abc",
    "מפתח סוד זה abc12345",
    "remember 4f9c2b7e8a1d3f6c9e0b5a7d2c4e6f81",                        # 32 hex chars: HMAC/API key
    "my hmac 9fceb02d0ae598e95dc970b74767f19372d61af8",
    "remember " + "ab12cd34" * 8,
    # review of #1690 R1: a broad word such as fix/rev/reset/checkout before 40 hex chars is no commit context
    "fix 9fceb02d0ae598e95dc970b74767f19372d61af8",
    "reset 9fceb02d0ae598e95dc970b74767f19372d61af8",
    "checkout 9fceb02d0ae598e95dc970b74767f19372d61af8",
    # review of #1690 R2: a bare "secret" with a value
    "my secret is xyz9876",
    "secret: hunter2",
    "client_secret=abc",
    # review of #1711 R1: a long hex key in dash-separated groups is judged by its joined length
    "remember " + "-".join(["4f9c2b7e8a1d3f6c"] * 4),
    # review of #1711 R2: "my ... key/token/secret is X" refuses any 6+ char value, digits or not
    "my api key is hunterfoobar",
    "my token is qwertyuiop",
    "הטוקן שלי הוא abcdefgh",
]
PLAIN = [
    "I prefer short answers in the morning",
    "אני מעדיף תשובות קצרות בבוקר",
    "play jazz while I cook dinner",
    "supercalifragilisticexpialidocious songs",
    "meeting reminders 15 minutes before",
    "fix token refresh expiry",
    "move the API key loader to settings",
    "rotate the client secret next week",
    "keep it a secret from the kids",
    "my secret is safe with you",
    "לתקן את רענון הטוקן",
    "לזהות טוקן חדש",
    "revert commit 9fceb02d0ae598e95dc970b74767f19372d61af8",
    "cherry-pick 9fceb02d0ae598e95dc970b74767f19372d61af8",
    "check build 9fceb02d0ae598e95dc970b7",                              # 24-char hex (short SHA)
    "ticket 3f2a9c1e-7b4d-4e8a-9c2f-1a5b7d9e3c6f",                       # UUID
    "rename test_prefs_sensitive_1099 to test_keys_42",
    "the token is expired after an hour",
    "the access key was rotated.",
    "the token is well-known",
    "the key is under the mat",
    "rename handleOAuth2CallbackResponse in the auth module",
    "deploy my-service-v2-deployment-config-sha256 today",
    "rename Build_Release_Pipeline_Config_v2 to BuildPipeline",
    "play the Rapi Key: Live album",
    "api keyboard: logitech",
    "private keynote: monday",
    "my favourite token is the blue one",                                # possessive, short value: not a key
    "the api key is hunterfoobar",                                       # not possessive: digit rule still applies
]


@pytest.mark.parametrize("text", KEYS)
def test_key_and_token_shaped_text_is_sensitive(text):
    assert prefs_mod.looks_sensitive(text) is True


@pytest.mark.parametrize("text", PLAIN)
def test_ordinary_preferences_are_not_sensitive(text):
    assert prefs_mod.looks_sensitive(text) is False


def test_known_token_shapes_come_from_the_shared_leak_check():
    """Review of #1690 R4: the sk-... case has no digit, so only logs.has_secret catches it; pin that dependency."""
    assert logs.has_secret("sk-abcDEFghiJKLmnoPQR")
    assert prefs_mod._keylike("my key is sk-abcDEFghiJKLmnoPQR") is False


def test_non_string_input_does_not_crash():
    assert prefs_mod.looks_sensitive(None) is False
    assert prefs_mod.looks_sensitive(12345678) is True                  # a long number stays sensitive


def test_suggest_refuses_a_key_and_stores_nothing(home):
    p = prefs_mod.Preferences(open_db(home))
    with pytest.raises(ValueError):
        p.suggest("my token is Zx9fQ2LmB7vT4kRw8YpN3sHd", "I said Zx9fQ2LmB7vT4kRw8YpN3sHd")
    assert p.items("proposed") == []


def test_goal_carrying_a_token_is_refused_and_stores_nothing(home):
    from jarves import goals as goals_mod
    g = goals_mod.Goals(open_db(home))
    with pytest.raises(ValueError):
        g.add("set up github", "my github_token=abc")
    assert g.add("fix token refresh expiry")["title"] == "fix token refresh expiry"
    assert len(g.list()) == 1
