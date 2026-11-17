"""Monthly best-in-class scout (JAR-233): registry/code drift, overdue rows, newer/missing/deprecated ids.

Hermetic: fake fetch, fixed --today, tmp registry and repo."""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import best_in_class_scout as bic  # noqa: E402

TODAY = dt.date(2026, 11, 17)
MODELS = "https://example.test/models"
DEPS = "https://example.test/deprecations"
PYPI = "https://example.test/pypi/openai-agents/json"

MODELS_HTML = """<html><script>var models=["gpt-realtime-2.7"];</script>
<img src="/img/gpt-realtime-2.1.png"><a>gpt-realtime-2.1</a><a>gpt-realtime-2.5</a>
<img src="gpt-realtime-3-texture.webp"><a>gpt-realtime-1.5</a><a>gpt-realtime-2.1-mini</a></html>"""
DEPS_HTML = """<h1>Deprecations</h1><p>We announce gpt-realtime-2.1 news here.</p><table>
<tr><td>tts-1</td><td>Deprecated</td><td>gpt-realtime-2.1-mini</td><td>Oct 1, 2026</td><td>Jan 6, 2027</td></tr>
<tr><td>gpt-6-terra ( reasoning.mode: pro )</td><td>Deprecated</td><td>gpt-6.1-terra</td><td>Jun 1, 2026</td><td>Dec 1, 2026</td></tr>
<tr><td>gpt-4o-mini-tts , gpt-4o-mini-tts-2025-12-15</td><td>Deprecated</td><td>gpt-realtime-2.1-mini</td>
<td>Oct 1, 2026</td><td>Jan 14, 2027</td></tr>
<tr><td>gpt-6.1-sol ( reasoning.mode: pro ) in the Responses API</td><td>Deprecated</td><td>gpt-6.2-sol</td>
<td>Jun 11, 2026</td><td>Jan 6, 2027</td></tr></table>"""


def registry(tmp_path, entries, max_age=31):
    lines = ["[meta]", f"max_age_days = {max_age}", f'deprecations = "{DEPS}"', ""]
    for e in entries:
        lines.append("[[entry]]")
        lines += [f"{k} = {json.dumps(v)}" for k, v in e.items()]  # JSON strings are valid TOML basic strings
        lines.append("")
    p = tmp_path / "reg.toml"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def model(recorded="gpt-realtime-2.1", family=r"^gpt-realtime-(\d+(?:\.\d+)?)$", **kw):
    e = {"id": "realtime_model", "stage": "voice", "kind": "openai-model", "recorded": recorded,
         "family": family, "source": MODELS, "checked": "2026-11-10"}
    e.update(kw)
    return e


def pypi(recorded="0.24.0", **kw):
    e = {"id": "openai-agents", "stage": "agents", "kind": "pypi", "package": "openai-agents",
         "recorded": recorded, "source": PYPI, "checked": "2026-11-10"}
    e.update(kw)
    return e


def fetcher(latest="0.24.0", fail=()):
    calls = []

    def fetch(url):
        calls.append(url)
        if url in fail:
            raise OSError("offline")
        return {MODELS: MODELS_HTML, DEPS: DEPS_HTML,
                PYPI: json.dumps({"info": {"version": latest}})}[url]
    fetch.calls = calls
    return fetch


def types(found):
    return sorted((f["id"], f["type"]) for f in found)


def test_real_registry_matches_code():
    data = bic.load()
    assert data["entry"], "registry is empty"
    drift = [f for f in bic.check_offline(data, dt.date.fromisoformat(min(e["checked"] for e in data["entry"])))
             if f["type"] == "drift"]
    assert drift == []


def test_drift_and_overdue(tmp_path):
    (tmp_path / "cfg.py").write_text('realtime_model = "gpt-realtime-2"\n', encoding="utf-8")
    data = bic.load(registry(tmp_path, [model(code="cfg.py", code_match='"gpt-realtime-2.1"', checked="2026-09-01")]))
    assert types(bic.check_offline(data, TODAY, repo=tmp_path)) == [("realtime_model", "drift"),
                                                                     ("realtime_model", "overdue")]
    data["entry"][0]["checked"] = "2026-11-01"
    data["entry"][0]["code_match"] = '"gpt-realtime-2"'
    assert bic.check_offline(data, TODAY, repo=tmp_path) == []


def test_newer_model_reads_page_data_and_strips_assets(tmp_path):
    """The official page keeps most ids in script data and image paths, so the raw HTML is scanned."""
    data = bic.load(registry(tmp_path, [model()]))
    found = bic.check_online(data, fetcher())
    assert types(found) == [("realtime_model", "newer")]
    detail = found[0]["detail"]
    assert "gpt-realtime-2.5, gpt-realtime-2.7, gpt-realtime-3 " in detail and "1.5" not in detail


def test_missing_and_deprecated(tmp_path):
    data = bic.load(registry(tmp_path, [model(recorded="gpt-4o-mini-tts", family=r"^gpt-(\d+)o?-mini-tts$")]))
    found = bic.check_online(data, fetcher())
    assert types(found) == [("realtime_model", "deprecated"), ("realtime_model", "missing")]
    dep = next(f for f in found if f["type"] == "deprecated")["detail"]
    assert "shutdown Jan 14, 2027" in dep and "replacement gpt-realtime-2.1-mini" in dep


def test_deprecations_parser_rows():
    rows = bic.deprecations(DEPS_HTML)
    assert rows["tts-1"]["shutdown"] == "Jan 6, 2027"
    assert rows["gpt-4o-mini-tts-2025-12-15"]["shutdown"] == "Jan 14, 2027"
    assert rows["gpt-6-terra"]["replacement"] == "gpt-6.1-terra"
    assert "gpt-6.1-sol" not in rows  # "... in the Responses API": a mode is deprecated, not the model
    assert "reasoning.mode" not in rows and "gpt-realtime-2.1" not in rows  # prose / replacement only


def test_pypi_rules(tmp_path):
    data = bic.load(registry(tmp_path, [pypi(recorded="1.1", pin_below="2")]))
    assert bic.check_online(data, fetcher("1.1.0")) == []          # 1.1 == 1.1.0
    assert bic.check_online(data, fetcher("1.1.7")) == []          # patch the pin already installs
    assert "evaluate" in bic.check_online(data, fetcher("1.3.0"))[0]["detail"]
    assert "outside the pin <2" in bic.check_online(data, fetcher("2.0.1"))[0]["detail"]
    for rec, latest in (("1.0", "1.0.5"), ("2.0.0", "2.0.3"), ("1", "1.0.2")):  # trailing zeros: a patch, not a minor
        assert bic.check_online(bic.load(registry(tmp_path, [pypi(recorded=rec)])), fetcher(latest)) == []
    assert "evaluate" in bic.check_online(bic.load(registry(tmp_path, [pypi(recorded="1.0")])), fetcher("1.1"))[0]["detail"]


def test_asset_suffix_stripped_only_with_image_extension():
    ids = bic.page_ids('<img src="gpt-x-3-texture.webp"><a>gpt-voice-block</a><a>gpt-y-texture</a>')
    assert "gpt-x-3" in ids and "gpt-voice-block" in ids and "gpt-y-texture" in ids


def test_unknown_kind_rejected(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="unknown kind 'pipy'"):
        bic.load(registry(tmp_path, [pypi(kind="pipy")]))


def test_fetch_errors_are_findings_and_sources_fetched_once(tmp_path):
    data = bic.load(registry(tmp_path, [model(), model(id="other"), pypi()]))
    f = fetcher(fail=(PYPI,))
    found = bic.check_online(data, f)
    assert ("openai-agents", "error") in types(found)
    assert f.calls.count(MODELS) == 1 and f.calls.count(DEPS) == 1


def test_manual_rows_not_fetched(tmp_path):
    data = bic.load(registry(tmp_path, [{"id": "stt", "stage": "voice", "kind": "manual", "recorded": "small",
                                         "source": "https://example.test/hf", "checked": "2026-11-10"}]))
    f = fetcher()
    assert bic.check_online(data, f) == [] and f.calls == []


def test_main_writes_report_and_exit_code(tmp_path, capsys):
    reg = registry(tmp_path, [model()])
    out = tmp_path / "r.md"
    rc = bic.main(["--registry", str(reg), "--online", "--today", "2026-11-17", "--report", str(out)], fetch=fetcher())
    assert rc == 1
    text = out.read_text(encoding="utf-8")
    assert "# Best-in-class scout 2026-11-17" in text and "**newer** `realtime_model`" in text
    assert bic.main(["--registry", str(reg), "--today", "2026-11-17", "--json"]) == 0
    assert capsys.readouterr().out.strip().endswith("[]")
