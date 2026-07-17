"""CLI for validating and generating scenario stack artifacts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .errors import SimstackError
from .generators.writer import default_out_dir, write_generated_stack, write_scenario_artifacts
from .presentation import explain, ports_text
from .provenance import generation_provenance
from .scenariospec import resolve_scenariospec_input


def add_profile_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", choices=("docker", "editor"), default="docker")


def add_out_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", help="Generated stack output directory. Defaults to stacks/generated/<stack-name>.")


def resolved_with_out(scenario: str, profile: str, out: str | None) -> tuple[object, Path]:
    if out:
        out_dir = Path(out).resolve()
        return resolve_scenariospec_input(scenario, out_dir, profile=profile), out_dir

    preliminary = resolve_scenariospec_input(scenario, profile=profile)
    out_dir = default_out_dir(preliminary)
    return resolve_scenariospec_input(scenario, out_dir, profile=profile), out_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate self-contained AirSim stack folders from ScenarioSpec input."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    version = sub.add_parser("version", help="Print stack generator and ScenarioSpec contract version metadata")
    version.add_argument("--json", action="store_true", help="Print machine-readable metadata")

    for name in ("validate", "explain"):
        p = sub.add_parser(name)
        p.add_argument("scenario", help="ScenarioSpec file or folder")
        add_profile_arg(p)

    for name in ("generate", "render-scenario", "ports"):
        p = sub.add_parser(name)
        p.add_argument("scenario", help="ScenarioSpec file or folder")
        add_profile_arg(p)
        add_out_arg(p)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.cmd == "version":
            data = generation_provenance()
            if args.json:
                print(json.dumps(data, indent=2, sort_keys=True))
            else:
                generator = data["generated_by"]
                scenariospec = data["contracts"]["scenariospec"]
                print(f"Stack generator: {generator['id']} {generator['version']}")
                print(
                    "ScenarioSpec: "
                    f"{scenariospec['schema_id']} v{scenariospec['schema_version']} "
                    f"({scenariospec['id']} {scenariospec['version']})"
                )
            return 0

        if args.cmd in ("validate", "explain"):
            resolved = resolve_scenariospec_input(args.scenario, profile=args.profile)
            if args.cmd == "validate":
                print("OK")
            else:
                print(explain(resolved))
            return 0

        resolved, out_dir = resolved_with_out(args.scenario, args.profile, getattr(args, "out", None))

        if args.cmd == "render-scenario":
            write_scenario_artifacts(resolved, out_dir)
            print(f"Generated scenario: {out_dir / 'config' / 'scenario'}")
            print(f"Manifest: {out_dir / 'scenario-artifacts-manifest.json'}")
            if resolved.runtime_profile == "editor":
                print(f"Editor args: {out_dir / 'editor-launch-args.txt'}")
            else:
                print(f"Docker args: {out_dir / 'scenario-docker-args.txt'}")
            return 0

        write_generated_stack(resolved, out_dir)

        if args.cmd == "generate":
            print(f"Generated stack: {out_dir}")
            profile_arg = "" if resolved.runtime_profile == "docker" else f" --profile {resolved.runtime_profile}"
            print(f"Run: stacks/scripts/run_stack.sh {out_dir} -d")
            print(f"Logs: stacks/scripts/log_stack.sh {out_dir} -f")
            print(f"Stop: stacks/scripts/stop_stack.sh {out_dir}")
            if profile_arg:
                print(f"Profile: {resolved.runtime_profile}")
            return 0
        if args.cmd == "ports":
            print(ports_text(out_dir))
            return 0
    except SimstackError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
