"""WP-O release hosting on Cloudflare R2: SigV4 (AWS test vectors), a local S3 stub (no Cloudflare, no network),
signed latest.json round trip CI -> app, retention with pins, clean skip without secrets, hosted update check."""
import base64
import datetime as dt
import hashlib
import importlib.util
import json
import re
import shutil
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jarves import updates
from jarves.config import Config, ConfigError, load_config
from jarves.updates import UpdateChecker, UpdateError

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("pkg_release_r2", ROOT / "packaging" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
AK, SK = "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
VECTOR_TIME = dt.datetime(2013, 5, 24, tzinfo=dt.timezone.utc)


def _sig(h):
    return h["Authorization"].rsplit("Signature=", 1)[1]


# ---------------------------------------------------------------------- SigV4 against AWS's published examples
def test_sigv4_matches_aws_documented_examples():
    get = release.sigv4("GET", "https://examplebucket.s3.amazonaws.com/test.txt", {"Range": "bytes=0-9"}, EMPTY,
                        AK, SK, region="us-east-1", now=VECTOR_TIME)
    assert _sig(get) == "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    assert "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date" in get["Authorization"]
    body = b"Welcome to Amazon S3."
    put = release.sigv4("PUT", "https://examplebucket.s3.amazonaws.com/test$file.text",
                        {"Date": "Fri, 24 May 2013 00:00:00 GMT", "x-amz-storage-class": "REDUCED_REDUNDANCY"},
                        hashlib.sha256(body).hexdigest(), AK, SK, region="us-east-1", now=VECTOR_TIME)
    assert _sig(put) == "98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd"
    lst = release.sigv4("GET", "https://examplebucket.s3.amazonaws.com/?max-keys=2&prefix=J", {}, EMPTY, AK, SK,
                        region="us-east-1", now=VECTOR_TIME)
    assert _sig(lst) == "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"


# ---------------------------------------------------------------------- local S3 stub (moto-style, in-process)
class S3Stub:
    """Path-style S3 subset (PUT/GET/DELETE object, ListObjectsV2 with paging) that checks every request's SigV4
    signature and payload hash. Two keys per page so pagination is exercised."""

    def __init__(self, bucket="jarves-releases", access=AK, secret=SK):
        self.bucket, self.access, self.secret = bucket, access, secret
        self.objects: dict[str, tuple[bytes, dict]] = {}
        self.log: list[tuple[str, str]] = []
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _deny(self, code, what):
                self.send_response(code)
                self.end_headers()
                self.wfile.write(f"<Error><Code>{what}</Code></Error>".encode())

            def _handle(self):
                u = urllib.parse.urlsplit(self.path)
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                auth = self.headers.get("Authorization", "")
                m = re.match(r"AWS4-HMAC-SHA256 Credential=([^/]+)/(\d{8})/auto/s3/aws4_request, "
                             r"SignedHeaders=([^,]+), Signature=([0-9a-f]{64})$", auth)
                if not m or m.group(1) != stub.access:
                    return self._deny(403, "InvalidAccessKeyId")
                if hashlib.sha256(body).hexdigest() != self.headers.get("x-amz-content-sha256"):
                    return self._deny(400, "XAmzContentSHA256Mismatch")
                names = m.group(3).split(";")
                hdrs = {k: self.headers[k] for k in names if k not in ("host", "x-amz-date", "x-amz-content-sha256")}
                when = dt.datetime.strptime(self.headers["x-amz-date"], "%Y%m%dT%H%M%SZ").replace(
                    tzinfo=dt.timezone.utc)
                again = release.sigv4(self.command, f"http://{self.headers['Host']}{self.path}", hdrs,
                                      self.headers["x-amz-content-sha256"], stub.access, stub.secret, now=when)
                if _sig(again) != m.group(4):
                    return self._deny(403, "SignatureDoesNotMatch")
                parts = urllib.parse.unquote(u.path).lstrip("/").split("/", 1)
                if parts[0] != stub.bucket:
                    return self._deny(404, "NoSuchBucket")
                key = parts[1] if len(parts) > 1 else ""
                stub.log.append((self.command, key or u.query))
                if self.command == "PUT":
                    stub.objects[key] = (body, {"type": self.headers.get("Content-Type"),
                                                "cache": self.headers.get("Cache-Control")})
                    self.send_response(200)
                    self.end_headers()
                elif self.command == "DELETE":
                    stub.objects.pop(key, None)
                    self.send_response(204)
                    self.end_headers()
                elif key:
                    if key not in stub.objects:
                        return self._deny(404, "NoSuchKey")
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(stub.objects[key][0])
                else:
                    q = dict(urllib.parse.parse_qsl(u.query))
                    keys = sorted(k for k in stub.objects if k.startswith(q.get("prefix", "")))
                    start = int(q.get("continuation-token", "0"))
                    page, more = keys[start:start + 2], start + 2 < len(keys)
                    xml = "".join(f"<Contents><Key>{k}</Key></Contents>" for k in page)
                    nxt = f"<NextContinuationToken>{start + 2}</NextContinuationToken>" if more else ""
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write((f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                                      f"<IsTruncated>{'true' if more else 'false'}</IsTruncated>{xml}{nxt}"
                                      "</ListBucketResult>").encode())

            do_PUT = do_GET = do_DELETE = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.endpoint = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def s3():
    stub = S3Stub()
    yield stub
    stub.close()


def _keypair():
    k = Ed25519PrivateKey.generate()
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()
    pub = base64.b64encode(k.public_key().public_bytes(serialization.Encoding.Raw,
                                                       serialization.PublicFormat.Raw)).decode()
    return pem, pub


def _dist(tmp_path, version, data=None):
    d = tmp_path / f"dist-{version}"
    d.mkdir()
    exe = d / f"JARVES-Setup-{version}.exe"
    exe.write_bytes(data or f"MZ installer {version}".encode())
    sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    (d / (exe.name + ".sha256")).write_text(f"{sha}  {exe.name}\n", encoding="ascii")
    (d / "build-info.json").write_text(json.dumps({
        "version": version, "commit": "c0ffee" * 6 + "abcd", "built": "2026-10-19T10:00:00Z", "channel": "main",
        "installer": exe.name, "sha256": sha, "size": exe.stat().st_size, "python": "3.12.15", "godot": "4.7.2"}),
        encoding="utf-8")
    return d


def _env(stub, pem, **kw):
    return {"R2_ACCOUNT_ID": "0123456789abcdef", "R2_ACCESS_KEY_ID": stub.access,
            "R2_SECRET_ACCESS_KEY": stub.secret, "R2_BUCKET": stub.bucket, "R2_PUBLIC_BASE": "https://pub-x.r2.dev",
            "JARVES_UPDATE_SIGNING_KEY": pem, "R2_ENDPOINT": stub.endpoint, **kw}


# ---------------------------------------------------------------------- CI side
def test_skips_cleanly_without_secrets_and_warns_on_partial(capsys):
    assert release.r2_publish(Path("does-not-matter"), env={}) == 0
    assert "SKIPPED - no R2 secrets" in capsys.readouterr().out
    assert release.r2_publish(Path("does-not-matter"), env={"R2_BUCKET": "b", "R2_ACCOUNT_ID": " "}) == 0
    out = capsys.readouterr().out
    assert out.startswith("::warning::") and "R2_ACCESS_KEY_ID" in out and "R2_ACCOUNT_ID" in out


def test_publish_uploads_signed_manifest_and_keeps_newest_three_plus_pins(tmp_path, s3, capsys):
    pem, pub = _keypair()
    prefix = "jarves/windows"
    s3.objects[f"{prefix}/pinned.txt"] = (b"# rollback point\nwin-v0.1.0.1\n", {})
    s3.objects["other/JARVES-Setup-0.0.0.1.exe"] = (b"not ours", {})            # other folder: never touched
    s3.objects[f"{prefix}/notes.txt"] = (b"owner file", {})
    for v in ("0.1.0.1", "0.1.0.2", "0.1.0.3", "0.1.0.4", "0.1.0.5"):
        assert release.r2_publish(_dist(tmp_path, v), keep=3, env=_env(s3, pem)) == 0
    out = capsys.readouterr().out
    assert pem.split("\n")[1] not in out and s3.secret not in out                 # no secret in the log
    exes = sorted(k for k in s3.objects if k.startswith(prefix) and k.endswith(".exe"))
    assert exes == [f"{prefix}/JARVES-Setup-0.1.0.{n}.exe" for n in (1, 3, 4, 5)]  # 0.1.0.1 pinned, 0.1.0.2 pruned
    assert f"{prefix}/JARVES-Setup-0.1.0.2.exe.sha256" not in s3.objects
    assert {"other/JARVES-Setup-0.0.0.1.exe", f"{prefix}/notes.txt", f"{prefix}/pinned.txt"} <= set(s3.objects)
    body, meta = s3.objects[f"{prefix}/latest.json"]
    assert meta["type"] == "application/json"
    m = updates.verify_manifest(json.loads(body), [pub])                          # CI signature -> app verify
    exe = s3.objects[f"{prefix}/JARVES-Setup-0.1.0.5.exe"][0]
    assert m["version"] == "0.1.0.5" and m["sha256"] == hashlib.sha256(exe).hexdigest() and m["size"] == len(exe)
    assert m["url"] == "https://pub-x.r2.dev/jarves/windows/JARVES-Setup-0.1.0.5.exe"
    assert m["commit"] and m["built"] and m["channel"] == "main"
    # order: installer before manifest (a PC never sees a manifest whose installer is missing)
    puts = [k for c, k in s3.log if c == "PUT"]
    last = max(i for i, k in enumerate(puts) if k == f"{prefix}/latest.json")
    assert puts.index(f"{prefix}/JARVES-Setup-0.1.0.5.exe") < last == len(puts) - 1


def test_rerun_of_an_older_build_does_not_replace_a_newer_manifest(tmp_path, s3, capsys):
    pem, _ = _keypair()
    release.r2_publish(_dist(tmp_path, "0.1.0.7"), env=_env(s3, pem))
    release.r2_publish(_dist(tmp_path, "0.1.0.6"), env=_env(s3, pem))
    assert "already has the newer 0.1.0.7" in capsys.readouterr().out
    assert json.loads(s3.objects["jarves/windows/latest.json"][0])["manifest"]["version"] == "0.1.0.7"
    assert "jarves/windows/JARVES-Setup-0.1.0.6.exe" not in s3.objects


def test_wrong_credentials_fail_loudly(tmp_path, s3):
    pem, _ = _keypair()
    with pytest.raises(release.S3Error, match="403"):
        release.r2_publish(_dist(tmp_path, "0.1.0.7"), env=_env(s3, pem, R2_SECRET_ACCESS_KEY="wrong"))


def test_custom_prefix_and_retention_unit():
    keys = [f"p/JARVES-Setup-0.1.0.{n}.exe" for n in range(1, 7)] + ["p/JARVES-Setup-0.1.0.6.exe.sha256",
                                                                     "p/sub/JARVES-Setup-0.1.0.1.exe", "p/latest.json"]
    gone = release.r2_to_prune(keys, "p", 3, {"0.1.0.2"}, "0.1.0.6")
    assert gone == ["p/JARVES-Setup-0.1.0.1.exe", "p/JARVES-Setup-0.1.0.3.exe"]
    assert release.parse_pins("0.1.0.5\nJARVES-Setup-0.2.0.1.exe # keep\n# 9.9.9.9\n") == {"0.1.0.5", "0.2.0.1"}


# ---------------------------------------------------------------------- signing round trip
def _manifest(**kw):
    m = {"product": "jarves", "platform": "windows-x64", "channel": "main", "version": "0.1.0.9",
         "installer": "JARVES-Setup-0.1.0.9.exe", "sha256": hashlib.sha256(b"MZ new").hexdigest(), "size": 6,
         "url": "https://pub-x.r2.dev/jarves/windows/JARVES-Setup-0.1.0.9.exe", "commit": "abc", "built": "now",
         "published": "2026-10-19T10:00:00+00:00", "notes_he": "גרסה חדשה"}
    m.update(kw)
    return m


def test_manifest_signing_round_trip_and_tamper_detection():
    pem, pub = _keypair()
    _, other = _keypair()
    env = release.sign_manifest(_manifest(), pem)
    assert env["signature"]["key_id"] == updates.key_id(base64.b64decode(pub))
    assert release.canonical(env["manifest"]) == updates.canonical(env["manifest"])   # one byte format, two places
    assert updates.verify_manifest(json.loads(json.dumps(env)), [other, pub])["version"] == "0.1.0.9"
    bad = json.loads(json.dumps(env))
    bad["manifest"]["sha256"] = "0" * 64
    for envelope, keys, code in [(bad, [pub], "UPDATE_BAD_SIGNATURE"), (env, [other], "UPDATE_BAD_SIGNATURE"),
                                 (env, [], "UPDATE_NO_KEY"), ({"manifest": {}}, [pub], "UPDATE_BAD_MANIFEST"),
                                 ({**env, "signature": {"alg": "rsa", "sig": ""}}, [pub], "UPDATE_BAD_SIGNATURE")]:
        with pytest.raises(UpdateError) as e:
            updates.verify_manifest(envelope, keys)
        assert e.value.code == code


@pytest.mark.skipif(not shutil.which("openssl"), reason="openssl CLI not on PATH (CI Ubuntu has it)")
def test_openssl_fallback_signature_verifies_in_the_app():
    pem, pub = _keypair()
    sig, raw = release.openssl_sign(updates.canonical(_manifest()), pem)
    assert base64.b64encode(raw).decode() == pub
    env = {"manifest": _manifest(), "signature": {"alg": "ed25519", "sig": base64.b64encode(sig).decode()}}
    assert updates.verify_manifest(env, [pub])["version"] == "0.1.0.9"


def test_keygen_writes_private_key_once_and_returns_public(tmp_path):
    out = tmp_path / "signing.pem"
    pub = release.keygen(out)
    assert out.read_text().startswith("-----BEGIN PRIVATE KEY-----") and len(base64.b64decode(pub)) == 32
    env = release.sign_manifest(_manifest(), out.read_text())
    assert updates.verify_manifest(env, [pub])
    with pytest.raises(SystemExit):
        release.keygen(out)                                                     # never overwrites a key


# ---------------------------------------------------------------------- app side: hosted source
BASE = "https://pub-x.r2.dev/jarves/windows"


class FakeHost:
    def __init__(self, files: dict):
        self.files, self.calls = files, []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        if url not in self.files:
            return 404, None
        body = self.files[url]
        return 200, (json.loads(body) if url.endswith(".json") else body)


def _hosted(tmp_path, exe=b"MZ new", served=None, **kw):
    pem, pub = _keypair()
    env = release.sign_manifest(_manifest(**{"sha256": hashlib.sha256(exe).hexdigest(), "size": len(exe), **kw}),
                                pem)
    host = FakeHost({f"{BASE}/latest.json": json.dumps(env).encode(),
                     f"{BASE}/JARVES-Setup-0.1.0.9.exe": served if served is not None else exe})
    return host, pub


def test_hosted_check_finds_update_without_sending_github_token(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVES_UPDATE_TOKEN", "github_pat_test")
    host, pub = _hosted(tmp_path)
    u = UpdateChecker(tmp_path, http=host, current="0.1.0.8", base_url=BASE, public_keys=[pub])
    s = u.check(force=True)
    assert s["available"] and s["latest"]["version"] == "0.1.0.9" and s["source"] == f"hosted:{BASE}"
    assert all("Authorization" not in h for _, h in host.calls)
    assert "0.1.0.9" in u.notice("he")
    assert not UpdateChecker(tmp_path, http=host, current="0.1.0.9", base_url=BASE,
                             public_keys=[pub]).check(force=True)["available"]


def test_hosted_check_without_trusted_key_is_unavailable_not_up_to_date(tmp_path):
    host, _ = _hosted(tmp_path)
    s = UpdateChecker(tmp_path, http=host, current="0.1.0.8", base_url=BASE, public_keys=[]).check(force=True)
    assert not s["available"] and "signing key" in s["error"]
    _, other = _keypair()
    s = UpdateChecker(tmp_path, http=host, current="0.1.0.8", base_url=BASE, public_keys=[other]).check(force=True)
    assert not s["available"] and "not signed by a trusted" in s["error"]
    s = UpdateChecker(tmp_path, http=FakeHost({}), current="0.1.0.8", base_url=BASE,
                      public_keys=[other]).check(force=True)
    assert not s["available"] and "HTTP 404" in s["error"]


def test_hosted_install_needs_approval_and_verifies_sha_and_size(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(updates, "backup_before_update", lambda home, label: home / "bak.zip")
    host, pub = _hosted(tmp_path)
    u = UpdateChecker(tmp_path, http=host, current="0.1.0.8", base_url=BASE, public_keys=[pub])
    u.check(force=True)
    with pytest.raises(UpdateError) as e:
        u.prepare_install(approved=False)
    assert e.value.code == "UPDATE_NEEDS_APPROVAL" and not (tmp_path / "updates").exists()
    r = u.prepare_install(approved=True)
    assert Path(r["installer"]).read_bytes() == b"MZ new" and r["version"] == "0.1.0.9"
    for served in (b"MZ evil", b"MZ new!"):
        host, pub = _hosted(tmp_path, served=served)
        u = UpdateChecker(tmp_path, http=host, current="0.1.0.8", base_url=BASE, public_keys=[pub])
        u.check(force=True)
        with pytest.raises(UpdateError) as e:
            u.prepare_install(approved=True)
        assert e.value.code == "UPDATE_CHECKSUM_MISMATCH"


def test_cached_release_is_reverified_at_install_time(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(updates, "backup_before_update", lambda home, label: home / "bak.zip")
    host, pub = _hosted(tmp_path, served=b"MZ evil")
    u = UpdateChecker(tmp_path, http=host, current="0.1.0.8", base_url=BASE, public_keys=[pub])
    u.check(force=True)
    d = json.loads((tmp_path / "updates.json").read_text(encoding="utf-8"))
    d["release"]["asset"]["digest"] = "sha256:" + hashlib.sha256(b"MZ evil").hexdigest()   # local cache edited
    d["release"]["asset"]["size"] = len(b"MZ evil")
    (tmp_path / "updates.json").write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(UpdateError) as e:
        u.prepare_install(approved=True)
    assert e.value.code == "UPDATE_CHECKSUM_MISMATCH"


def test_manifest_must_be_stable_windows_https(tmp_path):
    for kw in ({"channel": "pr"}, {"url": "http://pub-x.r2.dev/a.exe"}, {"installer": "evil.exe"}, {"size": 0}):
        host, pub = _hosted(tmp_path, **kw)
        s = UpdateChecker(tmp_path, http=host, current="0.1.0.8", base_url=BASE, public_keys=[pub]).check(force=True)
        assert not s["available"] and s["error"], kw


def test_switching_source_drops_the_cached_answer(tmp_path):
    host, pub = _hosted(tmp_path)
    UpdateChecker(tmp_path, http=host, current="0.1.0.8", base_url=BASE, public_keys=[pub]).check(force=True)
    gh = UpdateChecker(tmp_path, http=lambda url, h: (200, []), current="0.1.0.8", base_url="")
    assert not gh.status()["available"]
    assert not gh.check()["available"]                  # not served from the hosted cache


def test_config_update_base_url(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVES_UPDATE_BASE_URL", raising=False)
    p = tmp_path / "config.toml"
    p.write_text(f'[update]\nbase_url = "{BASE}/"\npublic_key = "abc="\n', encoding="utf-8")
    cfg, _ = load_config(p)
    u = UpdateChecker(tmp_path, cfg=cfg, current="0.1.0.8")
    assert u.base_url == BASE and u.public_keys[-1] == "abc="
    assert UpdateChecker(tmp_path, cfg=Config(), current="0.1.0.8").source.startswith("github:")
    p.write_text('[update]\nbase_url = "http://insecure.example"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="https"):
        load_config(p)
    # the installed build's default (CI variable baked into _build_info.json); config still wins
    bi = tmp_path / "_build_info.json"
    bi.write_text(json.dumps({"version": "0.1.0.8", "update_base_url": BASE}), encoding="utf-8")
    monkeypatch.setattr(updates, "BUILD_INFO", bi)
    assert UpdateChecker(tmp_path, cfg=Config()).base_url == BASE
    assert UpdateChecker(tmp_path, cfg=cfg).base_url == BASE
    monkeypatch.setenv("JARVES_UPDATE_BASE_URL", "http://plain.example")
    assert UpdateChecker(tmp_path, cfg=Config()).base_url == ""         # plain HTTP is never used
