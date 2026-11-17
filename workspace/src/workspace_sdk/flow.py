from dataclasses import dataclass, field
from typing import Any, Callable

from .node_settings import NodeSetting


@dataclass(frozen=True)
class Input:
    name: str


@dataclass(frozen=True)
class Output:
    step_id: str


@dataclass(frozen=True)
class Step:
    """A callable node. With track_generator, it stays running until its generator closes."""

    id: str
    function: Callable[..., Any]
    arguments: dict[str, Input | Output]
    capture_samples: bool = False
    track_generator: bool = False
    settings: dict[str, NodeSetting] = field(default_factory=dict)


@dataclass(frozen=True)
class Flow:
    inputs: tuple[str, ...]
    steps: tuple[Step, ...]
    output: Output
