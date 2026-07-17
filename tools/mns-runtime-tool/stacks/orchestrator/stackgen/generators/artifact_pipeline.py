"""Composable artifact generation pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..models import ResolvedScenario


@dataclass(frozen=True)
class ArtifactContext:
    resolved: ResolvedScenario
    run_dir: Path


class ArtifactGenerator(Protocol):
    name: str

    def outputs(self, context: ArtifactContext) -> list[Path]:
        """Return files/directories this generator is expected to create."""

    def write(self, context: ArtifactContext) -> None:
        """Write generated artifacts to disk."""


def run_artifact_generators(generators: list[ArtifactGenerator], context: ArtifactContext) -> None:
    for generator in generators:
        generator.write(context)
