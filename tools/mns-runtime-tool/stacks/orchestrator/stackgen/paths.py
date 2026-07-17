"""Repository path constants for stack generation."""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
STACKS_DIR = REPO_ROOT / "stacks"
ORCHESTRATOR_ROOT = STACKS_DIR / "orchestrator"
GENERATED_STACKS_DIR = STACKS_DIR / "generated"


def default_generated_dir(stack_name: str) -> Path:
    return GENERATED_STACKS_DIR / stack_name
