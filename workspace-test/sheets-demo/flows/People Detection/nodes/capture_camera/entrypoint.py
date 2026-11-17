from collections.abc import Iterator
from time import monotonic, sleep
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from numpy import ndarray


def capture_camera() -> Iterator["ndarray"]:
    """Capture frames from the default local camera."""
    import cv2

    camera = cv2.VideoCapture(0)
    try:
        if not camera.isOpened():
            raise RuntimeError("Could not open the default camera. Check camera permissions and try again.")
        next_frame_at = 0.0
        retry_until = None
        while True:
            ok, frame = camera.read()
            now = monotonic()
            if not ok or frame is None or not frame.size:
                if retry_until is None:
                    retry_until = now + 2.0
                if now >= retry_until:
                    raise RuntimeError("The camera stopped delivering frames. Reconnect it and try again.")
                sleep(0.1)
                continue
            retry_until = None
            if now >= next_frame_at:
                next_frame_at = now + 0.1
                yield frame
    finally:
        camera.release()
