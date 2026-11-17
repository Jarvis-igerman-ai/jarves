"""Monthly best-in-class scout (JAR-233).

Reads docs/decisions/best_in_class.toml (the models, voice and agent libraries JARVES ships) and reports:
  drift    - the code no longer contains the recorded value (registry and code disagree)
  overdue  - `checked` is older than meta.max_age_days
  newer    - (--online) the official source lists a newer option than the recorded one
  missing  - (--online) an OpenAI model id is no longer listed on the official models page
  deprecated - (--online) the official deprecations page lists a recorded id (shutdown date, replacement)
  error    - (--online) the source could not be read (reported, never guessed)

It only reports; switching to a newer option is a normal PR. Offline by default; `--online` fetches each
distinct `source` once (official docs page / PyPI JSON) with no credentials. Exit 1 when any finding exists.

  python core/tools/best_in_class_scout.py [--online] [--today YYYY-MM-DD] [--json] [--report FILE]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[2]
REGISTRY = REPO / "docs" / "decisions" / "best_in_class.toml"
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_IMG = re.compile(r"\.(png|webp|jpe?g|svg|gif)$")
_ASSET_SUFFIX = re.compile(r"-(texture|block)$")  # stripped only from image file names
KINDS = ("openai-model", "pypi", "manual")

Fetch = Callable[[str], str]


def load(path: Path = REGISTRY) -> dict:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for e in data.get("entry", []):
        for key in ("id", "stage", "kind", "recorded", "source", "checked"):
            if key not in e:
                raise ValueError(f"registry entry {e.get('id', '?')}: missing {key}")
        if e["kind"] not in KINDS:
            raise ValueError(f"registry entry {e['id']}: unknown kind {e['kind']!r} (expected one of {KINDS})")
    return data


def vtuple(text: str) -> tuple[int, ...]:
    """Numeric version tuple without trailing zeros, so 1.1 == 1.1.0."""
    parts = [int(p) for p in re.findall(r"\d+", text)]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


_DATE = r"[A-Z][a-z]{2} \d{1,2}, \d{4}"
_PAIR = re.compile(rf"({_DATE})\s+({_DATE})")  # announced, shutdown: closes one deprecations-table row
_ID = re.compile(r"^(?=.*[\d-])[a-z0-9][a-z0-9._-]*[a-z0-9]$")  # a model id: lowercase, has a digit or "-"


def page_text(html_text: str) -> str:
    import html
    t = re.sub(r"<(script|style)\b.*?</\1\s*>", " ", html_text, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))


def deprecations(html_text: str) -> dict[str, dict]:
    """Rows of the official deprecations table: id -> {replacement, announced, shutdown}. One linear pass."""
    text, out, prev = page_text(html_text), {}, 0
    for m in _PAIR.finditer(text):
        chunk, prev = text[prev:m.start()], m.end()
        if " Deprecated " not in f" {chunk} ":
            continue
        head, repl = chunk.rsplit("Deprecated", 1)
        row = {"replacement": repl.strip(), "announced": m[1], "shutdown": m[2]}
        # The row's ids are the id-like tokens right before "Deprecated"; stop at the first other word,
        # so page prose and "<id> (mode) in the Responses API" rows (a mode, not the model) are not matched.
        for tok in reversed(re.sub(r"\([^)]*\)", " ", head).replace(",", " ").split()):
            if not _ID.match(tok):
                break
            out.setdefault(tok, row)
    return out


def page_ids(text: str) -> set[str]:
    """Candidate ids in raw page HTML (the official page keeps most ids in script data and image paths),
    with image/asset suffixes removed."""
    out = set()
    for tok in _TOKEN.findall(text):
        tok = tok.rstrip(".-_")
        if _IMG.search(tok):
            tok = _ASSET_SUFFIX.sub("", _IMG.sub("", tok))
        out.add(tok)
    return out


def check_offline(data: dict, today: dt.date, repo: Path = REPO) -> list[dict]:
    max_age = int(data.get("meta", {}).get("max_age_days", 31))
    found = []
    for e in data.get("entry", []):
        code, match = e.get("code"), e.get("code_match")
        if code and match:
            p = repo / code
            if not p.exists() or match not in p.read_text(encoding="utf-8"):
                found.append({"id": e["id"], "type": "drift",
                              "detail": f"{code} no longer contains {match!r}: update the registry or the code"})
        age = (today - dt.date.fromisoformat(e["checked"])).days
        if age > max_age:
            found.append({"id": e["id"], "type": "overdue",
                          "detail": f"checked {e['checked']} ({age} days ago, limit {max_age})"})
    return found


def _openai(e: dict, text: str) -> list[dict]:
    ids = page_ids(text)
    fam = re.compile(e["family"])
    m = fam.match(e["recorded"])
    if not m:
        return [{"id": e["id"], "type": "error", "detail": f"recorded {e['recorded']} does not match its family regex"}]
    mine = vtuple(m.group(1) or "0")
    newer = sorted((i for i in ids if (fm := fam.match(i)) and vtuple(fm.group(1) or "0") > mine),
                   key=lambda i: vtuple(fam.match(i).group(1) or "0"))
    out = []
    if newer:
        out.append({"id": e["id"], "type": "newer",
                    "detail": f"{e['recorded']} -> listed: {', '.join(newer)} ({e['source']})"})
    if e["recorded"] not in ids:
        out.append({"id": e["id"], "type": "missing",
                    "detail": f"{e['recorded']} is not listed on {e['source']}: check for deprecation or a successor"})
    return out


def _minor(text: str) -> tuple[int, int]:
    """(major, minor), padding missing parts with 0, so 1.0 and 1.0.5 compare equal."""
    parts = [int(p) for p in re.findall(r"\d+", text)][:2]
    return tuple(parts + [0] * (2 - len(parts)))  # type: ignore[return-value]


def _pypi(e: dict, text: str) -> list[dict]:
    latest = json.loads(text)["info"]["version"]
    pin = e.get("pin_below")
    if pin and vtuple(latest) >= vtuple(pin):
        where = f"outside the pin <{pin}"
    elif _minor(latest) > _minor(e["recorded"]):
        where = "new minor/major, allowed by the pin: evaluate and raise the floor"
    else:
        return []  # a patch the pin already installs is not a scout finding
    return [{"id": e["id"], "type": "newer",
             "detail": f"{e['package']} {e['recorded']} -> latest {latest} ({where})"}]


def _deprecated(e: dict, rows: dict[str, dict], url: str) -> list[dict]:
    r = rows.get(e["recorded"])
    if not r:
        return []
    return [{"id": e["id"], "type": "deprecated",
             "detail": f"{e['recorded']} deprecated {r['announced']}, shutdown {r['shutdown']}, "
                       f"replacement {r['replacement']} ({url})"}]


def check_online(data: dict, fetch: Fetch) -> list[dict]:
    cache: dict[str, str | Exception] = {}
    found = []
    dep_url = data.get("meta", {}).get("deprecations")
    rows: dict[str, dict] = {}
    if dep_url and any(e["kind"] == "openai-model" for e in data.get("entry", [])):
        try:
            rows = deprecations(fetch(dep_url))
            if not rows:
                found.append({"id": "meta", "type": "error", "detail": f"no deprecation rows parsed from {dep_url}"})
        except Exception as exc:
            found.append({"id": "meta", "type": "error", "detail": f"could not read {dep_url}: {exc}"})
    for e in data.get("entry", []):
        if e["kind"] == "manual":
            continue
        src = e["source"]
        if src not in cache:
            try:
                cache[src] = fetch(src)
            except Exception as exc:  # network/HTTP failure is a finding, not a crash
                cache[src] = exc
        text = cache[src]
        if isinstance(text, Exception):
            found.append({"id": e["id"], "type": "error", "detail": f"could not read {src}: {text}"})
            continue
        try:
            if e["kind"] == "openai-model":
                found += _openai(e, text) + _deprecated(e, rows, dep_url)
            else:
                found += _pypi(e, text)
        except (ValueError, KeyError, re.error) as exc:
            found.append({"id": e["id"], "type": "error", "detail": f"could not parse {src}: {exc}"})
    return found


def http_fetch(url: str) -> str:  # pragma: no cover - real network, only with --online
    import httpx
    r = httpx.get(url, timeout=30, follow_redirects=True, headers={"User-Agent": "jarves-best-in-class-scout"})
    r.raise_for_status()
    return r.text


def render(data: dict, findings: list[dict], today: dt.date, online: bool) -> str:
    lines = [f"# Best-in-class scout {today.isoformat()}", "",
             f"Registry: `docs/decisions/best_in_class.toml`. Mode: {'online (official sources fetched)' if online else 'offline (no sources fetched)'}.",
             "The scout reports only; a switch is a separate PR with tests.", "",
             "| Stage | Id | Recorded | Checked | Source |", "|---|---|---|---|---|"]
    for e in data.get("entry", []):
        lines.append(f"| {e['stage']} | {e['id']} | `{e['recorded']}` | {e['checked']} | {e['source']} |")
    lines += ["", "## Findings", ""]
    lines += [f"- **{f['type']}** `{f['id']}`: {f['detail']}" for f in findings] or ["- none"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, fetch: Fetch | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--registry", type=Path, default=REGISTRY)
    ap.add_argument("--online", action="store_true", help="fetch each official source (network)")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", type=Path, help="also write the Markdown report to this file")
    a = ap.parse_args(argv)
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    data = load(a.registry)
    findings = check_offline(data, today)
    if a.online:
        findings += check_online(data, fetch or http_fetch)
    text = render(data, findings, today, a.online)
    if a.report:
        a.report.write_text(text, encoding="utf-8")
    sys.stdout.write(json.dumps(findings, indent=2) + "\n" if a.json else text)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
