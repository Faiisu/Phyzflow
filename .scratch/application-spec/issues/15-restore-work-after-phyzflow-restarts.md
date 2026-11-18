# 15: Restore the latest run state after restart

**What to build:** When Phyzflow starts again, it restores each sheet's last operating state and restarts interrupted runs from the first step.

**Blocked by:** [14: Restart unexpectedly terminated runs up to three times](14-restart-unexpectedly-terminated-runs.md)

**Status:** ready-for-agent

**Spec requirements:** RUN-03

- [ ] Restart the installed backend with active one-time, automation, and continuous runs. Verify that each gets a new run and begins at its first step without a new command.
- [ ] Include an automation actively waiting inside Python. Verify that its new run follows the flow's own waiting logic.
- [ ] Include manually stopped and terminal runs. Verify that stopped work stays stopped and terminal runs are not replayed. Verify that shutdown of an active process does not record an explicit user Stop.

