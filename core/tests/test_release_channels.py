"""Continuous release: BETA channel publish (disabled by default), candidate, dry run, rollback tool, channel setting.
Offline: a local GitHub stub (tests/test_release_hosting.py) and temp dirs; nothing is published anywhere."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from test_release_hosting import GitHubStub, REPO, TOKEN, _dist, _keypair, release  # noqa: F401  (fixtures + helpers)

from jarves import updates
from jarves.config import Config

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def gh():
    stub = GitHubStub()
    yield stub
    stub.close()


def _beta(stub, dist, pem, **env):
    client = release.GitHub(stub.repo, TOKEN, api_base=stub.base)
    return release.beta_publish(dist, gh=client, env={"RELEASES_TOKEN": TOKEN, "JARVES_UPDATE_SIGNING_KEY": pem,
                                                       "ENABLE_BETA_PUBLISH": "true", **env})


def _commit(dist, sha):
    p = dist / "build-info.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["commit"] = sha
    p.write_text(json.dumps(d), encoding="utf-8")


def test_beta_publish_is_disabled_by_default(tmp_path, gh, capsys):
    pem, _ = _keypair()
    assert _beta(gh, _dist(tmp_path, "4.1.0.5"), pem, ENABLE_BETA_PUBLISH="") == 0
    assert "DISABLED" in capsys.readouterr().out
    assert gh.releases == []


def test_beta_publish_makes_prerelease_and_signed_latest_beta(tmp_path, gh):
    pem, pub = _keypair()
    assert _beta(gh, _dist(tmp_path, "4.1.0.5"), pem) == 0
    tags = {r["tag_name"]: r for r in gh.releases}
    assert set(tags) == {"beta-v4.1.0.5", "beta-channel"}
    assert tags["beta-v4.1.0.5"]["prerelease"] is True and tags["beta-v4.1.0.5"]["name"] == "JARVES 4.1.0-beta.1"
    assert all(r["prerelease"] for r in tags.values())                           # a beta is never a stable release
    m = updates.verify_manifest(json.loads(gh.asset("beta-channel", "latest-beta.json")), [pub])
    assert m["channel"] == "beta" and m["beta"] == 1 and m["display_version"] == "4.1.0-beta.1"
    rel = updates.release_from_manifest(m, "https://x.test", "4.0.0.1", channel="beta")
    assert rel and rel["display"] == "4.1.0-beta.1"
    with pytest.raises(updates.UpdateError):                                     # stable never accepts a beta manifest
        updates.release_from_manifest(m, "https://x.test", "4.0.0.1", channel="stable")


def test_beta_numbers_count_up_and_same_commit_is_a_noop(tmp_path, gh, capsys):
    pem, _ = _keypair()
    assert _beta(gh, _dist(tmp_path, "4.1.0.5"), pem) == 0
    again = _dist(tmp_path / "again", "4.1.0.5")
    assert _beta(gh, again, pem) == 0
    assert "already has a beta" in capsys.readouterr().out
    assert len(gh.releases) == 2
    d2 = _dist(tmp_path / "d2", "4.1.0.6")
    _commit(d2, "d" * 40)
    assert _beta(gh, d2, pem) == 0
    assert "JARVES 4.1.0-beta.2" in [r["name"] for r in gh.releases]


def test_beta_keeps_only_the_newest_five(tmp_path, gh):
    pem, _ = _keypair()
    for i in range(1, 8):
        d = _dist(tmp_path / f"b{i}", f"4.1.0.{i}")
        _commit(d, f"{i:x}" * 40)
        assert _beta(gh, d, pem) == 0
    betas = sorted(r["tag_name"] for r in gh.releases if r["tag_name"].startswith("beta-v"))
    assert betas == [f"beta-v4.1.0.{i}" for i in range(3, 8)]


def test_dry_run_publishes_nothing(tmp_path, gh, capsys):
    d = _dist(tmp_path, "4.1.0.5")
    assert release.beta_publish(d, dry_run=True, env={}) == 0
    assert release.public_publish(d, dry_run=True, env={}) == 0
    out = capsys.readouterr().out
    assert out.count("DRY RUN") == 2 and "nothing is published" in out and gh.releases == []


def test_candidate_builds_artifact_and_publishes_nothing(tmp_path, gh):
    pem, pub = _keypair()
    d = _dist(tmp_path, "4.1.0.5")
    info = json.loads((d / "build-info.json").read_text(encoding="utf-8"))
    out = tmp_path / "cand"
    assert release.candidate(d, info["commit"], out, env={"JARVES_UPDATE_SIGNING_KEY": pem})["signed"] is True
    assert {p.name for p in out.iterdir()} >= {"JARVES-Setup-4.1.0.5.exe", "candidate-latest.json", "CANDIDATE.txt"}
    updates.verify_manifest(json.loads((out / "candidate-latest.json").read_text(encoding="utf-8")), [pub])
    assert gh.releases == []
    with pytest.raises(SystemExit):
        release.candidate(d, "0" * 40, tmp_path / "bad", env={})


def test_channel_setting_and_beta_label(tmp_path):
    cfg = Config()
    assert updates.UpdateChecker(tmp_path, cfg=cfg).channel == "stable"
    assert updates.set_channel(tmp_path, cfg, "beta") == "beta"
    assert updates.UpdateChecker(tmp_path, cfg=cfg).channel == "beta"
    assert updates.set_channel(tmp_path, cfg, "stable") == "stable"
    with pytest.raises(updates.UpdateError):
        updates.set_channel(tmp_path, cfg, "nightly")
    assert updates.beta_label(tmp_path) is None


def test_beta_label_survives_a_failed_install_and_a_rollback(tmp_path):
    """Issue #141 side effect: prepare_install records the NEW version before setup runs. When setup then failed, the
    running 4.0.1.349 lost its '4.0.1-beta.33' label and showed '4.0.1.349 (..., channel main)'."""
    d: dict = {}
    updates.remember_build(d, {"version": "4.0.1.349", "display": "4.0.1-beta.33", "channel": "beta", "commit": "a"})
    (tmp_path / updates.STATE_FILE).write_text(json.dumps(d), encoding="utf-8")
    assert updates.beta_label(tmp_path, "4.0.1.349") == "4.0.1-beta.33"
    updates.remember_build(d, {"version": "4.0.1.406", "display": "4.0.1-beta.40", "channel": "beta", "commit": "b"})
    (tmp_path / updates.STATE_FILE).write_text(json.dumps(d), encoding="utf-8")
    assert updates.beta_label(tmp_path, "4.0.1.349") == "4.0.1-beta.33"       # setup failed: the old copy still runs
    assert updates.beta_label(tmp_path, "4.0.1.406") == "4.0.1-beta.40"       # setup worked
    assert updates.beta_label(tmp_path, "4.0.1.999") is None                  # never installed by the updater
    for n in range(20):                                                        # bounded
        updates.remember_build(d, {"version": f"4.0.2.{n}", "display": f"4.0.2-beta.{n}", "channel": "beta"})
    assert len(d["builds"]) == updates.KNOWN_BUILDS_KEEP and d["installed"]["version"] == "4.0.2.19"


def test_a_record_from_before_builds_is_kept_when_the_next_install_starts(tmp_path):
    d = {"installed": {"version": "4.0.1.349", "display": "4.0.1-beta.33", "channel": "beta"}}
    updates.remember_build(d, {"version": "4.0.1.406", "display": "4.0.1-beta.40", "channel": "beta"})
    (tmp_path / updates.STATE_FILE).write_text(json.dumps(d), encoding="utf-8")
    assert updates.beta_label(tmp_path, "4.0.1.349") == "4.0.1-beta.33"
    stable = {"installed": {"version": "4.0.1.349", "display": "4.0.1.349", "channel": "main"}}
    (tmp_path / updates.STATE_FILE).write_text(json.dumps(stable), encoding="utf-8")
    assert updates.beta_label(tmp_path, "4.0.1.349") is None


def _rb():
    spec = importlib.util.spec_from_file_location("release_rollback", ROOT / "core" / "tools" / "release_rollback.py")
    rb = importlib.util.module_from_spec(spec)
    sys.modules["release_rollback"] = rb
    spec.loader.exec_module(rb)
    return rb


def _rel(tag, pre=False, manifest="latest.json"):
    return {"tag_name": tag, "draft": False, "prerelease": pre,
            "assets": [{"name": manifest, "browser_download_url": f"https://x.test/{tag}/{manifest}"}]}


def test_rollback_plan_demotes_newer_stable_and_refuses_bad_targets():
    rb = _rb()
    rels = [_rel("win-v4.0.0.1"), _rel("win-v4.0.0.2"), _rel("win-v4.0.0.3")]
    p = rb.plan(rels, "win-v4.0.0.1")
    assert p["problems"] == [] and p["demote"] == ["win-v4.0.0.2", "win-v4.0.0.3"]
    assert rb.plan(rels, "win-v9.9.9.9")["problems"] and rb.plan(rels, "beta-v4.0.0.1")["problems"]
    b = rb.plan([_rel("beta-v4.1.0.1", True, "latest-beta.json")], "beta-v4.1.0.1", "beta")
    assert b["problems"] == [] and b["demote"] == []


def test_rollback_defaults_to_dry_run(capsys, monkeypatch):
    rb = _rb()
    monkeypatch.setattr(sys, "argv", ["release_rollback.py", "--to", "win-v4.0.0.1"])
    called = []

    class Boom:
        def __getattr__(self, n):
            called.append(n)
            raise AssertionError("dry run must not touch GitHub")
    import inspect
    assert "execute" in inspect.signature(rb.rollback).parameters
    assert inspect.signature(rb.rollback).parameters["execute"].default is False
