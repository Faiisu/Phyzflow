from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from numpy.typing import NDArray
    from nodes.detect_people.entrypoint import Detection


def summarize(
    frames: list[NDArray], detections: list[list[Detection]], save_preview: bool,
) -> dict:
    """Report people per frame and optionally save a local annotated preview."""
    if type(save_preview) is not bool:
        raise ValueError("Save preview must be true or false.")

    counts = [len(people) for people in detections]
    peak_index = max(range(len(counts)), key=counts.__getitem__)
    height, width = frames[peak_index].shape[:2]
    result = {
        "frames_processed": len(frames),
        "people_per_frame": counts,
        "peak_people": counts[peak_index],
        "peak_frame": {
            "number": peak_index + 1,
            "image_size": {"width": width, "height": height},
            "detections": detections[peak_index],
        },
    }
    if save_preview:
        import cv2

        preview = frames[peak_index].copy()
        for detection in detections[peak_index]:
            left, top, right, bottom = detection["bbox_xyxy"]
            cv2.rectangle(preview, (left, top), (right, bottom), (0, 220, 100), 2)
            cv2.putText(
                preview, f"Person {detection['confidence']:.0%}", (left, max(15, top - 4)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 100), 1, cv2.LINE_AA,
            )
        directory = Path(__file__).resolve().parents[2] / ".workspace" / "webcam"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{uuid4().hex}.jpg"
        if not cv2.imwrite(str(path), preview):
            raise RuntimeError("Could not save the annotated preview.")
        result["preview_path"] = str(path)
    return result
