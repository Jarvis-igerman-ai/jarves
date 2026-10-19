"""`python -m jarves.camera status | on <switch> | off <switch> | people | forget <name> | forget-all`

`status` probes for a camera WITHOUT opening it and reads saved feature switches (legacy local settings only
when the features backend is absent). Run while the Core is stopped or running. Never opens the sensor or takes
a photo.
"""
from __future__ import annotations

import sys

from ..config import load_config, state_dir
from ..memory import MemoryStore
from .backends import probe
from .faces import FaceStore
from .switches import SWITCHES, Switches


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "status"
    home = state_dir()
    cfg, _ = load_config()
    mem = MemoryStore(home / "memory.db")
    sw = Switches(mem)
    p = probe(cfg.camera.backend)
    sw.register_all({"camera": lambda: (p.available, p.detail), "face_recognition": lambda: (p.available, p.detail),
                     "camera_when_away": lambda: (p.available, p.detail)})
    ids = [s[0] for s in SWITCHES]
    try:
        if cmd == "status":
            print(f"camera: {'found' if p.available else 'NOT available'} - {p.detail}" + (f" ({p.action})" if p.action else ""))
            if sw.backend == "features":
                from ..features import Features
                enabled = Features(cfg, home).enabled
            else:
                enabled = sw.local.enabled
            for fid, _, title, _ in SWITCHES:
                print(f"  {fid:<18} {'ON ' if enabled(fid) else 'off'}  {title}")
            print(f"switch backend: {sw.backend}")
            return 0
        if cmd in ("on", "off") and len(args) == 2 and args[1] in ids:
            if sw.backend != "local":
                print("switches are managed by the dashboard (jarves.features); use the dashboard")
                return 2
            sw.local.set(args[1], cmd == "on")
            print(f"{args[1]} -> {cmd}")
            return 0
        if cmd in ("people", "forget", "forget-all"):
            if not (home / "faces.db").exists():
                print("no face data stored")
                return 0
            fs = FaceStore(home / "faces.db")
            try:
                if cmd == "people":
                    for x in fs.people():
                        print(f"  {x['name']} ({x['samples']} samples)")
                elif cmd == "forget" and len(args) == 2:
                    print("deleted" if fs.forget(args[1]) else "no such person")
                elif cmd == "forget-all":
                    print(f"deleted {fs.forget_all()} people")
                else:
                    print(__doc__)
                    return 2
            finally:
                fs.close()
            return 0
        print(__doc__)
        return 2
    finally:
        mem.close()


if __name__ == "__main__":
    raise SystemExit(main())
