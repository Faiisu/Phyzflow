from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from .project import read_project_settings


class ZoomSettings(TypedDict):
    speed: float
    smooth_ms: int


@dataclass(frozen=True)
class UISettings:
    port: int
    zoom: ZoomSettings


def load_ui_settings(root: Path, port_override: int | None) -> UISettings:
    settings = read_project_settings(root)

    port = settings.get("port", 18080) if port_override is None else port_override
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("The UI port must be an integer between 0 and 65535.")
    zoom = settings.get("zoom", {})
    if not isinstance(zoom, dict):
        raise ValueError("zoom must be a JSON object.")
    speed = zoom.get("speed", 1.0)
    if type(speed) not in (int, float) or not 0 < speed <= 10:
        raise ValueError("zoom.speed must be a number greater than 0 and at most 10.")
    smooth_ms = zoom.get("smooth_ms", 160)
    if type(smooth_ms) is not int or not 0 <= smooth_ms <= 1000:
        raise ValueError("zoom.smooth_ms must be an integer between 0 and 1000.")
    return UISettings(port, {"speed": speed, "smooth_ms": smooth_ms})
