"""Owner decision for CodeQL #15: e-mail addresses are masked in health text in BOTH privacy modes, and redact_pii
uses the linear mask_emails() with the same behaviour as the old _EMAIL regex."""
import json
import random

import pytest

from jarves import logs
from jarves.health import Health

FAKE = "planted.fake-user+x@example-fake.test"   # synthetic, not a real address


@pytest.mark.parametrize("private", [True, False])
def test_health_never_stores_an_email_in_either_privacy_mode(tmp_path, private, monkeypatch):
    monkeypatch.setattr(logs, "_privacy", private)
    h = Health(tmp_path / "health.json")
    h.set("ai", "ok", f"ChatGPT account {FAKE}", f"sign in as {FAKE}")
    h.last_error = {"component": "ai", "msg": f"failed for {FAKE}"}
    h.save()
    for blob in ((tmp_path / "health.json").read_text(encoding="utf-8"), json.dumps(h.snapshot()),
                 json.dumps(h.components_view())):
        assert FAKE not in blob and "example-fake.test" not in blob and "planted.fake-user" not in blob
        assert "<email>" in blob


def test_redact_pii_matches_the_old_email_regex_on_generated_inputs():
    old = lambda s: logs._PHONE.sub(lambda m: "<phone>" if sum(c.isdigit() for c in m.group(0)) >= 9 else m.group(0),
                                    logs._EMAIL.sub("<email>", s))
    rnd = random.Random(1234)
    alphabet = "ab1_.+-@ @.xY-é \t,;()<>0123456789+"
    cases = ["", "a@b.c", "x@y", "@a.b", "a@.b", "a@b.", "a.b@c-d.e.f", "me@x.io, you@y.org", "mail:a@b.cc.",
             "a@@b.c", "+49 170 1234567 a@b.de", "ünï@exämple.com"]
    cases += ["".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 40))) for _ in range(5000)]
    for s in cases:
        assert logs.redact_pii(s) == old(s), repr(s)
    assert "<email>" in logs.redact_pii("a@b.c")


def test_redact_pii_is_fast_on_a_long_run():
    import time
    s = "a." * 20000 + "@"
    t = time.perf_counter()
    logs.redact_pii(s)
    assert time.perf_counter() - t < 2
