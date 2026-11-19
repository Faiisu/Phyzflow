# 01: Start Phyzflow and sign in on Windows

**What to build:** An operator can install and start Phyzflow on Windows from the shipped launcher and sign in from a separate browser on an isolated test network. Phyzflow selects the host's credential storage and permission handling automatically.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Install from a clean Windows checkout with Python 3.12. Verify that the shipped launcher starts the installed application and that a browser on another machine reaches the sign-in page.
- [ ] Verify that an incorrect password is rejected and that the initial administrator password opens the Workspaces page. Restart the application with the same state and verify that the credential still works.
- [ ] Verify that a separate unprivileged Windows account cannot read the initial password or modify authentication state, and that the application rejects existing credential state with unsafe permissions.
- [ ] Verify that the existing administrator sign-in and credential protection tests pass on macOS.
