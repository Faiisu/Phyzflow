# 05: Save node settings for the next run

Status: resolved

**What to build:** Change a Python-declared node setting from its dashboard and apply it to the next run.

**Blocked by:** 01: Open a node dashboard from files.

- [x] The dashboard can discover editable settings and show whether a change was saved or rejected.
- [x] Invalid types, out-of-range values and undeclared settings are rejected. The dashboard may only change settings exposed to its scope.
- [x] Settings survive reopening Workspace, and the next run uses the saved values. Runs take a consistent snapshot at startup.

## Verification

Chrome saved a value of 3, reopened Workspace and read it back, and rejected invalid types, ranges, and scopes. The next UI run used the saved value. A separate installed CLI run read a persisted value from the same settings store. The Runner test changed a stored value during execution and verified the current run retained its startup snapshot.
