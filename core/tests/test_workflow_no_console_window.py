"""Issue #889: workflow/prepush/steward subprocess launches must not open a visible console window on Windows."""
import ast
import sys
import unittest
from pathlib import Path

from jarves.workflow import winproc

CORE = Path(__file__).resolve().parents[1]
SCANNED = sorted((CORE / "src" / "jarves" / "workflow").glob("*.py")) + [
    CORE / "tools" / name for name in ("workflow_hook.py", "workflow_console.py", "prepush.py", "integration_branch.py")]
# Deliberately visible: opens a Claude window for the owner via "cmd /c start" (discuss.py).
EXEMPT = {("discuss.py", "_default_launch")}
SUBPROCESS_FUNCS = {"run", "Popen", "call", "check_call", "check_output"}
INJECTED = {"popen"}  # injectable Popen default in autopilot_update


def _hidden(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "creationflags":
            return True
        if kw.arg is None and isinstance(kw.value, ast.Name) and kw.value.id == "_HIDDEN":
            return True
        if kw.arg is None and isinstance(kw.value, ast.Call) and getattr(kw.value.func, "id", "") == "hidden_popen_kwargs":
            return True
    return False


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []

    def visit(node, scope):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = node.name
        if isinstance(node, ast.Call):
            f = node.func
            bad = False
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                if f.value.id == "subprocess" and f.attr in SUBPROCESS_FUNCS:
                    bad = True
                if f.value.id == "os" and f.attr == "system":
                    bad = True
            elif isinstance(f, ast.Name) and f.id in INJECTED:
                bad = True
            if bad and not _hidden(node) and (path.name, scope) not in EXEMPT:
                found.append(f"{path.name}:{node.lineno}")
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, "")
    return found


class NoConsoleWindowTests(unittest.TestCase):
    def test_every_launch_hides_the_console(self):
        bad = [v for p in SCANNED if p.exists() for v in _violations(p)]
        self.assertEqual(bad, [], "subprocess call without creationflags / hidden_popen_kwargs(): " + ", ".join(bad))

    def test_scanner_catches_a_missing_flag(self):
        sample = Path(__file__).with_name("_sample_889.py")
        try:
            sample.write_text("import subprocess\nsubprocess.run(['x'])\nsubprocess.run(['y'], creationflags=1)\n", encoding="utf-8")
            self.assertEqual(_violations(sample), ["_sample_889.py:2"])
        finally:
            sample.unlink(missing_ok=True)

    def test_helper_values(self):
        if sys.platform == "win32":
            plain = winproc.hidden_popen_kwargs()["creationflags"]
            self.assertTrue(plain)
            self.assertGreater(winproc.hidden_popen_kwargs(detached=True)["creationflags"], plain)
        else:
            self.assertEqual(winproc.hidden_popen_kwargs(), {})


if __name__ == "__main__":
    unittest.main()
