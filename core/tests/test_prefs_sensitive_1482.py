"""#1482: lowercase hex, base32 (also spaced/lowercase) and short base62/base64url secrets are refused; SHAs pass."""
import random
import string

import pytest

from jarves import devmanager as dev_mod
from jarves.diag import db as diag_db
from jarves.preferences import looks_sensitive
from jarves.store import Store

SHA = "5d4a5f84be1b703ff001e75d0cdccb785f087a78"
LEAKY = [
    "my webhook secret is cac0e7d816b7c1d886ed2d5dfe767db4",                    # 32 lowercase hex + credential word
    "API_KEY=cac0e7d816b7c1d886ed2d5dfe767db4",                                  # env line: '_' separates the word
    "github_token=b084f9e0094f68db07f3501e4eab9e3fbecd52da",                     # 40 hex after '_token'
    "טוקן cac0e7d816b7c1d886ed2d5dfe767db4",                                     # Hebrew 'token' + 32 hex
    "והמפתח שלי b084f9e0094f68db07f3501e4eab9e3fbecd52da",                       # Hebrew prefixes + 'key' + 40 hex
    "6b26aee60ab93d4337af4a7b1c7f527fe5b6b7df1",                                 # boundary: 41 hex alone
    "my totp seed JBSWY3DPEHPK3PXP for the bank",                                # base32 TOTP seed
    "my totp seed jbswy3dpehpk3pxp for the bank",                                # R1: lowercase base32
    "authenticator code JBSW Y3DP EHPK 3PXP then save",                          # R1: spaced groups, words around
    "authenticator code jbsw y3dp ehpk 3pxp gezd gnbv",                          # R1: spaced lowercase
    "Xk9fLm2QzRtPwYbNcVdH",                                                      # boundary: 20-char base62
    "use tk-live-aZ3kQ7m-PxR2tLw_NcYbVd4E as the key",                           # R2: base64url with '-' and '_'
    "x_Xk9fLm2QzR-tPwYbNcVdH",                                                   # R2: 19+ chars only when joined
    '"apiKey": "cac0e7d816b7c1d886ed2d5dfe767db4"',                             # review 2 R1: camelCase tail
    "apikey=cac0e7d816b7c1d886ed2d5dfe767db4",                                   # R1: joined name before '='
    "accessToken=b084f9e0094f68db07f3501e4eab9e3fbecd52da",                      # R1: camelCase, 40 hex
    "secretkey=cac0e7d816b7c1d886ed2d5dfe767db4",                                # R1: joined name before '='
    "cac0e7d816b7c1d886ed2d5dfe767db4 is my webhook secret",                     # R2: credential word after the value
    "bearer b084f9e0094f68db07f3501e4eab9e3fbecd52da",                           # R2: wider word list
    "my credential: cac0e7d816b7c1d886ed2d5dfe767db4",                           # R2: wider word list
    "u4Rq8ZpLx2Kc9Tm/Wn3Vb7Hy1Ja6Df0+Gs5Ek2Qo",                                 # R4: base64 with '/' and '+'
]
SAFE = [
    "fixed in commit 3f9a2c1, see the log",                                      # 7-char short SHA
    f"regression since {SHA} on main",                                           # 40-char SHA, no credential word
    "the keyboard layout from 12a2d26dfc9cbe3f1fc3082e1efacab1 build",           # 'keyboard' is not 'key'
    "monkey cac0e7d816b7c1d886ed2d5dfe767db4",                                   # 'key' inside a word
    "token cac0e7d816b7c1d886ed2d5dfe767db",                                     # boundary: 31 hex after a word
    "6b26aee60ab93d4337af4a7b1c7f527fe5b6b7df",                                  # boundary: 40 hex alone
    "Xk9fLm2QzRtPwYbNcVd",                                                       # boundary: 19 base62
    "rename TestPrefsSensitiveCases2024 and set MAX_PROPOSED_SUGGESTIONS_LIMIT",  # identifiers
    "push f2/JAR-633-fix-security-issue-1482 and claude/jar-633-security-1482-vzu11r",  # branch names
    "call 2345 next week",                                                       # four 4-char words, one number
    "THIS WORD WILL MAKE SENSE",                                                 # upper-case 4-char words
    "I prefer short answers in the morning",
    "decode the base64urlencoded sha256checksumfile",                           # R3: one digit group: a word
    "monkey: cac0e7d816b7c1d886ed2d5dfe767db4",                                 # 'key' inside a word, before ':'
    "src/Components/Header2/IndexPage/Layout",                                   # R4: a path, not random
]


@pytest.mark.parametrize("text", LEAKY)
def test_secret_shapes_are_sensitive(text):
    assert looks_sensitive(text)


@pytest.mark.parametrize("text", SAFE)
def test_shas_identifiers_and_prose_are_not_sensitive(text):
    assert not looks_sensitive(text)


def test_dev_draft_quoting_a_sha_near_a_credential_word_is_refused_with_a_reason_but_kept_without_it(home):
    """R3: the user-visible effect of the fail-safe rule. A 32+ hex run right after 'key'/'token' is refused with
    the 'looks sensitive' message and nothing is stored; the same SHA quoted without the word is saved."""
    st = Store(home / "store.db")
    diag_db.ensure(st)
    with pytest.raises(ValueError, match="looks sensitive"):
        dev_mod.suggest(st, "Key rotation bug", f"the key fix is {SHA}", by="voice")
    assert dev_mod.listing(st) == []
    d = dev_mod.suggest(st, "Rotation bug", f"regression since commit {SHA}", by="voice")
    assert SHA in d["body"] and [x["id"] for x in dev_mod.listing(st)] == [d["id"]]
    st.close()


def test_seeded_detection_rates_for_random_secret_shapes():
    """Hermetic measurement (fixed seed): random secrets of each #1482 shape are refused at these rates."""
    r = random.Random(1482)
    b32 = string.ascii_uppercase + "234567"

    def rate(make, n=500):
        return sum(looks_sensitive(make()) for _ in range(n)) / n

    def rnd(alphabet, k):
        return "".join(r.choice(alphabet) for _ in range(k))

    assert rate(lambda: "token " + rnd("0123456789abcdef", 32)) == 1.0
    assert rate(lambda: rnd("0123456789abcdef", 41)) == 1.0
    assert rate(lambda: rnd(b32, 16)) >= 0.78       # unbroken 16-char seed needs 2 digit groups (review 2 R3)
    assert rate(lambda: rnd(b32, 32).lower()) >= 0.97
    assert rate(lambda: " ".join(rnd(b32, 4) for _ in range(4))) >= 0.9
    assert rate(lambda: rnd(string.ascii_letters + string.digits, 20)) >= 0.85
    assert rate(lambda: "/".join(rnd(string.ascii_letters + string.digits, 15) for _ in range(3))) >= 0.9
    assert rate(lambda: "-".join(rnd(string.ascii_letters + string.digits, 8) for _ in range(4))) >= 0.85


def test_prompt_block_hides_a_stored_item_that_now_looks_sensitive_but_keeps_it_listed(home):
    """Review 2 R5: the read path only leaves such an item out of the instructions; it is not deleted or hidden."""
    from jarves import preferences as prefs_mod
    from jarves.userdata import open_db
    p = prefs_mod.Preferences(open_db(home))
    ok = p.add_confirmed("I prefer short answers")["item"]
    p.db.x("UPDATE learned_prefs SET text=? WHERE id=?", (f"the key fix is {SHA}", ok["id"]))  # stored pre-upgrade
    p.add_confirmed("Speak slowly")
    assert {r["text"] for r in p.items("confirmed")} == {f"the key fix is {SHA}", "Speak slowly"}
    block = prefs_mod.prompt_block(p)
    assert "Speak slowly" in block and SHA not in block
