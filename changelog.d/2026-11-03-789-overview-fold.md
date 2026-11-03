- Changed: The workflow Overview is calmer. Only the verdict bar, "needs you", "working now",
  "stuck" (shown only when something is stuck) and "done today" stay open; worker details,
  version progress, supervisor stops, the manager log, the morning report and the extra
  details are folded into one line each with a count. Each block remembers whether you opened
  it; a new alert or a failed worker action opens only its own block. "Working now" shows at
  most 7 rows, the rest under "more N" (#789).
