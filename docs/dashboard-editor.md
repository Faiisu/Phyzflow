# Edit dashboard panels in Phyzflow

Open a Workspace in the [local Phyzflow application](../README.md#start-the-local-application). Dashboard panels are authored in the Flow Editor of their source sheet. The Workspace Dashboard displays placements of existing Sheet panels.

## Create a panel

1. Open the source sheet in the Flow Editor and select **Visualize**. If the drawer is hidden, open the panel switcher and select **Visualize**.
2. Select **Add dashboard**. Set **Scope** to **Workflow** or choose a node by name.
3. Enter a **Panel ID**, such as `run-status`, and select **Create dashboard**. Use letters, digits, periods, underscores, or hyphens, with at least one letter or digit. Each ID must be unique within its scope. The ID `default` is reserved for the Overview.
4. Verify that the new panel opens with a **Dashboard** heading and **Waiting for data…** before a run is available. Run the sheet and verify that its status updates.

Phyzflow creates `index.html`, `style.css`, `dashboard.js`, and `bridge.json` together. Use the [Dashboard Bridge reference](../workspace/README.md#build-a-dashboard-for-a-workflow-or-node) to change the data the panel displays.

## Add a panel to the Workspace Dashboard

Open **Dashboard** from the Workspace toolbar. Open **Panels** and select the existing Sheet panel to add it to the board. Select **Open source sheet** to make file or settings changes in the Flow Editor. Use **Remove placement** to take a panel off the board and leave its source files in the sheet.

## Edit a panel

1. Open the panel and select **Edit dashboard** on its title bar.
2. Choose **Source file** and edit the contents. For a quick check, select `index.html` and change the text inside the `<h1>` element.
3. Select **Save**. Look for **Saved.**, then close the editor and verify that the preview shows the new heading. Save each file before switching to another. If a companion file is missing, select it and save your content to create it.

If Phyzflow reports that the file changed on disk, copy any edits you want to keep, reopen the file, and apply your changes to the latest version. If the preview reports a problem with `bridge.json`, open it from **Edit dashboard**, correct the declaration, and save to reload the preview.

## Remove a panel

1. Select **Remove dashboard** on the panel title bar.
2. Check the panel name in the confirmation, then select **Remove dashboard** again to permanently delete its dashboard assets. Verify that the preview closes and the panel disappears from **Visualize**.

To hide a panel, use its **Close dashboard** button when visible or toggle the panel from **Visualize**. Removing the Overview deletes its dashboard assets in the scope folder and preserves panels in the `panels/` subfolder.

## Find the source files on disk

| Scope | Folder for a panel created in the editor |
| --- | --- |
| Workflow | `dashboards/workflow/panels/<panel-id>/` from the sheet folder |
| Node | `dashboards/nodes/<step-id>/panels/<panel-id>/` from the sheet folder |

Overview assets sit directly in the scope folder, one level above `panels/`. The **Main** sheet uses the Workspace root as its sheet folder. For node folder names that need encoding, see the [workflow and node dashboard reference](../workspace/README.md#build-a-dashboard-for-a-workflow-or-node).
