# Automatically reload edited flows in the Workspace

When an AI agent, IDE, or browser editor saves Python source, an open sheet should detect the change and apply the latest stable version through the existing Reload flow path. Only the affected sheet reloads. The graph updates in place and the user starts the next run.

While the sheet is running or has an unsaved source draft, show that changes are pending. Preserve the run and the draft. When both are clear, apply the latest version. Show project validation errors and retry on the next source change. An explicitly stopped sheet stays stopped until the user opens or reloads it.

This is an extension of the local Web UI milestone. It depends on the existing sheet reload, shared validation, and source editors.
