"""Real cross-step Windows worker handoff, without building or installing software."""
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[2] / "packaging"


def invoke(script, action, state, *args):
    return subprocess.run(["pwsh", "-NoProfile", "-File", str(script), "-Action", action,
                           "-StateDir", str(state), *args], capture_output=True, text=True, timeout=30)


@pytest.mark.windows_only
@pytest.mark.parametrize("outcome", ["success", "failure", "abort"])
def test_worker_survives_start_step_then_joins_or_aborts(tmp_path, outcome):
    root = tmp_path / "build with spaces"
    root.mkdir()
    worker = root / "installer_job.ps1"
    shutil.copyfile(PKG / "installer_job.ps1", worker)
    # The fake only writes isolated markers; no downloads, registry, installer or product process.
    (root / "build_windows.ps1").write_text('''param($Version,$Channel,$Commit,$Tree,$Cache,$Out,[switch]$AlsoSmokeInstaller,$BridgeReadyFile)
"started" | Set-Content ($BridgeReadyFile + ".bundling")
$clock = [Diagnostics.Stopwatch]::StartNew()
while (-not (Test-Path $BridgeReadyFile)) {
  if ($clock.Elapsed.TotalSeconds -gt 20) { throw "test bridge never arrived" }
  Start-Sleep -Milliseconds 100
}
if ($Version -eq "failure") { throw "deliberate worker failure" }
"finished" | Set-Content ($BridgeReadyFile + ".finished")
exit 0
''')
    state = root / "state"
    started = invoke(worker, "Start", state, "-Version", outcome, "-Channel", "pr",
                     "-Commit", "abc", "-Tree", "def", "-Cache", str(root / "cache"), "-Out", str(root / "out"))
    assert started.returncode == 0, started.stdout + started.stderr
    try:
        deadline = time.monotonic() + 10
        while not (state / "bridge.ready.bundling").exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert (state / "bridge.ready.bundling").exists()
        assert not (state / "complete.json").exists()  # start step exited while bundling continues
        if outcome == "abort":
            joined = invoke(worker, "Wait", state, "-Abort")
            assert joined.returncode != 0 and "aborted" in joined.stderr
        else:
            (state / "bridge.ready").touch()
            joined = invoke(worker, "Wait", state, "-TimeoutS", "15")
            assert (joined.returncode == 0) == (outcome == "success"), joined.stdout + joined.stderr
            status = json.loads((state / "complete.json").read_text(encoding="utf-8-sig"))
            assert status["exit_code"] == (0 if outcome == "success" else 1)
            assert (state / "bridge.ready.finished").exists() == (outcome == "success")
    finally:
        if not (state / "complete.json").exists():
            invoke(worker, "Wait", state, "-Abort")


def test_independent_smokes_are_isolated_and_all_required():
    import yaml
    jobs = yaml.safe_load((PKG.parent / ".github/workflows/jarves-checks.yml").read_text())["jobs"]
    smoke = jobs["installer-smoke"]
    assert smoke["runs-on"] == "windows-2025"
    matrix = smoke["strategy"]["matrix"]
    assert set(matrix["scenario"]) == {"functional", "autostart-purge"}
    assert set(matrix["profile"]) == {"release", "ci-smoke"}
    assert "installer-smoke" in jobs["ci-ok"]["needs"]
    build_steps = jobs["installer"]["steps"]
    positions = {step.get("id"): i for i, step in enumerate(build_steps) if step.get("id")}
    assert positions["start"] < positions["bridge"] < positions["build"]
    assert "always()" in build_steps[positions["build"]]["if"]


def test_seeded_feature_choices_match_the_runtime_schema(tmp_path):
    import importlib.util
    from jarves.features import Features
    spec = importlib.util.spec_from_file_location("parallel_seed", PKG / "seed_legacy.py")
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    seed.seed(tmp_path)
    # A data-only install must recognize existing choices; an unchanged autostart command does not rewrite them.
    features = Features(home=tmp_path)
    assert features.enabled("autostart") is True
    assert features.enabled("chimes") is False
    assert seed.verify(tmp_path, legacy=True) == []
