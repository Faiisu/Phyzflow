# 17: Start Phyzflow at boot on macOS

**What to build:** An operator can deploy the headless macOS backend as a LaunchDaemon and access it remotely after a reboot.

**Blocked by:** [15: Restore the latest run state after restart](15-restore-work-after-phyzflow-restarts.md)

**Status:** ready-for-agent

**Spec requirements:** DEP-01, DEP-03, DEP-04, RUN-03

- [ ] Install the system-level LaunchDaemon and verify that it starts the installed backend under the configured execution account.
- [ ] Reboot a macOS test host while a sheet is active. Before desktop login, connect from a separate client and verify that the application is reachable and the flow restarted at its first step.
- [ ] Document the install and startup verification with the tested host, OS version, and account.

