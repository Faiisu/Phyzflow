from workspace_sdk import Flow, Input, Output, Step

from nodes.capture_webcam.entrypoint import capture_webcam
from nodes.detect_people.entrypoint import detect_people
from nodes.summarize.entrypoint import summarize

flow = Flow(
    inputs=("camera_index", "frame_count", "save_preview"),
    steps=(
        Step("Capture Webcam", capture_webcam, {
            "camera_index": Input("camera_index"),
            "frame_count": Input("frame_count"),
        }),
        Step("Detect People", detect_people, {"frames": Output("Capture Webcam")}),
        Step("Summarize", summarize, {
            "frames": Output("Capture Webcam"),
            "detections": Output("Detect People"),
            "save_preview": Input("save_preview"),
        }),
    ),
    output=Output("Summarize"),
)
