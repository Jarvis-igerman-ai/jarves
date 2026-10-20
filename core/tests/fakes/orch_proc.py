"""A real Orchestrator (bridge + registry) in its own process, for the crash / restart and idle-cost tests.

    orch_proc.py HOME CLAUDE_JSON CODEX_JSON [--measure SECONDS]
Prints {"ready": true, "pid": ...} once the bridge listens; with --measure it then stays idle for SECONDS and prints
{"cpu_s": ..., "rss_delta_mb": ..., "wall_s": ...} measured on ITSELF (all threads; the RSS added by importing and
starting the orchestrator over the bare interpreter). Ends when stdin closes (or is killed by the test)."""
from __future__ import annotations

import json
import os
import sys
import time


def rss_mb() -> float:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        psapi = ctypes.WinDLL("psapi")
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
        return c.WorkingSetSize / 1048576
    with open("/proc/self/status", encoding="ascii") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    return 0.0


def main(argv: list[str]) -> int:
    home, claude_cmd, codex_cmd = argv[0], json.loads(argv[1]), json.loads(argv[2])
    measure = float(argv[argv.index("--measure") + 1]) if "--measure" in argv else 0.0
    from jarves import approvals, capabilities, features, hebrew, logs  # noqa: F401  (the Core has these anyway)
    from jarves.config import Config
    base = rss_mb()                               # what the orchestrator ADDS to a Core
    from jarves.orchestrator.service import Orchestrator
    cfg = Config()
    cfg.agents.claude_command, cfg.agents.codex_command = claude_cmd, codex_cmd
    o = Orchestrator(__import__("pathlib").Path(home), cfg).start()
    print(json.dumps({"ready": True, "pid": os.getpid()}), flush=True)
    if measure:
        t0, c0 = time.monotonic(), time.process_time()
        r0 = rss_mb()
        time.sleep(measure)                       # idle: the bridge blocks in accept(), nothing polls
        print(json.dumps({"cpu_s": time.process_time() - c0, "wall_s": time.monotonic() - t0,
                          "rss_delta_mb": round(max(r0, rss_mb()) - base, 1)}), flush=True)
    sys.stdin.read()
    o.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
