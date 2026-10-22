"""Real zip-import and publication boundaries for the installer profiles."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[2] / "packaging"

def load(name):
    spec = importlib.util.spec_from_file_location(name, PKG / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_archive_imports_code_and_keeps_native_resources(tmp_path):
    site = tmp_path / "python/Lib/site-packages"
    site.mkdir(parents=True)
    (tmp_path / "python/python.exe").touch()
    for name in ("httpx", "openai", "pydantic", "jarves"):
        (site / name).mkdir()
        (site / name / "__init__.py").write_text("VALUE = 42\n")
    (site / "openai/data.json").write_text("{}")
    (site / "pydantic/native.pyd").write_bytes(b"native")
    (site / "httpx/__pycache__").mkdir()
    (site / "httpx/__pycache__/obsolete.pyc").touch()
    result = load("pack_python").pack(tmp_path)
    assert result["packed"] == ["httpx"]
    assert (site / "openai/data.json").is_file()
    assert (site / "pydantic/native.pyd").is_file()
    assert (site / "jarves/__init__.py").is_file()
    assert not list(tmp_path.rglob("*.pyc"))
    code = "import site; site.addsitedir(__import__('sys').argv[1]); import httpx; assert httpx.VALUE == 42; assert '.zip' in httpx.__file__"
    subprocess.run([sys.executable, "-I", "-S", "-c", code, str(site)], check=True)

def test_live_environment_cannot_be_packed(tmp_path):
    with pytest.raises(ValueError, match="staging"):
        load("pack_python").pack(tmp_path)

def test_smoke_profile_cannot_publish_even_with_main_channel(tmp_path):
    (tmp_path / "build-info.json").write_text(json.dumps({"build_profile": "ci-smoke", "channel": "main", "commit": "abc"}))
    with pytest.raises(SystemExit, match="smoke builds are never published"):
        load("release").verify(tmp_path, "abc")
