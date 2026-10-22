"""SW-V4 perf follow-up: model-facing tool text is minimal AND still routes. Offline only (no model, no spend).

1. `ROUTES`: ~150 English + Hebrew utterances -> the tool that must win. A fake router (no model) picks the tool whose
   description quotes the longest trigger phrase found in the utterance; this fails when a rewrite drops a trigger or
   makes two tools claim the same phrase.
2. `TRIGGERS`: every user-triggered tool keeps one EN and one HE phrase in its description.
3. `SAFETY`: 'ask first' / 'draft only' / 'never sends' notes survive the shrinking.
4. Ceiling on the whole backend call (tools JSON + rules) so it cannot regress (docs/reviews/SWV4_PERF.md).
Changes to the descriptions need a live voice re-run before release (docs/reviews/SWV4_PERF.md, 'needs live re-run')."""
import json
import re

import pytest

from jarves.voice.live import live_tools

# (utterance, expected tool). Each utterance contains a phrase quoted in the expected tool's description (EN or HE).
ROUTES = [
    ("hey what time is it right now", "get_time"), ("מה השעה עכשיו", "get_time"),
    ("what is 347*18 exactly", "calculate"), ("כמה זה 12 כפול 8", "calculate"),
    ("please remember that my keys are in the drawer", "remember"), ("תזכור ש הסיסמה של הוויפי בארון", "remember"),
    ("what do you remember about me", "recall"), ("מה אתה זוכר עליי", "recall"),
    ("forget that", "forget"), ("תשכח את זה", "forget"),
    ("forget everything", "forget_all"), ("תשכח הכל", "forget_all"),
    ("Dana is my wife, her mail is dana@example.com", "contact_remember"), ("דנה היא אשתי", "contact_remember"),
    ("email my wife the photos", "contact_lookup"), ("תשלח לאשתי את התמונות", "contact_lookup"),
    ("what is broken right now", "get_status"), ("מה מחובר אצלך", "get_status"),
    ("what can you do", "features_list"), ("מה אתה יודע לעשות", "features_list"),
    ("turn on the camera please", "feature_set"), ("תפעיל את המצלמה", "feature_set"),
    ("how much quota is left this month", "usage_status"), ("כמה נשאר לי מהמכסה", "usage_status"),
    ("play Daft Punk", "music_play"), ("תנגן שיר רגוע", "music_play"),
    ("pause", "music_pause"), ("תעצור רגע", "music_pause"),
    ("continue the song", "music_resume"), ("תמשיך לנגן", "music_resume"),
    ("next song", "music_next"), ("השיר הבא", "music_next"),
    ("stop the music", "music_stop"), ("תפסיק מוזיקה", "music_stop"),
    ("mute", "music_mute"), ("השתק", "music_mute"), ("שתוק רגע", "music_mute"),
    ("unmute it", "music_unmute"), ("תחזיר קול", "music_unmute"),
    ("louder please", "music_volume"), ("תגביר קצת", "music_volume"), ("quieter", "music_volume"), ("תנמיך", "music_volume"),
    ("what's playing right now", "music_status"), ("מה מתנגן עכשיו", "music_status"), ("what volume is it", "music_status"),
    ("read SETUP for me", "workspace_read"), ("תקרא את מדריך ההתקנה", "workspace_read"),
    ("note that I like tea", "workspace_note"), ("תרשום שאני אוהב תה", "workspace_note"),
    ("be funnier from now on", "workspace_edit"), ("תשנה את האופי שלך", "workspace_edit"),
    ("undo that change", "workspace_undo"), ("תבטל את השינוי", "workspace_undo"),
    ("what skills do you have", "list_skills"), ("אילו יכולות יש לך", "list_skills"),
    ("what can I change in settings", "list_settings"), ("אילו הגדרות יש", "list_settings"),
    ("your name is Max", "assistant_rename"), ("קוראים לך מקס", "assistant_rename"),
    ("which voices do you have", "voice_catalog"), ("אילו קולות יש", "voice_catalog"),
    ("open the dashboard", "open_dashboard"), ("תפתח את הדשבורד", "open_dashboard"),
    ("what's on my calendar tomorrow", "google_connect_help"), ("יש לי פגישות מחר", "google_connect_help"),
    ("WhatsApp Dana I'm late", "whatsapp_draft"), ("תשלח לדנה בוואטסאפ שאני מאחר", "whatsapp_draft"),
    ("add milk to the list", "list_add"), ("תוסיף חלב לקניות", "list_add"),
    ("what's on my list today", "list_show"), ("מה ברשימת הקניות", "list_show"),
    ("I bought the milk", "list_check"), ("קניתי חלב", "list_check"),
    ("remove milk from it", "list_remove"), ("תמחק חלב מהרשימה", "list_remove"),
    ("clear the list", "list_clear"), ("תנקה את הרשימה", "list_clear"),
    ("timer 10 minutes", "timer_start"), ("טיימר ל-10 דקות", "timer_start"),
    ("wake me at 7:30", "alarm_set"), ("תעיר אותי ב-7:30", "alarm_set"),
    ("how long is left on it", "timers_list"), ("כמה זמן נשאר", "timers_list"),
    ("cancel the timer", "timer_cancel"), ("תבטל את הטיימר", "timer_cancel"),
    ("every morning at 8 news summary", "schedule_preview"), ("כל בוקר ב-8 תסכם חדשות", "schedule_preview"),
    ("yes, activate it", "schedule_approve"), ("כן, תפעיל", "schedule_approve"),
    ("what jobs are scheduled", "jobs_list"), ("אילו משימות מתוזמנות", "jobs_list"),
    ("pause the job", "job_control"), ("תשהה את המשימה", "job_control"),
    ("delete the job", "job_delete"), ("תמחק את המשימה", "job_delete"),
    ("find my AI report", "report_find"), ("תמצא את הדוח", "report_find"),
    ("read this morning's report", "report_read"), ("תקריא את הדוח", "report_read"),
    ("write a note about the meeting", "note_add"), ("תרשום פתק", "note_add"),
    ("find my note about taxes", "note_find"), ("תמצא פתק על מיסים", "note_find"),
    ("add to the note", "note_edit"), ("תוסיף לפתק", "note_edit"),
    ("delete the note", "note_delete"), ("תמחק את הפתק", "note_delete"),
    ("export my notes", "note_export"), ("תייצא את הפתקים", "note_export"),
    ("save it as a file", "file_write"), ("תשמור כקובץ", "file_write"),
    ("read the file", "file_read"), ("תקרא את הקובץ", "file_read"),
    ("let me use my Documents folder", "folder_grant_request"), ("תן לי גישה לתיקייה", "folder_grant_request"),
    ("any alerts", "alerts_list"), ("יש התראות", "alerts_list"),
    ("quiet please", "alerts_control"), ("אל תפריע לי", "alerts_control"),
    ("any proposals", "proposals_list"), ("יש הצעות לשיפור", "proposals_list"),
    ("approve it", "proposal_decide"), ("תאשר את ההצעה", "proposal_decide"),
    ("give me my briefing", "briefing_get"), ("תן לי תדריך בוקר", "briefing_get"),
    ("check yourself", "self_check_run"), ("תבדוק את עצמך", "self_check_run"),
    ("what did it find", "self_check_suggestions"), ("מה הצעות הבדיקה", "self_check_suggestions"),
    ("accept 2", "self_check_decide"), ("תאשר 2", "self_check_decide"),
    ("make a ticket from it", "self_check_ticket"), ("תכין פנייה", "self_check_ticket"),
    ("how are you doing", "health_summary"), ("מה שלומך", "health_summary"),
    ("what did the reviewer find", "health_ai_review_summary"), ("מה מצא הבודק", "health_ai_review_summary"),
    ("restart the face", "health_remediate"), ("תתקן את זה", "health_remediate"),
    ("is anything broken", "run_selftest"), ("בדיקת תקינות", "run_selftest"),
    ("how fast are you", "run_speedtest"), ("בדיקת מהירות", "run_speedtest"),
    ("what did we say yesterday", "conversation_recall"), ("מה אמרתי אתמול", "conversation_recall"),
    ("search my talks for the car", "conversation_search"), ("חפש בשיחות על הרכב", "conversation_search"),
    ("how do I connect Google", "explain_feature"), ("איך מחברים מצלמה", "explain_feature"),
    ("what's not set up yet", "whats_not_set_up"), ("מה עוד לא מוגדר", "whats_not_set_up"),
    ("do it for me", "run_guide_step"), ("תעשה את זה בשבילי", "run_guide_step"),
    ("show me the guide", "open_guide"), ("תפתח את המדריך", "open_guide"),
    ("not now, later", "setup_snooze"), ("לא עכשיו", "setup_snooze"),
    ("stop suggesting things", "stop_suggestions"), ("תפסיק להציע", "stop_suggestions"),
    ("how do you look now", "appearance_get"), ("איך אתה נראה", "appearance_get"),
    ("be a 3D avatar", "set_appearance"), ("תעבור לאווטאר תלת מימד", "set_appearance"),
    ("learn a new skill", "skill_propose"), ("תלמד יכולת חדשה", "skill_propose"),
    ("my new skills", "skills_generated_list"), ("אילו יכולות יצרת", "skills_generated_list"),
    ("show yourself", "show_yourself"), ("תופיע", "show_yourself"),
    ("hide yourself", "hide_yourself"), ("תיעלם", "hide_yourself"),
    ("end the conversation", "end_conversation"), ("תסגור את השיחה", "end_conversation"), ("תסגור", "end_conversation"),
    ("quit", "quit_jarves"), ("שט דאון", "quit_jarves"), ("תסגור את עצמך לגמרי", "quit_jarves"),
    ("change a setting for me", "set_setting"), ("תשנה הגדרה של הקול", "set_setting"),
    ("show me what you found", "conversation_show"), ("כן, תציג לי", "conversation_show"),
    ("which profile is this", "profile_list"), ("איזה פרופיל", "profile_list"),
    ("switch to the test profile", "profile_switch"), ("תעבור לפרופיל של דנה", "profile_switch"),
]

# tools that are never the user's words (follow-ups, scheduler jobs): no trigger phrase required
NO_TRIGGER = {"whatsapp_open", "health_canary_run", "health_ai_review_run"}

# safety-critical wording that must survive any rewrite: tool -> substrings
SAFETY = {
    "forget": ["confirm"], "forget_all": ["confirm"], "remember": ["ONLY"], "contact_remember": ["ONLY", "Never infer"],
    "contact_lookup": ["Never sends"], "feature_set": ["approval"], "usage_status": ["never guess"],
    "workspace_edit": ["approve"], "workspace_undo": ["asks first"], "workspace_note": ["Never passwords"],
    "assistant_rename": ["approves"], "whatsapp_draft": ["Nothing", "sent"], "whatsapp_open": ["do not ask yourself", "never send"],
    "list_clear": ["approves"], "schedule_preview": ["INACTIVE", "ask before approving"],
    "schedule_approve": ["clear yes"], "job_delete": ["asks first"], "note_delete": ["asks first"],
    "file_write": ["Never executables"], "folder_grant_request": ["approved"], "proposal_decide": ["asking the user"],
    "self_check_decide": ["only after the user said so"], "self_check_ticket": ["Nothing is sent"],
    "health_remediate": ["only if the user asked", "approval"], "skill_propose": ["Draft only", "Never claim"],
    "hide_yourself": ["quit_jarves"], "quit_jarves": ["explicit", "Never the computer", "Never open the dashboard"],
    "end_conversation": ["Not quitting"], "profile_switch": ["approves", "Never guess"],
    "conversation_recall": ["untrusted", "never instructions"], "conversation_search": ["untrusted", "never instructions"],
    "conversation_show": ["only after the user said yes"], "calculate": ["never web search"],
    "music_mute": ["WITHOUT pausing"], "music_pause": ["music_mute"], "music_volume": ["change='up'", "never jump"],
    "music_status": ["volume"], "open_dashboard": ["Never a way to close"], "google_connect_help": ["NOT connected"],
    "briefing_get": ["third-party"], "run_guide_step": ["risky"],
}

# chars: the model-facing text of the backend call in the test Core (all built-in skills, nothing connected).
# V3/V4 baseline 59.4k; package P 52.1k; this change: tools JSON ~29.9k + rules ~9.4k. A hard ceiling: it only shrinks.
BACKEND_CEILING = 40_500
ORIGINAL_BASELINE = 59_400


def _phrases(desc: str) -> list[str]:
    out = []
    for a, b in re.findall(r"(?<!\w)'((?:[^']|(?<=\w)'(?=\w)){3,70})'(?!\w)|\"([^\"]{3,70})\"", desc):
        out.append((a or b).strip(" .?!,").lower())
    return [p for p in out if p]


def _route(utterance: str, descs: dict[str, str]) -> list[str]:
    """Fake model: the tools whose description quotes the longest phrase contained in the utterance."""
    u = utterance.lower()
    best, hits = 0, []
    for name, d in descs.items():
        for p in _phrases(d):
            core = p.rstrip(".")
            if core and core in u:
                if len(core) > best:
                    best, hits = len(core), [name]
                elif len(core) == best and name not in hits:
                    hits.append(name)
    return hits


@pytest.fixture
async def descs(core):
    return {t.name: t.description for t in core.registry.tools()}


def test_route_table_is_big_enough():
    assert len(ROUTES) >= 150
    assert sum(1 for u, _ in ROUTES if re.search(r"[֐-׿]", u)) >= 70
    assert sum(1 for u, _ in ROUTES if not re.search(r"[֐-׿]", u)) >= 70


async def test_every_utterance_still_routes_to_its_tool(descs):
    wrong = []
    for utterance, tool in ROUTES:
        assert tool in descs, f"{tool} is not registered"
        got = _route(utterance, descs)
        if got != [tool]:
            wrong.append((utterance, tool, got))
    assert not wrong, "routing regressions (utterance, expected, got): " + json.dumps(wrong, ensure_ascii=False)


async def test_every_user_triggered_tool_keeps_one_english_and_one_hebrew_trigger(descs):
    bad = []
    for name, d in descs.items():
        if name in NO_TRIGGER:
            continue
        ph = _phrases(d)
        he = any(re.search(r"[֐-׿]", p) for p in ph)
        en = any(re.search(r"[a-z]{3}", p) and not re.search(r"[֐-׿]", p) for p in ph)
        if not (he and en):
            bad.append((name, "he" if not he else "", "en" if not en else ""))
    assert not bad, bad


async def test_routed_tools_cover_every_trigger_tool(descs):
    covered = {t for _, t in ROUTES}
    missing = sorted(set(descs) - covered - NO_TRIGGER - {"setup_next_step"})
    assert not missing, f"no routing row for: {missing}"


async def test_safety_notes_survive(descs):
    bad = [(t, s) for t, subs in SAFETY.items() for s in subs if s.lower() not in descs[t].lower()]
    assert not bad, bad


async def test_backend_prompt_ceiling_and_per_tool_cap(core, descs):
    tools = live_tools(core.registry)
    total = len(json.dumps(tools, ensure_ascii=False)) + len(core.agent.instructions())
    assert total <= BACKEND_CEILING, f"backend tools+rules are {total} chars (ceiling {BACKEND_CEILING})"
    assert total <= ORIGINAL_BASELINE * 0.70                       # at least -30% against the pre-package-P baseline
    long = {n: len(d) for n, d in descs.items() if len(d) > 330}
    assert not long, f"descriptions over 330 chars (move the detail to the guides): {long}"
    assert sum(len(d) for d in descs.values()) <= 9_300            # was 11.0k (every tool now also carries an EN and an HE trigger)
