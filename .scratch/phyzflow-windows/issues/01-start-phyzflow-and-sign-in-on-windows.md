# 01: Start Phyzflow and sign in on Windows

**What to build:** An operator can install and start Phyzflow on Windows from the shipped launcher and sign in from a separate browser on an isolated test network. Phyzflow selects the host's credential storage and permission handling automatically.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] Install from a clean Windows checkout with Python 3.12. Verify that the shipped launcher starts the installed application and that a browser on another machine reaches the sign-in page.
- [x] Verify that an incorrect password is rejected and that the initial administrator password opens the Workspaces page. Restart the application with the same state and verify that the credential still works.
- [x] Verify that a separate unprivileged Windows account cannot read the initial password or modify authentication state, and that the application rejects existing credential state with unsafe permissions.
- [x] Verify that the existing administrator sign-in and credential protection tests pass on macOS.

## Verification

Commit `5aa7b39` passed the shipped launcher and remote Chrome sign-in on Windows 11 IoT Enterprise build `26200.9974` with Python 3.12.10, including password rejection and sign-in after restarting the backend. A temporary unprivileged local account could neither read the initial password nor read or modify the verifier and MCP database. The application refused state writable by Everyone. Both existing macOS administrator authentication tests passed.

Review follow-up in `4814aa3` verified that startup also rejects a private state directory under a parent that gives Everyone permission to delete its children. Normal Windows sign-in still passed.
