#!/usr/bin/env python3
"""Fly a CampaignSpec as N OSMO workflows, one per matrix point.

    osmo/campaign.py run    vio-osmo-condo            # materialise, generate, submit, collect
    osmo/campaign.py run    vio-osmo-condo --only calm-r1
    osmo/campaign.py status vio-osmo-condo            # the platform's scorecard, unchanged

This is an executor, not a runner. The platform's campaign runner
(MnS-Integration-Platform apps/scenario_launcher/campaign.py) already does
everything a campaign needs except talk to a cluster: it validates the spec,
expands variants x seeds x repeats into runs, merges each variant's overrides
into a materialised ScenarioSpec, generates a stack per run, and afterwards
scores the evidence and renders `campaign status`. All of that is reused here
by shelling into the product shell -- `./product.sh cli campaign plan` writes
every run's ScenarioSpec to disk and prints where, `./product.sh cli runtime
--no-run` generates a stack from one. Only the middle of the runner's `run_one`
-- build a docker-compose command, run it, tear it down -- is replaced, with:
render the OSMO workflow for the run, submit it, poll it, and pull its evidence
back into the directory layout the runner's own scorecard reads.

That layout is the contract this script honours, because nothing downstream
reads anything else:

    generated/campaigns/<id>/
      campaign_manifest.json         mns.vio_campaign_manifest.v1
      <run_key>/ScenarioSpec.yaml    written by the platform (plan)
      <run_key>/stack/               written by the platform (runtime --no-run)
      runs/<run_key>/                the evidence: bag/, eval/*.json,
                                     validation.json, topics.yaml, run.json
      reports/<run_key>.json         written by the campaign-level evaluator

Why not a `--target osmo` inside the platform's runner, which is where this
belongs eventually: the runner executes inside the product-shell container,
which has no `osmo` CLI, no ~/.osmo session and no kubeconfig. Proving the
shape here first costs nothing later; the seam in `run_one` is one function.

omega.yaml alignment. The platform's omega contract carries `tier`,
`verifies`, `platform.{sim,autopilot,middleware,comms}` and explicit gates,
which mns.campaign.v1 does not. The root CampaignSpec message rejects unknown
keys and the contract is a separately vendored repository, so they travel in
the one open slot -- `extensions: {mns.omega: {tier, verifies}}` -- and in
`evaluation.gates`, which the CampaignEvaluation message admits. `platform` is
never authored: it is derived here from the materialised spec's
runtime.profile and written into the manifest. The compose runner ignores all
of it, so one CampaignSpec drives both targets.

Exit codes follow the platform's: 0 every run flew and no gate failed, 1 a run
failed or a gate did, 2 usage, 42 the platform (cluster, submission) was not
usable.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "osmo" / "sim-bridge-vio.workflow.yaml"


def workspace_host() -> Path:
    """The host directory the cluster mounts at /workspace.

    The workflow resolves a stack as /workspace/generated/<stack>/config, and
    /workspace is whatever the kind config says it is -- the main checkout,
    not necessarily the checkout this script runs from. A worktree under it
    is still reachable, as a relative path through `..`, which is what the
    `stack` value becomes when this script runs from one."""
    for name in ("kind-osmo-cluster-config.gpu.yaml", "kind-osmo-cluster-config.yaml"):
        cfg = ROOT / "osmo" / name
        if not cfg.exists():
            continue
        doc = yaml.safe_load(cfg.read_text())
        for node in doc.get("nodes") or []:
            for mount in node.get("extraMounts") or []:
                if mount.get("containerPath") == "/workspace":
                    return Path(mount["hostPath"])
    return ROOT


WORKSPACE_HOST = workspace_host()
# The product shell, the cluster and the generated campaign tree all hang off
# the one checkout the cluster mounts, even when this script runs from a
# worktree beneath it: that checkout owns the pack store, and paths the
# workflow sees under /workspace are paths under it.
PRODUCT_SH = WORKSPACE_HOST / "product.sh"
CAMPAIGNS_HOST = WORKSPACE_HOST / "generated" / "campaigns"
MANIFEST_SCHEMA = "mns.vio_campaign_manifest.v1"
RUN_META_SCHEMA = "mns.vio_run_meta.v1"
# The campaign-level scorer. `vio-stress` postdates the `-latest` worker image
# on Docker Hub (2026-08-09); this tag is built from the platform checkout:
#   docker build -t dhdevspace/auto_mns:sim-real-eval-worker-<sha> \
#       MnS-Integration-Platform/tools/sim_real_eval
SIM_REAL_EVAL_IMAGE = os.environ.get("MNS_SIM_REAL_EVAL_IMAGE",
                                     "dhdevspace/auto_mns:sim-real-eval-worker-bcb899f")

# The runtime host pin the v1 lock still carries is the build without iceoryx2;
# every generated stack gets the rebuilt digest until the repin merges.
RUNTIME_HOST_BROKEN = "sha256:b876fc9ef95bee5259123b29850f5a8ff07d0a393c7c589aa417b8acc8398a8b"
RUNTIME_HOST_GOOD = "sha256:00700883f527a6a4194b2143797b5e748d1b7c7cf46acf33f73b5f9ffc4d18cf"

# runtime.profile -> the omega platform axes it implies. Derived, never
# authored: asking authors to restate what the ScenarioSpec already says is how
# the two drift.
PROFILE_PLATFORM = {
    "airsim_unreal_px4_docker": {"sim": "cosys-airsim-ue5", "autopilot": "px4"},
    "airsim_unreal_ardupilot_docker": {"sim": "cosys-airsim-ue5", "autopilot": "ardupilot"},
}
PLATFORM_FIXED = {"middleware": "ros2-mavros", "comms": "fastdds-ds"}

TERMINAL = {"COMPLETED", "FAILED", "FAILED_CANCELED", "FAILED_SERVER_ERROR",
            "FAILED_IMAGE_PULL", "CANCELLED", "FAILED_UPSTREAM"}


@dataclass
class RunRecord:
    """Exactly the platform's RunRecord (campaign.py:213-233). It round-trips
    `RunRecord(**r)` on resume, so no field is added here; OSMO-specific
    facts go on the manifest's top level or in run.json."""
    run_key: str
    variant: str
    seed: int | None
    repeat: int
    status: str = "pending"
    run_id: str | None = None
    scenario_id: str | None = None
    spec: str | None = None
    stack: str | None = None
    bundle: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    vehicle: str | None = None
    recording: str | None = None
    recording_valid: bool | None = None
    failed_checks: list[str] = field(default_factory=list)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sh(argv: list[str], *, cwd: Path | None = None, check: bool = True,
       env: dict[str, str] | None = None, capture: bool = True) -> subprocess.CompletedProcess:
    print("[campaign] $", shlex.join(argv), flush=True)
    return subprocess.run(argv, cwd=str(cwd or ROOT), check=check, text=True,
                          capture_output=capture, env={**os.environ, **(env or {})})


def product_cli(*args: str) -> subprocess.CompletedProcess:
    """The platform's launcher, through the product shell of the checkout the
    cluster mounts. Paths it prints are container paths under /workspace,
    which is that checkout."""
    return sh([str(PRODUCT_SH), "cli", *args], cwd=WORKSPACE_HOST,
              env={"MNS_IMAGE_PULL_POLICY": "missing"}, check=False)


def host_path(container_path: str) -> Path:
    if container_path.startswith("/workspace/"):
        return WORKSPACE_HOST / container_path[len("/workspace/"):]
    return Path(container_path)


def container_path(p: Path) -> str:
    return "/workspace/" + str(p.resolve().relative_to(WORKSPACE_HOST))


# --------------------------------------------------------------------------
# Spec
# --------------------------------------------------------------------------

def find_campaign(name: str) -> Path:
    for candidate in (Path(name), ROOT / "scenarios" / name / "CampaignSpec.yaml",
                      ROOT / "scenarios" / name, Path(name) / "CampaignSpec.yaml"):
        if candidate.is_file():
            return candidate.resolve()
        if candidate.is_dir() and (candidate / "CampaignSpec.yaml").is_file():
            return (candidate / "CampaignSpec.yaml").resolve()
    sys.exit(f"[campaign] no CampaignSpec for {name!r}")


def omega_fields(campaign: dict[str, Any]) -> dict[str, Any]:
    """tier/verifies: first-class if the contract ever grows them, else from
    extensions.mns.omega. The executor reads either; the file needs only one."""
    ext = (campaign.get("extensions") or {}).get("mns.omega") or {}
    return {"tier": campaign.get("tier", ext.get("tier")),
            "verifies": campaign.get("verifies", ext.get("verifies") or [])}


def gates(campaign: dict[str, Any]) -> dict[str, Any]:
    return dict((campaign.get("evaluation") or {}).get("gates") or {})


def derive_platform(spec: dict[str, Any]) -> dict[str, str]:
    runtime = spec.get("runtime") or {}
    profile = runtime.get("profile") if isinstance(runtime, dict) else runtime
    axes = dict(PROFILE_PLATFORM.get(str(profile), {"sim": str(profile), "autopilot": "unknown"}))
    axes.update(PLATFORM_FIXED)
    return axes


# --------------------------------------------------------------------------
# Materialise and generate -- both by the platform
# --------------------------------------------------------------------------

PLAN_LINE = re.compile(r"^\[campaign\] (?P<key>\S+): (?P<spec>/\S+ScenarioSpec\.yaml)\s*$")


def materialise(campaign_file: Path, out_root: Path, only: list[str]) -> list[tuple[str, Path]]:
    """Run `campaign plan`, which validates the spec, expands the matrix and
    writes <out_root>/<campaign id>/<run_key>/ScenarioSpec.yaml for every run
    -- the runner nests the id itself, so out_root is the campaigns directory.
    Returns [(run_key, host path of that spec)] in the runner's own order."""
    args = ["campaign", "plan", container_path(campaign_file), "--out", container_path(out_root)]
    if only:
        # One flag, every key: `--only` is nargs="+" and a repeated option
        # keeps only its last occurrence, so `--only a --only b` plans b
        # alone. The same argparse shape bites `osmo workflow submit --set`.
        args += ["--only", *only]
    proc = product_cli(*args)
    runs: list[tuple[str, Path]] = []
    for line in proc.stdout.splitlines():
        m = PLAN_LINE.match(line.strip())
        if m:
            runs.append((m.group("key"), host_path(m.group("spec"))))
    if proc.returncode != 0 or not runs:
        sys.stderr.write(proc.stdout + proc.stderr)
        sys.exit(f"[campaign] plan failed (rc={proc.returncode}); nothing materialised")
    return runs


# A third-person view for watching a run: a vehicle camera behind and above
# the drone, pitched down. Close enough to stay inside condo's 3.5 m bedroom.
# It shares the bridge's one combined image budget (~30 Hz across every camera
# of the vehicle), so it thins the stereo pair the estimator is fed -- a run
# with it is for looking at, not for scoring.
CHASE_CAMERA = {
    "position": {"x": -1.5, "y": 0.0, "z": -0.8},
    "pitch": -20.0,
    "capture_settings": [{"image_type": 0, "width": 640, "height": 480, "fov_degrees": 90}],
}


def with_chase_camera(spec_file: Path, out_dir: Path) -> Path:
    """A copy of a run's spec with the chase camera on every vehicle.

    A copy because `campaign plan` writes the materialised spec as root, and
    because the scored spec should stay exactly what the campaign declared.
    Relative paths in it are already absolute, so the copy generates alike."""
    spec = yaml.safe_load(spec_file.read_text())
    for vehicle in spec.get("vehicles") or []:
        vehicle.setdefault("cameras", {})["chase"] = CHASE_CAMERA
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "ScenarioSpec.yaml"
    out.write_text(yaml.safe_dump(spec, sort_keys=False))
    return out


def generate_stack(spec_file: Path, stack_dir: Path) -> None:
    proc = product_cli("runtime", "--scenario", container_path(spec_file.parent),
                       "--no-run", "--out", container_path(stack_dir))
    if proc.returncode != 0 or not (stack_dir / "docker-compose.yml").exists():
        sys.stderr.write(proc.stdout + proc.stderr)
        raise RuntimeError(f"stack generation failed for {spec_file}")
    env_file = stack_dir / ".env"
    if env_file.exists():
        env_file.write_text(env_file.read_text().replace(RUNTIME_HOST_BROKEN, RUNTIME_HOST_GOOD))


# --------------------------------------------------------------------------
# OSMO
# --------------------------------------------------------------------------

def osmo(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = sh(["osmo", *args], check=False)
    if check and proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        sys.exit(42)
    return proc


def route_blob(campaign_file: Path, campaign: dict[str, Any]) -> str:
    """The CampaignSpec's declared route, compiled the way the platform's runner
    compiles it: the YAML plan as base64 JSON, so it rides in a command line
    (`trajectory_command` in the platform's campaign.py). Empty when the
    campaign flies no route, and the workflow's corridor mission flies."""
    mission = campaign.get("mission") or {}
    rel = mission.get("trajectory")
    if not rel:
        return ""
    plan = yaml.safe_load((campaign_file.parent / rel).read_text())
    return base64.b64encode(json.dumps(plan).encode()).decode()


def submit(stack_dir: Path, campaign: dict[str, Any], run_gates: dict[str, Any],
           campaign_file: Path, viz: bool = False) -> str:
    mission = campaign.get("mission") or {}
    inputs = (campaign.get("evaluation") or {}).get("inputs") or {}
    stack_rel = os.path.relpath(stack_dir.resolve(), WORKSPACE_HOST / "generated")
    # One --set and one --set-string, each carrying every value. `osmo
    # workflow submit` declares both as nargs="+" and argparse keeps only the
    # last occurrence of a repeated option, so `--set a=1 --set b=2` submits
    # with b alone and the workflow silently runs on its defaults for the
    # rest -- run 25 flew nothing that way. --set casts to int/float where it
    # can; the values that must stay strings (JSON, base64, a number the
    # template quotes) go through --set-string.
    sets = {
        "stack": stack_rel,
        "fly": "true",
        "autopilot": str(mission.get("autopilot", "px4")),
        "est_topic": str(inputs.get("est_topic", "/ov_msckf/odomimu")),
        "viz": "true" if viz else "false",
    }
    strings = {
        "record_sec": str(int(mission.get("timeout_s", 300))),
        "gates_json": json.dumps(run_gates),
        "route_b64": route_blob(campaign_file, campaign),
    }
    argv = ["workflow", "submit", str(WORKFLOW),
            "--set", *[f"{k}={v}" for k, v in sets.items()],
            "--set-string", *[f"{k}={v}" for k, v in strings.items()]]
    proc = osmo(*argv)
    m = re.search(r"Workflow ID\s*-\s*(\S+)", proc.stdout)
    if not m:
        sys.stderr.write(proc.stdout)
        sys.exit("[campaign] submit gave no workflow id")
    return m.group(1)


def query(workflow_id: str) -> tuple[str, dict[str, str]]:
    """Overall status plus per-task status, from `osmo workflow query`."""
    proc = osmo("workflow", "query", workflow_id, check=False)
    status = ""
    tasks: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if line.startswith("Status"):
            status = line.split(":", 1)[1].strip()
        else:
            m = re.match(r"^(\S+)\s+(?:\S.*?\S|-)\s+(\S+)\s*$", line)
            if m and m.group(2).isupper():
                tasks[m.group(1)] = m.group(2)
    return status, tasks


def wait(workflow_id: str, poll_s: float = 20.0) -> tuple[str, dict[str, str]]:
    while True:
        status, tasks = query(workflow_id)
        if status in TERMINAL:
            return status, tasks
        time.sleep(poll_s)


AWS_CLI_IMAGE = "amazon/aws-cli:2.15.33"


def storage_endpoint() -> str:
    """Object storage as the host can reach it.

    The control plane's credential names http://localstack-s3.osmo:4566, a
    cluster-internal DNS name, so `osmo data download` cannot be run from
    here -- it resolves nothing. But the chart exposes localstack as a
    NodePort, and kind nodes sit on the host's `kind` docker network, so the
    node's InternalIP plus that port is reachable from a container on that
    network. Both are read live rather than hardcoded; a rebuilt cluster
    changes both."""
    node_ip = sh(["kubectl", "get", "node", "osmo-worker", "-o",
                  "jsonpath={.status.addresses[?(@.type==\"InternalIP\")].address}"]).stdout.strip()
    port = sh(["kubectl", "-n", "osmo", "get", "svc", "localstack-s3", "-o",
               "jsonpath={.spec.ports[0].nodePort}"]).stdout.strip()
    if not node_ip or not port:
        sys.exit(42)
    return f"http://{node_ip}:{port}"


def download(workflow_id: str, task: str, dest: Path, endpoint: str) -> bool:
    """Pull one task's uploaded {{output}} into dest. The control plane writes
    every task's output under s3://osmo/workflows/<workflow>/<task>/; this
    syncs that prefix with the aws CLI on the kind network, path-style, using
    the quick-start chart's fixed test credentials."""
    dest.mkdir(parents=True, exist_ok=True)
    proc = sh(["docker", "run", "--rm", "--network", "kind", "-v", f"{dest.resolve()}:/out",
               "-e", "AWS_ACCESS_KEY_ID=test", "-e", "AWS_SECRET_ACCESS_KEY=test",
               "-e", "AWS_DEFAULT_REGION=us-east-1",
               AWS_CLI_IMAGE, "--endpoint-url", endpoint,
               "s3", "sync", "--only-show-errors",
               f"s3://osmo/workflows/{workflow_id}/{task}/", "/out"], check=False)
    if proc.returncode != 0:
        print(f"[campaign]   no output for {task}: {proc.stderr.strip()[:160]}")
        return False
    return any(dest.rglob("*"))


# --------------------------------------------------------------------------
# One run
# --------------------------------------------------------------------------

def run_one(rec: RunRecord, spec_file: Path, root: Path, campaign: dict[str, Any],
            workflow_id: str, platform: dict[str, str], run_gates: dict[str, Any],
            endpoint: str) -> None:
    status, tasks = wait(workflow_id)
    rec.finished_at = now()
    bundle = root / "runs" / rec.run_key
    bundle.mkdir(parents=True, exist_ok=True)

    # A run that produced evidence flew; the campaign-level evaluator judges it.
    # That is the compose runner's semantics and the scorecard depends on it.
    # "Flew" is literal: the pilot task has to exist and have completed. Run
    # 25 had a COMPLETED recorder, a full bag, and no pilot task at all,
    # because the submit's values never reached the workflow.
    if tasks.get("recorder") == "COMPLETED" and tasks.get("pilot") == "COMPLETED":
        rec.status = "done"
    elif "pilot" not in tasks:
        rec.status = "failed"
        rec.error = f"workflow {status}: no pilot task -- the workflow ran on its defaults"
    else:
        rec.status = "failed"
        failed = [f"{t}={s}" for t, s in tasks.items() if s.startswith("FAILED")]
        rec.error = f"workflow {status}: " + (", ".join(failed) or "no recorder output")

    # Evidence, into the layout the scorecard reads.
    download(workflow_id, "recorder", bundle, endpoint)
    # The recorder writes how the flight ended beside the bag. A pilot task
    # exits 0 or 1 as COMPLETED -- a failed flight must not take the gang
    # down with it -- so the task table cannot tell a flight from a refusal
    # to arm; this can.
    mission = bundle / "mission.json"
    if rec.status == "done" and mission.exists():
        try:
            m = json.loads(mission.read_text())
            if not m.get("announced"):
                rec.status, rec.error = "failed", "the pilot never reported the mission done"
            elif m.get("pilot_exit") not in (0, None):
                rec.status, rec.error = "failed", f"pilot exited {m['pilot_exit']}"
            elif not (m.get("messages") or {}).get("/ov_msckf/odomimu"):
                rec.status, rec.error = "failed", "the estimator never published"
        except (OSError, json.JSONDecodeError):
            pass
    for evaluator in ("vio-eval", "spawn-eval", "validate"):
        download(workflow_id, evaluator, bundle / "eval" / evaluator, endpoint)
    validation = next(iter(bundle.rglob("validation.json")), None)
    if validation and validation.parent != bundle:
        (bundle / "validation.json").write_bytes(validation.read_bytes())
    if (bundle / "validation.json").exists():
        try:
            v = json.loads((bundle / "validation.json").read_text())
            rec.recording_valid = bool(v.get("ok", v.get("passed", not v.get("failed"))))
            rec.failed_checks = [str(c) for c in (v.get("failed_checks") or v.get("failed") or [])]
        except (OSError, json.JSONDecodeError):
            pass
    if tasks.get("verdict", "").startswith("FAILED") and rec.status == "done":
        rec.failed_checks.append("gate")
    rec.bundle = str(bundle)
    rec.recording = "kept" if any(bundle.rglob("*.mcap")) else None

    topics = Path(rec.stack) / "config" / "sim2real" / "topics.yaml" if rec.stack else None
    if topics is not None and topics.exists():
        (bundle / "topics.yaml").write_bytes(topics.read_bytes())

    (bundle / "run.json").write_text(json.dumps({
        "schema": RUN_META_SCHEMA,
        "campaign_id": campaign["id"],
        "run_key": rec.run_key, "variant": rec.variant, "seed": rec.seed, "repeat": rec.repeat,
        "run_id": workflow_id, "scenario_id": rec.scenario_id, "vehicle": rec.vehicle,
        "stack": rec.stack, "gt_bundle": None, "layout": None,
        "evaluation": campaign.get("evaluation"),
        # OSMO- and omega-specific facts live here, not in the RunRecord.
        "target": "osmo", "workflow_id": workflow_id, "workflow_status": status,
        "tasks": tasks, "platform": platform, "gates": run_gates,
        # Whether someone could watch this run, read from what actually ran.
        "viz": {"foxglove": "foxglove" in tasks,
                "chase_cam": (Path(rec.stack) / "config" / "unreal-airsim" / "settings.json").exists()
                and '"chase"' in (Path(rec.stack) / "config" / "unreal-airsim" / "settings.json").read_text()},
    }, indent=2))


# --------------------------------------------------------------------------
# Campaign
# --------------------------------------------------------------------------

def _merge_runs(root: Path, records: list[RunRecord]) -> list[dict[str, Any]]:
    fresh = {r.run_key: asdict(r) for r in records}
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    manifest = root / "campaign_manifest.json"
    if manifest.exists():
        try:
            for row in json.loads(manifest.read_text()).get("runs", []):
                key = row.get("run_key")
                if key and key not in seen:
                    merged.append(fresh.get(key, row))
                    seen.add(key)
        except (OSError, json.JSONDecodeError):
            pass
    merged += [row for key, row in fresh.items() if key not in seen]
    return merged


def write_manifest(root: Path, campaign_file: Path, campaign: dict[str, Any],
                   records: list[RunRecord], platform: dict[str, str] | None,
                   status: str | None = None) -> None:
    doc = {
        "schema": MANIFEST_SCHEMA,
        "campaign_id": campaign["id"],
        "name": campaign.get("name"),
        "spec": str(campaign_file),
        "scenario": str((campaign_file.parent / str(campaign["scenario"])).resolve()),
        "layout": None,
        "evaluation": campaign.get("evaluation"),
        "mission": campaign.get("mission"),
        "recording": campaign.get("recording") or {},
        "thresholds": campaign.get("thresholds") or {},
        "runs_dir": str(root / "runs"),
        "updated_at": now(),
        "provenance": {"executor": "osmo/campaign.py", "workflow": str(WORKFLOW)},
        "preflight": None,
        "status": status,
        # A --only invocation carries records for the runs it touched. The
        # manifest is the campaign's, not the invocation's, so the runs it
        # already holds are kept and only the ones just run are replaced --
        # otherwise re-running a single key silently drops its siblings from
        # the scorecard. Order follows the existing manifest, new keys append.
        "runs": _merge_runs(root, records),
        # Top level is unconstrained; this is where omega lives.
        "target": "osmo",
        "omega": omega_fields(campaign),
        "platform": platform,
        "gates": gates(campaign),
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "campaign_manifest.json").write_text(json.dumps(doc, indent=2))


def evaluate(root: Path, campaign: dict[str, Any] | None = None) -> int:
    """The platform's campaign-level evaluator, via its image, once per run.

    The runner scores a whole manifest in one call, which reads each run's bag
    end to end. Here the flight is a window inside the recording -- the
    vehicle is parked at both ends and this estimator diverges while it is --
    so each run is scored from the trimmed trajectory pair `vio-eval` already
    wrote, and `--est`/`--gt` take a path per run. That is why this is a loop:
    those flags are one value for the whole invocation.

    Without that pair (an older run, or an estimator that never published) the
    manifest call is the fallback, and it scores the whole recording.
    """
    est_topic = ((campaign or {}).get("evaluation") or {}).get("inputs", {}).get("est_topic")
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((root / "campaign_manifest.json").read_text())
    runs = [r for r in manifest.get("runs", []) if r.get("status") == "done" and r.get("bundle")]

    windowed = [r for r in runs
                if (Path(r["bundle"]) / "eval" / "vio-eval" / "estimate.tum").exists()]
    if not windowed:
        # The role map in a generated stack still says estimate: /odom, which
        # is the simulator's own relay; --est-topic is the CampaignSpec's
        # answer. See docs/osmo-runbook.md on the generator gap.
        argv = ["vio-stress", str(root / "campaign_manifest.json"), "--out", str(reports)]
        if est_topic:
            argv += ["--est-topic", est_topic]
        return sh(["docker", "run", "--rm", "-v", f"{root}:{root}", SIM_REAL_EVAL_IMAGE,
                   *argv], check=False, capture=False).returncode

    rc = 0
    for r in windowed:
        bundle = Path(r["bundle"])
        pair = bundle / "eval" / "vio-eval"
        proc = sh(["docker", "run", "--rm", "-v", f"{root}:{root}", SIM_REAL_EVAL_IMAGE,
                   "vio-stress", str(bundle),
                   "--est", str(pair / "estimate.tum"),
                   "--gt", str(pair / "ground_truth.tum"),
                   "--out", str(reports / r["run_key"])], check=False, capture=False)
        rc = rc or proc.returncode
    return rc


def cmd_run(args: argparse.Namespace) -> int:
    if args.chase_cam and not args.viz:
        sys.exit("[campaign] --chase-cam is for watching a run: it needs --viz")
    campaign_file = find_campaign(args.campaign)
    campaign = yaml.safe_load(campaign_file.read_text())
    root = CAMPAIGNS_HOST / str(campaign["id"])
    if args.chase_cam:
        # Its own manifest and scorecard: a chase-camera run flew a different
        # image budget, and must never sit in the campaign it was copied from.
        root = root.with_name(root.name + "-viz")
    # The product shell runs as root and `campaign plan` creates <root>/<key>/
    # as root. The manifest, the stacks and the evidence are written by this
    # user, so their directories are made here first, and the stacks live
    # beside the run directories rather than inside them.
    for d in (root, root / "stacks", root / "runs", root / "reports"):
        d.mkdir(parents=True, exist_ok=True)
    run_gates = gates(campaign)
    om = omega_fields(campaign)
    print(f"[campaign] {campaign['id']}: tier={om['tier']} verifies={om['verifies']} gates={run_gates}")

    runs = materialise(campaign_file, CAMPAIGNS_HOST, args.only or [])
    print(f"[campaign] {len(runs)} run(s) materialised under {root}")
    endpoint = storage_endpoint()

    records: list[RunRecord] = []
    submitted: list[tuple[RunRecord, Path, str]] = []
    platform: dict[str, str] | None = None
    for key, spec_file in runs:
        spec = yaml.safe_load(spec_file.read_text())
        variant, _, tail = key.rpartition("-r")
        rec = RunRecord(run_key=key, variant=variant.split("-s")[0], seed=None,
                        repeat=int(tail) if tail.isdigit() else 1,
                        scenario_id=spec.get("id"), spec=str(spec_file),
                        vehicle=(spec.get("vehicles") or [{}])[0].get("name"),
                        started_at=now())
        platform = platform or derive_platform(spec)
        records.append(rec)
        stack_dir = root / "stacks" / key
        try:
            if args.chase_cam:
                generate_stack(with_chase_camera(spec_file, root / "viz" / key), stack_dir)
            else:
                generate_stack(spec_file, stack_dir)
            rec.stack = str(stack_dir)
            wf = submit(stack_dir, campaign, run_gates, campaign_file, viz=args.viz)
            rec.run_id = wf
            print(f"[campaign] {key}: submitted {wf}")
            if args.viz:
                print(f"[campaign] {key}: watch live with  osmo/campaign.py watch {wf}")
            submitted.append((rec, spec_file, wf))
        except RuntimeError as exc:
            rec.status, rec.error, rec.finished_at = "failed", str(exc), now()
        write_manifest(root, campaign_file, campaign, records, platform)
        if args.serial and submitted:
            rec_s, spec_s, wf_s = submitted.pop()
            run_one(rec_s, spec_s, root, campaign, wf_s, platform or {}, run_gates, endpoint)
            write_manifest(root, campaign_file, campaign, records, platform)

    for rec, spec_file, wf in submitted:
        run_one(rec, spec_file, root, campaign, wf, platform or {}, run_gates, endpoint)
        print(f"[campaign] {rec.run_key}: {rec.status}" + (f" ({rec.error})" if rec.error else ""))
        write_manifest(root, campaign_file, campaign, records, platform)

    rc = 0
    if any(r.status != "done" for r in records):
        rc = 1
    if campaign.get("evaluation") and not args.no_evaluate:
        ev = evaluate(root, campaign)
        if ev == 3:
            rc = 1
    write_manifest(root, campaign_file, campaign, records, platform)
    print(f"[campaign] manifest: {root / 'campaign_manifest.json'}")
    return rc


def cmd_watch(args: argparse.Namespace) -> int:
    """Give a running workflow's live Foxglove view an address on this host.

    Asks Kubernetes, not OSMO: OSMO labels every task pod with its workflow
    and task name, so the pod is found by label. OSMO's own gateway is avoided
    on purpose -- on the quick-start deployment `osmo workflow port-forward`
    and `osmo workflow query` both came back `504 upstream request timeout`
    while a gang was loading.

    By default it puts a NodePort Service in front of the pod and exits; the
    Service dies with the pod. `--tunnel` instead holds a `kubectl
    port-forward` to localhost until the run ends. The run stays up while
    someone is connected (the recorder watches /viz/viewers), so connect
    within a minute of the recording closing.
    """
    wf = args.workflow
    selector = f"osmo.workflow_id={wf},osmo.task_name=foxglove"
    deadline = time.time() + args.wait
    said = None
    while True:
        out = subprocess.run(
            ["kubectl", "get", "pods", "-n", "default", "-l", selector, "-o",
             "jsonpath={range .items[*]}{.metadata.name} {.status.phase} "
             "{.status.containerStatuses[?(@.name=='foxglove')].ready}{\"\\n\"}{end}"],
            capture_output=True, text=True).stdout.split()
        pod, phase, ready = (out + [None, None, None])[:3]
        if pod and phase == "Running" and ready == "true":
            break
        if phase in ("Succeeded", "Failed"):
            sys.exit(f"[campaign] {wf}: foxglove pod is {phase}; the run is over")
        state = f"{phase or 'no pod yet'}"
        if state != said:
            print(f"[campaign] {wf}: foxglove {state}; waiting", flush=True)
            said = state
        if time.time() > deadline:
            sys.exit(f"[campaign] {wf}: foxglove never came up ({state}); was it submitted with --viz?")
        time.sleep(3)
    # A running container is not a listening bridge: OSMO's sidecar holds the
    # task's own command until the whole gang is up, and one refused
    # connection is enough for kubectl to drop the tunnel for good. So wait
    # for the port inside the pod, then keep the tunnel open for as long as
    # the pod lives.
    def alive() -> bool:
        return subprocess.run(["kubectl", "get", "pod", "-n", "default", pod, "-o",
                               "jsonpath={.status.phase}"], capture_output=True,
                              text=True).stdout == "Running"

    def listening() -> bool:
        return subprocess.run(["kubectl", "exec", "-n", "default", pod, "-c", "foxglove", "--",
                               "bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/8765"],
                              capture_output=True).returncode == 0

    while not listening():
        if not alive() or time.time() > deadline:
            sys.exit(f"[campaign] {wf}: foxglove_bridge never started listening")
        time.sleep(3)
    if args.tunnel:
        print(f"[campaign] {wf}: open Foxglove at ws://localhost:{args.port}  (ctrl-c to stop)", flush=True)
        while alive():
            subprocess.run(["kubectl", "port-forward", "-n", "default", "--address", "127.0.0.1",
                            f"pod/{pod}", f"{args.port}:8765"])
            time.sleep(1)
        print(f"[campaign] {wf}: the run is over", flush=True)
        return 0

    url = expose_nodeport(wf, pod)
    print(f"[campaign] {wf}: open Foxglove at {url}", flush=True)
    print(f"[campaign] {wf}: from another machine: ssh -L 8765:{url.split('//')[1]} <this host>, "
          f"then ws://localhost:8765", flush=True)
    return 0


def expose_nodeport(wf: str, pod: str) -> str:
    """A NodePort Service in front of a run's foxglove pod; returns its ws:// URL.

    Owned by the pod, so Kubernetes deletes it with the pod: no process to keep
    alive, nothing to clean up. The address is the pod's node, which is
    reachable from this host on the kind network.
    """
    meta = json.loads(subprocess.run(["kubectl", "get", "pod", "-n", "default", pod, "-o", "json"],
                                     capture_output=True, text=True, check=True).stdout)
    name = f"foxglove-{wf}"
    svc = {
        "apiVersion": "v1", "kind": "Service",
        "metadata": {"name": name, "namespace": "default",
                     "labels": {"osmo.workflow_id": wf, "tevv.viewer": "foxglove"},
                     "ownerReferences": [{"apiVersion": "v1", "kind": "Pod",
                                          "name": pod, "uid": meta["metadata"]["uid"]}]},
        "spec": {"type": "NodePort",
                 "selector": {"osmo.workflow_id": wf, "osmo.task_name": "foxglove"},
                 "ports": [{"name": "ws", "port": 8765, "targetPort": 8765, "protocol": "TCP"}]},
    }
    subprocess.run(["kubectl", "apply", "-f", "-"], input=json.dumps(svc), text=True,
                   capture_output=True, check=True)
    node_port = subprocess.run(["kubectl", "get", "svc", "-n", "default", name, "-o",
                                "jsonpath={.spec.ports[0].nodePort}"],
                               capture_output=True, text=True, check=True).stdout.strip()
    node = meta["spec"]["nodeName"]
    ip = subprocess.run(["kubectl", "get", "node", node, "-o",
                         'jsonpath={.status.addresses[?(@.type=="InternalIP")].address}'],
                        capture_output=True, text=True, check=True).stdout.strip()
    return f"ws://{ip}:{node_port}"


def cmd_reindex(args: argparse.Namespace) -> int:
    """Rebuild the manifest's runs from the evidence on disk.

    Every run bundle carries a run.json holding what the record needs, so the
    manifest is derivable from the evidence rather than the other way round.
    That makes it recoverable: a manifest lost, truncated by a --only run
    written before those merged, or copied from another machine can be built
    back from `runs/*/run.json` without re-flying anything.
    """
    campaign_file = find_campaign(args.campaign)
    campaign = yaml.safe_load(campaign_file.read_text())
    root = CAMPAIGNS_HOST / str(campaign["id"])
    records: list[RunRecord] = []
    platform: dict[str, str] | None = None
    for meta_file in sorted((root / "runs").glob("*/run.json")):
        meta = json.loads(meta_file.read_text())
        bundle = meta_file.parent
        validation = bundle / "validation.json"
        valid, failed = None, []
        if validation.exists():
            try:
                v = json.loads(validation.read_text())
                valid = bool(v.get("ok", v.get("passed", not v.get("failed"))))
                failed = [str(c) for c in (v.get("failed_checks") or v.get("failed") or [])]
            except (OSError, json.JSONDecodeError):
                pass
        tasks = meta.get("tasks") or {}
        done = tasks.get("recorder") == "COMPLETED" and tasks.get("pilot") == "COMPLETED"
        records.append(RunRecord(
            run_key=meta["run_key"], variant=meta.get("variant", ""), seed=meta.get("seed"),
            repeat=meta.get("repeat", 1), status="done" if done else "failed",
            run_id=meta.get("run_id"), scenario_id=meta.get("scenario_id"),
            spec=str(root / meta["run_key"] / "ScenarioSpec.yaml"), stack=meta.get("stack"),
            bundle=str(bundle), vehicle=meta.get("vehicle"),
            recording="kept" if any(bundle.rglob("*.mcap")) else None,
            recording_valid=valid, failed_checks=failed))
        platform = platform or meta.get("platform")
    if not records:
        sys.exit(f"[campaign] no run bundles under {root / 'runs'}")
    # Replace rather than merge: the evidence is the authority here.
    (root / "campaign_manifest.json").unlink(missing_ok=True)
    write_manifest(root, campaign_file, campaign, records, platform)
    print(f"[campaign] rebuilt {len(records)} run(s) into {root / 'campaign_manifest.json'}")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    """Score an existing manifest again -- after a scorer fix, or a rebuild."""
    campaign_file = find_campaign(args.campaign)
    campaign = yaml.safe_load(campaign_file.read_text())
    root = CAMPAIGNS_HOST / str(campaign["id"])
    return 0 if evaluate(root, campaign) in (0, 1) else 1


def cmd_status(args: argparse.Namespace) -> int:
    campaign_file = find_campaign(args.campaign)
    campaign = yaml.safe_load(campaign_file.read_text())
    root = CAMPAIGNS_HOST / str(campaign["id"])
    proc = product_cli("campaign", "status", container_path(root / "campaign_manifest.json"),
                       *(["--json"] if args.json else []))
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="osmo/campaign.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="materialise, generate, submit every run, collect evidence, evaluate")
    p.add_argument("campaign", help="campaign name under scenarios/, or a path")
    p.add_argument("--only", action="append", help="run key(s) to include")
    p.add_argument("--serial", action="store_true", help="wait for each run before submitting the next")
    p.add_argument("--no-evaluate", action="store_true")
    p.add_argument("--viz", action="store_true",
                   help="add the live Foxglove task to every run (see `watch`)")
    p.add_argument("--chase-cam", action="store_true",
                   help="with --viz: a third-person camera behind the drone. Thins the "
                        "stereo pair's rate, so the run is for looking, not scoring")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("watch", help="print the address of a running workflow's live Foxglove view")
    p.add_argument("workflow", help="workflow id, as `run` printed it")
    p.add_argument("--tunnel", action="store_true",
                   help="kubectl port-forward to localhost instead of a NodePort (held until the run ends)")
    p.add_argument("--port", type=int, default=8765, help="local port with --tunnel (default 8765)")
    p.add_argument("--wait", type=float, default=900.0, help="seconds to wait for the task to start")
    p.set_defaults(fn=cmd_watch)
    p = sub.add_parser("reindex", help="rebuild the manifest's runs from the evidence on disk")
    p.add_argument("campaign")
    p.set_defaults(fn=cmd_reindex)
    p = sub.add_parser("evaluate", help="run the campaign-level scorer over the manifest again")
    p.add_argument("campaign")
    p.set_defaults(fn=cmd_evaluate)
    p = sub.add_parser("status", help="the platform's scorecard over this campaign's manifest")
    p.add_argument("campaign")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
