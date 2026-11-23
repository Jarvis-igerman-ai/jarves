"""#1099: looks_sensitive refuses key/token-shaped text (vendor token shapes, credential words, high-entropy runs)."""
import pytest

from jarves.preferences import looks_sensitive

LEAKY = [
    "my key is sk-proj-AbCdEfGhIjKlMnOp",                    # OpenAI-style key (shared logs.has_secret shapes)
    "use ghp_abcdefghijklmnopqrstuvwxyzABCD",                 # GitHub token
    "aws AKIAIOSFODNN7EXAMPLE please",                        # AWS key id
    "the token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.abc",  # JWT
    "remember my api key for later",                          # credential word, value not shown
    "my access token is in the drawer",
    "save the client secret too",
    "keep my private key safe",
    "use bearer auth with that value",
    "תזכור את הטוקן שלי",                                      # Hebrew "token"
    "המפתח הסודי נמצא אצלי",                                   # Hebrew "secret key"
    "the value Zq8LmP2vT9xRk4NwB7yHc3Fd",                     # 24-char mixed-case + digit high-entropy run
]

SAFE = [
    "I prefer short answers in the morning",
    "fix a3f9c2e1b7d4f6a8c0e2b4d6f8a1c3e5b7d9f0a2",           # 40-char git SHA (one case only)
    "call HandleUserRequestV2Async in the handler",           # long camelCase identifier, low entropy
    "use fewer tokens in long answers",                       # plain word "tokens" is not a credential word
    "keep the keyboard shortcuts simple",
    "internationalization",
]


@pytest.mark.parametrize("text", LEAKY)
def test_key_and_token_shapes_are_refused(text):
    assert looks_sensitive(text)


@pytest.mark.parametrize("text", SAFE)
def test_ordinary_text_is_not_refused(text):
    assert not looks_sensitive(text)


def test_non_string_input_is_safe_not_a_crash():
    assert looks_sensitive(None) is False
