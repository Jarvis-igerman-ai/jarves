"""Issue #869: logs.redact covers common secret shapes; normal text and env lookups stay readable.
Fake values are assembled at runtime so no scanner sees a real-looking literal."""
from __future__ import annotations

import pytest

from jarves import logs

AWS_ID = "AKIA" + "A1B2C3D4E5F6G7H8"
STRIPE = "sk_" + "live_" + "abcdefghij0123456789"
AIZA = "AIza" + "Sy" + "a" * 33
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----\\nMIIEabc123\\nxyz\\n-----END " + "RSA PRIVATE KEY-----"
VAL = "Zq9" + "x" * 20


@pytest.mark.parametrize("text,leak", [
    (f"id {AWS_ID} used", AWS_ID),
    (f"AWS_SECRET_ACCESS_KEY={VAL}", VAL),
    (f'{{"access_token": "{VAL}"}}', VAL),
    (f'{{"client_secret":"{VAL}"}}', VAL),
    (f"GITHUB_TOKEN={VAL}", VAL),
    (f"DB_PASSWORD={VAL}", VAL),
    (f"key {STRIPE}", STRIPE),
    (f"key {AIZA}", AIZA),
    (f"data {PEM} tail", "MIIEabc123"),
    ("-----BEGIN PRIVATE KEY-----\nMIIEabc123\ntruncated", "MIIEabc123"),
])
def test_shape_is_redacted(text, leak):
    out = logs.redact(text)
    assert leak not in out
    assert logs.redact(out) == out                       # idempotent


@pytest.mark.parametrize("text", [AWS_ID, STRIPE, AIZA, PEM])
def test_has_secret_sees_new_token_shapes(text):
    assert logs.has_secret(text)


@pytest.mark.parametrize("text", [
    "api_key = os.environ.get('X')",
    "token = os.getenv('X')",
    "password: ${DB_PASSWORD}",
    '{"openai_api_key": false, "github_token": null}',
    "tokens used: 120, max_tokens=100",
    "the secretary asked about passwords policy",
    "AKIA is a prefix; sk_live_ alone; BEGIN PRIVATE KEY mentioned in docs",
    "ordinary log line: connected in 12 ms to host-1",
])
def test_normal_text_unchanged(text):
    assert logs.redact(text) == text


@pytest.mark.parametrize("text", [
    f"cmd --password={VAL}",
    f"cmd --token={VAL}",
    f"jarvis login --api-key={VAL}",
    f"password=${VAL}",
    f"password=<{VAL}>",
    f"password=null-{VAL}",
    f"token=environ{VAL}",
    f"secret=true-{VAL}",
    f"password=$ENV{VAL}",
])
def test_old_leak_cases_stay_masked(text):
    """Opus review of PR #913: all of these were masked on main and must stay masked."""
    out = logs.redact(text)
    assert VAL not in out
    assert out != text


def test_json_numbers_in_prefixed_settings_stay_valid_json():
    import json
    for text in ('{"max_token": 100}', '{"session_token": 7, "a": 1}'):
        assert json.loads(logs.redact(text)) == json.loads(text)
    assert logs.redact('{"token": 100}') != '{"token": 100}'          # main masked a plain token key


def test_has_secret_truncated_pem_with_later_mask():
    text = "-----BEGIN PRIVATE KEY-----\nMIIEabc123\n\nlater sk-*** and ***"
    assert logs.has_secret(text)


def test_redact_is_linear_on_kebab_and_hyphen_runs():
    """Opus round 2 on #913: an unbounded name prefix made redact() quadratic (17 s on 40 KB). Generous bound."""
    import random
    import time
    rnd = random.Random(7)
    kebab = "-".join("".join(rnd.choice("abcdefghij") for _ in range(rnd.randint(1, 6))) for _ in range(8000))
    for text in ("a-" * 20000, kebab[:40000], "a_b-" * 10000):
        t0 = time.perf_counter()
        logs.redact(text)
        assert time.perf_counter() - t0 < 1.0
    assert VAL not in logs.redact("--db-password=" + VAL) and "***" in logs.redact("a-b-c-api_key=" + VAL)


@pytest.mark.parametrize("text", [
    "password=${Hunter2" + VAL + "}", "password=getenv(" + VAL + ")", "password=process.env." + VAL.lower() + "x", "password=${DB_PASSWORD}x", "password=${" + VAL, "password=getenv('X')y", "password=getenv(VAL" + VAL,
    "password=process.envX" + VAL, "password=os.environ['A']" + VAL,
    "password=${NAME}" + VAL,
])
def test_placeholder_prefix_is_not_a_free_pass(text):
    assert VAL not in logs.redact(text)


@pytest.mark.parametrize("text", [
    "password=${DB_PASSWORD}", "password=os.environ['DB_PASSWORD']", "password=os.getenv('DB_PASSWORD')",
    "password=process.env.DB_PASSWORD", "token=os.environ.get('TOKEN')",
])
def test_exact_placeholders_stay_readable(text):
    assert logs.redact(text) == text


# ---- r4: generic differential test against main's key=value rule (no new leak, whatever the suffix) ----
import itertools
import re as _re

import pytest as _pytest

from jarves.logs import redact as _redact

_MAIN_KV = _re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|cookie|authorization)(\"?\s*[:=]\s*\"?)"
                       r"(?!\*\*\*|<\d+ chars>|<secret>)[^\s\",}&]+")


def _main_redact(s: str) -> str:
    """Frozen copy of main's (32d3c091) key=value rule: the reference the PR must never be weaker than."""
    return _MAIN_KV.sub(r"\1\2***", s)


_SECRET = "Hunter2xyz"
_KEYS = ["password", "token", "api_key", "secret", "PASSWORD", "db_password", "access_token", "client_secret",
         "authorization", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN"]
_SEPS = ["=", ": ", ":", "=\"", "\": \"", "\":\""]
_HEADS = ["", "true", "false", "null", "none", "<x>", "<aws-key-id>", "<user>", "<X>", "${A}", "${A_B}", "${a}",
          "os.environ['X']", "os.environ[X]", "os.environ.get('X')", "os.getenv('X')", "getenv(X)",
          "process.env.A", "process.env.a", "***", "<secret>", "<5 chars>", "abc", "a1"]
_SUFFIXES = ["", "]", ">", "}", ")", ";", "'", "\"", "a", "Z", "1", "_", "-", ".", "/", "x]", "]]", ")]", "[", "\\"]


def _corpus():
    for k, sep, head, suf in itertools.product(_KEYS, _SEPS, _HEADS, _SUFFIXES):
        yield f"{k}{sep}{head}{suf}{_SECRET}"


def test_differential_no_new_leak_vs_main():
    """For every input where main masks the secret tail, the PR masks it too (placeholders only exempt when they
    are the WHOLE value, so a glued suffix never exposes the tail)."""
    leaks = [s for s in _corpus() if _SECRET not in _main_redact(s) and _SECRET in _redact(s)]
    assert not leaks, leaks[:20]


@_pytest.mark.parametrize("value", ["true", "false", "null", "none", "<x>", "<aws-key-id>", "${NAME}",
                                    "os.environ['X']", "os.environ[X]", "os.environ.get('X')", "os.getenv(X)",
                                    "getenv('X')", "process.env.NAME"])
@_pytest.mark.parametrize("end", ["", " ", ",", "}", "\"", "&"])
def test_exact_placeholder_is_the_only_exemption(value, end):
    """The explicit exemption list: a placeholder that is the whole value stays readable ..."""
    s = f"password={value}{end}"
    assert _redact(s) == s


@_pytest.mark.parametrize("s", ["password=true]Hunter2xyz", "token=null]Hunter2xyz", "secret=process.env.A]Hunter2xyz",
                               "password=${A}]Hunter2xyz", "api_key=os.environ.get('X')]Hunter2xyz",
                               "password=<x>Hunter2xyz", "password=<aws-key-id>Hunter2xyz",
                               "{\"password\": \"<x>Hunter2xyz\"}", "password=<user>Hunter2xyz"])
def test_r3_regressions_bracket_and_tag_suffix(s):
    assert _SECRET not in _redact(s)
