"""#1099: looks_sensitive refuses key/token-shaped text (vendor token shapes, credential words, high-entropy runs)."""
import pytest

from jarves import logs
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
    "remember my api token",
    "store the api secret here",
    "the secret token is on the fridge",
    "copy my session token",
    "my credentials are in the notebook",
    "set OPENAI_API_KEY to the value I gave you",             # env-var style name: "_" is no boundary (review R1)
    "MY_SECRET_KEY is in the notes",
    "export GITHUB_ACCESS_TOKEN first",
    "תזכור את הטוקן שלי",                                      # Hebrew "the token", one prefix letter
    "טוקן",
    "המפתח הסודי נמצא אצלי",                                   # Hebrew "secret key"
    "המפתח שלי נמצא במגירה",                                   # Hebrew "my key"
    "שמור את מפתח פרטי של השרת",                               # Hebrew "private key" (review 3 R3)
    "המפתח הפרטי בקובץ",                                       # Hebrew "the private key"
    "תשמור את מפתח api של השירות",                             # Hebrew "api key"
    "the value Zq8LmP2vT9xRk4NwB7yHc3Fd",                     # 24-char mixed-case + digit high-entropy run
    "the value PCB9t2039bicBTW5ZE9LFaez",                     # few class switches, near-maximal entropy
    "the value dGhpc0lzQVNlY3JldEtleTEyMzQ1Njc4OQ==",         # base64 key with "==" padding
    "the value Zq8LmP2vT9xRk4NwB7yHc3Fd==",
]

SAFE = [
    "I prefer short answers in the morning",
    "fix a3f9c2e1b7d4f6a8c0e2b4d6f8a1c3e5b7d9f0a2",           # 40-char git SHA (one case only)
    "call HandleUserRequestV2Async in the handler",           # long camelCase identifier, low entropy
    "use fewer tokens in long answers",                       # plain word "tokens" is not a credential word
    "תשתמש בפחות טוקנים בתשובות ארוכות",                       # same in Hebrew: "טוקנים" (tokens) is allowed (review R2)
    "set max_tokens lower",
    "keep the keyboard shortcuts simple",
    "internationalization",
    "the bearer of bad news",                                 # bare "bearer" is not a credential word
    "open core/src/jarves/guides/data/Feature2.toml",         # path: separators are not class switches
    "slug my-Post-2026-Title-Draft-v3-final",                 # dashed slug with digits
    "call getUserProfileByIdV2Handler next",                  # identifiers stay under the entropy rule
    "parse it with XMLHttpRequest2ResponseParser",
    "use GPT4oMiniRealtimePreview2024 for voice",
    "see docs/reviews/SWV4_P_PERF_BUDGETS first",            # path segments are short, not near-max entropy
    "use claude-sonnet-5-5 or gpt-4o-mini-transcribe",       # model ids (review 3 R4)
    "call AbstractSingletonProxyFactoryBean2 there",
    "set NSURLSessionDataTaskDelegate3 in iOS",
    "run test_prefs_sensitive_1099 then test_v5_followups",
    "the class WebRTCPeerConnectionFactory2 owns it",
    "rename it to getHTTP2ConnectionPoolMaxSize",
    "the value Zq8LmP2vT9xRk4NwB7yHc3F",                      # 23-char run: one under the 24-char limit
    "the value Zq8LmP2vT9xRk4NwB7yHc3==",                     # padding is not counted toward the 24 chars
]

# Caught only through the shared logs.has_secret patterns: if logs.py narrows one, this names the lost shape.
VENDOR = ["sk-proj-AbCdEfGhIjKlMnOp", "ghp_abcdefghijklmnopqrstuvwxyzABCD", "AKIAIOSFODNN7EXAMPLE",
          "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.abc"]


@pytest.mark.parametrize("token", VENDOR)
def test_vendor_shapes_rely_on_shared_logs_patterns(token):
    assert logs.has_secret(token), f"logs.has_secret no longer matches {token[:8]}...: looks_sensitive loses it"
    assert looks_sensitive(token)


@pytest.mark.parametrize("text", LEAKY)
def test_key_and_token_shapes_are_refused(text):
    assert looks_sensitive(text)


@pytest.mark.parametrize("text", SAFE)
def test_ordinary_text_is_not_refused(text):
    assert not looks_sensitive(text)


@pytest.mark.parametrize("value", [None, ""])
def test_empty_input_is_not_sensitive(value):
    assert looks_sensitive(value) is False


@pytest.mark.parametrize("value", [123, b"sk-proj-AbCdEfGhIjKlMnOp", ["sk-x"]])
def test_non_string_input_fails_closed(value):
    # review R3: a non-str value cannot be checked, so it is treated as sensitive (refused), never as safe
    assert looks_sensitive(value) is True


def test_devmanager_draft_naming_a_credential_is_refused_with_the_shared_message(home):
    # review 2 R2: dev drafts share this check, so naming a credential concept is refused on the safe side (the
    # narrower dev-only check is a follow-up question); pin the user-visible message and that nothing is stored.
    from jarves import devmanager
    from jarves.diag import db as diag_db
    from jarves.store import Store
    st = Store(home / "store.db")
    diag_db.ensure(st)
    for title in ("add an api key field to settings", "rotate the access token on login"):
        with pytest.raises(ValueError, match="this looks sensitive"):
            devmanager.suggest(st, title)
    assert devmanager.listing(st) == []
    assert devmanager.suggest(st, "use fewer tokens in long answers")["status"] == "draft"
    st.close()
