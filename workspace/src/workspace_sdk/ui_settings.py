from dataclasses import dataclass, replace
from ipaddress import IPv4Address
from pathlib import Path
import sys
from typing import Callable, TypedDict

from .project import read_project_settings


class ZoomSettings(TypedDict):
    speed: float
    smooth_ms: int


@dataclass(frozen=True)
class UISettings:
    port: int
    zoom: ZoomSettings
    ipv4: str = "127.0.0.1"


def validate_ipv4(value: object) -> str:
    try:
        if isinstance(value, str):
            return str(IPv4Address(value))
    except ValueError:
        pass
    raise ValueError("The UI IP must be an IPv4 address such as 127.0.0.1.")


def validate_port(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 65535:
        raise ValueError("The UI port must be an integer between 0 and 65535.")
    return value


def load_ui_settings(
    root: Path, port_override: int | None, *, ipv4_override: str | None = None,
) -> UISettings:
    settings = read_project_settings(root)

    port = validate_port(settings.get("port", 18080) if port_override is None else port_override)
    ipv4 = validate_ipv4(settings.get("ipv4", "127.0.0.1") if ipv4_override is None else ipv4_override)
    zoom = settings.get("zoom", {})
    if not isinstance(zoom, dict):
        raise ValueError("zoom must be a JSON object.")
    speed = zoom.get("speed", 1.0)
    if type(speed) not in (int, float) or not 0 < speed <= 10:
        raise ValueError("zoom.speed must be a number greater than 0 and at most 10.")
    smooth_ms = zoom.get("smooth_ms", 160)
    if type(smooth_ms) is not int or not 0 <= smooth_ms <= 1000:
        raise ValueError("zoom.smooth_ms must be an integer between 0 and 1000.")
    return UISettings(port, {"speed": speed, "smooth_ms": smooth_ms}, ipv4)


def prompt_ui_settings(settings: UISettings) -> UISettings:
    def ask[T: (str, int)](label: str, default: T, parse: Callable[[str], T]) -> T:
        while True:
            print(f"{label} [{default}]: ", end="", file=sys.stderr, flush=True)
            try:
                value = input().strip()
            except EOFError:
                print(file=sys.stderr)
                return default
            if not value:
                return default
            try:
                return parse(value)
            except ValueError as error:
                print(error, file=sys.stderr)

    return replace(
        settings,
        ipv4=ask("Bind IP (IPv4)", settings.ipv4, validate_ipv4),
        port=ask("Port (0 for an available port)", settings.port, lambda value: validate_port(int(value))),
    )
