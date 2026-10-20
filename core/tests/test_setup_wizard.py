"""First-run setup wizard (setup_wizard.py, `jarvis setup`): scripted runs with an answers file and fake probes,
Hebrew / English output, safe defaults, idempotent re-runs, resume after a stop, the add-on bundle check, the CLI
modes and the dashboard view. Offline, silent: no sensors, no browser, no network, no real key."""
import importlib
import json
import re

import pytest

for _m in ("jarves.app", "jarves.operations", "jarves.runtime", "jarves.usage", "jarves.backup", "jarves.appearance",
           "jarves.skills.generated", "jarves.guides"):
    importlib.import_module(_m)

from jarves import features as feat  # noqa: E402
from jarves import setup_wizard as sw  # noqa: E402
from jarves.config import Config  # noqa: E402

HE = re.compile(r"[֐-׿]")
FAKE = {"camera": (False, "no camera connected"), "face_recognition": (False, "no camera connected"),
        "camera_when_away": (False, "no camera connected"),
        "music": (False, "Pear Desktop is not running (start it with the API Server plugin)"),
        "google_calendar": (False, "Google account not connected"), "gmail_read": (False, "Google account not connected")}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    # fake probes: no hardware, no Pear, no Google
    monkeypatch.setattr(feat.Features, "available", lambda self, fid: FAKE.get(fid, (True, "")))
    return tmp_path


def wizard(home, *, lines=None, answers=None, defaults=False, lang=None, out=None, prepared=None, **kw):
    from jarves.config import load_config
    out = out if out is not None else []
    cfg, _ = load_config(home / "config.toml")             # like the CLI: what an earlier run wrote
    prepared = prepared if prepared is not None else []

    def prepare(fid, f, h):
        prepared.append(fid)
        return True, ""
    actions = {"ask_key": lambda h: 1, "prepare": prepare, "google_start": lambda: 0,
               "autostart": lambda on: prepared.append(f"autostart={on}"),       # never the real Run key
               **kw.pop("actions", {})}
    return sw.Wizard(cfg, home, config_path=home / "config.toml", io=sw.ConsoleIO(lines, out.append),
                     answers=answers, defaults=defaults, lang=lang, features=feat.Features(cfg, home),
                     actions=actions, **kw), out


def switches(home):
    return feat.Features(Config(), home)


# ---------------------------------------------------------------------- catalog coverage
def test_every_registered_feature_has_a_group_and_a_bundle_declaration():
    sw.load_addons()
    grouped = {i for _, _, _, ids in sw.GROUPS for i in ids}
    assert set(feat.CATALOG) <= grouped, set(feat.CATALOG) - grouped
    assert set(feat.CATALOG) <= set(sw.BUNDLE), set(feat.CATALOG) - set(sw.BUNDLE)
    assert [g for g in sw.groups() if g[0] == "other"] == []
    assert sw.SAFE_OFF <= set(feat.CATALOG)
    for fid in sw.SAFE_OFF:                                  # the catalog itself keeps them off by default too
        assert feat.CATALOG[fid].default_enabled is False


# ---------------------------------------------------------------------- defaults
def test_defaults_first_run_is_safe_and_finishes(env):
    w, out = wizard(env, defaults=True, lang="en")
    assert w.run() == 0
    f = switches(env)
    assert all(not f.enabled(fid) for fid in sw.SAFE_OFF)
    assert (env / "setup_done").read_text(encoding="utf-8") == "api_key"
    st = json.loads((env / sw.STATE_FILE).read_text(encoding="utf-8"))
    assert st["finished_at"] and st["completed"] == [] and st["language"] == "en"
    text = "\n".join(out)
    assert "Add-ons" in text and "What to do next" in text and "jarvis key" in text
    assert "pear-desktop" in text and "jarvis google start" in text            # Pear link + Google later
    assert not (env / "features.json").exists()            # nothing differed from the defaults: nothing written
    assert not sw.first_run(env)


def test_recommended_keeps_safe_off_on_first_run_only(env):
    f = switches(env)
    f.set("camera", True, by="test")
    assert sw.recommended("camera", f, first=True) is False
    assert sw.recommended("camera", f, first=False) is True
    assert sw.recommended("music", f, first=True) is True


# ---------------------------------------------------------------------- answers file
ANSWERS = {"language": "en", "connection": "chatgpt", "display_name": "Max", "spoken_name": "מקס",
           "address_me": "איגל, בלשון זכר", "wake_model": "hey_jarvis", "voice": "live:marin",
           "features": {"music": False, "camera": True, "dashboard_lan": False, "chimes": False},
           "privacy_mode": True}


def test_answers_file_applies_everything(env):
    prepared = []
    w, out = wizard(env, answers=ANSWERS, prepared=prepared)
    assert w.run() == 0
    ident = json.loads((env / "identity.json").read_text(encoding="utf-8"))
    assert ident["display_name"] == "Max" and ident["spoken_name"] == "מקס"
    user_md = (env / "workspace" / "USER.md").read_text(encoding="utf-8")
    assert sw.ADDRESS_MARK in user_md and "איגל, בלשון זכר" in user_md
    f = switches(env)
    assert not f.enabled("music") and f.enabled("camera") and not f.enabled("chimes")
    assert f.status("camera")["changed_by"] == "setup"
    assert prepared == ["camera"]                         # prerequisites only for what was switched ON
    assert 'connection = "chatgpt"' in (env / "config.toml").read_text(encoding="utf-8")
    assert json.loads((env / "logs" / "privacy.json").read_text(encoding="utf-8"))["privacy"] is True
    from jarves.memory import MemoryStore
    m = MemoryStore(env / "memory.db")
    assert m.get_setting("override:voice.voice") == "marin"
    m.close()
    assert any("chatgpt login" in line for line in out)    # never signs in silently: says what to do


def test_autostart_switch_also_sets_the_login_entry(env):
    calls = []
    w, _ = wizard(env, answers={"features": {"autostart": True}}, prepared=calls)
    assert w.run() == 0
    assert "autostart=True" in calls and switches(env).enabled("autostart")


def test_rerun_is_idempotent(env):
    w, _ = wizard(env, answers=ANSWERS)
    assert w.run() == 0
    before = {p.name: p.read_bytes() for p in (env / "features.json", env / "identity.json",
                                               env / "workspace" / "USER.md", env / "config.toml")}
    w2, _ = wizard(env, answers=ANSWERS)
    assert w2.run() == 0
    assert w2.changed == []
    after = {p.name: p.read_bytes() for p in (env / "features.json", env / "identity.json",
                                              env / "workspace" / "USER.md", env / "config.toml")}
    assert before == after
    # a re-run with other answers changes only that
    w3, _ = wizard(env, answers={**ANSWERS, "features": {"chimes": True}})
    assert w3.run() == 0 and w3.changed == ["chimes=on"]
    assert switches(env).enabled("camera")                  # untouched: re-run defaults are the current choices


@pytest.mark.parametrize("bad, msg", [({"nope": 1}, "unknown answer key"),
                                      ({"connection": "free"}, "connection must be one of"),
                                      ({"display_name": "<script>"}, "display_name"),
                                      ({"features": {"camera": "yes"}}, "true or false"),
                                      ({"monthly_budget_usd": -5}, "monthly_budget_usd"),
                                      ({"display_name": "sk-abcdefghijklmnop"}, "looks like a key")])
def test_bad_answers_are_refused(env, bad, msg):
    with pytest.raises(sw.AnswerError, match=msg):
        w, _ = wizard(env, answers=bad)
        w.run()
    assert not (env / "setup_done").exists()


def test_api_key_only_from_environment_when_asked(env, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real-0000000000")
    w, out = wizard(env, answers={"connection": "api_key", "api_key_from_env": True, "monthly_budget_usd": 25})
    monkeypatch.setattr("jarves.config.openai_key_present", lambda: False)
    assert w.run() == 0
    assert "OPENAI_API_KEY=" in (env / "secrets.env").read_text(encoding="utf-8")
    assert "sk-test-not-real" not in "\n".join(out)
    from jarves.usage import get_settings
    assert get_settings(env)["api_monthly_budget_usd"] == 25


# ---------------------------------------------------------------------- interactive
def test_interactive_all_enter_is_english_by_default(env, monkeypatch):
    monkeypatch.delenv("JARVES_CLI_LANG", raising=False)
    w, out = wizard(env, lines=[])
    assert w.run() == 0
    text = "\n".join(out)
    assert "JARVES setup" in text and "הגדרת" not in text


def test_interactive_all_enter_is_hebrew_and_safe(env, monkeypatch):
    monkeypatch.setenv("JARVES_CLI_LANG", "he")             # the CLI language setting (default is English)
    w, out = wizard(env, lines=[])                          # every question answered with Enter (end of input)
    assert w.run() == 0
    text = "\n".join(out)
    assert "הגדרת JARVES" in text and "תוספים" in text and "מה לעשות עכשיו" in text
    assert "Hey Jarvis" in text and "jarvis wake train" in text          # honest wake-phrase explanation
    assert all(not switches(env).enabled(fid) for fid in sw.SAFE_OFF)


def test_interactive_english_and_one_by_one_high_risk_needs_yes(env):
    lines = ["2", "", "", "", "", "", "",                   # en; connection, names x3, wake, voice
             "n"]                                           # not the recommended set: one by one
    order = [fid for _, _, _, ids in sw.groups() for fid in ids]
    for fid in order:
        if fid == "camera":
            lines += ["y", "no"]                            # "on?" yes, but the explicit confirmation is no
        elif fid == "chimes":
            lines += ["n"]
        elif fid in ("face_recognition", "camera_when_away"):
            pass                                            # skipped: their parent (camera) stayed off
        else:
            lines += [""]
    w, out = wizard(env, lines=lines)
    assert w.run() == 0
    f = switches(env)
    assert not f.enabled("camera") and not f.enabled("chimes") and f.enabled("music")
    text = "\n".join(out)
    assert "Add-ons" in text and "off because it needs Camera" in text
    assert "Free (your own YouTube Music account)" in text   # cost note from the music guide
    assert "pear-desktop" in text                            # availability hint with the Pear link


def test_stop_and_resume(env):
    lines = ["2", "", "Max", "", "", "", "", "zzz"]         # ...addons answered with nonsense, then input ends
    w, out = wizard(env, lines=lines)
    assert w.run() == 1
    st = json.loads((env / sw.STATE_FILE).read_text(encoding="utf-8"))
    assert st["completed"] == ["language", "connection", "names", "wake", "voice"] and not st.get("finished_at")
    assert not (env / "setup_done").exists()
    assert json.loads((env / "identity.json").read_text(encoding="utf-8"))["display_name"] == "Max"
    w2, out2 = wizard(env, lines=["y"])                     # continue where it stopped
    assert w2.run() == 0
    assert not any("Written name" in line for line in out2)  # names were not asked again
    assert (env / "setup_done").exists()


def test_google_offered_only_interactively(env):
    started = []
    w, _ = wizard(env, lines=["2"] + [""] * 9 + ["y"], actions={"google_start": lambda: started.append(1) or 0})
    # the final "y" lands on the Google question (all other answers are Enter)
    assert w.run() == 0 and started == [1]
    w2, out = wizard(env, answers={"connect_google_now": True}, actions={"google_start": lambda: started.append(2)})
    assert w2.run() == 0 and started == [1]                 # never from an answers file / silent install


# ---------------------------------------------------------------------- bundle check, CLI, dashboard
def test_check_addons_reports_missing_parts():
    res = sw.check_addons({"camera": [sw._mod("jarves_no_such_module")], "dashboard": [sw._data("jarves", "nope.js")]})
    assert not res["ok"]
    rows = {r["id"]: r for r in res["rows"]}
    assert not rows["camera"]["ok"] and "jarves_no_such_module" in rows["camera"]["missing"][0]
    assert not rows["dashboard"]["ok"]
    out = []
    sw.print_check(res, out.append)
    assert out[-1] == "ADDON CHECK: FAIL"
    good = sw.check_addons({"setup": [sw._mod("jarves.setup_wizard"), sw._data("jarves", "dashboard_static/setup.js"),
                                      sw._tz("UTC")]})
    assert {r["id"]: r["ok"] for r in good["rows"]}["setup"] is True


def _cli(home, monkeypatch, *argv, stdin=""):
    import io
    from jarves import cli
    monkeypatch.setenv("JARVES_HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "")
    return cli.main(list(argv))


def test_cli_answers_defaults_and_empty_answers(env, monkeypatch, capsys):
    assert _cli(env, monkeypatch, "setup", "--answers", "") == 0          # installer line without /SETUPANSWERS=
    assert not (env / "setup_done").exists()
    p = env / "answers.json"
    p.write_text(json.dumps({"display_name": "Max", "features": {"chimes": False}}), encoding="utf-8")
    assert _cli(env, monkeypatch, "setup", "--answers", str(p)) == 0
    assert (env / "setup_done").exists() and not switches(env).enabled("chimes")
    p.write_text("[1]", encoding="utf-8")
    assert _cli(env, monkeypatch, "setup", "--answers", str(p)) == 2
    p.write_text(json.dumps({"bogus": True}), encoding="utf-8")
    assert _cli(env, monkeypatch, "setup", "--answers", str(p)) == 2
    assert "unknown answer key" in capsys.readouterr().err
    assert _cli(env, monkeypatch, "setup", "--defaults", "--lang", "en") == 0
    assert _cli(env, monkeypatch, "setup", "--if-needed") == 0            # done before: hints only


def test_installer_and_smoke_wire_the_wizard():
    from pathlib import Path
    pkg = Path(sw.__file__).parents[3] / "packaging"
    iss = (pkg / "windows" / "jarves.iss").read_text(encoding="utf-8-sig")
    assert "setup --answers" in iss and "{param:SETUPANSWERS|}" in iss and "Check: WizardSilent" in iss
    assert "postinstall nowait skipifsilent" in iss               # the interactive wizard stays a ticked last page
    smoke = (pkg / "smoke_windows.ps1").read_text(encoding="utf-8")
    assert "--check-addons" in smoke and "ADDON CHECK: PASS" in smoke and "/SETUPANSWERS=" in smoke
    index = (Path(sw.__file__).parent / "dashboard_static" / "index.html").read_text(encoding="utf-8")
    assert "/static/setup.js" in index


def test_dashboard_view_groups_and_hints(env):
    cfg = Config()
    v = sw.view(cfg, env, feat.Features(cfg, env))
    ids = [g["id"] for g in v["groups"]]
    assert ids[:4] == ["core", "voice", "music", "google"] and "camera" in ids and "updates" in ids
    rows = {r["id"]: r for g in v["groups"] for r in g["features"]}
    assert rows["camera"]["safe_off"] and rows["camera"]["recommended"] is False
    assert rows["music"]["hint"]["link"] == sw.PEAR_URL and rows["music"]["guide"] == "music"
    assert rows["google_calendar"]["hint"]["command"] == "jarvis google start"
    assert HE.search(rows["music"]["title_he"]) and rows["music"]["cost_en"]
    assert v["first_run"] and v["next_steps"]
    routes = []
    sw.dashboard_routes(lambda m, p, h: routes.append((m, p)))
    assert routes == [("GET", r"/api/setup")]
