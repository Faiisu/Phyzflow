# 18: Start Phyzflow at boot on Windows

**What to build:** An operator can deploy the headless Windows backend with a Task Scheduler startup trigger and access Phyzflow remotely after reboot.

**Blocked by:** [15: Restore the latest run state after restart](15-restore-work-after-phyzflow-restarts.md)

**Status:** ready-for-agent

**Spec requirements:** DEP-01, DEP-03, DEP-04, RUN-03

- [ ] Configure the startup task under the deployment account to run whether or not that account is logged on. Verify that it starts the installed backend.
- [ ] Reboot a Windows test host while a sheet is active. Before desktop login, connect from a separate client and verify that the application is reachable and the flow restarted at its first step.
- [ ] Document the install and startup verification with the tested host, OS version, and account.

