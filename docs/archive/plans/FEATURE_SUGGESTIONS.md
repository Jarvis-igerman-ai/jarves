# JARVES — ranked feature suggestions (research 19 Oct 2026, Issue #17)

Backlog only: nothing here is authorized until the owner picks it. Value V1–5, effort S/M/L. Every item keeps the rules: Hebrew-first, confirmation for external effects, a Pi path, a feature switch, a guide page.

## Top 5 recommended next
1. **#21 Pikud HaOref alerts** (V5, S): local polling filtered to the owner's area, loud spoken alert, overrides quiet hours; labelled "unofficial, backup only".
2. **#2 Timers/alarms + #4 shopping/to-do lists** (V5, S): local, no LLM, work offline.
3. **#1 Morning briefing** (V5, S): calendar, weather, important mail, news, yesterday's notes as a scheduled report read on first hello.
4. **#7 Home Assistant via its MCP server** (V5, M): lights, AC, boiler through one connector; actions need confirmation.
5. **#11 Conversation recall** (V5, S): FTS5 search over past conversations/reports ("מה אמרתי על הרופא?").

## All suggestions
**Daily life:** 1 morning briefing; 2 timers/alarms/stopwatch; 3 Hebrew calendar (Shabbat times, holidays, Omer, yahrzeit; hebcal offline); 4 shopping/to-do lists; 5 Israeli local info (weather, trains, leave-time from traffic); 6 parcel/bill tracker from Gmail (summarise only).
**Home/IoT:** 7 Home Assistant MCP bridge; 8 routines by voice (stored as approved scheduled jobs); 9 habit-pattern suggestions (suggest_and_ask only); 10 away/arrive-home modes.
**Productivity:** 11 conversation recall; 12 capture inbox with weekly review; 13 share-from-phone to the LAN dashboard; 14 outbound-only Telegram channel for reminders/reports (instead of a remote dashboard); 15 draft replies with read-back and explicit send (off by default).
**Entertainment/companionship:** 16 Israeli live radio and podcasts (mpv); 17 mood/activity music; 18 voice games; 19 hands-free cooking mode; 20 "on this day" callbacks (rare, switchable).
**Safety/health:** 21 Pikud HaOref alerts; 22 medication/water reminders with confirmation; 23 wind-down/sleep routine.
**Personality:** 24 persona sliders on the dashboard (write SOUL.md via the approved path); 25 subtle emotion on the face from the turn sentiment.
**Self-improvement:** 26 skill from a solved task (draft installs disabled under approval); 27 MCP client with an allowlist; 28 weekly self-report read on Friday.

## Avoid
Always-on camera watching while away; continuous 30-minute LLM heartbeat polling (use events); autonomous email/WhatsApp sending or auto-purchases; unofficial WhatsApp clients (ToS/ban); fall/emergency detection or medical advice; diagnosing emotion from face or voice aloud; importing skills from public hubs unreviewed; exposing the dashboard to the internet; any proactive speech during quiet hours/Shabbat except real Pikud HaOref alerts.

## Competitor notes (sources checked 19 Oct 2026)
OpenClaw (HEARTBEAT.md, Markdown skills, known security problems), Hermes Agent (skills from solved tasks, FTS5 session search), ChatGPT Pulse (overnight briefing cards), Alexa+ (multi-step voice routines, pattern suggestions), Home Assistant 2026.10 (built-in MCP server), Gemini for Home (camera automations behind a paid tier), Pikud HaOref (only unofficial endpoints: `oref`, `alarmpy`, an MCP server; can be late or wrong).
