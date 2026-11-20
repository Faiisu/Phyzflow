from dataclasses import asdict, dataclass, replace
from ipaddress import IPv4Address
import json
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from typing import Callable, TypedDict


class ZoomSettings(TypedDict):
    speed: float
    smooth_ms: int


@dataclass(frozen=True)
class UISettings:
    port: int
    zoom: ZoomSettings
    ipv4: str = "127.0.0.1"


@dataclass(frozen=True)
class DeploySettings:
    ipv4: str
    port: int
    container_port: int
    context: str | None


@dataclass(frozen=True)
class ApplicationNetwork:
    ipv4: str = "0.0.0.0"
    port: int = 18080


def read_project_settings(root: Path) -> dict:
    """Read setting.json from a project root, returning {} when absent."""
    try:
        values = json.loads((root / "setting.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(values, dict):
        raise ValueError("setting.json must contain a JSON object.")
    return values


def validate_ipv4(value: object, label: str = "The UI IP") -> str:
    try:
        if isinstance(value, str):
            return str(IPv4Address(value))
    except ValueError:
        pass
    raise ValueError(f"{label} must be an IPv4 address such as 127.0.0.1.")


def validate_port(value: object, label: str = "The UI port", *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= 65535:
        raise ValueError(f"{label} must be an integer between {minimum} and 65535.")
    return value


def application_network(values: dict) -> ApplicationNetwork:
    defaults = ApplicationNetwork()
    return ApplicationNetwork(
        validate_ipv4(values.get("ipv4", defaults.ipv4), "The bind IP"),
        validate_port(values.get("port", defaults.port), "The server port", minimum=1),
    )


def load_application_network(path: Path) -> ApplicationNetwork:
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ApplicationNetwork()
    if not isinstance(values, dict):
        raise ValueError("network.json must contain a JSON object.")
    return application_network(values)


def save_application_network(path: Path, settings: ApplicationNetwork) -> None:
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        pending = Path(stream.name)
        try:
            json.dump(asdict(settings), stream, indent=2)
            stream.write("\n")
            stream.close()
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)


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


def load_deploy_settings(
    project: Path, *, ipv4: str | None = None, port: int | None = None,
    container_port: int | None = None, context: str | None = None,
) -> DeploySettings:
    """Validate and resolve Docker deployment settings from setting.json and CLI overrides."""
    values = read_project_settings(project).get("deploy", {})
    if not isinstance(values, dict):
        raise ValueError("deploy must be a JSON object in setting.json.")
    unknown = values.keys() - {"ipv4", "port", "container_port", "context"}
    if unknown:
        raise ValueError(f"Unknown deploy settings: {', '.join(sorted(unknown))}.")

    resolved_ipv4 = validate_ipv4(values.get("ipv4", "127.0.0.1") if ipv4 is None else ipv4, "deploy.ipv4")
    resolved_port = validate_port(values.get("port", 8080) if port is None else port, "deploy.port")
    resolved_container_port = validate_port(
        values.get("container_port", 8000) if container_port is None else container_port,
        "deploy.container_port",
        minimum=1,
    )
    resolved_context = values.get("context") if context is None else context
    if resolved_context is not None and (not isinstance(resolved_context, str) or not resolved_context.strip()):
        raise ValueError("deploy.context must be a non-empty Docker context name or null.")
    return DeploySettings(resolved_ipv4, resolved_port, resolved_container_port, resolved_context)


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
