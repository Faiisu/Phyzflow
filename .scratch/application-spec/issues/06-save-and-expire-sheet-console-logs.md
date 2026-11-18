# 06: Save and expire console logs per sheet

**What to build:** A developer can view live console output and configure saved logs independently for each sheet.

**Blocked by:** [01: Create a Workspace and run its first flow](01-create-and-run-a-workspace.md)

**Status:** ready-for-agent

**Spec requirements:** FLOW-03, FLOW-04

- [ ] Run a sheet with default settings and verify that stdout and stderr are saved by run under the sheet's log directory and remain available after reopening. Verify that saving is enabled and retention defaults to seven days.
- [ ] Turn saving off for one sheet, run it again, and verify that it produces live console output without creating saved logs for that run. Verify that another sheet retains its original settings.
- [ ] Change retention for a sheet and verify daily log rotation and expiry across a date boundary during a continuous run. Verify that cleanup preserves unexpired logs.

