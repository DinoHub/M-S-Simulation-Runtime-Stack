"""Concrete artifact generators for self-contained stack folders."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import yaml

from ..autopilots import get_autopilot_profile
from ..models import ResolvedScenario
from ..paths import STACKS_DIR
from .airsim_settings import airsim_settings
from .artifact_pipeline import ArtifactContext, ArtifactGenerator
from .compose import compose_yaml
from .manifest import execution_context, manifest, scenario_artifact_manifest
from .metrics import HOST_METRICS_DIR, metrics_manifest
from .scenario_artifacts import (
    docker_scenario_args,
    editor_launch_args,
    object_clutter_config,
    scenario_conditions_spec,
    scenario_plugin_config,
    scenario_runtime_spec,
)


class AirSimSettingsArtifact:
    name = "airsim_settings"

    def outputs(self, context: ArtifactContext) -> list[Path]:
        return [context.run_dir / "config" / "unreal-airsim" / "settings.json"]

    def write(self, context: ArtifactContext) -> None:
        path = self.outputs(context)[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(airsim_settings(context.resolved), f, indent=2)
            f.write("\n")


class ComposeArtifact:
    name = "docker_compose"

    def outputs(self, context: ArtifactContext) -> list[Path]:
        return [context.run_dir / "docker-compose.yml"]

    def write(self, context: ArtifactContext) -> None:
        path = self.outputs(context)[0]
        with path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(compose_yaml(context.resolved, context.run_dir), f, sort_keys=False, default_flow_style=False)


class ManifestArtifact:
    name = "generated_manifest"

    def outputs(self, context: ArtifactContext) -> list[Path]:
        return [context.run_dir / "generated-manifest.json"]

    def write(self, context: ArtifactContext) -> None:
        path = self.outputs(context)[0]
        with path.open("w", encoding="utf-8") as f:
            json.dump(manifest(context.resolved), f, indent=2)
            f.write("\n")


class ExecutionContextArtifact:
    name = "execution_context"

    def outputs(self, context: ArtifactContext) -> list[Path]:
        return [context.run_dir / "execution-context.json"]

    def write(self, context: ArtifactContext) -> None:
        path = self.outputs(context)[0]
        with path.open("w", encoding="utf-8") as f:
            json.dump(execution_context(context.resolved, context.run_dir), f, indent=2)
            f.write("\n")


class ScenarioArtifactsArtifact:
    name = "scenario_artifacts"

    def outputs(self, context: ArtifactContext) -> list[Path]:
        scenario_dir = context.run_dir / "config" / "scenario"
        outputs = [
            scenario_dir / "object_clutter.yaml",
            scenario_dir / "object_clutter.json",
            scenario_dir / "scenario_runtime.json",
            scenario_dir / "scenario_conditions.json",
            context.run_dir / "scenario-docker-args.txt",
            context.run_dir / "scenario-artifacts-manifest.json",
        ]
        if context.resolved.runtime_profile == "editor":
            outputs.append(context.run_dir / "editor-launch-args.txt")
        return outputs

    def write(self, context: ArtifactContext) -> None:
        resolved = context.resolved
        scenario_dir = context.run_dir / "config" / "scenario"
        scenario_dir.mkdir(parents=True, exist_ok=True)

        clutter_config = object_clutter_config(resolved.object_clutter)
        with (scenario_dir / "object_clutter.yaml").open("w", encoding="utf-8") as f:
            yaml.safe_dump(clutter_config, f, sort_keys=False, default_flow_style=False)
        with (scenario_dir / "object_clutter.json").open("w", encoding="utf-8") as f:
            json.dump(clutter_config, f, indent=2)
            f.write("\n")
        with (scenario_dir / "scenario_runtime.json").open("w", encoding="utf-8") as f:
            json.dump(scenario_runtime_spec(resolved), f, indent=2)
            f.write("\n")
        with (scenario_dir / "scenario_conditions.json").open("w", encoding="utf-8") as f:
            json.dump(scenario_conditions_spec(resolved), f, indent=2)
            f.write("\n")
        with (context.run_dir / "scenario-docker-args.txt").open("w", encoding="utf-8") as f:
            f.write(" \\\n  ".join(docker_scenario_args(resolved)))
            f.write("\n")
        if resolved.runtime_profile == "editor":
            with (context.run_dir / "editor-launch-args.txt").open("w", encoding="utf-8") as f:
                f.write(" \\\n  ".join(editor_launch_args(resolved, context.run_dir)))
                f.write("\n")
        with (context.run_dir / "scenario-artifacts-manifest.json").open("w", encoding="utf-8") as f:
            json.dump(scenario_artifact_manifest(resolved, context.run_dir), f, indent=2)
            f.write("\n")


class ScenarioPluginArtifact:
    name = "scenario_plugin"

    def outputs(self, context: ArtifactContext) -> list[Path]:
        return [context.run_dir / "config" / "scenario-plugin" / "scenario_plugin.json"]

    def write(self, context: ArtifactContext) -> None:
        path = self.outputs(context)[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        (context.run_dir / "config" / "asset-packs").mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(scenario_plugin_config(context.resolved, context.run_dir), f, indent=2)
            f.write("\n")


class MetricsRuntimeArtifact:
    name = "metrics_runtime"

    def outputs(self, context: ArtifactContext) -> list[Path]:
        return [
            context.run_dir / "config" / "metrics" / "metrics_runtime.json",
            context.run_dir / HOST_METRICS_DIR,
        ]

    def write(self, context: ArtifactContext) -> None:
        config_path = self.outputs(context)[0]
        metrics_dir = self.outputs(context)[1]
        config_path.parent.mkdir(parents=True, exist_ok=True)
        metrics_dir.mkdir(parents=True, exist_ok=True)
        with config_path.open("w", encoding="utf-8") as f:
            json.dump(metrics_manifest(context.resolved, context.run_dir), f, indent=2)
            f.write("\n")


class EnvArtifact:
    name = "env"

    def outputs(self, context: ArtifactContext) -> list[Path]:
        return [context.run_dir / ".env"]

    def write(self, context: ArtifactContext) -> None:
        resolved = context.resolved
        with self.outputs(context)[0].open("w", encoding="utf-8") as f:
            # Bash exposes UID as a readonly shell variable. The stack runner
            # exports it via source_strict_env.sh, so generated Compose uses a
            # writable HOST_UID alias for direct docker compose --env-file usage.
            f.write(f"HOST_UID={os.getuid()}\n")
            f.write(f"GID={os.getgid()}\n")
            f.write("CONFIG_ROOT=./config\n")
            f.write(f"AIRSIM_IMAGE={resolved.airsim_image}\n")
            for key, value in metrics_manifest(resolved, context.run_dir)["environment"].items():
                f.write(f"{key}={value}\n")
            f.write("AUTO_BUILD=false\n")
            f.write("COMPOSE_PARALLEL_LIMIT=1\n")
            f.write(f"SIMRUNNER_PROFILE={resolved.runtime_profile}\n")


class QGroundControlConfigArtifact:
    name = "qgroundcontrol_config"

    def outputs(self, context: ArtifactContext) -> list[Path]:
        qgc_dir = context.run_dir / "config" / "qgroundcontrol"
        return [
            qgc_dir / "qgc_config" / "QGroundControl.ini",
            qgc_dir / "user_config" / "QGroundControl.ini",
        ]

    def write(self, context: ArtifactContext) -> None:
        resolved = context.resolved
        if not resolved.qgroundcontrol or resolved.autopilot_type.lower() != "px4":
            return

        config = px4_qgroundcontrol_config()
        for path in self.outputs(context):
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(path.parent / "ParamCache", ignore_errors=True)
            path.write_text(config, encoding="utf-8")


class SupportConfigArtifact:
    name = "support_config"

    def outputs(self, context: ArtifactContext) -> list[Path]:
        profile = get_autopilot_profile(context.resolved.autopilot_type)
        return [context.run_dir / "config" / rel for rel in profile.support_config_dirs]

    def write(self, context: ArtifactContext) -> None:
        config_dir = context.run_dir / "config"
        profile = get_autopilot_profile(context.resolved.autopilot_type)
        src_stack = STACKS_DIR / profile.support_config_stack / "config"
        for rel in profile.support_config_dirs:
            src = src_stack / rel
            dst = config_dir / rel
            if src.exists():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)


class SourceTreeArtifact:
    name = "source_tree"

    def outputs(self, context: ArtifactContext) -> list[Path]:
        return [context.run_dir / "source"]

    def write(self, context: ArtifactContext) -> None:
        resolved = context.resolved
        dst = self.outputs(context)[0]
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        dst.mkdir(parents=True, exist_ok=True)

        copied = False
        source_root = resolved.source_root.resolve() if resolved.source_root.exists() else None
        for src in resolved.source_files:
            if not src.exists() or not src.is_file():
                continue
            src_resolved = src.resolve()
            if source_root is not None:
                try:
                    rel = src_resolved.relative_to(source_root)
                except ValueError:
                    rel = Path(src_resolved.name)
            else:
                rel = Path(src_resolved.name)
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_resolved, target)
            copied = True

        if copied:
            return

        if resolved.source_root.exists() and resolved.source_root.is_dir():
            shutil.copytree(
                resolved.source_root,
                dst,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", ".git", ".pytest_cache"),
            )


SCENARIO_ARTIFACT_GENERATORS: list[ArtifactGenerator] = [
    ScenarioArtifactsArtifact(),
    ScenarioPluginArtifact(),
]

FULL_STACK_GENERATORS: list[ArtifactGenerator] = [
    AirSimSettingsArtifact(),
    ComposeArtifact(),
    ManifestArtifact(),
    ExecutionContextArtifact(),
    ScenarioArtifactsArtifact(),
    ScenarioPluginArtifact(),
    MetricsRuntimeArtifact(),
    EnvArtifact(),
    SupportConfigArtifact(),
    QGroundControlConfigArtifact(),
    SourceTreeArtifact(),
]


def generated_outputs(generators: list[ArtifactGenerator], resolved: ResolvedScenario, run_dir: Path) -> dict[str, list[Path]]:
    context = ArtifactContext(resolved=resolved, run_dir=run_dir)
    return {generator.name: generator.outputs(context) for generator in generators}


def px4_qgroundcontrol_config() -> str:
    return """[General]
SettingsVersion=9
_deleteBingNoTileTilesDone=true
appFontPointSize=9
savePath=/home/qgc/Documents/QGroundControl
version=1

[LinkConfigurations]
Link0\\auto=true
Link0\\autoConnectAllowed=true
Link0\\high_latency=false
Link0\\hostCount=0
Link0\\localPort=14550
Link0\\name=PX4_UDP
Link0\\port=14550
Link0\\type=1
count=1

[LinkManager]
count=1

[MAVLinkLogGroup]
Description=QGroundControl Session
Email=
EnableAutoStart=false
EnableAutoUpload=true
EnableDelete=false
LogURL=https://logs.px4.io/upload
PublicLog=true
RateKey=notset
VideoURL=
WindSpeed=-1

[MainWindowState]
height=0
visibility=0
width=0
x=0
y=0

[QGCQml]
IsPIPVisible=true

[QGC_MAVLINK_PROTOCOL]
GCS_SYSTEM_ID=255
VERSION_CHECK_ENABLED=true
"""
