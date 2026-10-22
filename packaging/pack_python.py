"""Pack reviewed code-only Python dependencies; keep native/resource packages and JARVES on disk.

The normal site loader reads jarves-libraries.pth even with python -I. Native DLLs, metadata, certificates,
model data and the app's path-based resources stay unpacked. Unknown packages stay unpacked by default.
"""
from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path

# Code-only candidates reviewed against the locked payload; packages with data/native files are still refused.
CANDIDATES = (
    "agents", "openai", "pydantic", "httpx", "httpcore", "httpx2", "httpcore2", "h11", "idna", "sniffio",
    "anyio", "annotated_types", "typing_extensions", "typing_inspection", "packaging", "requests", "urllib3",
    "charset_normalizer", "websockets", "click", "attrs", "attr", "mcp", "mcp_types", "jsonschema",
    "referencing", "sse_starlette", "starlette", "uvicorn", "python_multipart", "multipart", "jwt",
)


def pack(stage: Path) -> dict:
    stage = Path(stage).resolve()
    python = stage / "python"
    site = python / "Lib" / "site-packages"
    if not (python / "python.exe").is_file() or not site.is_dir():
        raise ValueError("expected an isolated Windows staging payload, not a live Python environment")
    before = sum(p.is_file() for p in stage.rglob("*"))
    for cache in list(python.rglob("__pycache__")):
        if cache.is_symlink():
            raise ValueError("symlink in staged payload")
        shutil.rmtree(cache)
    for pyc in python.rglob("*.pyc"):
        pyc.unlink()
    selected, kept = [], {}
    for name in CANDIDATES:
        root = site / name
        if not root.exists():
            root = site / (name + ".py")
        if not root.exists():
            continue
        files = sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else [root]
        if any(p.is_symlink() for p in [root, *files]):
            raise ValueError("symlink in staged dependency")
        if any(p.suffix not in (".py", ".pyi") and p.name != "py.typed" for p in files):
            kept[name] = "native or resource files; keep path-based loading"
            continue
        if files:
            selected.append((root, files))
    archive = site / "jarves-libraries.zip"
    if archive.exists():
        raise ValueError("payload was already packed; build from a fresh stage")
    temporary = archive.with_suffix(".tmp")
    count = 0
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_STORED) as z:
        for _, files in selected:
            for p in files:
                entry = zipfile.ZipInfo(p.relative_to(site).as_posix(), (2020, 1, 1, 0, 0, 0))
                entry.external_attr = 0o644 << 16
                z.writestr(entry, p.read_bytes())
                count += 1
    with zipfile.ZipFile(temporary) as z:
        if z.testzip():
            raise ValueError("library archive failed CRC verification")
    temporary.replace(archive)
    (site / "jarves-libraries.pth").write_text("jarves-libraries.zip\n", encoding="utf-8")
    for root, _ in selected:
        shutil.rmtree(root) if root.is_dir() else root.unlink()
    result = {"files_before": before, "files_after": sum(p.is_file() for p in stage.rglob("*")),
              "archived_files": count, "archive_bytes": archive.stat().st_size,
              "packed": [p.name for p, _ in selected], "kept": kept, "bytecode_precompiled": False}
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    result = pack(args.stage)
    text = json.dumps(result, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
