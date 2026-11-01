"""Owner-question wording: short plain Hebrew, one decision per card, technical text under Details.

The owner reads Hebrew and is not a developer. Every generated question follows one shape:
title = one short Hebrew sentence (about 12 words at most, no English except product names and ids such as #123);
``why`` = one sentence; 2-3 options, each one short line saying what happens, the recommended one first with a
one-line reason; time and "if you skip" in one line each; steps only for physical actions (numbered, at most 3).
Ids, task names in English and acceptance criteria go to ``details`` (shown collapsed).

Questions that are the same decision per version (the owner-gate cards) carry ``group`` = ``gate:<version>`` and a
short ``item`` name; the console shows one card per group with a checklist instead of one card per task. Each task
keeps its own question and its own answer, so nothing about answering or the ledger changes.

``rewrite_pending_questions`` is the one-shot migration of already stored questions: display fields only.
"""
from __future__ import annotations

import re

from .store import ACTIVE_QUESTIONS, Store, question_view

HEBREW = re.compile(r"[֐-׿]")
# The project's mixed-language rule: no run of 3+ bare English words in a Hebrew text.
ENGLISH_RUN = re.compile(r"\b[A-Za-z]+ [A-Za-z]+ [A-Za-z]+\b")
DISPLAY_TEXT_FIELDS = ("title", "why", "recommendation", "safe_default", "skip_effect", "item")
TITLE_WORDS = 12


def english_runs(text: str) -> list[str]:
    """Runs of three or more bare English words (the Hebrew-mix lint)."""
    return ENGLISH_RUN.findall(str(text or ""))


def visible_texts(question: dict) -> list[str]:
    """Every text the card shows outside Details: title, why, reason, defaults, options and steps."""
    texts = [str(question.get(k) or "") for k in DISPLAY_TEXT_FIELDS]
    for choice in question.get("choices") or []:
        if isinstance(choice, dict):
            texts += [str(choice.get("label") or ""), str(choice.get("description") or "")]
        else:
            texts.append(str(choice))
    texts += [str(s) for s in question.get("steps") or []]
    return [t for t in texts if t]


def plain_hebrew(text: str) -> bool:
    """Hebrew text that passes the mix rule (product names like JARVES/Gate/Opus and ids stay allowed)."""
    return bool(HEBREW.search(str(text or ""))) and not english_runs(text)


# ---- owner-gate questions (one per gated task, grouped per version in the console) ---------------------------

GATE_CHOICES = [
    {"id": "later-today", "label": "לתזמן להיום", "recommended": True,
     "description": "המנהל מוסיף לרשימת היום עם הצעדים. שום דבר לא רץ עד שתעשה. אפשר לשנות אחר כך."},
    {"id": "tell-me", "label": "תגיד לי כשהגרסה מוכנה",
     "description": "נשאר בתור. המנהל יודיע כששאר הגרסה מוכן לזה. אפשר לשנות אחר כך."},
    {"id": "skip-version", "label": "לדלג בגרסה הזו",
     "description": "יוצא מהגרסה הזו, והגרסה יכולה לצאת בלעדיו. אפשר לשנות אחר כך."},
]
# "אפשר לשנות אחר כך": an answered question can be reopened (dashboard or `jarvis workflow reopen`).
GATE_WHY = "סוכן לא יכול לסיים את זה לבד: צריך מכשיר אמיתי, כניסה לחשבון או החלטה שלך."
GATE_SAFE_DEFAULT = "שום דבר לא נעשה ולא מאושר בלעדיך"


def _short(text: str, words: int) -> str:
    parts = str(text or "").split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def gate_text(task: dict, minutes: int) -> dict:
    """Display fields of the owner-gate question of one task (Hebrew; raw text and criteria under details)."""
    version = str(task.get("version") or "").strip() or "הגרסה"
    name = str(task.get("title") or "").strip()
    item = name if plain_hebrew(name) else f"משימה {task['id']}"
    gate = str(task.get("owner_gate") or "").strip()
    why = f"{GATE_WHY.split(':')[0]}. נדרש: {gate}" if plain_hebrew(gate) and len(gate) <= 160 else GATE_WHY
    criteria = [str(row.get("label", "")).strip() for row in task.get("acceptance") or []
                if isinstance(row, dict) and str(row.get("label", "")).strip()]
    details = [f"משימה: {task['id']} · {version}"]
    if name and name != item:
        details.append(f"שם המשימה: {name}")
    if gate:
        details.append(f"מה נדרש (מקור): {gate}")
    if criteria:
        details.append("מה ייבדק:\n" + "\n".join(f"- {c}" for c in criteria[:8]))
    return {
        "title": f"צריך אותך: {_short(item, TITLE_WORDS - 2)}",
        "item": item[:300],
        "group": f"gate:{version}"[:120],
        "why": why[:1000],
        "recommendation": f"כך {version} ממשיכה להתקדם. לוקח כ־{minutes} דקות.",
        "skip_effect": f"שום דבר לא נשבר ולא מאושר. המשימה נשארת בתור, ו־{version} יכולה לצאת בלעדיה.",
        "safe_default": GATE_SAFE_DEFAULT,
        "steps": [],
        "details": "\n".join(details)[:4000],
        "context": "",
        "choices": [dict(c) for c in GATE_CHOICES],
    }


def manual_test_text(task: dict) -> dict:
    """The autopilot's 'owner runs the manual test' question, in Hebrew."""
    name = str(task.get("title") or "").strip()
    sheet = ", ".join(task.get("source_paths") or []) or "ראו את תנאי הקבלה של המשימה"
    item = name if plain_hebrew(name) else f"משימה {task['id']}"
    return {"title": f"בדיקה ידנית שלך: {_short(item, TITLE_WORDS - 3)}",
            "why": "הקוד מוכן, ורק בדיקה שלך במכשיר תסגור את המשימה.",
            "skip_effect": "המשימה נשארת בבדיקה ולא נסגרת.",
            "safe_default": "המשימה מחכה לבדיקה שלך",
            "context": "",
            "details": f"משימה: {task['id']}\nגיליון בדיקה: {sheet}\nאת התוצאה רושמים עם check --criterion --evidence "
                       "(טקסט של תשובה אינו ראיה)."}


# ---- one-shot rewrites of manager-written questions still open in the live ledger ----------------------------
# Matched by id AND by their original (or already rewritten) title, so a reused id is never touched.

LIVE_REWRITES = {
    "q-94e8b67aebdc": {
        "original": "gh-122 voice latency: approve paid live sessions?",
        "title": "לאשר 5 שיחות קול בתשלום כדי למדוד מהירות?",
        "why": "את מהירות הקול אפשר למדוד רק בשיחות אמיתיות, והן עולות כסף.",
        "choices": [
            {"id": "a", "label": "לאשר: 5 שיחות, עד 5 דולר, רק במחשב", "recommended": True,
             "description": "סוכן מריץ 5 שיחות קצרות ומודד. לא יותר מ־5 דולר, וכסף שיצא לא חוזר."},
            {"id": "b", "label": "רק קוד, בלי שיחות בתשלום",
             "description": "מכינים את המדידה בקוד. המדידה האמיתית מחכה."},
            {"id": "c", "label": "לדחות לגרסה מאוחרת יותר", "description": "#122 עובר לגרסה הבאה."},
        ],
        "recommendation": "בלי מדידה אמיתית אי אפשר לסגור את #122.",
        "safe_default": "רק קוד, בלי שיחות בתשלום",
        "skip_effect": "שום כסף לא יוצא, ו־#122 נשאר פתוח.",
        "minutes": 30,
    },
    "q-a92cdde685b5": {
        "original": "Retire legacy igalgerman/" "JARVIS repo: archive it on GitHub?",
        "title": "המאגר הישן JARVIS ב־GitHub: לארכב אותו?",
        "why": "המאגר הישן כבר לא בשימוש. ארכוב מונע בלבול, אבל קשה לבטל אותו.",
        "choices": [
            {"id": "archive_after_check", "label": "קודם בדיקה, ואז אתה מארכב", "recommended": True,
             "description": "סוכן מוודא ששום דבר ייחודי לא נשאר. אחר כך אתה לוחץ Archive. קשה לבטל."},
            {"id": "keep_open", "label": "להשאיר כמו שהוא בינתיים",
             "description": "המאגר נשאר לקריאה בלבד, בלי ארכוב."},
        ],
        "recommendation": "הבדיקה מוודאת שלא מאבדים כלום לפני צעד שקשה לבטל.",
        "safe_default": "המאגר נשאר כמו שהוא",
        "skip_effect": "שום דבר לא משתנה, ו־#5 נשאר פתוח.",
        "minutes": 5,
    },
    "q-bf7f4acd789f": {
        "original": "Installer #58: confirm a time slot for owner-PC acceptance (earlier answer 'ok' was unconfirmed)",
        "title": "איך לבדוק את ההתקנה במחשב שלך?",
        "why": "צריך למדוד שההתקנה לוקחת פחות מ־2 דקות, ולראות שחלון ההתקנה נראה טוב.",
        "choices": [
            {"id": "b", "label": "סוכן מתקין בשקט את הבטא האחרונה", "recommended": True,
             "description": "קודם גיבוי, אחר כך התקנה שקטה ומדידת זמן. את חלון ההתקנה תבדוק בעיניים."},
            {"id": "a", "label": "אתה מריץ את הצעדים כשאתה ליד המחשב",
             "description": "המנהל מוסיף לרשימת היום. שום דבר לא מותקן בלעדיך."},
            {"id": "c", "label": "לדחות לגיליון הבדיקות של 5.0",
             "description": "הבדיקה תיעשה יחד עם שאר בדיקות 5.0."},
        ],
        "recommendation": "התקנה שקטה נותנת מדידת זמן אמיתית בלי להפריע לך.",
        "safe_default": "לדחות לגיליון הבדיקות של 5.0",
        "skip_effect": "שום דבר לא מותקן, ו־#58 נשאר פתוח.",
        "minutes": 15,
    },
}


def _live_text(question: dict) -> dict | None:
    spec = LIVE_REWRITES.get(question["id"])
    if not spec or question.get("title") not in (spec["original"], spec["title"]):
        return None
    out = {k: v for k, v in spec.items() if k != "original"}
    if question.get("title") == spec["original"]:
        raw = [f"ניסוח מקורי: {spec['original']}"]
        if question.get("context"):
            raw.append(question["context"])
        if question.get("why"):
            raw.append(f"למה (מקור): {question['why']}")
        out["details"] = "\n".join(raw)[:4000]
        out["context"] = ""
    return out


def _minutes(question: dict, task: dict) -> int:
    scope = task.get("scope")
    minutes = scope.get("owner_minutes") if isinstance(scope, dict) else None
    if type(minutes) is int and 0 < minutes <= 480:
        return minutes
    return question.get("minutes") if type(question.get("minutes")) is int and 0 < question["minutes"] <= 480 else 15


def proposed_text(question: dict, tasks: dict) -> dict | None:
    """The new display fields of one stored question, or None when it has no template."""
    task = tasks.get(question.get("task_id")) or {}
    if question["id"].startswith("gate-") and task.get("owner_gate"):
        return {**gate_text(task, _minutes(question, task)), "minutes": _minutes(question, task)}
    if question["id"].startswith("manual-") and task:
        return manual_test_text(task)
    return _live_text(question)


def rewrite_pending_questions(store: Store, *, apply: bool = True) -> list[dict]:
    """One-shot migration: plain Hebrew display text for every still-open question that has a template.

    Display fields only (title, why, options text, details, ...). Ids, status, answers and timestamps stay as they
    are. Skips answered questions, questions with any recorded answer and answers that need owner confirmation.
    Idempotent: a second run returns no rows. ``apply=False`` is the dry run.
    """
    snap = store.snapshot()
    tasks = {t["id"]: t for t in snap["tasks"]}
    rows = []
    for question in snap["questions"]:
        view = question_view(question)
        if (question.get("status") not in ACTIVE_QUESTIONS or question.get("answer") not in (None, "")
                or view.get("needs_owner_confirmation")):
            continue
        new = proposed_text(question, tasks)
        if not new:
            continue
        changed = sorted(k for k, v in new.items() if question.get(k) != v)
        if not changed:
            continue
        row = {"id": question["id"], "before": question.get("title", ""), "after": new.get("title", question.get("title")),
               "fields": changed, "group": new.get("group", question.get("group", ""))}
        if apply:
            try:
                row["applied"] = store.rewrite_display(question["id"], new)
            except ValueError as exc:  # e.g. an older question whose choice ids differ: left as it was
                row.update(applied=False, error=str(exc))
        rows.append(row)
    return rows


def rewrite_table(rows: list[dict], applied: bool) -> str:
    """Before/after table for the CLI."""
    if not rows:
        return "nothing to rewrite: every open question already has the plain wording"
    groups = {r["group"] for r in rows if r["group"]}
    lines = [f"{'applied' if applied else 'dry run'}: {len(rows)} question(s) rewritten; "
             f"{sum(1 for r in rows if r['group'])} grouped into {len(groups)} card(s)", ""]
    for r in rows:
        lines.append(f"{r['id']}  [{', '.join(r['fields'])}]" + (f"  NOT APPLIED: {r['error']}" if r.get("error") else ""))
        lines.append(f"  before: {r['before']}")
        lines.append(f"  after:  {r['after']}")
    return "\n".join(lines)
