"""Command line interface for MnS ScenarioSpec validation."""
from __future__ import annotations

import argparse
import sys

from .schema import DEFAULT_SCHEMA_PATH, ScenarioSpecError, load_scenariospec_schema, validate_scenariospec_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate MnS ScenarioSpec schema and documents.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("schema-path", help="Print the bundled ScenarioSpec schema path.")

    validate_schema = sub.add_parser("validate-schema", help="Validate a ScenarioSpecSchema.yaml contract.")
    validate_schema.add_argument("--schema", default=str(DEFAULT_SCHEMA_PATH), help="Schema YAML path.")

    validate = sub.add_parser("validate", help="Validate a ScenarioSpec file or folder.")
    validate.add_argument("scenario", help="ScenarioSpec file or folder.")
    validate.add_argument("--schema", default=str(DEFAULT_SCHEMA_PATH), help="Schema YAML path.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.cmd == "schema-path":
            print(DEFAULT_SCHEMA_PATH)
            return 0
        if args.cmd == "validate-schema":
            load_scenariospec_schema(args.schema)
            print("OK")
            return 0
        if args.cmd == "validate":
            validate_scenariospec_path(args.scenario, args.schema)
            print("OK")
            return 0
    except ScenarioSpecError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
