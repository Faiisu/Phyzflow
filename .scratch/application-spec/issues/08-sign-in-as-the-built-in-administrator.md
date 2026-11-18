# 08: Sign in as the built-in administrator

**What to build:** An installer receives credentials for one built-in web administrator, who can sign in and manage Workspaces.

**Blocked by:** [01: Create a Workspace and run its first flow](01-create-and-run-a-workspace.md)

**Status:** ready-for-agent

**Spec requirements:** AUTH-01

- [ ] Install two independent instances. Verify that each has one administrator named `admin` and that the installer receives a distinct initial password privately.
- [ ] Verify that an unauthenticated client cannot manage Workspaces, an incorrect password is rejected, and the correct password opens the landing page.
- [ ] Sign in with the same credential after restarting an installation. Include host reboot verification in the platform release scenarios.

