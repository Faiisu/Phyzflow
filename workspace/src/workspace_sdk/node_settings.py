from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
import json
from math import isfinite
import os
from pathlib import Path
import sqlite3
from threading import Lock
from uuid import uuid4


SettingValue = bool | int | float | str
active_settings: ContextVar[dict["NodeSetting", SettingValue] | None] = ContextVar("active_settings", default=None)
active_checkpoint: ContextVar[Callable[[], None] | None] = ContextVar("active_checkpoint", default=None)


@dataclass(frozen=True, eq=False)
class NodeSetting:
    """A node's editable setting. get() returns the default outside the Runner."""

    default: SettingValue
    minimum: int | float | None = None
    maximum: int | float | None = None
    live: bool = False

    def __post_init__(self):
        if type(self.live) is not bool:
            raise ValueError("Setting live support must be a boolean.")
        for bound in (self.minimum, self.maximum):
            if bound is not None and (
                type(self.default) not in (int, float)
                or type(bound) not in (int, float)
                or not isfinite(bound)
            ):
                raise ValueError("Setting bounds require finite numeric values.")
        self.validate(self.default)

    def validate(self, value) -> SettingValue:
        kind = type(self.default)
        if kind not in (bool, int, float, str) or (
            type(value) is not kind and not (kind is float and type(value) is int)
        ):
            raise ValueError(f"Expected a {kind.__name__} setting.")
        if type(value) is str and len(value) > 512:
            raise ValueError("Text settings must fit in 512 characters.")
        if kind in (int, float) and (
            type(value) is int and value.bit_length() > 53 or not isfinite(value)
        ):
            raise ValueError("Expected a finite number in JavaScript's safe range.")
        if self.minimum is not None and value < self.minimum:
            raise ValueError(f"Minimum value is {self.minimum}.")
        if self.maximum is not None and value > self.maximum:
            raise ValueError(f"Maximum value is {self.maximum}.")
        return float(value) if kind is float else value

    def get(self) -> SettingValue:
        values = active_settings.get()
        return self.default if values is None else values.get(self, self.default)


def apply_node_settings() -> None:
    """Apply queued live settings at a checkpoint in the current node."""
    if (checkpoint := active_checkpoint.get()) is not None:
        checkpoint()


def parse_setting_defaults(source: str, definitions: dict[str, list[NodeSetting]]) -> dict[str, SettingValue]:
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError(f"Duplicate setting {name!r}.")
            result[name] = value
        return result

    try:
        values = json.loads(source, object_pairs_hook=unique)
    except json.JSONDecodeError as error:
        raise ValueError(f"Line {error.lineno}: {error.msg}.") from error
    if not isinstance(values, dict):
        raise ValueError("Settings defaults must be a JSON object.")
    for name, value in values.items():
        if name not in definitions:
            raise ValueError(f"Unknown setting {name!r}. Declare it in Step.settings first.")
        try:
            for setting in definitions[name]:
                setting.validate(value)
        except ValueError as error:
            raise ValueError(f"{name}: {error}") from error
    return values


class SettingsStore:
    def __init__(self, root: Path):
        self.path = root / ".workspace" / "node-settings.sqlite3"

    def snapshot(self, steps) -> dict[str, dict[str, SettingValue]]:
        configured = {step.id: step for step in steps if step.settings}
        values = {
            node: {name: step.setting_defaults.get(name, setting.default) for name, setting in step.settings.items()}
            for node, step in configured.items()
        }
        if not configured or not self.path.is_file():
            return values
        with sqlite3.connect(self.path) as db:
            self.create_table(db)
            for node, name, value in db.execute("SELECT node, name, value FROM setting"):
                step = configured.get(node)
                if step is not None and (setting := step.settings.get(name)) is not None:
                    try:
                        values[node][name] = setting.validate(json.loads(value))
                    except ValueError:
                        # A saved value may have become invalid after a source edit.
                        continue
        return values

    @staticmethod
    def create_table(db):
        db.execute("CREATE TABLE IF NOT EXISTS setting (node TEXT, name TEXT, value TEXT, PRIMARY KEY (node, name))")

    def save(self, node: str, name: str, value: SettingValue) -> None:
        self.path.parent.mkdir(parents=True, mode=0o700 if os.name == "posix" else 0o777, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            self.create_table(db)
            db.execute(
                "INSERT INTO setting VALUES (?, ?, ?) ON CONFLICT (node, name) DO UPDATE SET value=excluded.value",
                (node, name, json.dumps(value, allow_nan=False)),
            )


class LiveSettings:
    def __init__(self):
        self.lock = Lock()
        self.run_id: str | None = None
        self.active: set[str] = set()
        self.pending: dict[tuple[str, str], dict] = {}

    def start(self, run_id: str) -> None:
        with self.lock:
            self.run_id = run_id

    def start_node(self, node: str) -> None:
        with self.lock:
            self.active.add(node)

    def queue(self, run_id: str, node: str, name: str, value: SettingValue) -> dict:
        with self.lock:
            if run_id != self.run_id or node not in self.active:
                raise ValueError("This node is not active in the selected run.")
            if (node, name) in self.pending:
                raise ValueError("This setting already has a pending change.")
            command = {"id": uuid4().hex, "status": "queued", "name": name, "value": value}
            self.pending[node, name] = command
            return command

    def take(self, node: str, *, finish: bool = False) -> list[dict]:
        with self.lock:
            if finish:
                self.active.discard(node)
            return [self.pending.pop(key) for key in list(self.pending) if key[0] == node]

    def stop(self) -> None:
        with self.lock:
            self.run_id = None
            self.active.clear()
            self.pending.clear()
