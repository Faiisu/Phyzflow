from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from numpy.typing import NDArray


class Detection(TypedDict):
    confidence: float
    bbox_xyxy: tuple[int, int, int, int]


def detect_people(frames: list[NDArray]) -> list[list[Detection]]:
    """Locate people in each frame with Apple Vision's upper-body detector."""
    import cv2
    import objc
    import Vision
    from Foundation import NSData, NSDictionary

    detections: list[list[Detection]] = []
    for frame in frames:
        with objc.autorelease_pool():
            ok, encoded = cv2.imencode(".png", frame)
            if not ok:
                raise RuntimeError("Could not prepare a webcam frame for Vision.")
            image = encoded.tobytes()
            data = NSData.dataWithBytes_length_(image, len(image))
            handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, NSDictionary.dictionary())
            request = Vision.VNDetectHumanRectanglesRequest.alloc().init()
            request.setUpperBodyOnly_(True)
            succeeded, error = handler.performRequests_error_([request], None)
            if not succeeded:
                raise RuntimeError(f"Vision person detection failed: {error}")

            height, width = frame.shape[:2]
            people: list[Detection] = []
            for result in request.results() or ():
                box = result.boundingBox()
                x, y = box.origin.x, box.origin.y
                w, h = box.size.width, box.size.height
                people.append({
                    "confidence": round(float(result.confidence()), 3),
                    "bbox_xyxy": (
                        max(0, min(width - 1, round(x * width))),
                        max(0, min(height - 1, round((1 - y - h) * height))),
                        max(0, min(width - 1, round((x + w) * width))),
                        max(0, min(height - 1, round((1 - y) * height))),
                    ),
                })
            detections.append(people)
    return detections
