# Workflow console interaction and responsive QA

31 October 2026. UI-only follow-up to #712; fixes #718. The same existing local
ledger, API and native manager remain responsible for actual state and actions.
No provider launcher, runtime approval, worker scheduler or release policy changes.

## Changes

- Four mobile tabs in one reachable row; 44px action controls and wrapping layouts.
- Overview counters lead to matching lists. Roadmap version chips, queued filter,
  native expandable sections and task/agent history links reduce scanning.
- Readable action facts, capture coverage and failure details; raw stored JSON
  is available in a collapsed disclosure. Tool data is rendered as text.
- Exact date/time filters, explicit frozen/live feedback, older-page navigation
  and copying of the existing prepared retro prompt.
- Preserve question drafts and focus the next choice after answering. New tasks
  become visible even if a live state update rendered before local filter changes.
- Discard stale filter responses and coalesce a live revision arriving during
  an in-flight history request, so its last event is not silently missed.

## Browser evidence

Cloud Chrome, production assets with an explicitly synthetic API fixture. The
standalone interactive HTML uses the same assets and fixture with no real
provider/network actions. Fictional completion and test evidence are labelled
demo data; this is not the repository's actual version completion report.

- Baseline at 390x844: three navigation columns put the fourth tab at y832–879,
  outside the viewport. Fixed: all four links at y777–840, within the viewport.
- At 320x844: document width/scroll width both 305px; heading actions 44px,
  all four navigation targets 63px. A detailed failure dialog had equal client
  and scroll widths (254px), with raw JSON initially collapsed.
- Version selection and task → filtered history → readable tool failure worked.
  Requested model is explicitly distinguished from proven effective model.
- Question draft survived a state update. Answer was retained in history and
  focus moved to the next radio choice instead of opening a text keyboard.
- History pagination produced 105 unique rows (100 + 5), then froze analysis.
  A one-second exact range returned one event; reversed range showed a clear
  error; returning live selected the relative 24-hour range.
- With one-second response delay, changing actor twice during a paused request
  displayed only the final actor's 35 events. A new recorded failure arriving
  during live loading appeared through the coalesced follow-up refresh.
- Creating a V6 task while V7/done was selected displayed the new queued task
  immediately. The completed overview counter cleared prior version/search filters.

## Verification and limits

`node --check core/src/jarves/workflow/static/app.js` and `git diff --check` passed.
The six existing workflow HTTP/trace/store/control/native/sync suites passed
(63 tests). Prepush and remote CI evidence belong to the associated PR/check runs.

No native owner-PC, physical phone, Windows scheduler, real Claude/Codex/OpenCode,
screen reader or device acceptance was performed here. These remain UNVERIFIED.
The HTML demo is temporary local interaction state, not durable production data.
