# People Detection

From the repository root, install the local SDK and the sheet's camera dependency into the Workspace environment.

```sh
uv venv --allow-existing --python 3.12 workspace-test/sheets-demo/.venv
uv pip install --python workspace-test/sheets-demo/.venv/bin/python ./workspace \
  -r 'workspace-test/sheets-demo/flows/People Detection/requirements.txt'
workspace-test/sheets-demo/.venv/bin/workspace --project workspace-test/sheets-demo check --sheet 'People Detection'
workspace-test/sheets-demo/.venv/bin/workspace --project workspace-test/sheets-demo ui
```

Open **People Detection**, select **Visualize** in the workflow toolbar, then select
**Run flow**. Allow camera access for the application that launched Workspace if
your OS prompts you. The **Live run** dashboard shows the default local camera
and draws a box labeled **Person** around each detection.

OpenCV's built-in CPU detector works best with standing people whose full bodies
are in view. The sheet processes up to 10 frames per second. The actual rate
depends on the camera and CPU.

Click **Stop** on the sheet tab to release the camera. Use **Open flow** before
starting another run. The camera keeps running if you close the dashboard or
browser during a run. Workspace streams the JPEG previews only in the live
session and excludes them from run history.
