# 16: Start Phyzflow at boot on Linux

**What to build:** An operator can deploy Phyzflow as a Linux systemd service and access it remotely after a reboot.

**Blocked by:** [15: Restore the latest run state after restart](15-restore-work-after-phyzflow-restarts.md)

**Status:** resolved

**Spec requirements:** DEP-01, DEP-03, DEP-04, RUN-03

- [x] Enable the installed service through systemctl. Verify that the service and its flow run under the configured deploy user's account.
- [x] Reboot a Linux test host while a sheet is active. Before host login, connect from a separate client over the isolated test network and verify that Phyzflow and the flow have started.
- [x] Document the install and startup verification with the tested host, OS version, and account.

## Verification

Followed the [Linux installation instructions](../../../README.md#deploy-on-linux-with-systemd) in an arm64 Lima VM running Ubuntu 24.04.5 LTS, systemd 255, and CPython 3.12.3. The service was enabled and both systemd's main process and the active sheet ran as the deployment account `faiisu` (UID 501).

Rebooted the VM with a sheet running. The macOS client reached the login endpoint over Lima's isolated port forwarding before an interactive login. The sheet recorded a different kernel boot ID at its first node and received a new run ID under the same account. An MCP client inspected a granted Workspace and was denied access to an ungranted Workspace before and after reboot.

The installer accepted a checkout path containing spaces, a percent sign, and a dollar sign. Re-running it preserved the saved network configuration and brought up the service on the configured port. The installer rejected root invocation and a host without Linux systemd.

