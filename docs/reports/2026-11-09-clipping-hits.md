# Clipping / overlap / scroll-cue hits (report-only, JAR-79)

Rules: `clipped-text`, `overlap`, `scroll-cue` in `core/tools/all_pages_rules.js` (only with `opts.clipping`, severity `info`; nothing blocks).
Reproduce: dashboard `all_pages_check.run(langs=["en","he"], widths=[1440,390,360], clipping=True, channel="msedge")`; phone `JARVES_CLIPPING_REPORT=out.tsv pytest core/tests/test_clipping_report.py` (EN+HE, 390/360 px, quiet hours forced, 2 waiting approvals, long unbroken title; no clock).
Run 2026-11-09, Edge on Windows, demo data. Inline elements are skipped by `overlap` (wrapped inline boxes intersect by nature; first run gave 374 such false hits).

## Dashboard (405 hits, 6 distinct)
| # | Rule | Where | Detail | Pages | Likely owner / note |
|---|---|---|---|---|---|
| 1 | clipped-text | `span#privacyBanner` | 263>228 px, ellipsis but no `title` | all (378 hits) | top bar; add `title`. Same area as proposal finding 6 |
| 2 | clipped-text | `.xlabels>span` | "10-15" cut, 6>4 px, no ellipsis | costs, 360 px | chart axis labels |
| 3 | scroll-cue | `div.tscroll` | 566>320 px sideways, no cue | logs, 360/390 | add fade/cue |
| 4 | scroll-cue | `div.card.m4-scroll` | 1646>1118 px | features, 1440 | add fade/cue |
| 5 | overlap | settings table cells | sticky `.save-bar` button covers last rows' text | settings (1440 EN/HE, 360 HE) | probably the bar covering content at scroll end; needs a bottom padding check |

## Phone `/m/` (8 hits, 1 distinct)
| Rule | Where | Detail |
|---|---|---|
| scroll-cue | `.jc-empty>.jc-sugg` | suggestions row 544>326 px, no cue (chat, EN 390) |

Not reproduced: the quiet-hours pill over the first message (proposal finding 3) and the clipped connection-dot halo (finding 4, a box-shadow, not covered by these rules). UNVERIFIED on a real device; the check ran in an emulated Chromium only.
Hebrew phone tabs produced no hits in this state.

## Caveats
- Heuristics; hits 2 and 5 may be benign. Review before making blocking.
- `test_dashboard_a11y.py::test_every_page_is_clean_in_a_real_browser` fails on this machine before and after this change (`no Edge / Chrome / Chromium found`: `apc.run` is called without a channel); the other 4 tests pass.
