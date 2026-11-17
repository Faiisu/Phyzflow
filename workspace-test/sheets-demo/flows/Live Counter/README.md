# Live Counter

Open the **Live Counter** sheet and select **Visualize** in the workflow toolbar. Click
**Run flow** to start streaming. The chart plots elapsed seconds on the horizontal axis and
the count and multiplied output on the vertical axis.

The chart shows live samples from the Multiply by Ten node and keeps a rolling 30-second
window in the open dashboard. Saved runs do not contain the chart samples. Click **Stop**
in the sheet tab to stop the counter. Select **Open flow** to run it again from one.

To inspect a single node, select **Multiply by Ten** on the graph and click **Visualize**
in the Node Inspector. Choose **Live run** and click **Run flow**. The node dashboard
shows the latest input, its output multiplied by ten, and elapsed time. Its files are in
[the node dashboard folder](dashboards/nodes/Multiply%20by%20Ten/). Edit them and select
**Reload dashboard** to preview changes.
