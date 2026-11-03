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
