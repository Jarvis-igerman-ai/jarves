"""console_rtl: Hebrew for terminals without bidi (conhost / Windows Terminal). Pure functions, captured output, no
real console."""
import io

from jarves import console_rtl as r


def test_pure_hebrew_is_reversed_and_logical_mode_is_untouched():
    assert r.render("שלום", mode="visual") == "םולש"
    assert r.render("שלום", mode="logical") == "שלום"
    assert r.render("only English [2]: x", mode="visual") == "only English [2]: x"


def test_mixed_line_keeps_latin_runs_and_reverses_run_order():
    out = r.render("הרץ jarvis key עכשיו", mode="visual")
    assert out == "וישכע jarvis key ץרה"
    assert "jarvis key" in out                                    # the command is never reversed


def test_digits_paths_and_keys_stay_left_to_right():
    path = "%LOCALAPPDATA%\\jarves\\config.toml"
    out = r.render("הקובץ ב-" + path + " נשמר 12:30", mode="visual")
    assert path in out and "12:30" in out
    assert r.render("מפתח OPENAI_API_KEY=sk-abc", mode="visual").count("OPENAI_API_KEY=sk-abc") == 1


def test_brackets_are_mirrored_in_hebrew_runs():
    assert r.render("(שלום)", mode="visual") == "(םולש)"
    assert r.render("הרץ (jarvis key) עכשיו", mode="visual") == "וישכע (jarvis key) ץרה"


def test_prompt_with_choice_marker_and_numbers():
    assert r.render("[2]: ", mode="visual") == "[2]: "
    out = r.render("> 1  עברית", mode="visual")
    assert "תירבע" in out and "1" in out and "עברית" not in out


def test_wrapping_happens_before_reversing():
    text = ("זה משפט ארוך מאוד שצריך להישבר לכמה שורות ולא להתהפך כולו " * 2) + "jarvis setup"
    out = r.render(text, width=30, mode="visual").split("\n")
    assert len(out) > 2 and all(len(x) <= 30 for x in out)
    assert "זה"[::-1] in out[0]                                   # the first words stay on the first line
    assert "jarvis setup" in out[-1]


def test_detect_mode():
    win = dict(platform="win32", isatty=True)
    assert r.detect_mode({}, **win) == "visual"
    assert r.detect_mode({"WT_SESSION": "x"}, **win) == "visual"
    assert r.detect_mode({"TERM_PROGRAM": "mintty"}, **win) == "logical"
    assert r.detect_mode({"MSYSTEM": "MINGW64"}, **win) == "logical"
    assert r.detect_mode({}, platform="linux", isatty=True) == "logical"
    assert r.detect_mode({}, platform="win32", isatty=False) == "logical"          # pipes and files
    assert r.detect_mode({"JARVES_CONSOLE_RTL": "visual"}, platform="linux", isatty=False) == "visual"
    assert r.detect_mode({"JARVES_CONSOLE_RTL": "logical"}, **win) == "logical"
    assert r.detect_mode({"JARVES_CONSOLE_RTL": "auto"}, **win) == "visual"


def test_stream_wrapper_converts_lines_and_prompts():
    raw = io.StringIO()
    s = r.RtlStream(raw, width_fn=lambda: 80)
    s.write("שלום jarvis\n")
    s.write("כן? ")
    s.flush()
    assert raw.getvalue() == "jarvis םולש\n" + "?ןכ "


def test_install_respects_no_rtl_fix(monkeypatch):
    import sys
    monkeypatch.setenv("JARVES_CONSOLE_RTL", "visual")
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    assert r.install(["setup", "--no-rtl-fix"]) == "logical"
    assert not isinstance(sys.stdout, r.RtlStream)
    assert r.install(["setup"]) == "visual"
    assert isinstance(sys.stdout, r.RtlStream)
