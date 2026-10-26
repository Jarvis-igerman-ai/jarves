"""Trusted phone connection, path A (#513): the JARVES local CA. Hermetic: fake clock, no internet, no real phone. The chain is
validated with `cryptography` itself (x509.verification), including the name constraints and expiry."""
import datetime as dt
import json
import os
import ssl
import stat
import sys
import threading
from types import SimpleNamespace

import pytest

from jarves import phone_trust as pt
from jarves import tailnet
from jarves.config import Config

from test_mobile_access import HOST, admin, free_port, pair, req
from jarves import dashboard

T0 = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.timezone.utc)
LAN = ["localhost", "127.0.0.1", "192.168.1.20"]


@pytest.fixture
def ca(home):
    pt.create_ca(home, T0)
    return home


def _leaf(home, now=T0, hosts=LAN):
    crt, _ = pt.issue_leaf(home, hosts, now)
    return crt.read_bytes(), pt.ca_certificate_pem(home)


# ---------------------------------------------------------------- the CA itself
def test_ca_shape_and_limits(ca):
    from cryptography import x509
    c = pt._load_ca_cert(ca)
    bc = c.extensions.get_extension_for_class(x509.BasicConstraints)
    assert bc.critical and bc.value.ca and bc.value.path_length == 0
    nc = c.extensions.get_extension_for_class(x509.NameConstraints)
    assert nc.critical
    assert (c.not_valid_after_utc - c.not_valid_before_utc) <= dt.timedelta(days=pt.CA_MAX_DAYS + 1)
    ku = c.extensions.get_extension_for_class(x509.KeyUsage).value
    assert ku.key_cert_sign and not ku.digital_signature
    st = pt.status(ca, T0)
    assert st["ca"] and not st["ca_expired"] and st["fingerprint"].count(":") == 31


def test_create_is_idempotent_and_key_is_private(ca):
    fp = pt.fingerprint(ca)
    assert pt.create_ca(ca, T0 + dt.timedelta(days=5))["fingerprint"] == fp          # kept, not replaced
    key = pt.ca_paths(ca)[1]
    if os.name != "nt":
        assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert b"PRIVATE KEY" in key.read_bytes()
    assert b"PRIVATE KEY" not in pt.ca_certificate_pem(ca)


def test_acl_command_is_owner_only(home, monkeypatch):
    monkeypatch.setenv("USERNAME", "owner1")
    seen = []
    pt.create_ca(home, T0, run=seen.append)
    cmds = [" ".join(c) for c in seen]
    assert any(c.startswith("icacls") and "ca.key" in c and "/inheritance:r" in c and "/grant:r owner1:(F)" in c for c in cmds)


# ---------------------------------------------------------------- chain validation with cryptography
def test_leaf_validates_for_lan_names_with_only_the_ca(ca):
    leaf, ca_pem = _leaf(ca)
    for host in LAN:
        assert pt.verify_chain(leaf, ca_pem, host, T0) == (True, "ok")


def test_leaf_is_not_valid_for_other_hosts_or_after_expiry(ca):
    leaf, ca_pem = _leaf(ca)
    assert pt.verify_chain(leaf, ca_pem, "192.168.1.99", T0)[0] is False          # host name check
    assert pt.verify_chain(leaf, ca_pem, "192.168.1.20", T0 + dt.timedelta(days=pt.LEAF_DAYS + 2))[0] is False
    assert pt.verify_chain(leaf, ca_pem, "192.168.1.20", T0 - dt.timedelta(days=10))[0] is False


def test_name_constraints_forbid_public_names_even_with_the_key(ca):
    """A stolen CA key still cannot vouch for a public site: sign one by hand and check the verifier refuses it."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    cacert = pt._load_ca_cert(ca)
    cakey = serialization.load_pem_private_key(pt.ca_paths(ca)[1].read_bytes(), None)
    k = ec.generate_private_key(ec.SECP256R1())
    bad = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bank.example.com")]))
           .issuer_name(cacert.subject).public_key(k.public_key()).serial_number(x509.random_serial_number())
           .not_valid_before(T0 - dt.timedelta(days=1)).not_valid_after(T0 + dt.timedelta(days=10))
           .add_extension(x509.SubjectAlternativeName([x509.DNSName("bank.example.com"), x509.IPAddress(__import__("ipaddress").ip_address("8.8.8.8"))]), critical=False)
           .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
           .sign(cakey, hashes.SHA256()))
    pem = bad.public_bytes(serialization.Encoding.PEM)
    assert pt.verify_chain(pem, pt.ca_certificate_pem(ca), "bank.example.com", T0)[0] is False
    assert pt.verify_chain(pem, pt.ca_certificate_pem(ca), "8.8.8.8", T0)[0] is False


def test_issue_drops_names_the_ca_may_not_sign(ca):
    crt, _ = pt.issue_leaf(ca, ["192.168.1.20", "evil.example.com", "8.8.8.8", "100.101.102.103", "pc.tail1234.ts.net"], T0)
    assert pt.leaf_info(ca)["hosts"] == ["192.168.1.20"]
    with pytest.raises(RuntimeError):
        pt.issue_leaf(ca, ["evil.example.com"], T0)


def test_permitted_rules():
    for ok in ("localhost", "127.0.0.1", "10.1.2.3", "172.16.5.5", "172.31.255.1", "192.168.0.7", "pc.local", "nas.lan", "x.home.arpa"):
        assert pt.permitted(ok), ok
    for no in ("8.8.8.8", "172.32.0.1", "100.64.0.1", "example.com", "local", "pc.ts.net", "127.0.0.2", "localhost.evil.com"):
        assert not pt.permitted(no), no


def test_leaf_is_not_a_ca_and_is_short_lived(ca):
    from cryptography import x509
    pt.issue_leaf(ca, LAN, T0)
    c = x509.load_pem_x509_certificate(pt.leaf_paths(ca)[0].read_bytes())
    assert c.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    assert c.not_valid_after_utc - T0 <= dt.timedelta(days=pt.LEAF_DAYS)


# ---------------------------------------------------------------- renewal on the fake clock
def test_renewal_reuse_then_renew_then_san_change(ca):
    first = pt.ensure_leaf(ca, LAN, T0)
    s1 = pt.leaf_paths(ca)[0].read_bytes()
    assert pt.ensure_leaf(ca, LAN, T0 + dt.timedelta(days=5)) == first
    assert pt.leaf_paths(ca)[0].read_bytes() == s1                                  # reused
    pt.ensure_leaf(ca, LAN, T0 + dt.timedelta(days=pt.LEAF_DAYS - pt.RENEW_BEFORE_DAYS + 1))
    s2 = pt.leaf_paths(ca)[0].read_bytes()
    assert s2 != s1                                                                  # renewed near expiry
    pt.ensure_leaf(ca, LAN + ["192.168.1.77"], T0 + dt.timedelta(days=22))
    assert "192.168.1.77" in pt.leaf_info(ca)["hosts"] and pt.leaf_paths(ca)[0].read_bytes() != s2    # new address


def test_leaf_never_outlives_the_ca(ca):
    late = T0 + dt.timedelta(days=pt.CA_MAX_DAYS - 3)
    pt.issue_leaf(ca, LAN, late)
    assert pt.leaf_info(ca)["not_after"] <= pt._load_ca_cert(ca).not_valid_after_utc


def test_no_ca_means_no_leaf(home):
    assert pt.ensure_leaf(home, LAN, T0) is None
    assert pt.tls_pair(home, True, T0) is None


# ---------------------------------------------------------------- tls source order, feature gate
def test_default_off_and_tls_order(ca, monkeypatch):
    assert pt.trust_on(ca) is False                                                 # beta, OFF by default
    from jarves import mobile
    mobile.make_selfsigned(ca, "192.168.1.20")
    assert tailnet.choose_tls(ca, {}, trust=False)[1] == "selfsigned"
    monkeypatch.setattr(pt, "lan_hosts", lambda extra=None: LAN)
    got, src = tailnet.choose_tls(ca, {}, trust=True)
    assert src == "localca" and got == pt.leaf_paths(ca)
    assert tailnet.choose_tls(ca, {}, trust=None)[1] == "selfsigned"                # feature still off


def test_revoke_deletes_everything_and_falls_back(ca, monkeypatch):
    from jarves import mobile
    mobile.make_selfsigned(ca, "192.168.1.20")
    pt.ensure_leaf(ca, LAN, T0)
    r = pt.revoke(ca)
    assert r["removed"] == 4
    assert not pt.ca_dir(ca).exists() and not pt.leaf_paths(ca)[0].exists()
    assert pt.status(ca)["ca"] is False
    assert pt.ensure_leaf(ca, LAN, T0) is None                                      # nothing can be signed any more
    assert tailnet.choose_tls(ca, {}, trust=True)[1] == "selfsigned"


# ---------------------------------------------------------------- the listener really serves the chain (loopback only)
def test_verify_listener_with_only_the_ca(home):
    """ssl uses the real clock, so this one test issues its CA and leaf with the real clock (no fixed date is asserted)."""
    import socket
    pt.create_ca(home)
    pt.ensure_leaf(home, ["localhost", "127.0.0.1"])
    leaf_c, leaf_k = pt.leaf_paths(home)
    srv_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    srv_ctx.load_cert_chain(str(leaf_c), str(leaf_k))
    ls = socket.socket()
    ls.bind(("127.0.0.1", 0))
    ls.listen(2)
    port = ls.getsockname()[1]

    def accept():
        try:
            c, _a = ls.accept()
            with srv_ctx.wrap_socket(c, server_side=True):
                pass
        except (OSError, ssl.SSLError):
            pass
    t = threading.Thread(target=accept, daemon=True)
    t.start()
    ok, why = pt.verify_listener(home, "127.0.0.1", port)
    t.join(5)
    ls.close()
    assert ok, why
    assert pt.verify_listener(home, "127.0.0.1", 1)[0] is False                    # nothing listening: an honest failure


# ---------------------------------------------------------------- the paired-phone download route
@pytest.fixture
def srv(home):
    from test_mobile_access import FakeCore
    d = dashboard.Dashboard(home, Config(), free_port(), mobile_on=True, lan_host=HOST,
                            mobile_bridge=FakeCore().bridge()).start()
    yield d
    d.stop()


def test_ca_download_needs_a_paired_phone_and_the_feature(srv, home, monkeypatch):
    pt.create_ca(home, T0)
    assert req(srv, "GET", pt.CA_ROUTE)[0] == 401                                   # no token
    tok, _dev, _ = pair(srv)
    assert req(srv, "GET", pt.CA_ROUTE, token="x" * 40)[0] == 401                   # bad token
    assert req(srv, "GET", pt.CA_ROUTE, token=tok)[0] == 404                        # feature OFF: nothing is served
    monkeypatch.setattr(pt, "trust_on", lambda h: True)
    st, body, r = req(srv, "GET", pt.CA_ROUTE, token=tok)
    assert st == 200 and r.getheader("Content-Type") == "application/x-x509-ca-cert"
    assert body == pt.ca_certificate_pem(home) and b"PRIVATE" not in body
    key = pt.ca_paths(home)[1].read_bytes()
    assert key not in body
    # unpaired (revoked) phone: refused again
    req(srv, "POST", "/api/m/unpair", token=tok, body={})
    assert req(srv, "GET", pt.CA_ROUTE, token=tok)[0] == 401
    pt.revoke(home)
    tok2, _d2, _ = pair(srv, "Second")
    assert req(srv, "GET", pt.CA_ROUTE, token=tok2)[0] == 404                       # no CA: honest 404


def test_audit_records_download_without_the_certificate(srv, home, monkeypatch):
    pt.create_ca(home, T0)
    monkeypatch.setattr(pt, "trust_on", lambda h: True)
    tok, dev, _ = pair(srv)
    req(srv, "GET", pt.CA_ROUTE, token=tok)
    text = srv.mobile.audit_path.read_text("utf-8")
    assert "trust_ca_downloaded" in text
    assert "BEGIN CERTIFICATE" not in text and "PRIVATE KEY" not in text


# ---------------------------------------------------------------- CLI
def _cli(home, *argv):
    import argparse
    import io
    from contextlib import redirect_stderr, redirect_stdout
    from jarves import phone_cli
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    phone_cli.add_cli(sub)
    a = ap.parse_args(["phone", "trust", *argv])
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = phone_cli.cmd_phone(a, Config(), home)
    return rc, out.getvalue(), err.getvalue()


def test_cli_enable_needs_the_feature_then_revoke(home, monkeypatch):
    rc, _o, err = _cli(home, "enable")
    assert rc == 1 and "phone_trust" in err and not pt.ca_paths(home)[0].exists()
    monkeypatch.setattr(pt, "trust_on", lambda h: True)
    rc, out, _e = _cli(home, "enable")
    assert rc == 0 and pt.ca_paths(home)[0].is_file()
    assert "Install a certificate" in out and "SHA-256" in out and "private-network" in out
    assert "PRIVATE KEY" not in out
    rc, out, _e = _cli(home, "ca-path")
    assert rc == 0 and out.strip().endswith("ca.crt")
    rc, out, _e = _cli(home, "status", "--json")
    assert json.loads(out)["ca"] is True
    rc, out, _e = _cli(home, "revoke")
    assert rc == 0 and "User credentials" in out and not pt.ca_paths(home)[0].exists()
    assert _cli(home, "ca-path")[0] == 1
