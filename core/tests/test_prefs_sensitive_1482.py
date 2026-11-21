"""#1482: lowercase hex, base32 and short base62 secrets must be refused by looks_sensitive; git SHAs stay allowed."""
import random
import string

import pytest

from jarves import preferences as prefs_mod
from jarves.preferences import looks_sensitive

LEAKY = [
    "my webhook secret is cac0e7d816b7c1d886ed2d5dfe767db4",                       # 32 lowercase hex + credential word
    "the stripe signing key: cffa2026befdef48eea19fcd766ca22fecb6cd161f6eb7a6786ccc976c96d3c6",
    "use token=b084f9e0094f68db07f3501e4eab9e3fbecd52da for the bot",               # 40 hex (old token shape) + word
    "store 6b26aee60ab93d4337af4a7b1c7f527fe5b6b7df1dd1cd9d03aaaa65f35b6b412",      # 65 hex on its own (> SHA length)
    "my totp seed JBSWY3DPEHPK3PXP for the bank",                                    # base32 TOTP seed
    "backup code GEZDGNBVGY3TQOJQGEZDGNBV",                                          # base32, longer
    "use Xk9fLm2QzRtPwYbNcVdH",                                                      # 20-char base62, 2 digit runs
    "the bot uses aZ3kQ7mPxR2tLwNcYbVd4E",                                           # 22-char base62
    "המפתח שלי b084f9e0094f68db07f3501e4eab9e3fbecd52da",                            # Hebrew credential word + 40 hex
    "טוקן cac0e7d816b7c1d886ed2d5dfe767db4",                                         # Hebrew 'token' + 32 hex
    "הסוד: 59dbbb9210cfabbba394c0e43d68ad47",                                        # Hebrew 'secret' + 32 hex
    "והמפתח שלי b084f9e0094f68db07f3501e4eab9e3fbecd52da",                           # Hebrew prefix letters + 'key' + 40 hex
    "שלח לטוקן cac0e7d816b7c1d886ed2d5dfe767db4",                                    # prefix ל + 'token' + 32 hex
    "the key fix is 5d4a5f84be1b703ff001e75d0cdccb785f087a78",                       # SHA right after 'key': refused on purpose (fails safe)
    "token cac0e7d816b7c1d886ed2d5dfe767db4",                                        # boundary: exactly 32 hex after a word
    "6b26aee60ab93d4337af4a7b1c7f527fe5b6b7df1",                                     # boundary: exactly 41 hex alone
    "Xk9fLm2QzRtPwYbNcVdH",                                                          # boundary: exactly 20 base62
    "API_KEY=cac0e7d816b7c1d886ed2d5dfe767db4",                                      # env line: '_' separates the word
    "WEBHOOK_SECRET=cac0e7d816b7c1d886ed2d5dfe767db4",                               # 'webhook_' and '_SECRET'
    "github_token=b084f9e0094f68db07f3501e4eab9e3fbecd52da",                         # 40 hex after '_token'
    "stripe_signing_key cac0e7d816b7c1d886ed2d5dfe767db4",                           # '_key' then 32 hex
    "GH=ghp_aZ3kQ7mPxR2tLwNcYbVd4EaZ3kQ7mPxR2tLw",                                   # prefixed base62 token after '_'
    "המפתחות cac0e7d816b7c1d886ed2d5dfe767db4",                                      # Hebrew plural 'the keys' + 32 hex: fails safe
]

SAFE = [
    "fixed in commit 3f9a2c1, see the log",                                          # 7-char short SHA
    "regression since 5d4a5f84be1b703ff001e75d0cdccb785f087a78 on main",             # 40-char full SHA
    "revert f4fa370e26c393a662f78ed3230e40e8f38fe69e please",                        # 40-char SHA, no credential word
    "checksum 59dbbb9210cfabbba394c0e43d68ad47 matches",                             # md5 with no credential word
    "the keyboard layout from 12a2d26dfc9cbe3f1fc3082e1efacab1 build",               # 'keyboard' is not 'key'
    "rename TestPrefsSensitiveCases2024 to something shorter",                       # CamelCase identifier
    "README CONTRIBUTING and LICENSE files",                                         # upper-case words
    "I prefer short answers in the morning",
    "set MAX_PROPOSED_SUGGESTIONS_LIMIT higher",                                     # constant name
    "token cac0e7d816b7c1d886ed2d5dfe767db",                                         # boundary: 31 hex after a word
    "6b26aee60ab93d4337af4a7b1c7f527fe5b6b7df",                                      # boundary: 40 hex alone (SHA length)
    "Xk9fLm2QzRtPwYbNcVd",                                                           # boundary: 19 base62
    "המפתחות במגירה",                                                                 # Hebrew 'keys' with no secret after it
    "monkey cac0e7d816b7c1d886ed2d5dfe767db4",                                       # 'key' inside a word is not a credential word
]


@pytest.mark.parametrize("text", LEAKY)
def test_hex_base32_and_short_base62_secrets_are_sensitive(text):
    assert looks_sensitive(text)


@pytest.mark.parametrize("text", SAFE)
def test_git_shas_and_ordinary_identifiers_are_not_sensitive(text):
    assert not looks_sensitive(text)


def test_lowercase_hex_secret_suggestion_is_refused_and_not_stored(home):
    from jarves.userdata import open_db
    p = prefs_mod.Preferences(open_db(home))
    with pytest.raises(ValueError):
        p.suggest("remember my webhook secret cac0e7d816b7c1d886ed2d5dfe767db4", "x", by="voice")
    assert p.items("proposed") == []


def test_dev_draft_with_a_lowercase_hex_secret_is_refused_and_not_stored(home):
    from jarves import devmanager as dev_mod
    from jarves.diag import db as diag_db
    from jarves.store import Store
    st = Store(home / "store.db")
    diag_db.ensure(st)
    with pytest.raises(ValueError):
        dev_mod.suggest(st, "Webhook setup", "the webhook secret is cac0e7d816b7c1d886ed2d5dfe767db4", by="voice")
    assert dev_mod.listing(st) == []
    st.close()


def test_goal_with_a_lowercase_hex_secret_is_refused_and_not_stored(home):
    from jarves import goals as goals_mod
    from jarves.userdata import open_db
    g = goals_mod.Goals(open_db(home))
    with pytest.raises(ValueError):
        g.add("Set up the bot", "token cac0e7d816b7c1d886ed2d5dfe767db4", by="voice")
    assert g.list() == []


def test_seeded_detection_rates_for_random_secret_shapes():
    """Hermetic measurement (fixed seed): random secrets of each #1482 shape are refused at these rates."""
    r = random.Random(1482)

    def rate(alphabet, n, prefix=""):
        return sum(looks_sensitive(prefix + "".join(r.choice(alphabet) for _ in range(n))) for _ in range(500)) / 500

    assert rate("0123456789abcdef", 32, "token ") == 1.0
    assert rate("0123456789abcdef", 41) == 1.0
    assert rate(string.ascii_uppercase + "234567", 16) >= 0.93    # misses only seeds with no digit 2-7
    assert rate(string.ascii_letters + string.digits, 20) >= 0.85
    assert rate(string.ascii_letters + string.digits, 32) >= 0.95
