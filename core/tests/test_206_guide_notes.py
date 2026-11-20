"""#206 H3/H4: the guides state two known exposures plainly. Read through the guides loader and the `jarvis help`
text the owner sees, checking key terms rather than whole sentences."""
from jarves import guides


def _guide(name: str):
    g = guides.find(name)
    assert g is not None, name
    return g


def test_h3_mobile_guide_says_the_status_permission_shows_when_the_pc_mic_is_in_use():
    g = _guide("mobile_access")
    for term in ("status", "microphone", "someone is at the computer"):
        assert term in g.privacy_en
    for term in ("מצב", "המיקרופון", "מישהו ליד המחשב"):
        assert term in g.privacy_he
    assert g.privacy_en in guides.help_text(g)


def test_h4_project_guide_says_the_check_command_runs_without_a_sandbox():
    g = _guide("project_manager")
    for term in ("check command", "without a sandbox", "allow-listed", "trust"):
        assert term in g.privacy_en
    for term in ("פקודת הבדיקה", "לא בארגז חול"):
        assert term in g.privacy_he
    assert g.privacy_en in guides.help_text(g)


def test_h4_guide_claims_match_the_check_runner(tmp_path, monkeypatch):
    """Review R3 on #1444: what the H4 note says the check command gets (the worktree, an allow-listed environment
    without keys unless allowed) is what gitops.run_check does."""
    import subprocess
    from jarves.projectmgr import gitops
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("ALLOWED_TOKEN", "allowed")
    seen = {}

    def fake_run(argv, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(argv, 0, "ok", "")
    monkeypatch.setattr(gitops.subprocess, "run", fake_run)
    assert gitops.run_check(tmp_path, ["check"])[0] is True
    assert seen["cwd"] == str(tmp_path) and "shell" not in seen and "OPENAI_API_KEY" not in seen["env"]
    gitops.run_check(tmp_path, ["check"], env_keep=["ALLOWED_TOKEN"])
    assert seen["env"]["ALLOWED_TOKEN"] == "allowed" and "OPENAI_API_KEY" not in seen["env"]
