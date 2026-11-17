# 02: Fix invalid flow source in the Workspace

**What to build:** When project validation rejects a sheet's saved Python source, show the diagnostic and let the user reopen and correct the sheet's `flow.py` from the Workspace. Let the user reload the corrected version in the same session.

**Blocked by:** 01: Edit and save a sheet's flow source.

**Status:** ready-for-agent

- [ ] Save a syntax error in `flow.py` and select Reload flow. The sheet reports the validation stage and the file and line when available.
- [ ] Reopen the actual file and save a correction even when the sheet cannot start its worker. The stale file protection from ticket 01 still applies.
- [ ] After a successful retry, the sheet displays the corrected graph and can run the corrected code.
- [ ] Verify the invalid save, failed reload, correction, and successful run through the browser in one Workspace session.
