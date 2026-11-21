"""#1099: preferences.looks_sensitive must refuse key/token-shaped text (keywords and high-entropy strings).

Fake secrets are assembled at run time so secret scanners do not flag this file; none is a real credential."""
import pytest

from jarves import goals as goals_mod
from jarves import preferences as prefs_mod
from jarves.userdata import open_db

_TAIL = "Q7xLm2Vb9KpR4tZw8NcH3yFd"
SECRET_TEXTS = [
    "my key is " + "sk" + "-proj-" + _TAIL,                     # OpenAI-style key
    "use " + "gh" + "p_" + _TAIL + "1234",                       # GitHub token
    "AK" + "IA" + "Q7XLM2VB9KPR4TZW",                            # AWS access key id
    "d41d8cd98f00b204e9800998ecf8427e",                          # 32-char hex secret
    "remember my api key please",
    "the access token for the router",
    "store my secret key",
    "-----BEGIN " + "PRIVATE KEY-----",
    "תזכור את הטוקן שלי",
    "מפתח ה-API שלי הוא זה",                                     # R3: Hebrew API key, hyphenated
    "bearer " + _TAIL,                                           # R2: a real bearer value is still caught
]
# Preference-path only (entropy rule): shapes with no prefix and no 6-digit run.
PREF_ONLY_SECRETS = [
    "here: " + _TAIL + "a8e4",                                   # bare high-entropy string, no prefix
    "QxLmVbKpRtZwNcHyFdJgSaUe",                                  # R4: 24 mixed-case letters, no digit
    "dead1beef2cafe3f00d4bad5a1b2c3d4e5f6",                      # R4: 36-char hex, no 6-digit run
]
SAFE_TEXTS = [
    "I prefer short answers in the morning",
    "internationalization documentation preferred",              # long word, no digits
    "play jazz after 8pm on weekdays",
    "תשובות קצרות בבוקר",
    "reduce token usage in long chats",                          # the word 'token' alone is not a secret
    "version 2026-11-20 notes",
    "צמצם שימוש בטוקנים בשיחות ארוכות",                         # R1: Hebrew twin of 'token usage'
    "I don't want to be the bearer of bad news first thing",     # R2: ordinary English 'bearer'
    "InternationalizationDocs",                                  # R4: CamelCase word stays allowed
]


@pytest.mark.parametrize("text", SECRET_TEXTS)
def test_key_or_token_shaped_text_is_sensitive(text):
    assert prefs_mod.looks_sensitive(text)


@pytest.mark.parametrize("text", SAFE_TEXTS)
def test_ordinary_preferences_are_not_sensitive(text):
    assert not prefs_mod.looks_sensitive(text)


@pytest.mark.parametrize("text", PREF_ONLY_SECRETS)
def test_preference_path_refuses_high_entropy_shapes(text, home):
    assert prefs_mod.looks_sensitive(text, entropy=True)
    with pytest.raises(ValueError):
        prefs_mod.Preferences(open_db(home)).suggest("remember " + text, "remember " + text, by="voice")


def test_other_callers_keep_git_shas_uuids_and_ids(home):
    # R5: goals/dev drafts may carry a commit SHA, UUID or code id; only the preference path applies entropy
    # (samples without a 6-digit run, which the older digit rule already refuses everywhere).
    sha, uid = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4", "9b2c4f1e-7a3d-4e8b-b5c6-0d1e2f3a4b5c"
    for text in (sha, uid, "test_prefs_sensitive_1099_v2"):
        assert not prefs_mod.looks_sensitive(text)
    g = goals_mod.Goals(open_db(home)).add("ship the fix", details=f"commit {sha} run {uid}")
    assert g["title"] == "ship the fix"


def test_suggest_refuses_and_stores_nothing_for_a_bare_token(home):
    p = prefs_mod.Preferences(open_db(home))
    with pytest.raises(ValueError):
        p.suggest("remember " + _TAIL + "a8e4", "remember " + _TAIL + "a8e4", by="voice")
    assert p.items("proposed") == []
