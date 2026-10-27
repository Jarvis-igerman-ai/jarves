# Webtop Teacher Workspace — independent proposal

**Status:** RESEARCH / PROPOSAL ONLY · 23 October 2026. No implementation, teacher account login, API access, external AI data transfer or Webtop writes authorized.

**Strict boundary:** a separate staff-role integration, *not* the guardian/student Webtop project. See [parent/student scope](WEBTOP_PARENT_STUDENT.md). No shared login, secret storage namespace, caches, data access, user profiles or assistant permissions. This repository is public, so keep any identifying school, pupil, class, staff or account details out of GitHub.

## 1. Intended experience for teachers

- **Tomorrow at a glance:** personal timetable, replacements, cancellations, needed preparation.
- **Lesson reporting:** list of classes whose reports remain unfilled, ideally status and deep link without pupil details; see source UI evidence below.
- **Teacher messages:** notification counters and optional summary **only when data is permitted and safely filtered**; school messages may include protected pupil data.
- **AI teaching assistant:** generic English lesson plans, worksheets, differentiated exercises, quizzes and drafts with teacher review, using *no* identifying pupil information.
- **Draft reporting:** compose a lesson-topic / homework draft that the teacher pastes or reviews in official Webtop. Actual submission is a future, separately approved action.
- Could be a standalone staff app or an optional JARVES profile Skill. This is a **product option**, not an extension of guardian credentials or an automatically authorized V7 feature.

## 2. Ministry policy and privacy — hard gates

**Authoritative references to recheck before any real use:**
- [Ministry of Education — Generative AI guidelines, January 2026](https://meyda.education.gov.il/files/Pop/0files/lemida-metukshevet/AI/Guidelines.pdf). A teacher preparing instructional materials on their own using an AI tool must meet the rules, in particular on personal information, pupil-facing use and school approvals.
- [Ministry — Digital and AI tools](https://pop.education.gov.il/sherutey-tiksuv-bachinuch/international-digital-ai-tools/). Teachers must check generated material; do not input identifying/sensitive pupil or staff data into AI tools.
- [Privacy Protection Authority — Student privacy in education (PDF)](https://www.gov.il/BlobFolder/reports/students_privacy_guide/he/students_privacy_guide.pdf) and [short guidance](https://www.gov.il/BlobFolder/reports/students_privacy_guide/he/guide_edu_list.pdf). Protected pupil data is kept in designated authorized systems; external copies, personal devices and commercial sharing need appropriate controls/authorization.
- [Education Ministry parents portal — confidentiality](https://parents.education.gov.il/gov-education/rights/parental-rights/). School-acquired pupil information has confidentiality requirements.

**Engineering interpretation, not legal advice:** generic preparation with AI can be suitable without pupil information. A private PC or a home server is not automatically an approved school information system. Before accessing real teacher data, obtain appropriate school/IT/privacy and Webtop provider permission, define the allowed fields and retention, and check whether an automated API/browser workflow is supported by the terms.

| Data / operation | Default policy |
|---|---|
| Teacher-owned schedule, substitution dates | Candidate read-only metadata, after institutional approval |
| Report completion **status only** | Candidate; must test whether student details can be excluded |
| Generic worksheets, lesson-plan text | AI may help without personal or confidential details |
| Message titles/bodies or attachments | **Do not export automatically**; may contain pupil records |
| Student names, grades, attendance, personal notes, parental contacts | **No retrieval/retention or cloud-model transmission** by default |
| Reports, messages, attendance, grade *writes* | **Forbidden in first phase**; later only with explicit human preview and confirmation after approval |

For any AI feature: apply a deterministic allowlist/sanitizer **before** model calls; treat message text as untrusted instructions; no tokens in prompts, telemetry, logs or test fixtures. Do not assume automatic pseudonymization eliminates privacy risk. Local JARVES/ChatGPT plugins must never bypass these gates.

## 3. Technical interface research — verified vs unverified

The public Python projects [pywebtop](https://github.com/t0mer/pywebtop) and [smartschool-webtop](https://github.com/Shanksg/smartschool-webtop) show the current Israeli SmartSchool parent/student HTTP API and `webToken` session mechanisms. Neither verifies authenticated **teacher** endpoints.

A separately published `webtop-api-client` TypeScript package has documented `userType=3` (teacher) and schedule/inbox methods, but refers to older `www.webtop.co.il/mobilev2` infrastructure. The package's published descriptions do not demonstrate a working current teacher login, report status or reporting write. Its older error-handling and TLS choices are unsuitable to adopt blindly. No authenticated teacher-endpoint test, no production write and no school data access has been performed.

**Options:** (a) first-party read-only staff API connector if institution/vendor approve and schema is verified; (b) supervised official-browser workflow for schedule/report navigation, if permitted. Fail closed on 401, 403, HTML in place of JSON, schema changes and unsupported login; never interpret failures as "no lessons".

## 4. Proposed architecture and acceptance

    Authorized staff session (if approved)
                  |
         role-scoped provider
            read-only allowlist
                  |
      local metadata / freshness / access checks
           /                       \
     timetable/status           generic AI drafting
          |                   (no pupil information)
          +-----------------------+
                     |
               teacher workspace
            (read / draft / manual action)

**Phase A: research and permissions only.** Confirm Webtop used at the school, staff identity and access rights, vendor terms, school privacy approval, current official UI fields, and relevant API capability matrix. No credentials, cookies, raw HTTP traces or pupil content in GitHub or AI.

**Phase B: offline fixture prototype (new approval required).** Synthetic schedule / change / report-status views, optional generic drafting. Test account separation, prompt scrubbing, state accuracy and error handling. No real login.

**Phase C: staff-supervised live read-only beta (new approval required).** Validate data accuracy against official UI with actual school permission. Store least data, securely manage local tokens, expire/revoke sessions, no other family member access. Verify phone/desktop UX and no protected-data leakage.

**Phase D: optional reporting actions (new approval required).** Exact preview of date, class, subject, teacher-authored content and recipients; explicit confirmation; clear server receipt. Never auto-submit attendance, grades, disciplinary events or family messages.

### Acceptance checklist

- [ ] Institutional and vendor permissions, lawful purpose, approved processing and retention confirmed
- [ ] Teacher API capabilities authenticated and independently verified, or browser-only route explicitly approved
- [ ] No cross-role exposure to parent/student profile; no pupil records sent to cloud AI
- [ ] Accurate timetable + changes; missing reports status verified, no false "empty" on failure
- [ ] AI drafts based solely on non-confidential content and checked by teacher
- [ ] First rollout read-only, feature off by default and user can disconnect/delete data
- [ ] Separate authorization for any future write / messaging capability

## 5. Teacher Webtop research and compatibility risks

**22 October 2026 finding — scope remains RESEARCH ONLY.** The initial two open-source repositories target **parent/student** access. They do **not** establish that the same methods, token handling or permissions work for a teacher. Teacher use is a distinct user role and trust boundary; a school can configure which functions each educator may access.

**Verified in the official Webtop feature/help documentation (the application UI, NOT an authenticated API test):**
- Teachers have a personal daily timetable and timetable changes in the daily overview.
- Lesson reports can record a held/cancelled lesson, lesson topic, homework and events such as absences, lateness and positive notes.
- Teachers can use student information and grade/evaluation modules according to school-assigned permissions.
- Webtop messaging permits composing messages/parent notifications and drafts subject to recipient/role permissions.

**Known private API references from public parent/student libraries only (NOT verified for teachers):** `https://webtopserver.smartschool.co.il/server/api/`, `user/LoginByUserNameAndPassword`, `dashboard/InitDashboard`, `messageBox/GetMessagesInbox`. The `pywebtop` wrapper labels itself a **student portal** library; its username/password login lacks CAPTCHA/SSO handling. `smartschool-webtop` documents a browser-established token/biometric-login renewal under a parent account. These are **not evidence of working teacher API operations**. Do not invent teacher endpoint names or assume a valid token grants broader access.

| Teacher use case | Official Webtop UI evidence | API verification | Suggested JARVES pilot |
|---|---|---|---|
| Personal schedule and daily substitutions | Confirmed feature | Unverified teacher endpoint / data model | **P0 read-only**, strongest first candidate |
| Messages and reminders to teacher | Confirmed feature | Inbox endpoint known for student flow only; teacher role unverified | **P0 read-only**, privacy and sender scopes first |
| Lesson-report completion status | Lesson-report workflows documented | No authenticated teacher endpoint verified | **P1 research**, read-only checklist if a reliable status field exists |
| Homework/lesson topic preparation | Lesson-report form documented | No teacher API endpoint or write schema verified | **P1 draft locally**, manual copy or guided Webtop entry only after approval |
| Writing actual lesson reports or sending parent messages | Official UI supports actions | Teacher API methods/authorizations **not verified** | **P2 gated action**, after explicit preview + per-action approval and terms review |
| Student grades, attendance and individual notes | Official UI and role-gated data | No permitted API scope established | **DEFER**, not in the default pilot; sensitive student data |

### Teacher-specific feasibility and privacy gates

1. **Confirm provider and role:** does the teacher's school actually use the Israeli Webtop/SmartSchool account rather than Mashov or another product? Can the authorized teacher view teacher schedule and lesson reports in her normal account? Avoid Belgian `smartschool.be` projects; they refer to an unrelated platform with the same name.
2. **Obtain permission:** contact the school/system administrator or SmartSchool about integration/API terms, automated usage and data processing. A teacher's ability to see a student record is not blanket permission to export it to JARVES or a cloud AI provider.
3. **Begin read-only:** teacher-authorized browser session; verify UI workflow and request/response metadata locally with the teacher present if permitted. Record **only path, method, sanitized field names and status**; do not export cookies, tokens, full URLs with IDs, student names, messages, lesson notes, grades or responses to GitHub/chat. Do not bypass MFA/CAPTCHA, try credentials in CI or reverse-engineer server access controls.
4. **Test role and reliability:** teacher account may have dual teacher/parent roles, multiple schools, changing class/group rights and school-year boundaries; role switching must not mix student/teacher scopes. Use fake school/test fixtures and a separate JARVES profile or explicit role isolation. Recover honestly from logout, token expiry, unauthorized and rate limits.
5. **Actions are a separate later milestone:** for any send/save/report submission require a human preview of class/date, recipients, full content and consequences, explicit confirmation, and a verified receipt. Never auto-mark attendance, submit grades or send parent messages based on an AI interpretation alone.

**Recommended technical decision:** a `WebtopTeacherProvider` behind the existing School Skill provider interface, with role-scoped capabilities and no student data in the model by default. No teacher integration is `implemented` yet. Prioritize read-only personal schedule/changes and teaching workflow reminders; defer all student-specific data and mutations until legal/institutional approval and a verified interface.

**Official sources (feature-level, not API specifications):**
- [Webtop support center, teacher categories](https://webtop.zendesk.com/hc/he)
- [Daily overview / teachers' schedule](https://webtop.zendesk.com/hc/he/articles/360009357279-%D7%A8%D7%99%D7%9B%D7%95%D7%96-%D7%99%D7%95%D7%9E%D7%99)
- [Lesson-event reports](https://webtop.zendesk.com/hc/he/articles/360009314439-%D7%93%D7%99%D7%95%D7%95%D7%97-%D7%90%D7%99%D7%A8%D7%95%D7%A2%D7%99%D7%9D-%D7%91%D7%A9%D7%99%D7%A2%D7%95%D7%A8)
- [Sending messages](https://webtop.zendesk.com/hc/he/articles/360009243620-%D7%A9%D7%9C%D7%99%D7%97%D7%AA-%D7%94%D7%95%D7%93%D7%A2%D7%94-%D7%97%D7%93%D7%A9%D7%94)
- [Teacher grades categories](https://webtop.zendesk.com/hc/he/categories/360001934119-%D7%A6%D7%99%D7%95%D7%A0%D7%99%D7%9D-%D7%9E%D7%95%D7%A8%D7%99%D7%9D-%D7%91%D7%9C%D7%91%D7%93)

## 6. Agent instruction — scoped research, no coding approval

> Read AGENTS.md and docs/NOW.md. This document concerns **teacher/staff Webtop only**; docs/plans/WEBTOP_PARENT_STUDENT.md is a separate scope and must not share credentials or data. Research staff-specific APIs, UI, vendor approval and privacy/AI policies; prepare an evidence-based ADR and fixture-test plan. Do not log into a real school account, ingest protected pupil records, implement or deploy connectors, or perform school-system writes without distinct owner and institutional gates.
