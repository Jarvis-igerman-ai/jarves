"""#1099: preferences.looks_sensitive must refuse key/token-shaped text (keywords, known token shapes, high-entropy runs)."""
import pytest

from jarves.preferences import looks_sensitive

LEAKY = [
    "my api key is abc",                                         # keyword: api key
    "remember my API_KEY for the weather service",
    "my github access token for the account",                    # keyword: access token
    "token = abc for the weather",                               # labelled value
    "keep this secret: hunter",                                  # labelled value
    "the OAuth client secret",
    "my private key lives in the drawer",
    "the access key for the cloud",
    "שמור את הטוקן שלי",                                          # Hebrew: token
    "מפתח API של מזג האוויר",                                     # Hebrew: API key
    "use sk-proj-AbCdEfGhIjKlMnOp for answers",                  # OpenAI-shaped key (known token shape)
    "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4",                     # GitHub token shape
    "AKIA" + "ABCDEFGHIJKLMNOP",                                 # AWS key id
    "prefer Zx9Qw3Er7Ty1Ui5Op2As8Df4Gh6Jk",                      # unlabelled high-entropy string
    "aGVsbG8td29ybGQtMTIzNDU2Nzg5MGFiY2RlZg==",                  # base64-looking blob
    "my key is 3f2a9c1e-b7d4-4e8a-9c5f-a1b2c3d4e5f6",            # UUID-shaped key (R1)
    "see commit 9af144bface62e59ef2b17acc36aaaa77f8b6ee8",       # bare 40-hex: same shape as a hex API token (R2)
    "טוקן",                                                      # Hebrew bare token: refused alone (R3)
    "remember wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",         # base64 secret with / (AWS secret-key shape)
    "my secret: I hum while cooking",                            # labelled "secret:" refused on the safe side
    "the key QxZvBnMkLpWrTyUiOpAsDfGhJkLzXcVb",                  # letter-only random mixed case
    "use Zx9Qw3Er7Ty1-Ui5Op2As8_Df4Gh6Jk-mN3bV7cX9zL2kQ8w",       # unprefixed URL-safe token with - and _ (review R1)
    "id x7Kp_Q2vR9-mT4wZ8nB3_cY6hJ1-dF5sL0",                      # random segments joined by _ and -
    "Zx9Qw3Er7Ty1Ui5Op2As",                                      # 20-char letter+digit run, entropy 4.3 (R3 boundary)
    "0123456789abcdef0a1b",                                      # 20-char hex run, entropy 3.9 (R3 boundary)
    "hooks.example.com/services/T0AB12CD/B0EF34GH/Xy7Kq2",       # credential split over short URL segments (R3)
]
SAFE = [
    "short answers in the morning",
    "I prefer jazz while cooking",
    "תשובות קצרות בבוקר",
    "call me by my first name",
    "plan a secret birthday party",                            # ordinary word, not a credential
    "secret-ish title quokka",
    "internationalization-friendly formatting",               # long word, no digits: not a key
    "play the playlist morning-chill-2024",                    # short mixed run: not high entropy
    "open 2fa-backup-codes-for-office365 notes last",          # long dashed word slug: not a key
    "videos like youtube.com/watch?v=dQw4w9WgXcQ",             # short id in a URL
    "review example.com/owner/repo/pull/1450 first",           # URL path with digits (R2)
    "https://github.com/Jarvis-igerman-ai/jarves/pull/1450",
    "see commit 9af144b",                                      # short commit id (R2)
    "send a token of thanks",                                  # English bare token: ordinary word (R3)
    "call it ThisIsMyVeryLongPreferenceName",                  # CamelCase words: not random mixed case
    "SupercalifragilisticExpialidocious songs",                # long letter-only word
    "docs at example.com/Guides/Setup/Step2/Wifi/Router",      # URL path with capitalised segments
    "my_favourite_morning_playlist_v2",                        # underscore slug of words (R1)
    "Living_Room-Lamp_Kitchen-TV_Zone2",                       # mixed-case word slug (R1)
    "release-2026-11-20-hotfix-build",                         # dated slug (R1)
    "skip songs by skrillex",                                  # words starting with "sk" (review R2)
    "ghost stories at night",                                  # words starting with "gh" (R2)
    "my hotkey: F5",                                           # "...key:" is not a credential label (R2)
    "the monkey: grey",
    "skeleton key on the hook",                                # "key" alone is an ordinary word (R2)
    "tokens are counted per month",
    "Zx9Qw3Er7Ty1Ui5Op2A",                                     # 19 chars: below the 20-char minimum (R3 boundary)
    "9af144bface62e59ef2b",                                    # 20-char hex, entropy 3.35 < 3.5 (R3 boundary)
    "serial AAAA1111BBBB2222CCCC",                             # 20-char code with repeats, entropy 2.3 (R3)
]

def test_known_shapes_come_from_the_log_redactor():
    # AKIA... (all upper case) and sk-... are caught only by logs.has_secret (no second pattern list here) (R4)
    from jarves import logs
    assert logs.has_secret("AKIA" + "ABCDEFGHIJKLMNOP") and logs.has_secret("use sk-proj-AbCdEfGhIjKlMnOp for answers")
    assert not logs.has_secret("prefer Zx9Qw3Er7Ty1Ui5Op2As8Df4Gh6Jk")      # this one needs the entropy branch


TOKEN_TEXT = "use Zx9Qw3Er7Ty1Ui5Op2As8Df4Gh6Jk for the weather"


def test_goal_refuses_token_text(tmp_path):
    from jarves.goals import Goals
    from jarves.userdata import open_db
    g = Goals(open_db(tmp_path))
    for kw in ({"title": TOKEN_TEXT}, {"title": "ok", "details": TOKEN_TEXT}, {"title": "ok", "steps": [TOKEN_TEXT]}):
        with pytest.raises(ValueError, match="sensitive"):
            g.add(**kw)
    assert g.db.rows("SELECT id FROM goals") == []


def test_goal_plan_refuses_token_text(tmp_path):
    from jarves.goal_plans import PlanStore
    from jarves.userdata import open_db
    s = PlanStore(open_db(tmp_path))
    with pytest.raises(ValueError, match="sensitive"):
        s.propose(TOKEN_TEXT, [{"tool": "time_now", "args": {}}])
    with pytest.raises(ValueError, match="sensitive"):
        s.propose("plan", [{"tool": "time_now", "args": {}, "why": TOKEN_TEXT}])
    assert s.list() == []


def test_dev_draft_refuses_token_text(tmp_path):
    from jarves import devmanager
    from jarves.store import Store
    st = Store(tmp_path / "store.db")
    for title, body in ((TOKEN_TEXT, ""), ("a draft", TOKEN_TEXT)):
        with pytest.raises(ValueError, match="sensitive"):
            devmanager.suggest(st, title, body)
    assert devmanager.listing(st) == []


def test_suggest_refuses_token_text_and_stores_nothing(tmp_path):
    from jarves import preferences as prefs_mod
    from jarves.userdata import open_db
    p = prefs_mod.Preferences(open_db(tmp_path))
    with pytest.raises(ValueError, match="sensitive"):
        p.suggest("use Zx9Qw3Er7Ty1Ui5Op2As8Df4Gh6Jk for the weather", "owner said so")
    assert p.items("proposed") == []


@pytest.mark.parametrize("text", LEAKY)
def test_key_or_token_shaped_text_is_sensitive(text):
    assert looks_sensitive(text)


@pytest.mark.parametrize("text", SAFE)
def test_ordinary_preferences_are_not_sensitive(text):
    assert not looks_sensitive(text)
