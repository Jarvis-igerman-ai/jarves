"""SW-V4 package B (issue #15): every logs.* call has a fixed message and only registered fields (AST lint).

The lint itself lives in tools/log_lint.py (also a CLI: `python tools/log_lint.py`). These tests run it over the
whole package, prove it catches each kind of violation (f-string, % formatting, concatenation, a variable, an
unregistered field, an unknown ** expansion, a reserved keyword, indirect calls through a level variable or a
wrapper), and check the registry itself."""
from __future__ import annotations

import ast
import re
import sys
import textwrap
from pathlib import Path

import pytest

from jarves import logs

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
from tools import log_lint  # noqa: E402


def lint_src(tmp_path, src: str, name: str = "mod.py"):
    p = tmp_path / name
    p.write_text("from . import logs\n" + textwrap.dedent(src), encoding="utf-8")
    return log_lint.scan_file(p, logs.FIELD_CLASS)


def test_every_logs_call_in_the_package_is_clean():
    calls, bad = log_lint.scan(logs.FIELD_CLASS)
    assert len(calls) >= 450                       # the whole package is covered (466 call sites at V4 B)
    assert not bad, "\n".join(f"{b['file']}:{b['line']}: {b['rule']} {b['detail']}" for b in bad)


def test_the_lint_finds_calls_in_every_module_that_imports_logs():
    """100 % coverage: each module that imports logs and calls it has its calls found by the scanner."""
    src = CORE / "src" / "jarves"
    found = {c["file"] for c in log_lint.scan(logs.FIELD_CLASS)[0]}
    for p in src.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        tree = ast.parse(text)
        if not log_lint._aliases(tree) or p.name == "logs.py":
            continue
        if any(re.search(rf"\b{a}\.(info|warn|error|debug|verbose|log)\(", text) for a in log_lint._aliases(tree)):
            assert p.relative_to(CORE).as_posix() in found, p


@pytest.mark.parametrize("call, rule", [
    ('logs.info("c", "CODE", f"user said {x}")', "msg-not-literal"),
    ('logs.info("c", "CODE", "user said %s" % x)', "msg-not-literal"),
    ('logs.info("c", "CODE", "user said " + x)', "msg-not-literal"),
    ('logs.info("c", "CODE", "said {}".format(x))', "msg-not-literal"),
    ('logs.info("c", "CODE", x)', "msg-not-literal"),
    ('logs.warn("c", "CODE", str(e))', "msg-not-literal"),
    ('logs.info("c", "CODE")', "msg-missing"),
    ('logs.info("c", "CODE", "fixed", not_in_the_registry=1)', "field-not-registered"),
    ('logs.info("c", "CODE", "fixed", **payload)', "star-expansion"),
    ('logs.info("c", "CODE", "fixed", **{k: v for k, v in d.items()})', "star-expansion"),
    ('logs.info("c", "CODE", "fixed", level=3)', "reserved-keyword"),
    ('logs.info("c", make_code(), "fixed")', "code-not-literal"),
    ('logs.info("c", "CODE", "fixed", "extra positional", 1)', "extra-positional"),
])
def test_the_lint_catches_each_violation(tmp_path, call, rule):
    _, bad = lint_src(tmp_path, f"def f(x, e, d, payload, make_code):\n    {call}\n")
    assert rule in {b["rule"] for b in bad}, bad


def test_the_lint_follows_level_variables_and_wrappers(tmp_path):
    _, bad = lint_src(tmp_path, """
        def f(ok, x, self):
            level = logs.info if ok else logs.warn
            level("c", "CODE", f"text {x}")
            self._log(logs.info, "CODE", f"text {x}")
    """)
    assert [b["rule"] for b in bad] == ["msg-not-literal", "msg-not-literal"]


def test_the_lint_accepts_the_allowed_forms(tmp_path):
    calls, bad = lint_src(tmp_path, """
        def f(x, e, ok, code, stats):
            logs.info("c", "CODE", "fixed text", text=x, n=3)
            logs.warn("c", "OK" if ok else "BAD", "fixed text", **logs.error_fields(e))
            logs.info("c", f"JOB_{x}", "fixed", **({"chars": 1} if ok else {}))
            logs.error("c", code, "fixed", exc=True)
            logs.info("c", "CODE", "fixed", **logs.numeric_fields(stats, ("_s",)))
            logs.log("c", "CODE", "fixed", 40, provider_error=x)
    """)
    assert len(calls) == 6 and not bad, bad


def test_registry_names_are_unique_lowercase_and_classified():
    assert set(logs.FIELD_CLASS.values()) <= set(logs.CLASSES)
    assert all(k == k.lower() and k.isidentifier() for k in logs.FIELD_CLASS)
    for k, c in logs.FIELD_CLASS.items():   # a credential-shaped name is never registered as anything but secret
        if any(f in k for f in logs._SENSITIVE_FRAGMENTS):
            assert logs.field_class(k) == logs.SECRET, k
    # the core classes of the spec
    for k in ("text", "utterance", "provider_error", "exc_text", "lib_text", "facts", "detail", "why", "query"):
        assert logs.field_class(k) == logs.CONTENT, k
    for k in ("email", "phone", "name", "to", "frm", "profile", "mic", "speaker", "address", "recipient"):
        assert logs.field_class(k) == logs.PII, k
    for k in ("api_key", "token", "password", "cookie", "authorization", "refresh_token", "client_secret"):
        assert logs.field_class(k) == logs.SECRET, k
    for k in ("ms", "n", "code_name", "error_type", "error_code", "exc_frames", "status", "tool", "skill"):
        assert logs.field_class(k) == logs.SAFE, k
    assert logs.field_class("never_registered_name") == logs.CONTENT        # unknown -> content


def test_issue_15_repro_1_free_text_message_is_impossible_and_fields_are_masked(tmp_path):
    """#15 repro 1 (logs.info free text): the sentence now goes into a classified field and is masked."""
    path = logs.setup(tmp_path, privacy=True, console=False)
    logs.info("test", "PRIVATE", "user sent an address", text="a user wrote their full home address in this message")
    for h in logs._logger.handlers:
        h.flush()
    raw = path.read_text(encoding="utf-8")
    assert "home address in this message" not in raw and '"text": "<52 chars>"' in raw
    assert '"msg": "user sent an address"' in raw and '"v": 2' in raw
    # and the lint refuses the old form
    _, bad = lint_src(tmp_path, 'def f(t):\n    logs.info("test", "PRIVATE", f"{t}")\n', "repro.py")
    assert bad and bad[0]["rule"] == "msg-not-literal"
