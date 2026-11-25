# JAR-260 reopened: reproduction and root cause (JAR-824)

## Reproduction (real Windows 11, owner PC, main d5627fdb)
Create `sessions.json` in a fresh workflow home (it inherits the folder ACL:
`SYSTEM:(I)(F)`, `Administrators:(I)(F)`, `OWNER RIGHTS:(I)(F)`), then call
`server.load_sessions(home)` as `Server.__init__` does at startup. `icacls`
after the load still shows the same inherited ACEs. Acceptance of #824 says
"owner-only ACL on access.token and sessions.json **at startup**".

## Root cause
- JAR-450 (#1695) re-tightens an existing `access.token` at startup
  (`_tighten_once`), but `load_sessions` only read `sessions.json`. An upgraded
  install keeps the inherited ACL until a login makes `save_sessions` rewrite it.
- Why it escaped: both JAR-450 tests used freshly created files. The mocked
  test covered the existing-file path for `access.token` only. Untested path,
  not a wrong assertion.
- Second defect in the same helper: `_tighten_once` marked a file done before
  `owner_only_file` ran and ignored its result, so a failed icacls was never retried.
- Why the first JAR-824 attempt (#1752) did not land: the fix was judged correct,
  but the Windows-only real-ACL test never finished on CI (runs cancelled by new
  pushes), so it stayed UNVERIFIED. This round ran it on real Windows.

## Fix
`load_sessions` tightens an existing file once per process; `_tighten_once`
records success only, retries a failure at most 3 times, logs one error at the
cap, and holds its lock only around the counters (icacls runs outside it).

## Evidence
`cd core && uv run python -m pytest -q -o pythonpath=src tests/test_workflow_home_acl.py tests/test_workflow_824_acl.py tests/test_changelog_parts.py`
on Windows 11: 16 passed, 0 deselected (the 3 real-ACL tests ran). The new
real-ACL test and the 3 mocked tests are red on main d5627fdb.
