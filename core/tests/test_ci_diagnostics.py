"""SW-V4 M: the CI failure diagnostics are redacted before they are uploaded to a public repo."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import ci_diagnostics as cd  # noqa: E402

KEY = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2"


def test_secrets_emails_paths_and_content_are_removed(tmp_path):
    src = tmp_path / "logs"
    src.mkdir()
    (src / "jarves.log").write_text("\n".join([
        json.dumps({"code": "CORE_READY", "ms": 1700, "transcript": "call my mother", "api_key": KEY}),
        f"Authorization: Bearer {KEY}abcdef",
        r"opened C:\Users\igal\AppData\Local\jarves\x.db for someone@example.com phone +972 54 123 4567",
        "FAILED tests/test_core.py::test_x - AssertionError",
    ]), encoding="utf-8")
    (src / "ignore.bin").write_bytes(b"\x00\x01")
    out = tmp_path / "out"
    names = cd.collect(out, [src])
    text = "\n".join((out / n).read_text(encoding="utf-8") for n in names)
    assert "ignore.bin" not in " ".join(names)
    for leak in (KEY, "call my mother", "someone@example.com", "igal", "4567"):
        assert leak not in text, leak
    assert "CORE_READY" in text and "FAILED tests/test_core.py::test_x" in text   # the useful part survives
    assert "environment.txt" in names


def test_output_is_discarded_if_a_secret_survives(tmp_path, monkeypatch):
    f = tmp_path / "a.log"
    f.write_text("fine", encoding="utf-8")
    monkeypatch.setattr(cd, "scrub_text", lambda t: "ghp_" + "A" * 30)           # a broken scrubber must not leak
    out = tmp_path / "out"
    assert cd.main([str(out), str(f)]) == 2
    assert not out.exists()


def test_main_usage_and_ok(tmp_path, capsys):
    assert cd.main([]) == 64
    f = tmp_path / "pytest.out"
    f.write_text("1 failed", encoding="utf-8")
    assert cd.main([str(tmp_path / "o"), str(f)]) == 0
    assert "2 redacted file" in capsys.readouterr().out
