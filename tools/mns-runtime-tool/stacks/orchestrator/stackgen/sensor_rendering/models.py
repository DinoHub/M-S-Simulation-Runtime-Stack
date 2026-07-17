"""Small data models returned by sensor rendering."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RenderedSensors:
    sensors: dict[str, Any]
    cameras: dict[str, Any]
