# Agent 2 retro, 27 Oct 2026 (Step 0)

- Docs-only PRs (#636, #637) already merge in about 3 min: the "docs-only CI fast path" exists (ci_plan DOCS rule + validate gate). Removed from the to-do list; no new workflow needed.
- Time sink 1: a code PR costs about 17 min PR run + 15-20 min merge group (the full matrix runs a second time on the same tree). Fix: merge group reuses the green PR proof when the docs-free tree is identical (Tier R PR, alone).
- Time sink 2: groups wait for runners (3 agents merging, ~21 jobs per group). The reuse above removes most of the jobs; #516 (browser retry only failed files) is still worth landing as its own small Tier R change.
- Time sink 3: manual install test (30-45 min). `jarvis install-latest` (PR #650, races from #644 fixed) replaces it with one command.
- Process change: none beyond the item list above; `jarvis release run` (#645) and the watchdog (#638) are merged. Next retro after 4.5.1.
