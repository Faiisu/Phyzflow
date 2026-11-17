# Dashboard Demo

Open the **Dashboard Demo** sheet in Workspace. Click **Visualize** in the toolbar to see both nodes and their timings. Click **Collect readings** in the overview, or select that node in the graph and click **Visualize** in its Inspector.

Click **Run flow** to collect the sample batch. The node dashboard updates its reading count and progress bar while the batch runs. After it finishes, select the saved run in the dashboard to see the final count again. The sample returns `{"count":20,"mean":23.1}`.

The dashboards are plain HTML, CSS, and JavaScript in the workflow and node folders under `dashboards/`. Edit the files and click **Reload dashboard** to see your changes.
