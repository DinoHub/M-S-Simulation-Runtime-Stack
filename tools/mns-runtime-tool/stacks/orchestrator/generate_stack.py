#!/usr/bin/env python3
"""Primary entrypoint for ScenarioSpec stack generation."""
from __future__ import annotations

import sys

from stackgen.cli import main


if __name__ == "__main__":
    sys.exit(main())
