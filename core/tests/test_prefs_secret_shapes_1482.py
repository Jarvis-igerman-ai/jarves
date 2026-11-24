"""#1482: looks_sensitive refuses lowercase hex keys and short random base62 tokens (base32 seeds: #1516), while
git commit SHAs, checksums and ordinary identifiers stay allowed in drafts, goals and preferences."""
import hashlib

import pytest

from jarves import devmanager
from jarves.preferences import looks_sensitive
from jarves.store import Store


def _hex(seed: str, n: int) -> str:
    return (hashlib.sha256(seed.encode()).hexdigest() * 2)[:n]


def _b62(seed: str, n: int) -> str:
    """A deterministic random-looking base62 token, built at runtime so no secret-like literal sits in the repo."""
    abc = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(abc[b % 62] for b in hashlib.sha256(seed.encode()).digest())[:n]


SHA40 = _hex("commit", 40)
SHA_DIGITS = "cc1234567fe87a1b2"        # a short SHA with 6+ digits in a row (the long-number rule refused it)
SHA40_DIGITS = ("ab1234567" + _hex("d40", 40))[:40]       # a 40-char SHA with a run of 6+ digits
SHA256_DIGITS = ("ab1234567" + _hex("d64", 64))[:64]      # named digests with a run of 6+ digits
SHA512_DIGITS = ("cd7654321" + _hex("d128", 128))[:128]

LEAKY = [
    _hex("bare32", 32), _hex("bare64", 64), _hex("upper", 32).upper(), _hex("h48", 48),
    "the webhook secret is " + _hex("hook", 32),
    "token " + SHA40, "GITHUB_TOKEN=" + SHA40, "use " + SHA40 + " as the token",
    "commit key " + _hex("ck", 32),                     # a credential word beats a hash word
    "use " + _b62("t20", 20), "use " + _b62("t24", 24) + " for the bot",
    "token 12345678ab",                                 # a SHA-like id next to a credential word keeps the number rule
    "my totp seed JBSWY3DPEHPK3PXP",                    # base32 seed (#1516 rule, kept)
]

SAFE = [
    "fixed in commit " + SHA40, "fixed in " + SHA40, "see 3f9c2ab for the change",
    "regression since commit " + SHA_DIGITS,
    "sha256 " + _hex("file", 64) + " of the installer", "md5 checksum " + _hex("md5", 32),
    "https://github.com/MyOrg/RepoName/commit/" + SHA40,
    "rename getUserByIdAndName2024 to findUser", "call parseJSONv2ResponseBody here",
    "/Users/Owner/Documents/Project2024/v3/file1", "api regression since " + SHA40,
    "the bearer of the news", "a monkey and a turkey played hockey, fixed in " + SHA40,
    "deadbeef is a hex word", "short answers in the morning",
]


@pytest.mark.parametrize("text", LEAKY)
def test_secret_shapes_are_refused(text):
    assert looks_sensitive(text), text


@pytest.mark.parametrize("text", SAFE)
def test_shas_checksums_and_identifiers_are_allowed(text):
    assert not looks_sensitive(text), text


@pytest.mark.parametrize("text", ["my id is 12345678ab", "regression since " + SHA_DIGITS, "a1234567"])
def test_digit_heavy_ids_without_a_hash_word_keep_the_long_number_rule(text):
    assert looks_sensitive(text), text


@pytest.mark.parametrize("word", ["apikey", "APIKEY=", "secretkey", "authtoken", "accesstoken", "passwords:",
                                  "api-keys", "private_key", "client secret"])
def test_glued_and_plural_credential_words_refuse_a_sha(word):
    assert looks_sensitive(f"{word} {SHA40}"), word


@pytest.mark.parametrize("text", ["אסימון " + SHA40, "הטוקן שלי " + SHA40, "המפתחות: " + SHA40, "הסוד שלנו " + SHA40,
                                  "השתמש ב-" + SHA40 + " בתור הטוקן"])
def test_hebrew_credential_words_refuse_a_sha(text):
    assert looks_sensitive(text), text


@pytest.mark.parametrize("text", ["תוקן בקומיט " + SHA40, "המפתחים תיקנו ב-" + SHA40, "יסודי: " + SHA40])
def test_hebrew_words_that_only_look_like_a_credential_word_keep_a_sha(text):
    assert not looks_sensitive(text), text


def test_hebrew_developer_is_refused_on_the_safe_side():
    # מפתח is both "key" and "developer"; it cannot be told apart, so the draft is refused rather than a key let through
    assert looks_sensitive("המפתח תיקן ב-" + SHA40)


def test_random_base62_tokens_are_refused_at_a_pinned_rate():
    # The case-switch heuristic misses a few random tokens. Measured on these seeds: 92% of 20-char, 96% of 24-char and
    # 98.5% of 32-char tokens refused (13-16% before the fix). The floors pin it so it cannot silently get worse.
    for n, floor in ((20, 0.85), (24, 0.90), (32, 0.97)):
        refused = sum(looks_sensitive("use " + _b62(f"rate-{n}-{i}", n)) for i in range(1000))
        assert refused >= floor * 1000, (n, refused)


@pytest.mark.parametrize("text", ["token 3f9c2ab4e1d07b5a", "apikey: a1b2c3d4e5f6a7b8c9d0", "my password is c0ffee7ab",
                                  "הטוקן 3f9c2ab4e1d07b5a"])
def test_short_hex_next_to_a_credential_word_is_refused(text):
    assert looks_sensitive(text), text


@pytest.mark.parametrize("text", ["the key was defaced", "keys: accede and effaced"])
def test_letters_only_hex_words_next_to_a_credential_word_are_allowed(text):
    assert not looks_sensitive(text), text


@pytest.mark.parametrize("text", [SHA40_DIGITS, "fixed in " + SHA40_DIGITS, "use " + SHA40_DIGITS + " here"])
def test_bare_40_hex_with_a_digit_run_keeps_the_long_number_rule(text):
    # Same as main: without a hash word or a commit URL, a 40-hex value with 6+ digits in a row is refused.
    assert looks_sensitive(text), text


@pytest.mark.parametrize("text", ["fixed in commit " + SHA40_DIGITS, "https://github.com/MyOrg/Repo/commit/" + SHA40_DIGITS,
                                  "sha256 " + SHA256_DIGITS + " of the installer", "sha512 checksum " + SHA512_DIGITS])
def test_named_shas_and_digests_with_a_digit_run_are_allowed(text):
    assert not looks_sensitive(text), text


def test_dev_draft_keeps_shas_and_refuses_a_hex_key(home):
    store = Store(home / "store.db")
    assert devmanager.suggest(store, "regression since commit " + SHA_DIGITS, "bisected to " + SHA40)
    with pytest.raises(ValueError):
        devmanager.suggest(store, "add webhook", "the secret is " + _hex("d", 32))


def test_preferences_refuse_a_hex_key(home):
    from jarves.preferences import Preferences
    from jarves.userdata import open_db
    with pytest.raises(ValueError, match="sensitive"):
        Preferences(open_db(home)).suggest("use " + _hex("caller", 32), "I said so")
