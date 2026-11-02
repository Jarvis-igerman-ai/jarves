- Fixed: The workflow supervisor keeps exactly one dashboard web server. The server
  records its own pid and start time; the supervisor stops a leftover server by that
  exact pid (never by name) on start, before a restart and when it exits, and the
  port bind is exclusive on Windows so a stale server can no longer answer (#789).
