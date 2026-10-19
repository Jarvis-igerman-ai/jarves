"""WP-O release hosting in a separate PUBLIC GitHub repo (release files only): a local fake GitHub REST server
(no GitHub, no network), signed latest.json round trip CI -> app, retention with pins, clean skip without
secrets, hosted update check + approval-gated install, end to end from the CI publish to the app's install."""
import base64
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
spec = importlib.util.spec_from_file_location("pkg_release_public", ROOT / "packaging" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

REPO = "igalgerman/jarvis-releases"
TOKEN = "github_pat_fake_releases_token"


# ---------------------------------------------------------------------- local fake GitHub (REST + uploads)
class GitHubStub:
    """The REST calls public_publish makes, with GitHub's shapes: releases (draft/latest), tags, asset uploads via
    the release's upload_url template, deletes. Rejects any request without the right Bearer token."""

    def __init__(self, repo=REPO, token=TOKEN):
        self.repo, self.token = repo, token
        self.releases: list[dict] = []          # newest last
        self.blobs: dict[int, bytes] = {}       # asset id -> bytes
        self.tags: set[str] = set()
        self.log: list[tuple[str, str]] = []
        self._id = 100
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None):
                body = json.dumps(obj).encode() if obj is not None else b""
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                if self.headers.get("Authorization") != f"Bearer {stub.token}":
                    return self._send(401, {"message": "Bad credentials"})
                u = urllib.parse.urlsplit(self.path)
                stub.log.append((self.command, u.path))
                return self._send(*stub.route(self.command, u.path, dict(urllib.parse.parse_qsl(u.query)), body,
                                              self.headers.get("Content-Type")))

            do_GET = do_POST = do_PATCH = do_DELETE = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def nid(self):
        self._id += 1
        return self._id

    def view(self, r):
        return {**r, "upload_url": f"{self.base}/uploads/repos/{self.repo}/releases/{r['id']}/assets{{?name,label}}"}

    def latest(self):
        pub = [r for r in self.releases if not r["draft"]]
        flagged = [r for r in pub if r.get("latest")]
        return (flagged or pub or [None])[-1]

    def route(self, method, path, q, body, ctype):
        pre = f"/repos/{self.repo}"
        up = re.fullmatch(rf"/uploads{pre}/releases/(\d+)/assets", path)
        if up and method == "POST":
            r = next(x for x in self.releases if x["id"] == int(up.group(1)))
            if any(a["name"] == q["name"] for a in r["assets"]):
                return 422, {"message": "already_exists"}
            a = {"id": self.nid(), "name": q["name"], "size": len(body), "content_type": ctype}
            self.blobs[a["id"]] = body
            r["assets"].append(a)
            return 201, a
        if not path.startswith(pre):
            return 404, {"message": "Not Found"}
        p = path[len(pre):]
        if method == "GET" and p == "/releases/latest":
            r = self.latest()
            return (200, self.view(r)) if r else (404, {"message": "Not Found"})
        if method == "GET" and p.startswith("/releases/tags/"):
            r = next((x for x in self.releases if x["tag_name"] == p.rsplit("/", 1)[1]), None)
            return (200, self.view(r)) if r else (404, {"message": "Not Found"})
        if method == "GET" and p == "/releases":
            per, page = int(q.get("per_page", 30)), int(q.get("page", 1))
            items = list(reversed(self.releases))[(page - 1) * per: page * per]
            return 200, [self.view(r) for r in items]
        if method == "POST" and p == "/releases":
            d = json.loads(body)
            r = {"id": self.nid(), "tag_name": d["tag_name"], "name": d["name"], "body": d["body"],
                 "draft": d["draft"], "prerelease": d["prerelease"], "assets": [], "latest": False,
                 "created_at": f"2026-10-19T10:00:{len(self.releases):02d}Z"}
            self.releases.append(r)
            self.tags.add(d["tag_name"])
            return 201, self.view(r)
        m = re.fullmatch(r"/releases/(\d+)", p)
        if m and method == "PATCH":
            r = next(x for x in self.releases if x["id"] == int(m.group(1)))
            d = json.loads(body)
            r["draft"] = d.get("draft", r["draft"])
            if d.get("make_latest") == "true":
                for x in self.releases:
                    x["latest"] = x is r
            return 200, self.view(r)
        if m and method == "DELETE":
            self.releases = [x for x in self.releases if x["id"] != int(m.group(1))]
            return 204, None
        m = re.fullmatch(r"/releases/assets/(\d+)", p)
        if m and method == "DELETE":
            for r in self.releases:
                r["assets"] = [a for a in r["assets"] if a["id"] != int(m.group(1))]
            return 204, None
        m = re.fullmatch(r"/git/refs/tags/(.+)", p)
        if m and method == "DELETE":
            if m.group(1) not in self.tags:
                return 404, {"message": "Reference does not exist"}
            self.tags.discard(m.group(1))
            return 204, None
        return 404, {"message": "Not Found"}

    def asset(self, tag, name):
        r = next(x for x in self.releases if x["tag_name"] == tag)
        return self.blobs[next(a["id"] for a in r["assets"] if a["name"] == name)]

    def downloads(self):
        """What anonymous https://github.com/<repo>/releases/... download links would serve (the app's view)."""
        files = {}
        for r in self.releases:
            if r["draft"]:
                continue
            for a in r["assets"]:
                files[f"https://github.com/{self.repo}/releases/download/{r['tag_name']}/{a['name']}"] = \
                    self.blobs[a["id"]]
        lt = self.latest()
        for a in (lt or {}).get("assets", []):
            files[f"https://github.com/{self.repo}/releases/latest/download/{a['name']}"] = self.blobs[a["id"]]
        return files

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def gh():
    stub = GitHubStub()
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
    d.mkdir(parents=True)
    exe = d / f"JARVES-Setup-{version}.exe"
    exe.write_bytes(data or f"MZ installer {version}".encode())
    sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    (d / (exe.name + ".sha256")).write_text(f"{sha}  {exe.name}\n", encoding="ascii")
    (d / "build-info.json").write_text(json.dumps({
        "version": version, "commit": "c0ffee" * 6 + "abcd", "built": "2026-10-19T10:00:00Z", "channel": "main",
        "installer": exe.name, "sha256": sha, "size": exe.stat().st_size, "python": "3.12.15", "godot": "4.7.2"}),
        encoding="utf-8")
    return d


def _publish(stub, dist, pem, keep=3, token=TOKEN, **env):
    client = release.GitHub(stub.repo, token, api_base=stub.base)
    return release.public_publish(dist, keep=keep, gh=client,
                                  env={"RELEASES_TOKEN": token, "JARVES_UPDATE_SIGNING_KEY": pem, **env})


# ---------------------------------------------------------------------- CI side
def test_skips_cleanly_without_secrets_and_warns_on_partial(capsys):
    assert release.public_publish(Path("does-not-matter"), env={}) == 0
    assert "SKIPPED - secrets RELEASES_TOKEN and JARVES_UPDATE_SIGNING_KEY are not set" in capsys.readouterr().out
    assert release.public_publish(Path("does-not-matter"), env={"RELEASES_TOKEN": "github_pat_SECRETVALUE",
                                                                "JARVES_UPDATE_SIGNING_KEY": " "}) == 0
    out = capsys.readouterr().out
    assert out.startswith("::warning::") and "JARVES_UPDATE_SIGNING_KEY" in out and "SECRETVALUE" not in out


def test_publish_releases_signed_manifest_and_keeps_newest_three_plus_pinned(tmp_path, gh, capsys):
    pem, pub = _keypair()
    gh.releases.append({"id": 1, "tag_name": "win-v0.1.0.0", "name": "JARVES 0.1.0.0 [pinned]", "body": "",
                        "draft": False, "prerelease": False, "assets": [], "latest": False, "created_at": "2026-01"})
    gh.releases.append({"id": 2, "tag_name": "notes-2026", "name": "owner notes", "body": "", "draft": False,
                        "prerelease": False, "assets": [], "latest": False, "created_at": "2026-02"})
    for v in ("0.1.0.1", "0.1.0.2", "0.1.0.3", "0.1.0.4", "0.1.0.5"):
        assert _publish(gh, _dist(tmp_path, v), pem) == 0
    out = capsys.readouterr().out
    assert pem.split("\n")[1] not in out and TOKEN not in out                     # no secret in the log
    tags = sorted(r["tag_name"] for r in gh.releases)
    assert tags == ["notes-2026", "win-v0.1.0.0", "win-v0.1.0.3", "win-v0.1.0.4", "win-v0.1.0.5"]
    assert "win-v0.1.0.1" not in gh.tags and "win-v0.1.0.2" not in gh.tags      # tags of pruned releases gone
    assert gh.latest()["tag_name"] == "win-v0.1.0.5" and not gh.latest()["draft"]
    names = sorted(a["name"] for a in gh.latest()["assets"])
    assert names == ["JARVES-Setup-0.1.0.5.exe", "JARVES-Setup-0.1.0.5.exe.sha256", "latest.json"]
    m = updates.verify_manifest(json.loads(gh.asset("win-v0.1.0.5", "latest.json")), [pub])  # CI sig -> app verify
    exe = gh.asset("win-v0.1.0.5", "JARVES-Setup-0.1.0.5.exe")
    assert m["version"] == "0.1.0.5" and m["sha256"] == hashlib.sha256(exe).hexdigest() and m["size"] == len(exe)
    assert m["url"] == f"https://github.com/{REPO}/releases/download/win-v0.1.0.5/JARVES-Setup-0.1.0.5.exe"
    assert m["commit"] and m["built"] and m["channel"] == "main"
    # draft first, published only after every file is uploaded
    calls = [(c, p) for c, p in gh.log if p.endswith("/releases") and c == "POST" or "/uploads/" in p or c == "PATCH"]
    last = [i for i, (c, p) in enumerate(calls) if c == "PATCH"][-1]
    assert [c for c, _ in calls[last - 4:last + 1]] == ["POST", "POST", "POST", "POST", "PATCH"]


def test_rerun_is_idempotent_and_older_build_never_replaces_newer(tmp_path, gh, capsys):
    pem, _ = _keypair()
    _publish(gh, _dist(tmp_path, "0.1.0.7"), pem)
    gh.releases[-1]["draft"] = True                                   # a run that died before publishing
    gh.releases[-1]["assets"] = gh.releases[-1]["assets"][:1]
    assert _publish(gh, _dist(tmp_path / "again", "0.1.0.7"), pem) == 0
    assert len(gh.releases) == 1 and not gh.releases[0]["draft"] and len(gh.releases[0]["assets"]) == 3
    _publish(gh, _dist(tmp_path, "0.1.0.6"), pem)
    assert "already has the newer win-v0.1.0.7" in capsys.readouterr().out
    assert [r["tag_name"] for r in gh.releases] == ["win-v0.1.0.7"]


def test_wrong_token_fails_loudly(tmp_path, gh):
    pem, _ = _keypair()
    with pytest.raises(release.GitHubError, match="401"):
        _publish(gh, _dist(tmp_path, "0.1.0.7"), pem, token="wrong")


def test_bad_repo_name_and_tampered_dist_refuse(tmp_path):
    pem, _ = _keypair()
    env = {"RELEASES_TOKEN": TOKEN, "JARVES_UPDATE_SIGNING_KEY": pem}
    with pytest.raises(SystemExit, match="RELEASES_REPO"):
        release.public_publish(tmp_path, env={**env, "RELEASES_REPO": "x/../y?z"})
    d = _dist(tmp_path, "0.1.0.7")
    (d / "JARVES-Setup-0.1.0.7.exe").write_bytes(b"swapped after the tests")
    with pytest.raises(SystemExit, match="SHA256"):
        release.public_publish(d, env=env)


# ---------------------------------------------------------------------- signing round trip
def _manifest(**kw):
    m = {"product": "jarves", "platform": "windows-x64", "channel": "main", "version": "0.1.0.9",
         "installer": "JARVES-Setup-0.1.0.9.exe", "sha256": hashlib.sha256(b"MZ new").hexdigest(), "size": 6,
         "url": f"https://github.com/{REPO}/releases/download/win-v0.1.0.9/JARVES-Setup-0.1.0.9.exe", "commit": "abc", "built": "now",
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
BASE = f"https://github.com/{REPO}/releases/latest/download"


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
                     env["manifest"]["url"]: served if served is not None else exe})
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
    for kw in ({"channel": "pr"}, {"url": "http://example.com/a.exe"}, {"installer": "evil.exe"}, {"size": 0}):
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


def test_default_source_is_the_public_repo_once_a_key_is_trusted(tmp_path, monkeypatch):
    for k in ("JARVES_UPDATE_BASE_URL", "JARVES_RELEASES_REPO"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(updates, "BUILD_INFO", tmp_path / "none.json")
    assert UpdateChecker(tmp_path, current="0.1.0.8", public_keys=[]).source.startswith("github:")   # no key yet
    u = UpdateChecker(tmp_path, current="0.1.0.8", public_keys=["k="])
    assert u.base_url == BASE and u.source == f"hosted:{BASE}"
    assert UpdateChecker(tmp_path, cfg=Config(), current="0.1.0.8", public_keys=["k="]).base_url == BASE


def test_end_to_end_ci_publish_then_app_check_and_install(tmp_path, gh, monkeypatch):
    """CI publishes into the fake public repo; the app (anonymous downloads only) finds, verifies and installs."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(updates, "backup_before_update", lambda home, label: home / "bak.zip")
    pem, pub = _keypair()
    _publish(gh, _dist(tmp_path, "0.1.0.9", b"MZ the real build"), pem)
    host = FakeHost(gh.downloads())
    home = tmp_path / "home"
    home.mkdir()
    u = UpdateChecker(home, http=host, current="0.1.0.8", public_keys=[pub])
    s = u.check(force=True)
    assert s["available"] and s["latest"]["version"] == "0.1.0.9", s["error"]
    assert all("Authorization" not in h for _, h in host.calls)                   # anonymous
    assert all(url.startswith(f"https://github.com/{REPO}/releases/") for url, _ in host.calls)  # no REST API
    r = u.prepare_install(approved=True)
    assert Path(r["installer"]).read_bytes() == b"MZ the real build"


def test_generated_notes_do_not_pin_every_release():
    """Regression: the notes explain `[pinned]`; that mention must not pin (it made prune keep everything)."""
    info = {"version": "0.1.0.5", "commit": "abc", "built": "now", "installer": "JARVES-Setup-0.1.0.5.exe",
            "sha256": "0" * 64, "size": 1 << 20}
    for body in (release.notes(info, "https://ci"), release.public_notes(info)):
        assert "[pinned]" in body and not release.is_pinned({"name": "JARVES 0.1.0.5", "body": body})
        assert release.is_pinned({"name": "JARVES 0.1.0.5", "body": body + "\nrollback [pinned]"})
