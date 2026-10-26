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
    ("please show a checklist of what to pack", "ui_show"), ("תציג רשימה לסימון של מה לארוז", "ui_show"),
    # V5 tools (goals, plans, preferences, proactive, development, issues, self-management)
    ("please set a goal to run a 10k by March", "goal_add"), ("אני רוצה לרוץ 10 ק\"מ עד מרץ", "goal_add"),
    ("what are my goals this week", "goal_show"), ("מה היעדים שלי", "goal_show"),
    ("do a goals check-in please", "goal_checkin"), ("תן לי סיכום יעדים", "goal_checkin"),
    ("add a step to the goal: buy shoes", "goal_step_add"), ("תוסיף צעד ליעד: לקנות נעליים", "goal_step_add"),
    ("the step is done", "goal_step_done"), ("סיימתי את הצעד", "goal_step_done"),
    ("pause the goal for now", "goal_status"), ("תשהה את היעד", "goal_status"),
    ("the goal is at 50 percent", "goal_progress"), ("היעד ב-50 אחוז", "goal_progress"),
    ("delete the goal", "goal_delete"), ("תמחק את היעד", "goal_delete"),
    ("plan how to clean the garage", "goal_plan_propose"), ("תתכנן איך לסדר את המוסך", "goal_plan_propose"),
    ("how is the plan going", "goal_plan_show"), ("מה מצב התוכנית", "goal_plan_show"),
    ("run the plan now", "goal_plan_run"), ("תריץ את התוכנית", "goal_plan_run"),
    ("I always prefer short answers", "pref_suggest"), ("אני תמיד מעדיף תשובות קצרות", "pref_suggest"),
    ("yes keep that preference", "pref_confirm"), ("כן תשמור את ההעדפה", "pref_confirm"),
    ("no do not keep that preference", "pref_decline"), ("לא תשמור את ההעדפה", "pref_decline"),
    ("what preferences did you learn about me", "pref_show"), ("אילו העדפות למדת עליי", "pref_show"),
    ("change the preference to tea", "pref_edit"), ("תשנה את ההעדפה לתה", "pref_edit"),
    ("forget that preference please", "pref_forget"), ("תשכח את ההעדפה הזאת", "pref_forget"),
    ("is there anything to mention", "proactive_show"), ("יש משהו להזכיר", "proactive_show"),
    ("dismiss that offer", "proactive_dismiss"), ("תתעלם מההצעה", "proactive_dismiss"),
    ("I have an idea for JARVES: a night mode", "dev_suggest"), ("רעיון לפיתוח: מצב לילה", "dev_suggest"),
    ("show me the dev drafts", "dev_drafts"), ("תראה לי טיוטות פיתוח", "dev_drafts"),
    ("discard the draft", "dev_discard"), ("תמחק את הטיוטה", "dev_discard"),
    ("how is development going", "dev_status"), ("מה מצב הפיתוח", "dev_status"),
    ("what issues do you have", "list_issues"), ("אילו תקלות יש לך", "list_issues"),
    ("be faster", "performance_advice"), ("שפר ביצועים", "performance_advice"),
    ("give me your self status", "self_status"), ("תן לי סטטוס עצמי", "self_status"),
    ("change yourself so you are shorter", "self_change"), ("תשנה את עצמך", "self_change"),
    ("connect my account", "self_connect"), ("תחבר את החשבון", "self_connect"),
    ("undo your change", "self_undo"), ("בטל את שינוי העצמי", "self_undo"),
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
    ("what page is this", "dashboard_page"), ("איפה אני בלוח", "dashboard_page"),
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
    ("please show me around", "show_tour"), ("תראה לי סיור", "show_tour"),
    ("not now, later", "setup_snooze"), ("לא עכשיו", "setup_snooze"),
    ("stop suggesting things", "stop_suggestions"), ("תפסיק להציע", "stop_suggestions"),
    ("how do you look now", "appearance_get"), ("איך אתה נראה", "appearance_get"),
    ("be a 3D avatar", "set_appearance"), ("תעבור לאווטאר תלת מימד", "set_appearance"),
    ("learn a new skill", "skill_propose"), ("תלמד יכולת חדשה", "skill_propose"),
    ("my new skills", "skills_generated_list"), ("אילו יכולות יצרת", "skills_generated_list"),
    ("show yourself", "show_yourself"), ("תופיע", "show_yourself"),
    ("hide yourself", "hide_yourself"), ("תיעלם", "hide_yourself"),
    ("end the conversation", "end_conversation"), ("תסגור את השיחה", "end_conversation"), ("תסגור", "end_conversation"),
    ("stop hearing me", "mute_microphone"), ("אל תקשיב לי", "mute_microphone"),
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

ENV_GATED = ("show_yourself", "hide_yourself", "ui_show")      # display_control: the probe fails on a runner without the Godot face

# chars: the model-facing text of the backend call in the test Core (all built-in skills, nothing connected).
# V3/V4 baseline 59.4k; package P 52.1k; this change: tools JSON ~29.9k + rules ~9.4k. A hard ceiling: it only shrinks.
# 4.0.1: the 28 owner-approved V5 tools (goals, plans, preferences, proactive, development, issues, self-management) add
# ~8k chars that trimming cannot remove (a tool costs ~120 chars of JSON frame before any text). Measured 47.4k; the
# ceiling is that +2%. The -30% target (40.5k) is DEFERRED to 4.0.2 (lazy tool exposure by group, see SWV4_PERF.md).
BACKEND_CEILING = 48_400
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
    d = {t.name: t.description for t in core.registry.tools()}
    for n in ENV_GATED:                    # offered only where the probe passes (a Godot window): same text everywhere
        if n not in d and n in core.registry._tools:
            d[n] = core.registry._tools[n][1].description
    return d


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


async def test_backend_prompt_ceiling_and_per_tool_cap(hermetic_features, core, descs):
    tools = live_tools(core.registry)
    total = len(json.dumps(tools, ensure_ascii=False)) + len(core.agent.instructions())
    assert total <= BACKEND_CEILING, f"backend tools+rules are {total} chars (ceiling {BACKEND_CEILING})"
    assert total <= ORIGINAL_BASELINE * 0.82                       # -18% against the pre-package-P baseline (the -30% target is deferred, see above)
    long = {n: len(d) for n, d in descs.items() if len(d) > 330}
    assert not long, f"descriptions over 330 chars (move the detail to the guides): {long}"
    assert sum(len(d) for n, d in descs.items() if n != "ui_show") <= 11_300           # was 11.0k; 9.3k before the V5 tools (every tool now also carries an EN and an HE trigger)
