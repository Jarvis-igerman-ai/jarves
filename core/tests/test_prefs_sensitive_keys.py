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
])
def test_ordinary_text_is_not_sensitive(text):
    assert not looks_sensitive(text)
