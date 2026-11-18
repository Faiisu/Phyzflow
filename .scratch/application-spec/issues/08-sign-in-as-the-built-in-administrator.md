# 08: Sign in as the built-in administrator

**What to build:** An installer receives credentials for one built-in web administrator, who can sign in and manage Workspaces.

**Blocked by:** [01: Create a Workspace and run its first flow](01-create-and-run-a-workspace.md)

**Status:** resolved

**Spec requirements:** AUTH-01

- [x] Install two independent instances. Verify that each has one administrator named `admin` and that the installer receives a distinct initial password privately.
- [x] Verify that an unauthenticated client cannot manage Workspaces, an incorrect password is rejected, and the correct password opens the landing page.
- [x] Sign in with the same credential after restarting an installation.


## Verification

On macOS 27.0.1 with CPython 3.12.11, browser and HTTP tests against the installed `workspace app` verified that two independent installations had distinct owner-only password files. Incorrect usernames and passwords were rejected, guests could neither read mounted Workspace and flow data nor start a run, and an authenticated browser could create, add, open, and run Workspaces. Writes required an exact same-origin header. The original password still worked after the application restarted with the initial password file removed, while its earlier session was invalid. A concurrent first-start check verified that two processes sharing one state folder accepted the same credential and their startup output reported the delivery path without revealing the password.

Run from the repository root with the SDK and Playwright installed and Google Chrome available.

```sh
PYTHONPATH=workspace-test python -m unittest discover -s workspace-test/tests -p 'test_application*.py'
```

Host reboot verification is tracked by the [macOS boot scenario](17-start-phyzflow-at-boot-on-macos.md) and the [release acceptance scenarios](../../../docs/application-spec.md#acceptance-scenarios).
