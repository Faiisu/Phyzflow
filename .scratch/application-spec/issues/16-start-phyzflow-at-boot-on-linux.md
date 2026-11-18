# 16: Start Phyzflow at boot on Linux

**What to build:** An operator can deploy Phyzflow as a Linux systemd service and access it remotely after a reboot.

**Blocked by:** [15: Restore the latest run state after restart](15-restore-work-after-phyzflow-restarts.md)

**Status:** ready-for-agent

**Spec requirements:** DEP-01, DEP-03, DEP-04, RUN-03

- [ ] Enable the installed service through systemctl. Verify that the service and its flow run under the configured deploy user's account.
- [ ] Reboot a Linux test host while a sheet is active. Before host login, connect from a separate client over the isolated test network and verify that Phyzflow and the flow have started.
- [ ] Document the install and startup verification with the tested host, OS version, and account.

