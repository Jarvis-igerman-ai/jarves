"""#1482: looks_sensitive refuses secret-shaped values (lowercase hex keys, base32 seeds, short base62 tokens,
base64 keys) while git commit SHAs, paths and ordinary identifiers stay allowed in drafts, goals and preferences."""
import base64
import hashlib

import pytest

from jarves import devmanager
from jarves.preferences import looks_sensitive
from jarves.store import Store


def _hex(seed: str, n: int) -> str:
    return (hashlib.sha256(seed.encode()).hexdigest() * 2)[:n]


def _b62(seed: str, n: int) -> str:
    """A deterministic random-looking base62 string (built here, so no secret-like literal sits in the repo)."""
    abc = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(abc[b % 62] for b in hashlib.sha256(seed.encode()).digest())[:n]


SHA40 = _hex("commit", 40)
SHA_WITH_DIGIT_RUN = "cc" + "".join(map(str, range(1, 8))) + "fe87a1b2"   # 6+ digits in a row inside a SHA (old \d{6,} false positive)

LEAKY = [
    "the webhook secret is " + _hex("hook", 32),
    "api key " + _hex("api", 32),
    _hex("bare32", 32),                                   # 32-char lowercase hex on its own
    _hex("bare64", 64),                                   # 64-char lowercase hex on its own
    _hex("upper", 32).upper(),
    "token " + SHA40,                                     # 40 hex next to a credential word
    "my totp seed " + base64.b32encode(b"ten bytes!").decode(),   # base32, 16 chars, built at runtime
    "seed " + base64.b32encode(b"a twenty byte seed!!").decode(),
    "use " + _b62("t20", 20),                             # 20-24 char base62
    "use " + _b62("t24", 24) + " for the bot",
    "key " + base64.b64encode(hashlib.sha256(b"k").digest()).decode(),   # base64 with + / =
    "commit key " + _hex("ck", 32),                       # a credential word overrides a hash word
    "sha256 secret " + _hex("ss", 64),
]

SAFE = [
    "fixed in commit " + SHA40,
    "fixed in " + SHA40,                                  # a 40-char SHA on its own
    "see 3f9c2ab for the change",                         # 7-char SHA
    "regression since commit " + SHA_WITH_DIGIT_RUN,     # a short digit-heavy SHA named by a hash word
    "sha256 " + _hex("file", 64) + " of the installer",   # a checksum named as one
    "md5 checksum " + _hex("md5", 32),
    "/Users/Owner/Documents/Project2024/v3/file1",
    "rename ThermostatController2 to HeatingController3",
    "add a HTTP2SERVERCONFIG option",
    "the bearer of the news",
    "short answers in the morning",
    "deadbeef is a hex word",
]


@pytest.mark.parametrize("text", LEAKY)
def test_secret_shapes_are_refused(text):
    assert looks_sensitive(text), text


@pytest.mark.parametrize("text", SAFE)
def test_shas_paths_and_identifiers_are_allowed(text):
    assert not looks_sensitive(text), text


def test_long_numbers_are_still_refused():
    assert looks_sensitive("my id is 123456789")
    assert looks_sensitive("call 054 123 4567 8")
    assert looks_sensitive("a1234567")                   # one hex letter is not enough to count as a SHA


@pytest.mark.parametrize("text", ["token 12345678ab", "api key 1234567abc", "my secret is 9988776655ff"])
def test_digit_heavy_hex_next_to_a_credential_word_is_refused(text):
    # a short SHA-like token next to a credential word is not exempt from the long-number rule
    assert looks_sensitive(text), text


def _mixed_case(h: str) -> str:
    return "".join(c.upper() if i % 2 else c for i, c in enumerate(h))


@pytest.mark.parametrize("text", [_hex("h41", 41), _hex("h48", 48), _hex("h63", 63),
                                  _mixed_case(_hex("mc32", 32))])
def test_hex_of_41_to_63_chars_and_mixed_case_hex_are_refused(text):
    # hex lengths between a SHA-1 and a SHA-256, and mixed-case 32-char hex, are not digests
    assert looks_sensitive(text), text


@pytest.mark.parametrize("text", ["my id is 12345678ab", "account 1234567890ab", "customer 98765432EF",
                                  "regression since " + SHA_WITH_DIGIT_RUN])
def test_digit_heavy_hex_id_without_a_hash_word_is_still_refused(text):
    # the SHA exemption does not relax the long-number rule: only 40-char SHAs or ones after a hash word are exempt
    assert looks_sensitive(text), text


def test_dev_draft_keeps_shas_and_refuses_a_hex_key(home):
    store = Store(home / "store.db")
    assert devmanager.suggest(store, "regression since commit " + SHA_WITH_DIGIT_RUN, "bisected to " + SHA40)
    with pytest.raises(ValueError):
        devmanager.suggest(store, "add webhook", "the secret is " + _hex("d", 32))


@pytest.mark.parametrize("text", ["rename getUserByIdAndName2024 to findUser", "call parseJSONv2ResponseBody here",
                                  "open MyApp2/ConfigLoaderV3x/HttpClientV2 next"])
def test_short_word_camelcase_and_relative_paths_are_allowed(text):
    # CamelCase with short words and digits, and relative paths, are not random tokens
    assert not looks_sensitive(text), text


@pytest.mark.parametrize("text", ["key " + _b62("s1", 16) + "/" + _b62("s2", 18),       # random '/' segments
                                  "commit " + _hex("c48", 48)])
def test_random_slash_segments_and_hex_of_no_digest_length_are_refused(text):
    # random base64 segments are not path words; a hash word only excuses hex of a digest length (32/40/64/128)
    assert looks_sensitive(text), text


def _b64_key(i: int) -> str:
    return base64.b64encode(hashlib.sha256(f"key-{i}".encode()).digest()).decode()


def test_random_base62_tokens_are_refused_at_a_pinned_rate():
    # The case-switch heuristic misses some random tokens. Measured on these seeds: about 7-9% of 20-char and
    # 4-5% of 24-char tokens, under 0.5% of 32-char tokens. The floors pin it so it cannot silently get worse;
    # tightening the 20-24 char range is tracked in #1604.
    for n, floor in ((20, 0.88), (24, 0.92), (32, 0.99)):
        refused = sum(looks_sensitive("use " + _b62(f"rate-{n}-{i}", n)) for i in range(1000))
        assert refused >= floor * 1000, (n, refused)


def test_base64_encoded_32_byte_keys_are_all_refused():
    # a key split by two or more '/' into short segments must still be refused
    keys = [_b64_key(i) for i in range(500)]
    assert sum(k.count("/") >= 2 for k in keys) >= 20      # the sample really holds multi-'/' keys
    leaked = [k for k in keys if not looks_sensitive("the key is " + k)]
    assert not leaked, leaked[:3]


@pytest.mark.parametrize("text", ["GITHUB_TOKEN=" + _hex("x", 40), "api_key: " + _hex("x", 40),
                                  "auth_token=" + _hex("z", 40), "api_key=12345678ab",
                                  "the webhook secret for prod is " + _hex("w", 40),     # 3 words in between
                                  "use " + _hex("u", 40) + " as the token"])             # credential word after
def test_snake_case_credential_names_and_a_wider_window_refuse_a_sha_shape(text):
    # '_' separates credential words, and the window spans 4 words before and 3 after
    assert looks_sensitive(text), text


@pytest.mark.parametrize("text", ["api regression since " + _hex("s", 40), "auth bug in " + _hex("b", 40)])
def test_bare_api_and_auth_do_not_make_a_sha_a_secret(text):
    # "api"/"auth" alone are not credential words
    assert not looks_sensitive(text), text


def test_base64_key_with_plus_and_no_slash_is_refused():
    # the '+'-only base64 branch; both parts are under 20 chars, so _BASE62 cannot catch them
    key = _b62("p1", 14) + "+" + _b62("p2", 17) + "="
    assert looks_sensitive("use " + key), key


SHA40_DIGIT_RUN = (SHA_WITH_DIGIT_RUN * 3)[:40]


@pytest.mark.parametrize("text", ["המפתח: 1234567abc", "הסוד שלי הוא 9988776655ff", "הטוקן שלי " + SHA40_DIGIT_RUN,
                                  "אסימון " + SHA40, "ומפתח ה-API שלנו " + SHA40, "השתמש ב-" + SHA40 + " בתור הטוקן"])
def test_hebrew_credential_words_refuse_a_sha_shape(text):
    # the SHA exemption must not reopen a leak for Hebrew credential phrasing
    assert looks_sensitive(text), text


def test_hebrew_text_without_a_credential_word_keeps_a_sha():
    assert not looks_sensitive("תוקן בקומיט " + SHA40)
    assert not looks_sensitive("יסודי: " + SHA40)        # "סוד" inside another word is not a credential word


@pytest.mark.parametrize("text", ["המפתחים תיקנו ב-" + SHA40, "סודר בקומיט " + SHA40, "שתיתי סודה, תוקן ב-" + SHA40])
def test_hebrew_words_that_only_start_like_a_credential_word_keep_a_sha(text):
    # "developers"/"arranged"/"soda" are not credential words
    assert not looks_sensitive(text), text


@pytest.mark.parametrize("text", ["המפתחות: " + SHA40, "הסיסמאות שלנו " + SHA40, "הטוקנים " + SHA40])
def test_inflected_hebrew_credential_words_still_refuse_a_sha(text):
    assert looks_sensitive(text), text


@pytest.mark.parametrize("text", ["https://github.com/MyOrg/RepoName/commit/" + SHA40,
                                  "see https://github.com/OpenAI/OpenAI-Python/blob/" + SHA40 + "/src/Client2.py",
                                  "https://gitlab.com/MyTeam2024/WebApp3/-/commit/" + SHA40[:12]])
def test_repo_links_with_a_sha_are_allowed(text):
    # a URL with mixed-case org/repo names and a SHA goes through the '/' base64 branch
    assert not looks_sensitive(text), text
