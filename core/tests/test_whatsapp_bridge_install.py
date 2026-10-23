"""Issue #17: the owner-initiated, signed "Download the WhatsApp bridge" step. Offline only: a fake HTTP callable and the
local GitHub stub of test_release_hosting.py; nothing touches the network, no real bridge is started."""
from __future__ import annotations

import base64
import hashlib
import json

import pytest
from test_release_hosting import GitHubStub, release  # noqa: F401  (release = packaging/release.py)
from release_fakes import Keys

from jarves import cli, dashboard, updates
from jarves import whatsapp_bridge as wab
from jarves import whatsapp_bridge_install as wbi
from jarves.config import Config
from test_dashboard import req  # noqa: F401

BASE = "https://bridge.example.test/whatsapp-bridge"
EXE = b"MZ fake whatsapp bridge 0.1.0\n" + b"\x00" * 2048


def manifest(data: bytes = EXE, version: str = "0.1.0", **over) -> dict:
    m = {"product": wbi.PRODUCT, "platform": wbi.PLATFORM, "asset": wbi.ASSET, "version": version, "commit": "abc",
         "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "url": f"{BASE}/whatsapp_bridge-{version}.exe",
         "published": "2026-10-23T10:00:00+00:00"}
    m.update(over)
    return m


def sign(keys: Keys, m: dict, domain: bytes = wbi.SIGN_DOMAIN) -> dict:
    sig = keys.private.sign(updates.canonical(m, domain))
    return {"format": 1, "manifest": m, "signature": {"alg": "ed25519", "key_id": "t",
                                                      "sig": base64.b64encode(sig).decode()}}


class Net:
    """The only 'network': url -> (status, body). Records every URL asked for and the headers sent."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[int, object]] = {}
        self.hits: list[str] = []
        self.headers: list[dict] = []

    def put(self, name: str, body: object, code: int = 200) -> None:
        self.files[f"{BASE}/{name}"] = (code, body)

    def __call__(self, url: str, headers: dict):
        self.hits.append(url)
        self.headers.append(headers)
        if url not in self.files:
            return 404, None
        if isinstance(self.files[url][1], Exception):
            raise self.files[url][1]
        return self.files[url]


@pytest.fixture
def keys():
    return Keys()


@pytest.fixture
def net(keys):
    n = Net()
    n.put(wbi.MANIFEST, json.dumps(sign(keys, manifest())).encode())
    n.put("whatsapp_bridge-0.1.0.exe", EXE)
    return n


def go(home, net, keys, **kw):
    kw.setdefault("platform", "win32")
    kw.setdefault("running", lambda: False)
    return wbi.install(home, approved=True, http=net, public_keys=[keys.public], base=BASE, **kw)


@pytest.fixture(autouse=True)
def no_bridge_elsewhere(monkeypatch, tmp_path):
    monkeypatch.delenv(wab.BRIDGE_ENV, raising=False)
    monkeypatch.delenv(wbi.BASE_ENV, raising=False)
    monkeypatch.setattr(wab, "status_or_none", lambda h: None)


def test_download_installs_verified_exe_and_signed_manifest(tmp_path, net, keys):
    r = go(tmp_path, net, keys)
    assert r["ok"] and r["version"] == "0.1.0" and r["sha256"] == hashlib.sha256(EXE).hexdigest()
    exe = wbi.installed_exe(tmp_path)
    assert exe.read_bytes() == EXE and wbi.sidecar(tmp_path).is_file()
    assert wbi.verify_installed(tmp_path, [keys.public])["version"] == "0.1.0"
    assert wbi.status(tmp_path)["downloaded"] is True
    assert not list(exe.parent.glob("*.partial"))                       # atomic: nothing half-written left
    assert net.hits == [f"{BASE}/{wbi.MANIFEST}", f"{BASE}/whatsapp_bridge-0.1.0.exe"]
    assert all("Authorization" not in h for h in net.headers)           # no token is ever sent


def test_bridge_command_finds_the_downloaded_exe_only_with_its_manifest(tmp_path, net, keys):
    go(tmp_path, net, keys)
    assert wab.bridge_command(tmp_path) == [str(wbi.installed_exe(tmp_path))] and wab.bridge_installed(tmp_path)
    wbi.sidecar(tmp_path).unlink()
    assert wab.bridge_command(tmp_path) is None                          # a bare exe without its signed manifest is ignored


def test_nothing_downloads_without_the_owners_approval(tmp_path, net, keys):
    with pytest.raises(PermissionError):
        wbi.install(tmp_path, approved=False, http=net, public_keys=[keys.public], base=BASE, platform="win32")
    assert net.hits == [] and not wbi.installed_exe(tmp_path).exists()


def test_not_published_is_an_honest_message_not_a_crash(tmp_path, keys):
    with pytest.raises(wbi.BridgeInstallError) as e:
        go(tmp_path, Net(), keys)
    assert e.value.code == "BRIDGE_NOT_PUBLISHED" and "not published yet" in e.value.message and e.value.he
    assert not wbi.installed_exe(tmp_path).exists()


@pytest.mark.parametrize("case,code", [
    ("unsigned", "BRIDGE_BAD_MANIFEST"), ("other_key", "BRIDGE_BAD_SIGNATURE"),
    ("installer_domain", "BRIDGE_BAD_SIGNATURE"), ("tampered_sha", "BRIDGE_BAD_SIGNATURE"),
    ("wrong_product", "BRIDGE_BAD_MANIFEST"), ("http_url", "BRIDGE_BAD_MANIFEST"), ("huge", "BRIDGE_BAD_MANIFEST"),
    ("bad_version", "BRIDGE_BAD_MANIFEST"), ("wrong_sha", "BRIDGE_CHECKSUM"), ("wrong_size", "BRIDGE_CHECKSUM"),
    ("exe_500", "BRIDGE_DOWNLOAD_FAILED"), ("offline", "BRIDGE_OFFLINE"), ("manifest_500", "BRIDGE_OFFLINE")])
def test_every_bad_download_installs_nothing(tmp_path, net, keys, case, code):
    good = sign(keys, manifest())
    if case == "unsigned":
        net.put(wbi.MANIFEST, json.dumps({"format": 1, "manifest": manifest(), "signature": None}).encode())
    elif case == "other_key":
        net.put(wbi.MANIFEST, json.dumps(sign(Keys(), manifest())).encode())
    elif case == "installer_domain":                                    # a validly signed UPDATE manifest is not a bridge
        net.put(wbi.MANIFEST, json.dumps(sign(keys, manifest(), updates.SIGN_DOMAIN)).encode())
    elif case == "tampered_sha":
        good["manifest"]["sha256"] = "0" * 64
        net.put(wbi.MANIFEST, json.dumps(good).encode())
    elif case == "wrong_product":
        net.put(wbi.MANIFEST, json.dumps(sign(keys, manifest(product="jarves"))).encode())
    elif case == "http_url":
        net.put(wbi.MANIFEST, json.dumps(sign(keys, manifest(url="http://x.test/b.exe"))).encode())
    elif case == "huge":
        net.put(wbi.MANIFEST, json.dumps(sign(keys, manifest(size=wbi.MAX_BYTES + 1))).encode())
    elif case == "bad_version":
        net.put(wbi.MANIFEST, json.dumps(sign(keys, manifest(version="../../x"))).encode())
    elif case == "wrong_sha":
        net.put("whatsapp_bridge-0.1.0.exe", b"X" * len(EXE))
    elif case == "wrong_size":
        net.put("whatsapp_bridge-0.1.0.exe", EXE + b"extra")
    elif case == "exe_500":
        net.put("whatsapp_bridge-0.1.0.exe", b"", 500)
    elif case == "offline":
        net.put(wbi.MANIFEST, ConnectionError("down"))
    elif case == "manifest_500":
        net.put(wbi.MANIFEST, b"", 500)
    with pytest.raises(wbi.BridgeInstallError) as e:
        go(tmp_path, net, keys)
    assert e.value.code == code and e.value.he
    assert not (tmp_path / "bridge").exists() or not list((tmp_path / "bridge").iterdir())


def test_a_failed_replacement_keeps_the_working_bridge(tmp_path, net, keys):
    go(tmp_path, net, keys)
    net.put(wbi.MANIFEST, json.dumps(sign(keys, manifest(b"new", "0.2.0", url=f"{BASE}/n.exe"))).encode())
    net.put("n.exe", b"WRONG")
    with pytest.raises(wbi.BridgeInstallError):
        go(tmp_path, net, keys)
    assert wbi.installed_exe(tmp_path).read_bytes() == EXE and wbi.verify_installed(tmp_path, [keys.public])


def test_older_signed_manifest_and_running_bridge_are_refused(tmp_path, net, keys):
    go(tmp_path, net, keys)
    net.put(wbi.MANIFEST, json.dumps(sign(keys, manifest(version="0.0.9"))).encode())
    with pytest.raises(wbi.BridgeInstallError) as e:
        go(tmp_path, net, keys)
    assert e.value.code == "BRIDGE_OLDER"
    with pytest.raises(wbi.BridgeInstallError) as e:
        go(tmp_path, net, keys, running=lambda: True)
    assert e.value.code == "BRIDGE_RUNNING"
    with pytest.raises(wbi.BridgeInstallError) as e:
        go(tmp_path / "x", net, keys, platform="linux")
    assert e.value.code == "BRIDGE_UNSUPPORTED"


def test_hashed_again_before_every_start_and_a_changed_file_is_not_started(tmp_path, net, keys, monkeypatch):
    go(tmp_path, net, keys)
    started = []
    monkeypatch.setattr(wab.subprocess, "Popen", lambda *a, **k: started.append(a))
    monkeypatch.setattr(wbi, "verify_installed", wbi.verify_installed)
    exe = wbi.installed_exe(tmp_path)
    monkeypatch.setattr(wab, "bridge_command", lambda h: [str(exe)])
    monkeypatch.setattr(updates, "UPDATE_PUBLIC_KEYS", (keys.public,))
    exe.write_bytes(EXE[:-1] + b"!")                                      # same size, one byte different
    ok, why = wab.start_bridge(tmp_path, wait_s=0.2)
    assert ok is False and "no longer matches its signed manifest" in why and started == []
    exe.write_bytes(EXE)
    wbi.sidecar(tmp_path).write_text(wbi.sidecar(tmp_path).read_text().replace("0.1.0", "9.9.9"), encoding="utf-8")
    with pytest.raises(wbi.BridgeInstallError) as e:                      # an edited manifest breaks its signature
        wbi.verify_installed(tmp_path, [keys.public])
    assert e.value.code == "BRIDGE_TAMPERED"


def test_installed_builds_ignore_every_override(tmp_path, net, keys, monkeypatch):
    monkeypatch.setattr(updates, "is_installed_build", lambda: True)
    monkeypatch.setenv(wbi.BASE_ENV, "https://evil.example.test/x")
    assert wbi.base_url(BASE) == (f"https://github.com/{updates.DEFAULT_RELEASES_REPO}/releases/download/"
                                  f"{wbi.RELEASE_TAG}")
    assert wbi._keys([keys.public]) == tuple(updates.UPDATE_PUBLIC_KEYS)
    with pytest.raises(wbi.BridgeInstallError):                           # the test key is not a built-in key
        wbi.install(tmp_path, approved=True, http=net, public_keys=[keys.public], base=BASE, platform="win32",
                    running=lambda: False)
    assert net.hits and all(h.startswith("https://github.com/igalgerman/jarvis-releases/") for h in net.hits)


def test_source_checkout_may_use_its_own_https_folder(monkeypatch):
    monkeypatch.setattr(updates, "is_installed_build", lambda: False)
    monkeypatch.setenv(wbi.BASE_ENV, "https://dev.example.test/b/")
    assert wbi.base_url() == "https://dev.example.test/b"


# ---- surfaces: dashboard button, CLI, setup flow
def test_dashboard_install_route_is_local_only_and_answers_honestly(tmp_path, monkeypatch):
    monkeypatch.setattr(wab, "bridge_command", lambda h: None)

    def no_network(url, headers):                      # the route uses the real downloader: never reach the network
        raise OSError("offline in tests")

    monkeypatch.setattr(updates, "_httpx_get", no_network)
    dash = dashboard.Dashboard(tmp_path, Config()).start()
    try:
        r, body = req(dash, "POST", "/api/whatsapp/install", headers={"X-Jarves": "1"}, body={})
        d = json.loads(body)
        # which honest answer depends on the machine: not Windows / no built-in signing key / offline
        assert r.status == 200 and d["ok"] is False and d["message_he"] and d["code"] in (
            "BRIDGE_UNSUPPORTED", "BRIDGE_NO_KEY", "BRIDGE_NOT_PUBLISHED", "BRIDGE_OFFLINE")
        r, body = req(dash, "GET", "/api/whatsapp")
        v = json.loads(body)
        assert v["bridge_download"]["downloaded"] is False and v["bridge_installed"] is False
        r, body = req(dash, "POST", "/api/whatsapp/setup", headers={"X-Jarves": "1"}, body={})
        assert json.loads(body)["can_download"] is True
    finally:
        dash.stop()
    js = (dashboard.STATIC / "whatsapp.js").read_text(encoding="utf-8")
    assert '"install"' in js and "Download the WhatsApp bridge" in js


def test_cli_install_uses_the_same_backend(tmp_path, capsys, monkeypatch):
    calls = []
    monkeypatch.setattr(wbi, "install", lambda home, approved: calls.append(approved) or (_ for _ in ()).throw(
        wbi.BridgeInstallError("BRIDGE_NOT_PUBLISHED")))
    rc = cli.main(["whatsapp", "install"])
    out = capsys.readouterr().out
    assert rc == 1 and calls == [True] and "not published yet" in out and "BRIDGE_NOT_PUBLISHED" in out


# ---- publishing side (packaging/release.py) -> the app, end to end against the local GitHub stub
def _bridge_dist(tmp_path, version="0.1.0"):
    d = tmp_path / "bridge-dist"
    d.mkdir()
    (d / "whatsapp_bridge.exe").write_bytes(EXE)
    sha = hashlib.sha256(EXE).hexdigest()
    (d / "whatsapp_bridge.exe.sha256").write_text(f"{sha}  whatsapp_bridge.exe\n", encoding="ascii")
    (d / "build-info.json").write_text(json.dumps({"name": "whatsapp_bridge", "version": version, "go": "go1.27.0",
                                                   "whatsmeow": "v0", "commit": "abc", "sha256": sha}), encoding="utf-8")
    return d


def test_bridge_publish_signs_with_its_own_domain_and_the_app_installs_it(tmp_path):
    from test_release_hosting import TOKEN, _keypair
    pem, pub = _keypair()
    stub = GitHubStub()
    try:
        client = release.GitHub(stub.repo, TOKEN, api_base=stub.base)
        assert release.bridge_publish(_bridge_dist(tmp_path), gh=client,
                                      env={"RELEASES_TOKEN": TOKEN, "JARVES_UPDATE_SIGNING_KEY": pem}) == 0
        rel = stub.releases[0]
        assert rel["tag_name"] == "whatsapp-bridge" and rel["prerelease"] is True
        assert [a["name"] for a in rel["assets"]][-1] == "whatsapp-bridge.json"      # the switch is last
        files = stub.downloads()
        base = wbi.base_url()
        assert f"{base}/{wbi.MANIFEST}" in files
        n = Net()
        n.files = {u: (200, b) for u, b in files.items()}
        home = tmp_path / "home"
        r = wbi.install(home, approved=True, http=n, public_keys=[pub], platform="win32", running=lambda: False)
        assert r["ok"] and wbi.installed_exe(home).read_bytes() == EXE
        env = json.loads(files[f"{base}/{wbi.MANIFEST}"])
        with pytest.raises(updates.UpdateError):                       # not valid as an installer manifest
            updates.verify_manifest(env, [pub])
    finally:
        stub.close()


def test_bridge_publish_skips_without_secrets_refuses_a_mismatched_exe_and_dry_runs(tmp_path, capsys):
    d = _bridge_dist(tmp_path)
    assert release.bridge_publish(d, env={}) == 0
    assert "SKIPPED" in capsys.readouterr().out
    assert release.bridge_publish(d, env={}, dry_run=True) == 0
    assert "DRY RUN (bridge)" in capsys.readouterr().out
    (d / "whatsapp_bridge.exe").write_bytes(EXE + b"!")
    with pytest.raises(SystemExit):
        release.bridge_publish(d, env={})
