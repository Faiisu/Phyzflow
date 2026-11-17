from collections.abc import Iterable
from typing import TYPE_CHECKING

from workspace_sdk import publish_frame

if TYPE_CHECKING:
    from numpy import ndarray


def detect_people(frames: Iterable["ndarray"]) -> dict[str, int]:
    """Detect people and publish the annotated frame to the live dashboard."""
    import cv2

    detector = cv2.HOGDescriptor()
    detector.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    color = (224, 245, 99)
    people = 0
    for frame in frames:
        height, width = frame.shape[:2]
        scale = min(1, 640 / max(width, height))
        if scale < 1:
            frame = cv2.resize(
                frame, (max(1, round(width * scale)), max(1, round(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
        width_min, height_min = detector.winSize
        boxes = ()
        if frame.shape[1] >= width_min and frame.shape[0] >= height_min:
            boxes, _ = detector.detectMultiScale(frame, winStride=(8, 8), padding=(8, 8), scale=1.05)
        people = len(boxes)
        for x, y, w, h in boxes:
            cv2.rectangle(frame, (x, y), (x + w - 1, y + h - 1), color, 2)
            origin = (x + 4, y + 18)
            cv2.putText(frame, "Person", origin, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(frame, "Person", origin, cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
        if not ok:
            raise RuntimeError("Could not encode the camera preview.")
        publish_frame(jpeg.tobytes())
    return {"people": people}
