"""#1099: preferences.looks_sensitive must refuse key/token-shaped text (keywords and high-entropy strings).

Fake secrets are assembled at run time so secret scanners do not flag this file; none is a real credential."""
import pytest

from jarves import goals as goals_mod
from jarves import preferences as prefs_mod
from jarves.userdata import open_db

_TAIL = "Q7xLm2Vb9KpR4tZw8NcH3yFd"
# Real key shapes: refused for EVERY caller (preferences, goals, plans, dev drafts).
PREFIX_SECRETS = [
    "my key is " + "sk" + "-proj-" + _TAIL,                     # OpenAI-style key
    "use " + "gh" + "p_" + _TAIL + "1234",                       # GitHub classic token
    "use " + "gh" + "s_" + _TAIL + "1234",                       # GitHub server token (gh[ousr]_)
    "AK" + "IA" + "Q7XLM2VB9KPR4TZW",                            # AWS access key id
    "maps " + "AI" + "za" + _TAIL,                               # Google API key
    "slack " + "xo" + "xb-" + "1234-abcd-" + _TAIL,              # Slack token
    "jwt " + "ey" + "J" + _TAIL + "." + _TAIL,                   # JWT
    "bearer " + _TAIL,                                           # a bearer value
    "-----BEGIN " + "PRIVATE KEY-----",
    "d41d8cd98f00b204e9800998ecf8427e",                          # hex with a 6-digit run (older digit rule)
]
# Key wording and random-looking strings: refused on the preference path only (strict=True).
PREF_ONLY_SECRETS = [
    "remember my api key please",
    "the access token for the router",
    "store my secret key",
    "תזכור את הטוקן שלי",
    "מפתח ה-API שלי הוא זה",                                     # Hebrew API key, hyphenated
    "here: " + _TAIL + "a8e4",                                   # bare mixed-case+digit string, no prefix
    "QxLmVbKpRtZwNcHyFdJgSaUe",                                  # 24 mixed-case letters, no digit
    "dead1beef2cafe3f00d4bad5a1b2c3d4e5f6",                      # 36-char hex, no 6-digit run
    "k3j9x2m8q7w4z1p6r5t0y8u2",                                  # lowercase base36, digit-heavy
]
# Ordinary text: allowed on BOTH paths.
SAFE_TEXTS = [
    "I prefer short answers in the morning",
    "internationalization documentation preferred",              # long word, no digits
    "play jazz after 8pm on weekdays",
    "תשובות קצרות בבוקר",
    "reduce token usage in long chats",                          # the word 'token' alone is not a secret
    "צמצם שימוש בטוקנים בשיחות ארוכות",                         # Hebrew twin of 'token usage'
    "version 2026-11-20 notes",
    "I don't want to be the bearer of bad news first thing",     # ordinary English 'bearer'
    "InternationalizationDocs",                                  # CamelCase word (letter-only threshold 4.0)
    "save notes in my_project_folder_v2",                        # snake_case id with a version digit
    "keep backups in /home/user/projects/myapp2",                # path
    "name files like notes_2026_backup_final",
    "the file core/tests/test_prefs_1099.py",
]


@pytest.mark.parametrize("text", PREFIX_SECRETS)
@pytest.mark.parametrize("strict", [False, True])
def test_real_key_shapes_are_sensitive_on_every_path(text, strict):
    assert prefs_mod.looks_sensitive(text, strict=strict)


@pytest.mark.parametrize("text", PREF_ONLY_SECRETS)
def test_preference_path_refuses_key_wording_and_random_strings(text, home):
    assert prefs_mod.looks_sensitive(text, strict=True)
    p = prefs_mod.Preferences(open_db(home))
    with pytest.raises(ValueError):
        p.suggest("remember " + text, "remember " + text, by="voice")
    assert p.items("proposed") == []


@pytest.mark.parametrize("text", SAFE_TEXTS)
@pytest.mark.parametrize("strict", [False, True])
def test_ordinary_text_is_not_sensitive_on_either_path(text, strict):
    assert not prefs_mod.looks_sensitive(text, strict=strict)


@pytest.mark.parametrize("text", ["save notes in my_project_folder_v2", "InternationalizationDocs",
                                  "short answers in the morning"])
def test_suggest_accepts_ordinary_preferences(text, home):
    assert prefs_mod.Preferences(open_db(home)).suggest(text, text, by="voice")["status"] == "proposed"


def test_other_callers_keep_key_wording_shas_uuids_and_ids(home):
    # Goals and dev drafts may say "add API key support" or carry a commit SHA, UUID or code id (samples without a
    # 6-digit run, which the older digit rule already refuses everywhere).
    sha, uid = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4", "9b2c4f1e-7a3d-4e8b-b5c6-0d1e2f3a4b5c"
    texts = (sha, uid, "test_prefs_sensitive_1099_v2", "add API key support to the weather skill",
             "rotate the access token")
    for text in texts:
        assert not prefs_mod.looks_sensitive(text)
    g = goals_mod.Goals(open_db(home))
    assert g.add("add API key support to the weather skill", details=f"commit {sha} run {uid}")["title"]
    with pytest.raises(ValueError):
        g.add("ship it", details="use " + "gh" + "p_" + _TAIL + "1234")      # a real key is still refused there


def test_prompt_block_skips_a_sensitive_confirmed_item_and_logs_once(home, monkeypatch):
    p = prefs_mod.Preferences(open_db(home))
    pid = p.add_confirmed("short answers in the morning")["item"]["id"]
    p.db.x("UPDATE learned_prefs SET text=? WHERE id=?", ("my api key is handy", pid))   # stored before #1099
    seen = []
    monkeypatch.setattr(prefs_mod.logs, "info", lambda *a, **k: seen.append((a[1], k)))
    monkeypatch.setattr(prefs_mod, "_SKIP_LOGGED", set())
    assert prefs_mod.prompt_block(p) == "" and prefs_mod.prompt_block(p) == ""
    assert seen == [("PREF_SKIPPED_SENSITIVE", {"id": pid})]
