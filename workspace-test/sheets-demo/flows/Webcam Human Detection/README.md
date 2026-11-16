# Webcam Human Detection

Capture a short webcam batch, detect people with Apple Vision, and report counts and pixel coordinates. The detector looks for visible upper bodies, including people seated at a desk. The demo runs on macOS with the Workspace project's Python environment.

From the repository root, install the demo requirements into the environment used to open the sheets demo.

```sh
uv pip install --python workspace-test/sheets-demo/.venv/bin/python \
  -r "workspace-test/sheets-demo/flows/Webcam Human Detection/requirements.txt"
```

Open the sheets demo in Workspace, select **Webcam Human Detection**, and click **Run flow**. If macOS requests access, allow the application hosting the Workspace process to use the camera.

- `camera_index` selects the camera. The sample uses `1` for the built-in FaceTime camera on the development Mac. Use `0` on a Mac with one camera.
- `frame_count` controls the number of frames in a batch. The default is eight.
- `save_preview` defaults to `false`. Set it to `true` in the Run panel to save a JPEG of the frame with the most detected people. **Run details** returns its local path and the detected boxes. The boxes use pixel coordinates in left, top, right, bottom order.

The camera is released after capture. Images are processed locally, and the optional preview is stored under the sheet's `.workspace/webcam/` directory.

To run the same batch from a terminal, start in the repository root.

```sh
workspace-test/sheets-demo/.venv/bin/workspace --project workspace-test/sheets-demo \
  run --sheet "Webcam Human Detection" \
  '{"camera_index":1,"frame_count":8,"save_preview":false}'
```
