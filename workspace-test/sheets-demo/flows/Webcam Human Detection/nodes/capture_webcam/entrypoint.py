from __future__ import annotations

from io import BytesIO
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from numpy.typing import NDArray


def capture_webcam(camera_index: int, frame_count: int) -> list[NDArray]:
    """Capture a short batch of frames from a macOS camera."""
    if type(camera_index) is not int or camera_index < 0:
        raise ValueError("Camera index must be a non-negative integer.")
    if type(frame_count) is not int or not 1 <= frame_count <= 20:
        raise ValueError("Frame count must be an integer from 1 to 20.")

    import numpy as np

    # AVFoundation requests camera permission on the capture process's main thread.
    command = [sys.executable, str(Path(__file__).resolve()), str(camera_index), str(frame_count)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=25)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Camera capture timed out. Allow macOS Camera access and run the flow again.") from error
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or "The camera capture process failed.")
    frames = BytesIO(result.stdout)
    return [np.load(frames, allow_pickle=False) for _ in range(frame_count)]


def _capture_frames(camera_index: int, frame_count: int) -> list[NDArray]:
    import cv2

    camera = cv2.VideoCapture(camera_index, cv2.CAP_AVFOUNDATION)
    try:
        if not camera.isOpened():
            raise RuntimeError("Could not open the selected webcam. Check the camera index and macOS Camera permission.")
        frames = []
        warmup = 3
        deadline = monotonic() + 10
        while len(frames) < frame_count and monotonic() < deadline:
            ok, frame = camera.read()
            if not ok or frame is None or not frame.size:
                sleep(.05)
                continue
            if warmup:
                warmup -= 1
                continue
            height, width = frame.shape[:2]
            if width > 640:
                frame = cv2.resize(frame, (640, max(1, round(height * 640 / width))), interpolation=cv2.INTER_AREA)
            frames.append(frame)
        if len(frames) != frame_count:
            raise RuntimeError("The webcam did not return enough frames. Check whether another app is using the camera.")
        return frames
    finally:
        camera.release()


if __name__ == "__main__":
    import numpy as np

    try:
        for frame in _capture_frames(int(sys.argv[1]), int(sys.argv[2])):
            payload = BytesIO()
            np.save(payload, frame, allow_pickle=False)
            sys.stdout.buffer.write(payload.getbuffer())
    except RuntimeError as error:
        sys.exit(str(error))
