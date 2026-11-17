from workspace_sdk import Flow, Output, Step

from nodes.capture_camera.entrypoint import capture_camera
from nodes.detect_people.entrypoint import detect_people


flow = Flow(
    inputs=(),
    steps=(
        Step("Capture Camera", capture_camera, {}, track_generator=True),
        Step("Detect People", detect_people, {"frames": Output("Capture Camera")}),
    ),
    output=Output("Detect People"),
)
