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
    "my api keys are in the drawer",                     # plurals (review R5)
    "both secret keys are on the fridge",
    "auth_token expiry",                                 # names a credential: refused on purpose (review R5)
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
    # development drafts quote paths, URLs, versions, SHAs and UUIDs (Opus review of PR #1354)
    "core/tests/test_v5_goal_plans.py fails",
    "stage2/build_installer_v54.ps1 broke",
    "https://platform.openai.com/docs/guides/realtime-websocket2",
    "fixed in 6a222ff5c0d1e2b3a4f59a8c7d6e5a4b3c2d1e0f",   # (a 6+ digit run is the older number rule)
    "run 0f8e6c1a-3b2d-4e5f-9a7b-1c2d3e4f5a6b failed",
    "gpt-realtime-2026-08-28 is slow",
    "faster-whisper-large-v3 model",
    "openai-2.3.1-py3-none-any.whl does not install",
    "rerun core/tests/test_mobile_phone_e2e.py",
    "see https://github.com/Jarvis-igerman-ai/jarves/pull/1354",
    "access keyboard shortcuts",                         # "key" must end the word (review R5)
    "add a private keyword list",
    "TestV5GoalPlansCancelRace2Handler fails on CI",     # CamelCase identifier with digits (review R6)
    "PlanRunnerV2RetryBackoff3Handler times out",
    "oauth_token refresh fails",                         # identifier, no word boundary before "auth" (review R5)
])
def test_ordinary_text_is_not_sensitive(text):
    assert not looks_sensitive(text)


def test_dev_drafts_skip_credential_words_but_not_real_keys():
    """Review R3: a bug report about the API key field saves as a draft; a pasted key is still refused."""
    assert looks_sensitive("the OpenAI api key field does not save")
    assert not looks_sensitive("the OpenAI api key field does not save", credential_words=False)
    assert not looks_sensitive("access token expiry not shown", credential_words=False)
    assert looks_sensitive("key is sk-proj-Ab3dEf9hIjK2mNoPqR7sTuVwXyZ01234", credential_words=False)
    assert looks_sensitive("my password is hunter2", credential_words=False)
