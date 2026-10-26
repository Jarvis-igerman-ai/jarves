"""GitHub writes class 1 (V7 G3, issue #410): create/edit an issue, comment, add/remove labels. Nothing else.

Every kind has a strict payload schema (field whitelist, size caps) checked BEFORE the owner is asked, and a handler
that makes the real REST call through `GitHubClient.write` (fixed issues-family endpoints, the allow-listed repo only,
api.github.com only, redirects off). The ONLY entry is `GitHubWriter.execute()`: the gates, the exact-payload approval
and the audit live in github_write.py. Text with an @ or a link is allowed but flagged in the approval; @everyone-style
and team mentions are refused. Labels: only labels that already exist on the repo (nothing is created).
CLI: `jarvis github issue create|edit|comment|labels-add|labels-remove` (CLI only, no model tool in G3).
"""
from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import quote

from . import github_client as gc
from . import github_write as gw

TITLE_MAX, BODY_MAX, LABEL_MAX, LABELS_MAX, ASSIGNEES_MAX = 200, 800, 50, 10, 5
_NUM = re.compile(r"[1-9][0-9]{0,8}", re.ASCII)   # always .fullmatch: "$" would accept a trailing newline (#578)
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f­؜᠎​-‏ -‮⁠-⁯"
                   r"﻿￹-￻]")      # controls (C0, C1/ANSI) and invisible / bidi marks: the approval must show it all
_BROAD_MENTION = re.compile(r"(?<![\w@`])@(?:all|everyone|channel|here)\b|(?<![\w@`])@[A-Za-z0-9][\w-]*/[\w.-]+", re.I)
FIELDS = {"issue.create": {"title", "body", "labels", "assignees"},
          "issue.edit": {"title", "body", "state", "labels", "assignees"},
          "issue.comment": {"body"}, "labels.add": {"labels"}, "labels.remove": {"labels"}}


def _text(v, cap: int, what: str, empty_ok: bool = False) -> str:
    if not isinstance(v, str) or (not v.strip() and not empty_ok):
        return f"{what} must be non-empty text"
    if len(v) > cap:
        return f"{what} is longer than {cap} characters"
    if _CTRL.search(v):
        return f"{what} has control or invisible characters (bidi, zero-width)"
    if _BROAD_MENTION.search(v):
        return f"{what} mentions everyone or a team; not allowed"
    return ""


def _names(v, cap_n: int, cap_len: int, what: str, login: bool = False) -> str:
    if not isinstance(v, list) or not 1 <= len(v) <= cap_n or len({str(x).lower() for x in v}) != len(v):
        return f"{what} must be a list of 1 to {cap_n} different names"
    for x in v:
        if login and not (isinstance(x, str) and _LOGIN.fullmatch(x)):
            return f"bad {what} name"
        why = _text(x, cap_len, what)
        if why or (not login and (x.strip(".") == "" or "/" in x or "?" in x or "#" in x or "%" in x or x != x.strip())):
            return why or f"bad {what} name"
    return ""


def validate(a: gw.WriteAction) -> str:
    """"" if the payload fits the kind's schema, else why not (nothing unknown passes)."""
    kind, p = a.kind.strip().lower(), a.payload
    if not isinstance(p, dict) or kind not in FIELDS or set(p) - FIELDS[kind] or not p:
        return "payload fields are not allowed for this kind"
    if (a.target == "") != (kind == "issue.create") or (a.target and not _NUM.fullmatch(a.target)):
        return "target must be the issue number (empty only for a new issue)"
    checks = {"title": lambda v: _text(v, TITLE_MAX, "title"), "body": lambda v: _text(v, BODY_MAX, "body"),
              "state": lambda v: "" if v in ("open", "closed") else "state must be open or closed",
              "labels": lambda v: _names(v, LABELS_MAX, LABEL_MAX, "label"),
              "assignees": lambda v: _names(v, ASSIGNEES_MAX, 39, "assignee", login=True)}
    for k, v in p.items():
        why = checks.get(k, lambda _v: "")(v)
        if why:
            return why
    if kind == "issue.create" and "title" not in p:
        return "a new issue needs a title"
    if kind == "issue.comment" and "body" not in p:
        return "a comment needs a body"
    if kind.startswith("labels.") and "labels" not in p:
        return "labels are needed"
    return ""


def action(kind: str, repo: str, number: str = "", **payload) -> gw.WriteAction:
    """Build an exact action; the preview is generated here from the payload (short, plain)."""
    what = {"issue.create": "Create an issue", "issue.edit": "Edit issue", "issue.comment": "Comment on",
            "labels.add": "Add labels to", "labels.remove": "Remove labels from"}[kind]
    head = f"{what} #{number}" if number else what
    detail = payload.get("title") or payload.get("body") or ", ".join(payload.get("labels") or []) or ""
    return gw.WriteAction(kind=kind, repo=repo, target=str(number), payload=payload, preview=(f"{head}: {detail}")[:200])


def _clean(url) -> str:
    return url if isinstance(url, str) and url.startswith("https://github.com/") and len(url) < 300 else ""


def _check_labels(c: gc.GitHubClient, o: str, r: str, labels: list[str]) -> list[str]:
    """A FRESH listing right before the write (never cached). Returns the repo's own spelling of each name. GitHub creates a
    label that does not exist when it is named in an issue-create / edit / labels POST (there is no flag to forbid it), so
    the only defence is this re-check; the window left is the few milliseconds between this read and the POST."""
    have = {x.lower(): x for x in c.repo_labels(o, r)}
    missing = [x for x in labels if x.lower() not in have]
    if missing:                                     # POST would silently CREATE them: refuse instead
        raise gc.GitHubError("GITHUB_WRITE_BAD_ARGUMENT", f"label not on the repo (labels are never created): {missing[0][:50]}")
    return [have[x.lower()] for x in labels]


def _extra(a: gw.WriteAction, c: gc.GitHubClient) -> str:
    """issue.edit: what the edit REPLACES (a PATCH swaps whole label / assignee sets) and whether it is a pull request."""
    o, _, r = a.repo.partition("/")
    cur, p, out = c.issue(o, r, a.target), a.payload, []
    if "labels" in p:
        out.append(f"REPLACES labels: {', '.join(cur['labels']) or '(none)'} -> {', '.join(p['labels'])}")
    if "assignees" in p:
        out.append(f"REPLACES assignees: {', '.join(cur['assignees']) or '(none)'} -> {', '.join(p['assignees'])}")
    if cur["is_pr"]:
        out.append("WARNING: this is a pull request: closing it closes the PR")
    return "\n".join(out)


def _do(a: gw.WriteAction, c: gc.GitHubClient) -> dict:
    o, _, r = a.repo.partition("/")
    base, kind, p = f"/repos/{o}/{r}/issues", a.kind.strip().lower(), dict(a.payload)
    if "labels" in p:
        p["labels"] = _check_labels(c, o, r, p["labels"])
    if kind == "issue.create":
        j = c.write("POST", base, p)
    elif kind == "issue.edit":
        j = c.write("PATCH", f"{base}/{a.target}", p)
    elif kind == "issue.comment":
        j = c.write("POST", f"{base}/{a.target}/comments", p)
    elif kind == "labels.add":
        c.write("POST", f"{base}/{a.target}/labels", p)
        j = {}
    else:
        for name in p["labels"]:
            c.write("DELETE", f"{base}/{a.target}/labels/{quote(name, safe='')}")
        j = {}
    j = j if isinstance(j, dict) else {}
    return {k: v for k, v in {"number": j.get("number") or (int(a.target) if a.target else None),
                              "url": _clean(j.get("html_url"))}.items() if v}


async def handle(a: gw.WriteAction, client: gc.GitHubClient) -> dict:
    return await asyncio.to_thread(_do, a, client)


for _k in FIELDS:
    gw.HANDLERS[_k] = handle
    gw.VALIDATORS[_k] = validate
gw.EXTRAS["issue.edit"] = _extra


# ------------------------------------------------------------------------------------------ CLI
def cmd_github_issue(a, cfg, home) -> int:
    """jarvis github issue create|edit|comment|labels-add|labels-remove [number] --repo owner/repo ...: the SAME execute()
    path and approval as any write; the approval window shows the exact text."""
    mode, repos = a.write_action or "", list(getattr(cfg, "github_repos", []) or [])
    kind = {"create": "issue.create", "edit": "issue.edit", "comment": "issue.comment",
            "labels-add": "labels.add", "labels-remove": "labels.remove"}.get(mode)
    repo = a.repo or (repos[0] if len(repos) == 1 else "")
    if not kind or not repo or (kind != "issue.create") == (not a.number):
        print("GITHUB_WRITE_BAD_ARGUMENT: usage: jarvis github issue create|edit|comment|labels-add|labels-remove "
              "[number] --repo owner/repo [--title T] [--body B] [--state open|closed] [--label L]... [--assignee U]...")
        return 2
    p = {k: v for k, v in {"title": a.title, "body": a.body, "state": a.state, "labels": a.label or None,
                           "assignees": a.assignee or None}.items() if v is not None}
    return execute_cli(cfg, home, action(kind, repo, a.number or "", **p))


def execute_cli(cfg, home, act: gw.WriteAction) -> int:
    """The shared CLI tail (issues and pull requests): refuse agent sessions, then the SAME execute() path with the popup."""
    from .approvals import ApprovalService, PopupChannel
    from .cli import _cli_features
    repos = list(getattr(cfg, "github_repos", []) or [])
    why = gw.agent_session_reason()
    if why:      # the typed hash was no secret (a pty can fake a terminal): the owner approves in the POPUP, never here
        print(json.dumps({"ok": False, "code": "GITHUB_WRITE_AGENT_SESSION", "message":
                          f"refused: {why}. Run this command yourself in your own terminal; the approval opens as a popup "
                          "window on this computer."}, ensure_ascii=False))
        return 1
    svc = ApprovalService(home)
    svc.channels.append(PopupChannel(svc, enabled=True, lang=getattr(getattr(cfg, "ui", None), "popup_language", "en")))
    w = gw.GitHubWriter(home, _cli_features(cfg, home), svc, repos=repos)       # local_only: only the popup approves
    r = asyncio.run(w.execute(act, origin="user"))
    print(json.dumps({"ok": r.ok, "code": r.code, "message": r.message, **r.data}, ensure_ascii=False))
    return 0 if r.ok else 1
