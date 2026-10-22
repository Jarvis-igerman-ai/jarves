"""Exercise the installer's actual backup/migration transaction against fake old data."""
import importlib.util
import json
from pathlib import Path

import pytest

from jarves import backup, migrations

PKG = Path(__file__).resolve().parents[2] / "packaging"

def load(name):
    spec = importlib.util.spec_from_file_location(name, PKG / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

installer = load("installer_state")
seed = load("seed_legacy")

def test_upgrade_verifies_backup_before_migration_and_preserves_settings(tmp_path):
    home, state = tmp_path / "home", tmp_path / "state.json"
    seed.seed(home)
    result = installer.prepare(home, state)
    assert not (home / migrations.MARKER).exists()
    assert len(result["backups"]) == 2
    assert all(backup.verify(Path(item["backup"]))["ok"] for item in result["backups"])
    installer.complete(home, state)
    assert seed.verify(home, legacy=False) == []
    assert (home / "config.toml").read_text() == seed.CONFIG


def test_corrupt_backup_blocks_migration(tmp_path):
    home, state = tmp_path / "home", tmp_path / "state.json"
    seed.seed(home)
    result = installer.prepare(home, state)
    Path(result["backups"][0]["backup"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="verifies"):
        installer.complete(home, state)
    assert seed.verify(home, legacy=True) == []


def test_failed_migration_restores_old_database_layout(tmp_path, monkeypatch):
    home, state = tmp_path / "home", tmp_path / "state.json"
    seed.seed(home)
    installer.prepare(home, state)
    def fail(target, **kwargs):
        (target / migrations.MARKER).write_text('{"schema": 999}')
        raise RuntimeError("fake migration failure")
    monkeypatch.setattr(migrations, "run", fail)
    with pytest.raises(RuntimeError):
        installer.complete(home, state)
    assert seed.verify(home, legacy=True) == []


def test_fresh_choice_keeps_original_data_models_and_credentials_in_rollback(tmp_path):
    home, state = tmp_path / "home", tmp_path / "state.json"
    seed.seed(home)
    installer.prepare(home, state)
    record = installer.complete(home, state, keep=False, language="he")
    previous = Path(record["previous_data"])
    assert seed.verify(previous, legacy=True) == []
    assert 'language = "he"' in (home / "config.toml").read_text()
    assert not (home / "secrets.env").exists()


def test_shim_retirement_is_limited_reversible_and_keeps_user_replacements(tmp_path):
    user, app = tmp_path / "user", tmp_path / "app"
    root = user / ".local/bin"
    root.mkdir(parents=True)
    shim = root / "jarvis.cmd"
    shim.write_bytes(b"@python -m jarves %*")
    other = root / "jarvis.exe"
    other.write_bytes(b"unrelated executable")
    state = tmp_path / "state.json"
    state.write_text("{}")
    assert len(installer.retire_shims(app, state, user=user, roaming=user / "roaming")) == 1
    assert not shim.exists() and other.exists()
    installer.restore_shims(state)
    assert shim.read_bytes() == b"@python -m jarves %*"
    installer.retire_shims(app, state, user=user, roaming=user / "roaming")
    shim.write_bytes(b"replacement from user")
    installer.restore_shims(state)
    assert shim.read_bytes() == b"replacement from user"


def test_setup_route_falls_back_until_registered(tmp_path):
    assert installer.setup_url("http://127.0.0.1/?t=private", tmp_path) == "http://127.0.0.1/?t=private"
    (tmp_path / "setup.js").write_text("PAGES.setup = legacyPage;")
    assert not installer.setup_url("http://127.0.0.1/", tmp_path).endswith("#/setup")
    (tmp_path / "wizard.js").write_text("PAGES.setup = pageTour;")
    assert installer.setup_url("http://127.0.0.1/?t=private", tmp_path).endswith("#/setup")


def test_protected_home_is_refused(tmp_path):
    with pytest.raises(ValueError, match="unsafe"):
        installer.safe_home(Path.home())


def test_offline_rollback_restores_verified_legacy_data(tmp_path):
    home, state = tmp_path / "home", tmp_path / "state.json"
    seed.seed(home)
    installer.prepare(home, state)
    installer.complete(home, state)
    installer.restore_data(home, state)
    assert seed.verify(home, legacy=True) == []
