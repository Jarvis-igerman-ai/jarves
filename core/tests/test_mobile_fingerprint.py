"""The certificate fingerprint next to the pairing code (V7 A2 review): the Android app pins the certificate the owner confirms, so the
computer must SHOW it. Same value the app computes: SHA-256 of the DER certificate."""
from __future__ import annotations

import hashlib
import ssl

from jarves import mobile
from test_mobile_access import admin, fake, srv  # noqa: F401  (fixtures and helpers)


def test_the_fingerprint_is_the_sha256_of_the_der_certificate(tmp_path):
    assert mobile.cert_fingerprint(tmp_path) == ""                       # no certificate yet: nothing to show
    cert, _key = mobile.make_selfsigned(tmp_path, "127.0.0.1")
    der = ssl.PEM_cert_to_DER_cert(cert.read_text(encoding="ascii"))
    assert mobile.cert_fingerprint(tmp_path) == hashlib.sha256(der).hexdigest()
    assert len(mobile.cert_fingerprint(tmp_path)) == 64


def test_a_broken_certificate_file_gives_no_fingerprint_not_an_error(tmp_path):
    cert, _ = mobile.tls_paths(tmp_path)
    cert.parent.mkdir(parents=True)
    cert.write_text("not a certificate", encoding="ascii")
    assert mobile.cert_fingerprint(tmp_path) == ""


def test_the_pairing_code_answer_carries_the_fingerprint_only_when_there_is_a_certificate(srv, home):  # noqa: F811
    status, out = admin(srv, "POST", "/api/mobile/code", {})
    assert status == 200 and "fingerprint" not in out                    # plain http / no certificate: nothing to compare
    mobile.make_selfsigned(home, "127.0.0.1")
    status, out = admin(srv, "POST", "/api/mobile/code", {})
    assert status == 200 and out["fingerprint"] == mobile.cert_fingerprint(home)
