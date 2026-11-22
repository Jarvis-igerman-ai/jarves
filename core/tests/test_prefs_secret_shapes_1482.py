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
SHA_WITH_DIGIT_RUN = "cc146d119201fe87a1b2"          # 6+ digits in a row inside a SHA (old \d{6,} false positive)

LEAKY = [
    "the webhook secret is " + _hex("hook", 32),
    "api key " + _hex("api", 32),
    _hex("bare32", 32),                                   # 32-char lowercase hex on its own
    _hex("bare64", 64),                                   # 64-char lowercase hex on its own
    _hex("upper", 32).upper(),
    "token " + SHA40,                                     # 40 hex next to a credential word
    "my totp seed JBSWY3DPEHPK3PXP",                      # base32, 16 chars
    "seed " + base64.b32encode(b"a twenty byte seed!!").decode(),
    "use " + _b62("t20", 20),                             # 20-24 char base62
    "use " + _b62("t24", 24) + " for the bot",
    "key " + base64.b64encode(hashlib.sha256(b"k").digest()).decode(),   # base64 with + / =
]

SAFE = [
    "fixed in commit " + SHA40,
    "fixed in " + SHA40,                                  # a 40-char SHA on its own
    "see 3f9c2ab for the change",                         # 7-char SHA
    "regression since " + SHA_WITH_DIGIT_RUN,
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
    # review R1: a short SHA-like token next to a credential word is not exempt from the long-number rule
    assert looks_sensitive(text), text


def _mixed_case(h: str) -> str:
    return "".join(c.upper() if i % 2 else c for i, c in enumerate(h))


@pytest.mark.parametrize("text", [_hex("h41", 41), _hex("h48", 48), _hex("h63", 63),
                                  _mixed_case(_hex("mc32", 32))])
def test_hex_of_41_to_63_chars_and_mixed_case_hex_are_refused(text):
    # review R3 (round 3): pin hex lengths between a SHA-1 and a SHA-256, and mixed-case 32-char hex
    assert looks_sensitive(text), text


def test_sha_like_token_without_a_credential_word_is_allowed():
    # review R2 (round 3): the documented tradeoff, pinned so a change to it is deliberate
    assert not looks_sensitive("my id is 12345678ab")


def test_dev_draft_keeps_shas_and_refuses_a_hex_key(home):
    store = Store(home / "store.db")
    assert devmanager.suggest(store, "regression since " + SHA_WITH_DIGIT_RUN, "bisected to " + SHA40)
    with pytest.raises(ValueError):
        devmanager.suggest(store, "add webhook", "the secret is " + _hex("d", 32))
