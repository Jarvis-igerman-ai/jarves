"""Child process that runs ONE tool call of a generated Skill (skills/generated.py starts it per call).

Started as `python -I -S sandbox_child.py` with an empty environment (no API keys, no PATH), a throw-away working
directory and a hard timeout in the parent. Stdlib only, never imports jarves.
Layers here (on top of the parent's static AST validation and the approval gate):
- restricted builtins: no open/eval/exec/compile/getattr/type/vars/globals/__import__ except a guarded import that
  only admits the allow-listed modules sent by the parent;
- POSIX rlimits: memory, CPU seconds, no file writes, few file descriptors (Windows: the parent starts this process
  suspended inside a Job Object: one process, 512 MB, 10 s CPU, kill on close, no breakaway, UI restricted);
- the Skill reaches the outside only through `ctx` calls that the PARENT executes and checks against the user's
  grants (storage, network:<host>), one JSON line per request on stdout, the answer on stdin.
Protocol: stdin line 1 = {"code", "tool", "args", "imports"}; stdout = {"rpc", "id", "args"}* then {"done": true, ...}.
"""
import builtins
import json
import sys

SAFE = ("abs", "all", "any", "bool", "chr", "dict", "divmod", "enumerate", "filter", "float", "frozenset", "hash",
        "int", "isinstance", "issubclass", "iter", "len", "list", "map", "max", "min", "next", "ord", "pow", "range",
        "repr", "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple", "zip", "True", "False", "None",
        "Exception", "ValueError", "KeyError", "IndexError", "TypeError", "ArithmeticError", "ZeroDivisionError",
        "LookupError", "RuntimeError", "StopIteration")
_OUT = sys.stdout


def _limits() -> None:
    try:
        import resource
    except ImportError:          # Windows: the parent's timeout and the empty environment remain
        return
    for name, value in (("RLIMIT_AS", 512 << 20), ("RLIMIT_CPU", 10), ("RLIMIT_FSIZE", 0), ("RLIMIT_NOFILE", 16),
                        ("RLIMIT_NPROC", 0)):
        lim = getattr(resource, name, None)
        if lim is None:
            continue
        try:
            resource.setrlimit(lim, (value, value))
        except (ValueError, OSError):
            pass


def _send(obj) -> None:
    _OUT.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _OUT.flush()


class Ctx:
    """What a Skill function receives as `ctx`. Every call is executed and permission-checked by the parent."""

    def __init__(self) -> None:
        self._n = 0

    def _rpc(self, op, **args):
        self._n += 1
        _send({"rpc": op, "id": self._n, "args": args})
        line = sys.stdin.readline()
        if not line:
            raise RuntimeError("parent closed the channel")
        rep = json.loads(line)
        if not rep.get("ok"):
            raise PermissionError(str(rep.get("error", "denied")))
        return rep.get("result")

    def storage_get(self, key, default=None):
        return self._rpc("storage_get", key=key, default=default)

    def storage_set(self, key, value):
        return self._rpc("storage_set", key=key, value=value)

    def http_get(self, url):
        return self._rpc("http_get", url=url)

    def now(self):
        return self._rpc("now")

    def log(self, text):
        return self._rpc("log", text=str(text)[:300])


def main() -> None:
    _limits()
    req = json.loads(sys.stdin.readline())
    allowed = set(req.get("imports") or ())
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level != 0 or name.split(".")[0] not in allowed:
            raise ImportError(f"import of {name} is not allowed in a generated Skill")
        return real_import(name, globals, locals, fromlist, level)

    safe = {k: getattr(builtins, k) for k in SAFE}
    safe["__import__"] = guarded_import
    sys.stdout = sys.stderr      # stray output can never forge a protocol line
    g = {"__builtins__": safe, "__name__": "generated_skill"}
    try:
        exec(compile(req["code"], "skill.py", "exec"), g)       # noqa: S102 - validated, approved, isolated
        fn = g.get(req["tool"])
        if not callable(fn):
            raise LookupError(f"tool {req['tool']} not defined")
        res = fn(dict(req.get("args") or {}), Ctx())
        if isinstance(res, dict):
            out = {"message": str(res.get("message", "")), "data": res.get("data") if isinstance(res.get("data"), dict) else {}}
        else:
            out = {"message": "" if res is None else str(res), "data": {}}
        json.dumps(out)
        _send({"done": True, "ok": True, **out})
    except BaseException as e:   # noqa: BLE001 - report every failure as a result, never a traceback with paths
        _send({"done": True, "ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"})


if __name__ == "__main__":
    main()
