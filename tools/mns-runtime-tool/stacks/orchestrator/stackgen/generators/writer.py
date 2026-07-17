"""Write generated stack files to disk."""
from __future__ import annotations

import fcntl
from pathlib import Path

from ..models import ResolvedScenario
from ..paths import GENERATED_STACKS_DIR
from .artifact_pipeline import ArtifactContext, run_artifact_generators
from .stack_artifacts import (
    FULL_STACK_GENERATORS,
    SCENARIO_ARTIFACT_GENERATORS,
    SourceTreeArtifact,
    SupportConfigArtifact,
)


def default_out_dir(resolved: ResolvedScenario) -> Path:
    return GENERATED_STACKS_DIR / resolved.stack_name


def write_scenario_artifacts(resolved: ResolvedScenario, run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    run_artifact_generators(
        SCENARIO_ARTIFACT_GENERATORS,
        ArtifactContext(resolved=resolved, run_dir=run_dir),
    )


def copy_support_config(resolved: ResolvedScenario, run_dir: Path) -> None:
    SupportConfigArtifact().write(ArtifactContext(resolved=resolved, run_dir=run_dir))


def copy_source_tree(resolved: ResolvedScenario, run_dir: Path) -> None:
    SourceTreeArtifact().write(ArtifactContext(resolved=resolved, run_dir=run_dir))


def write_generated_stack(resolved: ResolvedScenario, run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / ".generate.lock").open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        write_generated_stack_unlocked(resolved, run_dir)
        fcntl.flock(lock_file, fcntl.LOCK_UN)


def write_generated_stack_unlocked(resolved: ResolvedScenario, run_dir: Path) -> None:
    run_artifact_generators(
        FULL_STACK_GENERATORS,
        ArtifactContext(resolved=resolved, run_dir=run_dir),
    )
