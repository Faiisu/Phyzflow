# 04: Install and open Workspace on macOS

**What to build:** Give a user an installable Workspace release and an Open Folder launcher. After selecting or creating a project folder, the user can open the browser UI and hand the same folder path to an AI agent.

**Blocked by:** 01 (Select a Workspace and sheet from the CLI) and 02 (Create a project and sheet from the CLI).

**Status:** resolved

- [x] The release bundles the versioned SDK, CLI, and browser assets. Setup works on the supported macOS target without a repository checkout.
- [x] The launcher opens an existing Workspace or initializes a new one at a user-selected path, including paths with spaces. It launches the browser against that project's Python environment and configured loopback port.
- [x] The launcher reports setup and startup errors, including a conflicting port, and does not open a different project's UI.
- [x] On a clean macOS environment, a user follows the release instructions, opens a project, creates and runs a sheet, and provides the folder path to an agent. The agent can locate that sheet and validate and run it using the project's installed SDK.

## Verification

The Apple Silicon macOS release ZIP was built and extracted outside the checkout. The launcher initialized a blank folder with a fresh environment and reported a port collision without opening the other Workspace. The extracted app opened the selected folder on its configured port. A sheet created and run in the browser was discovered, validated, and run from an unrelated directory using that project's installed CLI.
