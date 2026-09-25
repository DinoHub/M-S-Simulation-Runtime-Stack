#!/usr/bin/env python3
"""Fly a CampaignSpec as N OSMO workflows, one per matrix point.

    osmo/campaign.py run    vio-osmo-condo            # materialise, generate, submit, collect
    osmo/campaign.py run    vio-osmo-condo --only calm-r1
    osmo/campaign.py status vio-osmo-condo            # the platform's scorecard, unchanged
    osmo/campaign.py registry sync [vio-osmo-condo]   # the run registry, rebuilt from runs/

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

from registry import Registry

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
# --------------------------------------------------------------------------
# Images: from the catalog, never typed here
# --------------------------------------------------------------------------
#
# Every image a run uses is the one images/catalog.yaml pins for the product's
# channel, read from the image set the catalog renders
# (images/image-set.generated.yaml) -- the same file, and the same two
# overrides, the stack generator uses: MNS_IMAGE_SET_FILE for another file,
# MNS_IMAGE_SET for another set. Otherwise the set is the one MNS_CHANNEL's
# release channel names (product.sh's default, v1).
IMAGE_SET_FILE = Path(os.environ.get("MNS_IMAGE_SET_FILE",
                                     ROOT / "images" / "image-set.generated.yaml"))
CATALOG_FILE = ROOT / "images" / "catalog.yaml"
# product.sh's channel names -> the catalog's release_channels keys.
CHANNEL_NAMES = {"v1": "v1", "v2": "standalone_v2", "ue582": "standalone_v2_ue582"}

# Workflow value -> where the image set keeps that role.
WORKFLOW_IMAGES = {
    "runtime_host_image": ("simulators", "tevv_runtime_host"),
    "px4_image": ("autopilots", "px4"),
    "ardupilot_image": ("autopilots", "ardupilot"),
    "bridge_image": ("ros2_bridge",),
    "vio_estimator_image": ("vio_estimator",),
    "sim_real_eval_image": ("sim_real_eval",),
}

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


def image_set_name() -> str:
    if os.environ.get("MNS_IMAGE_SET"):
        return os.environ["MNS_IMAGE_SET"]
    channel = os.environ.get("MNS_CHANNEL", "v1")
    channels = ((yaml.safe_load(CATALOG_FILE.read_text()).get("consumers") or {})
                .get("release_channels") or {})
    entry = channels.get(CHANNEL_NAMES.get(channel, channel))
    if not entry:
        sys.exit(f"[campaign] MNS_CHANNEL={channel!r} names no release channel in {CATALOG_FILE}")
    return str(entry.get("image_set") or "published")


def pinned_images() -> tuple[str, dict[str, str]]:
    """(set name, {workflow value: repo:tag@sha256:...}) from the image set."""
    name = image_set_name()
    sets = (yaml.safe_load(IMAGE_SET_FILE.read_text()) or {}).get("image_sets") or {}
    if name not in sets:
        sys.exit(f"[campaign] image set {name!r} is not in {IMAGE_SET_FILE}; known: {', '.join(sets)}")
    refs: dict[str, str] = {}
    for var, path in WORKFLOW_IMAGES.items():
        node: Any = sets[name].get("images") or {}
        for part in path:
            node = (node or {}).get(part)
        if not isinstance(node, str) or not node:
            sys.exit(f"[campaign] image set {name!r} has no {'.'.join(path)} (for {var})")
        refs[var] = node
    return name, refs


def split_ref(ref: str) -> tuple[str, str | None]:
    """repo:tag@sha256:... -> (repo:tag, sha256:...)."""
    tag_ref, _, digest = ref.partition("@")
    return tag_ref, digest or None


def gpu_nodes() -> list[str]:
    out = subprocess.run(["kubectl", "get", "nodes", "-o",
                          "jsonpath={range .items[*]}{.metadata.name} "
                          "{.status.allocatable.nvidia\\.com/gpu}{\"\\n\"}{end}"],
                         capture_output=True, text=True).stdout.split("\n")
    return [line.split()[0] for line in out if len(line.split()) == 2 and line.split()[1] != "0"]


def verify_images(refs: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Is the image each tag names on the GPU node the one the catalog pins?

    A kind node's containerd holds what `kind load` gave it, by tag and image
    id, with no registry digest -- so a `repo:tag@sha256:` reference cannot be
    resolved there, and a pod is given the tag. The digest is checked here
    instead: the host's docker knows which local image carries the pinned
    registry digest, and that image's id must be the id the node holds under
    the tag. A tag that was repointed, or a node loaded from another build,
    shows up as a mismatch rather than as a run on an image nobody pinned."""
    nodes = gpu_nodes()
    report: dict[str, dict[str, Any]] = {}
    for var, ref in refs.items():
        tag_ref, digest = split_ref(ref)
        repo = tag_ref.rsplit(":", 1)[0]
        row: dict[str, Any] = {"pinned": ref, "submitted": tag_ref, "ok": False}
        pinned_id = None
        if digest:
            proc = subprocess.run(["docker", "image", "inspect", f"{repo}@{digest}",
                                   "--format", "{{.Id}}"], capture_output=True, text=True)
            pinned_id = proc.stdout.strip() if proc.returncode == 0 else None
        row["pinned_image_id"] = pinned_id
        for node in nodes:
            proc = subprocess.run(["docker", "exec", node, "crictl", "inspecti", "-o", "json",
                                   f"docker.io/{tag_ref}" if tag_ref.count("/") == 1 else tag_ref],
                                  capture_output=True, text=True)
            try:
                node_id = json.loads(proc.stdout)["status"]["id"] if proc.returncode == 0 else None
            except (json.JSONDecodeError, KeyError):
                node_id = None
            row.setdefault("nodes", {})[node] = node_id
        node_ids = set((row.get("nodes") or {}).values())
        if not digest:
            row["problem"] = "the image set pins no digest"
        elif not nodes:
            row["problem"] = ("no node advertises a GPU (nvidia.com/gpu allocatable is 0 or absent): "
                              "is the device plugin running on the GPU node?")
        elif pinned_id is None:
            row["problem"] = f"{repo}@{digest} is not on this host: docker pull it, then kind load"
        elif None in node_ids or not node_ids:
            row["problem"] = f"{tag_ref} is not on the GPU node: kind load docker-image {tag_ref}"
        elif node_ids != {pinned_id}:
            row["problem"] = (f"the node's {tag_ref} is not the pinned image "
                              f"(node {sorted(i[:19] for i in node_ids)}, pinned {pinned_id[:19]})")
        else:
            row["ok"] = True
        report[var] = row
    return report


def resolve_images(allow_drift: bool) -> tuple[dict[str, str], dict[str, Any]]:
    """The images a run is submitted with, and the record of how they were checked."""
    name, refs = pinned_images()
    report = verify_images(refs)
    bad = {var: row for var, row in report.items() if not row["ok"]}
    for var, row in report.items():
        print(f"[campaign] image {var}: {row['submitted']}"
              + ("" if row["ok"] else f"  -- {row['problem']}"))
    if bad and not allow_drift:
        sys.exit(f"[campaign] {len(bad)} image(s) are not what image set {name!r} pins "
                 f"({IMAGE_SET_FILE}); fix the node or the catalog, or pass --allow-image-drift "
                 "to fly them anyway (recorded in every run.json)")
    return ({var: row["submitted"] for var, row in report.items()},
            {"image_set": name, "file": str(IMAGE_SET_FILE), "verified": not bad, "images": report})


def evaluator_image() -> str:
    """The campaign-level scorer: the image set's sim_real_eval, unless
    MNS_SIM_REAL_EVAL_IMAGE names another. It must carry `vio-stress`."""
    if os.environ.get("MNS_SIM_REAL_EVAL_IMAGE"):
        return os.environ["MNS_SIM_REAL_EVAL_IMAGE"]
    return pinned_images()[1]["sim_real_eval_image"]


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
           campaign_file: Path, images: dict[str, str], viz: bool = False,
           viz_hold_sec: int = 0) -> str:
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
        # A viewer never holds a run open unless asked: the flight's end ends
        # the run, and the next one in the queue starts.
        "viz_hold_sec": str(int(viz_hold_sec)),
        "gates_json": json.dumps(run_gates),
        "route_b64": route_blob(campaign_file, campaign),
        # The workflow names no image of its own; each comes from the catalog.
        **images,
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


def workflow_times(workflow_id: str) -> dict[str, str | None]:
    """OSMO's own clock for a workflow: submitted, started (the run group left
    the queue), evaluating (the evaluate group started) and ended, as UTC ISO
    strings. The registry records these rather than when this script happened
    to poll: with several runs submitted at once, the one being waited on is
    the only one polled, and the rest were timed by when their turn came."""
    proc = osmo("workflow", "query", workflow_id, "-t", "json", check=False)
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}

    def utc(value: Any) -> str | None:
        return f"{value}+00:00" if value and "+" not in str(value) else value

    groups = {g.get("name"): g for g in doc.get("groups") or []}
    return {"submitted": utc(doc.get("submit_time")),
            "started": utc((groups.get("run") or {}).get("start_time") or doc.get("start_time")),
            "evaluating": utc((groups.get("evaluate") or {}).get("start_time")),
            "ended": utc(doc.get("end_time"))}


def wait(workflow_id: str, poll_s: float = 20.0,
         on_poll: Any = None) -> tuple[str, dict[str, str]]:
    while True:
        status, tasks = query(workflow_id)
        if status in TERMINAL:
            return status, tasks
        if on_poll is not None:
            on_poll(status, tasks)
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

def judge_flight(status: str, tasks: dict[str, str], bundle: Path) -> tuple[str, str | None]:
    """Whether a run flew: the RunRecord's done/failed, and why not.

    A run that produced evidence flew; the campaign-level evaluator judges it.
    That is the compose runner's semantics and the scorecard depends on it.
    "Flew" is literal: the pilot task has to exist and have completed. Run
    25 had a COMPLETED recorder, a full bag, and no pilot task at all,
    because the submit's values never reached the workflow."""
    if tasks.get("recorder") == "COMPLETED" and tasks.get("pilot") == "COMPLETED":
        verdict, error = "done", None
    elif "pilot" not in tasks:
        return "failed", f"workflow {status}: no pilot task -- the workflow ran on its defaults"
    else:
        failed = [f"{t}={s}" for t, s in tasks.items() if s.startswith("FAILED")]
        return "failed", f"workflow {status}: " + (", ".join(failed) or "no recorder output")
    # The recorder writes how the flight ended beside the bag. A pilot task
    # exits 0 or 1 as COMPLETED -- a failed flight must not take the gang
    # down with it -- so the task table cannot tell a flight from a refusal
    # to arm; this can.
    mission = bundle / "mission.json"
    if mission.exists():
        try:
            m = json.loads(mission.read_text())
            if not m.get("announced"):
                return "failed", "the pilot never reported the mission done"
            if m.get("pilot_exit") not in (0, None):
                return "failed", f"pilot exited {m['pilot_exit']}"
            if not (m.get("messages") or {}).get("/ov_msckf/odomimu"):
                return "failed", "the estimator never published"
        except (OSError, json.JSONDecodeError):
            pass
    return verdict, error


def read_validation(bundle: Path) -> tuple[bool | None, list[str]]:
    """validate_recording.py's answer: `valid` plus the checks that failed.
    (Older readers looked for `ok`/`passed` and so took every recording as
    valid; the tool has always written `valid`.) The validate task's own
    output wins over the copy at the top of the run directory, which can be
    left over from an earlier attempt."""
    path = bundle / "eval" / "validate" / "validation.json"
    if not path.exists():
        path = bundle / "validation.json"
    if not path.exists():
        return None, []
    try:
        v = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None, []
    valid = v.get("valid", v.get("ok", v.get("passed")))
    if valid is None:
        valid = not v.get("failed")
    return bool(valid), [str(c) for c in (v.get("failed_checks") or v.get("failed") or [])]


def run_one(rec: RunRecord, spec_file: Path, root: Path, campaign: dict[str, Any],
            workflow_id: str, platform: dict[str, str], run_gates: dict[str, Any],
            endpoint: str, images: dict[str, Any] | None = None,
            reg: Registry | None = None, others: list[str] | None = None) -> None:
    reg = reg or Registry(dsn="")

    def progress(s: str, t: dict[str, str]) -> None:
        # The runs still queued or flying behind this one, so the registry's
        # progress is live for the whole campaign, not only the run waited on.
        reg.progress(workflow_id, s, t)
        for other in others or []:
            if other != workflow_id and reg.on:
                reg.progress(other, *query(other))

    status, tasks = wait(workflow_id, on_poll=progress)
    rec.finished_at = now()
    bundle = root / "runs" / rec.run_key
    bundle.mkdir(parents=True, exist_ok=True)

    # Evidence, into the layout the scorecard reads.
    download(workflow_id, "recorder", bundle, endpoint)
    rec.status, rec.error = judge_flight(status, tasks, bundle)
    for evaluator in ("vio-eval", "spawn-eval", "validate", "verdict"):
        download(workflow_id, evaluator, bundle / "eval" / evaluator, endpoint)
    # runs/<key>/ is reused by every attempt of the key, and a sync adds files
    # without removing old ones: the copy at the top must come from this
    # attempt's validate task, or it is the last attempt's answer (run 62 was
    # reported invalid from a 22 Sept file).
    fresh = bundle / "eval" / "validate" / "validation.json"
    if fresh.exists():
        (bundle / "validation.json").write_bytes(fresh.read_bytes())
    else:
        (bundle / "validation.json").unlink(missing_ok=True)
    rec.recording_valid, rec.failed_checks = read_validation(bundle)
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
        # Which images flew, as pinned and as found on the node.
        "images": images,
        # Whether someone could watch this run, read from what actually ran.
        "viz": {"foxglove": "foxglove" in tasks,
                "chase_cam": (Path(rec.stack) / "config" / "unreal-airsim" / "settings.json").exists()
                and '"chase"' in (Path(rec.stack) / "config" / "unreal-airsim" / "settings.json").read_text()},
    }, indent=2))

    outcome, _ = registry_outcome(rec.status, rec.error, status, tasks, load_verdict(bundle, run_gates))
    if outcome != "passed":
        capture_logs(workflow_id, tasks, bundle, since=rec.started_at)
    register_run(reg, workflow_id, bundle, rec.status, rec.error, status, tasks, run_gates,
                 rec.recording_valid, rec.failed_checks, rec.finished_at)
    if others is not None and workflow_id in others:
        others.remove(workflow_id)


# --------------------------------------------------------------------------
# The run registry (osmo/registry.py): what the files say, where Grafana reads
# --------------------------------------------------------------------------

VERDICT_PY = ROOT / "osmo" / "files" / "verdict.py"

# The registry's headline numbers, from vio-eval's report. The full set stays
# in ClickHouse; these are what a campaign is compared on.
SUMMARY_METRICS = {
    "ate_rmse_m": ("ate_trans_m", "rmse", "m"),
    "ate_max_m": ("ate_trans_m", "max", "m"),
    "rpe_rmse_m": ("rpe_trans_m", "rmse", "m"),
    "ate_rot_rmse_deg": ("ate_rot_deg", "rmse", "deg"),
    "yaw_drift_deg_per_min": ("yaw_drift_deg_per_min", None, "deg/min"),
    "flight_s": ("duration_s", None, "s"),
}


def load_verdict(bundle: Path, run_gates: dict[str, Any]) -> dict[str, Any] | None:
    """The verdict task's verdict.json; for a run flown before the task wrote
    one, the same verdict.py rerun here over the same evaluator outputs."""
    path = bundle / "eval" / "verdict" / "verdict.json"
    if not path.exists() and (bundle / "eval").is_dir() and any((bundle / "eval").rglob("*.json")):
        proc = subprocess.run([sys.executable, str(VERDICT_PY)], capture_output=True, text=True,
                              env={**os.environ, "REPORT_DIR": str(bundle / "eval"),
                                   "OUTPUT_DIR": str(path.parent), "GATES": json.dumps(run_gates)})
        if path.exists():
            doc = json.loads(path.read_text())
            doc["recomputed_on_host"] = True
            path.write_text(json.dumps(doc, indent=2))
        elif proc.returncode not in (0, 1):
            return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def registry_outcome(flight: str, error: str | None, workflow_status: str,
                     tasks: dict[str, str], verdict: dict[str, Any] | None) -> tuple[str, str | None]:
    """The registry's status and failure_reason for a finished workflow."""
    if workflow_status == "FAILED_IMAGE_PULL":
        return "infra_failed", "image_pull"
    if workflow_status == "FAILED_SERVER_ERROR":
        return "infra_failed", "infra"
    if workflow_status in ("CANCELLED", "FAILED_CANCELED"):
        return "aborted", "cancelled"
    if flight != "done":
        err = error or ""
        if "no pilot task" in err:
            return "failed", "no_pilot"
        if "estimator never published" in err:
            return "failed", "no_estimate"
        if "pilot never reported" in err or "pilot exited" in err:
            return "failed", "mission_failed"
        return "failed", "pod_crash"
    rc = (verdict or {}).get("rc")
    if rc == 42:
        return "infra_failed", "no_evidence"
    if rc == 0 or (rc is None and tasks.get("verdict") == "COMPLETED"):
        return "passed", None
    if any(g.get("gate") == "spawn" and not g.get("passed") for g in (verdict or {}).get("gates", [])):
        return "failed", "spawn_failed"
    if rc == 1 or tasks.get("verdict", "").startswith("FAILED"):
        return "failed", "gate_failed"
    return "failed", "no_evidence"


def summary_metrics(bundle: Path) -> list[tuple[str, float, str | None, str]]:
    path = bundle / "eval" / "vio-eval" / "vio.json"
    try:
        traj = json.loads(path.read_text())["categories"]["trajectory"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return []
    rows = []
    for name, (field_, stat, unit) in SUMMARY_METRICS.items():
        value = traj.get(field_)
        if stat is not None:
            value = value.get(stat) if isinstance(value, dict) else None
        if isinstance(value, (int, float)):
            rows.append((name, float(value), unit, "vio-eval"))
    return rows


def run_artifacts(bundle: Path, workflow_id: str) -> list[tuple[str, str, int | None]]:
    def size(p: Path) -> int:
        return p.stat().st_size if p.is_file() else sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    rows = []
    for kind, path in (("bag", bundle / "bag"), ("eval", bundle / "eval"),
                       ("logs", bundle / "logs" / workflow_id)):
        if path.exists() and size(path):
            rows.append((kind, str(path), size(path)))
    reports = bundle.parent.parent / "reports"
    for report in sorted(reports.glob(f"{bundle.name}.*")) + sorted(reports.glob(f"{bundle.name}/*")):
        if report.is_file():
            rows.append(("report", str(report), report.stat().st_size))
    return rows


LOKI_NODE_PORT = 31100


def loki_url() -> str | None:
    """Loki as the host reaches it: MNS_LOKI_URL, else the service node's
    InternalIP plus the NodePort observability/install.sh made. None when
    there is no Loki (MNS_LOKI=off, or not installed)."""
    if os.environ.get("MNS_LOKI", "").lower() in ("off", "0", "false", "no"):
        return None
    if os.environ.get("MNS_LOKI_URL"):
        return os.environ["MNS_LOKI_URL"].rstrip("/")
    node_ip = subprocess.run(["kubectl", "get", "node", "osmo-worker", "-o",
                              'jsonpath={.status.addresses[?(@.type=="InternalIP")].address}'],
                             capture_output=True, text=True).stdout.strip()
    return f"http://{node_ip}:{LOKI_NODE_PORT}" if node_ip else None


def loki_logs(workflow_id: str, since: str | None) -> dict[str, list[str]] | None:
    """Every line Loki holds for a workflow, per task and source, in order.
    None when Loki cannot be asked; an empty dict when it has nothing."""
    import urllib.parse
    import urllib.request
    base = loki_url()
    if not base:
        return None
    start_ns = int((datetime.fromisoformat(since).timestamp() - 600) * 1e9) if since else \
        time.time_ns() - 24 * 3600 * 10**9
    end_ns = time.time_ns() + 60 * 10**9
    expr = f'{{job="osmo/workflow"}} | workflow_id="{workflow_id}"'
    out: dict[str, list[tuple[int, str]]] = {}
    limit = 5000

    # By window, not by "start after the last line": Loki's limit is not the
    # earliest N lines across streams, so paging from the last timestamp
    # skipped lines (a 7,675-line sim log came back as 4,293). A window that
    # fills the limit is split until each half fits.
    def fetch(lo: int, hi: int) -> None:
        url = base + "/loki/api/v1/query_range?" + urllib.parse.urlencode(
            {"query": expr, "start": lo, "end": hi, "limit": limit, "direction": "forward"})
        with urllib.request.urlopen(url, timeout=30) as resp:
            result = json.load(resp)["data"]["result"]
        if sum(len(st["values"]) for st in result) >= limit and hi - lo > 1_000_000:
            mid = (lo + hi) // 2
            fetch(lo, mid)
            fetch(mid, hi)
            return
        for stream in result:
            labels = stream["stream"]
            name = labels.get("task", "unknown") + (
                ".sidecar" if labels.get("source") == "sidecar" else "")
            out.setdefault(name, []).extend((int(ts), line) for ts, line, *_ in stream["values"])

    try:
        fetch(start_ns, end_ns)
    except Exception as exc:  # noqa: BLE001 -- any failure means "ask OSMO instead"
        print(f"[campaign]   Loki unavailable ({str(exc)[:120]}); using osmo workflow logs")
        return None
    return {name: [line for _, line in sorted(rows)] for name, rows in out.items()}


def capture_logs(workflow_id: str, tasks: dict[str, str], bundle: Path,
                 since: str | None = None) -> None:
    """Every task's output, kept with a run that did not pass.

    From Loki when it is up: Alloy read it from the node's container logs, all
    of it. OSMO's own copy (`osmo workflow logs`) is the fallback. It drops
    lines under load -- half of a 20,000-line burst -- and says so in the
    simulator's log as "Maximum logging rate exceeded"."""
    # Per workflow: the run directory outlives an attempt, its logs must not
    # be mistaken for the next one's.
    logs = bundle / "logs" / workflow_id
    logs.mkdir(parents=True, exist_ok=True)
    found = loki_logs(workflow_id, since)
    if found:
        for name, lines in found.items():
            (logs / f"{name}.log").write_text("\n".join(lines) + "\n")
        print(f"[campaign]   task logs from Loki kept in {logs}")
        return
    for task in tasks:
        proc = osmo("workflow", "logs", workflow_id, "--task", task, check=False)
        if proc.stdout.strip():
            (logs / f"{task}.log").write_text(proc.stdout)
    print(f"[campaign]   task logs from OSMO kept in {logs}")


def register_run(reg: Registry, workflow_id: str, bundle: Path, flight: str, error: str | None,
                 workflow_status: str, tasks: dict[str, str], run_gates: dict[str, Any],
                 recording_valid: bool | None, failed_checks: list[str],
                 ended_at: str | None) -> None:
    if not reg.on:
        return
    verdict = load_verdict(bundle, run_gates)
    status, reason = registry_outcome(flight, error, workflow_status, tasks, verdict)
    viz = None
    if (bundle / "run.json").exists():
        viz = json.loads((bundle / "run.json").read_text()).get("viz")
    times = workflow_times(workflow_id) if reg.on else {}
    reg.finish(workflow_id, status=status, reason=reason, error=error,
               workflow_status=workflow_status, tasks=tasks, run_dir=str(bundle),
               recording_valid=recording_valid, failed_checks=failed_checks, viz=viz,
               ended_at=times.get("ended") or ended_at, submitted_at=times.get("submitted"),
               started_at=times.get("started"), eval_started_at=times.get("evaluating"))
    reg.results(workflow_id, gates=(verdict or {}).get("gates", []),
                metrics=summary_metrics(bundle), artifacts=run_artifacts(bundle, workflow_id))
    print(f"[registry] {workflow_id}: {status}" + (f" ({reason})" if reason else ""))


def git_sha() -> str | None:
    proc = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True)
    return proc.stdout.strip() or None


def register_campaign(reg: Registry, campaign_id: str, campaign: dict[str, Any],
                      campaign_file: Path, platform: dict[str, str] | None) -> None:
    om = omega_fields(campaign)
    reg.upsert_campaign(campaign, campaign_id=campaign_id, spec_path=str(campaign_file),
                        scenario=str((campaign_file.parent / str(campaign["scenario"])).resolve()),
                        tier=om["tier"], verifies=om["verifies"], platform=platform,
                        gates=gates(campaign), git_sha=git_sha())


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


def evaluate(root: Path, campaign: dict[str, Any] | None = None,
             reg: Registry | None = None) -> int:
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
    image = evaluator_image()
    if sh(["docker", "run", "--rm", image, "vio-stress", "--help"], check=False).returncode != 0:
        # The published -latest worker predates vio-stress (2026-08-09). Until
        # a worker that has it is published and pinned in the catalog, point
        # MNS_SIM_REAL_EVAL_IMAGE at a local build of
        # MnS-Integration-Platform/tools/sim_real_eval.
        print(f"[campaign] {image} has no vio-stress; set MNS_SIM_REAL_EVAL_IMAGE to a worker "
              "that does. Runs are collected; `evaluate` scores them later.", file=sys.stderr)
        return 3

    windowed = [r for r in runs
                if (Path(r["bundle"]) / "eval" / "vio-eval" / "estimate.tum").exists()]
    if not windowed:
        # The role map in a generated stack still says estimate: /odom, which
        # is the simulator's own relay; --est-topic is the CampaignSpec's
        # answer. See docs/osmo-runbook.md on the generator gap.
        argv = ["vio-stress", str(root / "campaign_manifest.json"), "--out", str(reports)]
        if est_topic:
            argv += ["--est-topic", est_topic]
        return sh(["docker", "run", "--rm", "-v", f"{root}:{root}", image,
                   *argv], check=False, capture=False).returncode

    rc = 0
    for r in windowed:
        bundle = Path(r["bundle"])
        pair = bundle / "eval" / "vio-eval"
        proc = sh(["docker", "run", "--rm", "-v", f"{root}:{root}", image,
                   "vio-stress", str(bundle),
                   "--est", str(pair / "estimate.tum"),
                   "--gt", str(pair / "ground_truth.tum"),
                   "--out", str(reports / r["run_key"])], check=False, capture=False)
        rc = rc or proc.returncode
        if reg is not None and r.get("run_id"):
            for report in sorted(reports.glob(f"{r['run_key']}.*")):
                reg.add_artifact(r["run_id"], "report", str(report), report.stat().st_size)
    return rc


def wants_viz(spec: dict[str, Any], flag: bool | None) -> bool:
    """Whether a run gets the live Foxglove task.

    The ScenarioSpec's own switch, `runtime.features.foxglove_bridge`, so a
    campaign can turn it on for every run or, through a variant's overrides,
    for some; `--viz` / `--no-viz` override it for one invocation. Absent, it
    is off under OSMO -- the compose generator defaults it on, but here it adds
    a pod and holds the gang for viewers, which a scored matrix should not pay
    for by default."""
    if flag is not None:
        return flag
    runtime = spec.get("runtime")
    features = (runtime.get("features") if isinstance(runtime, dict) else None) or {}
    return bool(features.get("foxglove_bridge", False))


def cmd_run(args: argparse.Namespace) -> int:
    if args.chase_cam and args.viz is False:
        sys.exit("[campaign] --chase-cam is for watching a run: it cannot go with --no-viz")
    if args.chase_cam:
        args.viz = True
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

    images, image_record = resolve_images(args.allow_image_drift)
    runs = materialise(campaign_file, CAMPAIGNS_HOST, args.only or [])
    print(f"[campaign] {len(runs)} run(s) materialised under {root}")
    endpoint = storage_endpoint()
    # Keyed by the campaign directory, so a chase-camera copy is its own
    # campaign in the registry as it is on disk.
    reg = Registry()
    register_campaign(reg, root.name, campaign, campaign_file, None)

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
        attempt = reg.plan_attempt(root.name, key, variant=rec.variant, repeat=rec.repeat,
                                   seed=rec.seed, scenario_id=rec.scenario_id, vehicle=rec.vehicle,
                                   run_dir=str(root / "runs" / key))
        stack_dir = root / "stacks" / key
        try:
            if args.chase_cam:
                generate_stack(with_chase_camera(spec_file, root / "viz" / key), stack_dir)
            else:
                generate_stack(spec_file, stack_dir)
            rec.stack = str(stack_dir)
            viz = wants_viz(spec, args.viz)
            wf = submit(stack_dir, campaign, run_gates, campaign_file, images, viz=viz,
                        viz_hold_sec=args.viz_hold)
            rec.run_id = wf
            reg.submitted(root.name, key, attempt, wf, images=image_record, viz=viz, at=now())
            print(f"[campaign] {key}: submitted {wf}" + (" (live view on)" if viz else ""))
            if viz:
                print(f"[campaign] {key}: watch live with  osmo/campaign.py watch {wf}")
            submitted.append((rec, spec_file, wf))
        except RuntimeError as exc:
            rec.status, rec.error, rec.finished_at = "failed", str(exc), now()
            reg.not_submitted(root.name, key, attempt, "stack_generation", str(exc))
        write_manifest(root, campaign_file, campaign, records, platform)
        if args.serial and submitted:
            rec_s, spec_s, wf_s = submitted.pop()
            run_one(rec_s, spec_s, root, campaign, wf_s, platform or {}, run_gates, endpoint,
                    image_record, reg)
            write_manifest(root, campaign_file, campaign, records, platform)

    outstanding = [wf for _, _, wf in submitted]
    for rec, spec_file, wf in submitted:
        run_one(rec, spec_file, root, campaign, wf, platform or {}, run_gates, endpoint,
                image_record, reg, outstanding)
        print(f"[campaign] {rec.run_key}: {rec.status}" + (f" ({rec.error})" if rec.error else ""))
        write_manifest(root, campaign_file, campaign, records, platform)

    rc = 0
    if any(r.status != "done" for r in records):
        rc = 1
    register_campaign(reg, root.name, campaign, campaign_file, platform)
    if campaign.get("evaluation") and not args.no_evaluate:
        ev = evaluate(root, campaign, reg)
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
    port-forward` to localhost until the run ends. The view lasts as long as
    the flight: when the recording closes the run ends and the view with it,
    whoever is connected -- a viewer never delays a campaign. A run submitted
    with `--viz-hold SEC` instead stays up for a viewer after the recording,
    up to SEC seconds and 60 s after the last one leaves.
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

    url = expose_nodeport(wf, pod, args.node_port)
    print(f"[campaign] {wf}: open Foxglove at {url}  (live until the recording ends; "
          f"the recorded bag opens in Foxglove afterwards)", flush=True)
    print(f"[campaign] {wf}: from another machine: ssh -L 8765:{url.split('//')[1]} <this host>, "
          f"then ws://localhost:8765", flush=True)
    return 0


# The one port every viz run is reached on, so a Foxglove connection saved
# once keeps working run after run. Inside the NodePort range (30000-32767)
# and easy to read as 8765's stand-in. One GPU means one run at a time, so
# one fixed port is enough; if it is still held -- a previous run's pod not
# yet gone -- the Service takes whatever port Kubernetes assigns instead.
FOXGLOVE_NODE_PORT = 30765


def expose_nodeport(wf: str, pod: str, node_port: int = FOXGLOVE_NODE_PORT) -> str:
    """A NodePort Service in front of a run's foxglove pod; returns its ws:// URL.

    Owned by the pod, so Kubernetes deletes it with the pod: no process to keep
    alive, nothing to clean up. The address is the pod's node, which is
    reachable from this host on the kind network. `node_port` 0 lets
    Kubernetes pick; a fixed port that is already allocated falls back to that.
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
    if node_port:
        svc["spec"]["ports"][0]["nodePort"] = node_port
    proc = subprocess.run(["kubectl", "apply", "-f", "-"], input=json.dumps(svc), text=True,
                          capture_output=True)
    if proc.returncode != 0 and node_port and "already allocated" in proc.stderr:
        print(f"[campaign] {wf}: node port {node_port} is held by another Service; "
              f"taking an assigned one", flush=True)
        del svc["spec"]["ports"][0]["nodePort"]
        proc = subprocess.run(["kubectl", "apply", "-f", "-"], input=json.dumps(svc), text=True,
                              capture_output=True)
    if proc.returncode != 0:
        sys.exit(f"[campaign] {wf}: could not create Service {name}: {proc.stderr.strip()}")
    node_port = subprocess.run(["kubectl", "get", "svc", "-n", "default", name, "-o",
                                "jsonpath={.spec.ports[0].nodePort}"],
                               capture_output=True, text=True, check=True).stdout.strip()
    node = meta["spec"]["nodeName"]
    ip = subprocess.run(["kubectl", "get", "node", node, "-o",
                         'jsonpath={.status.addresses[?(@.type=="InternalIP")].address}'],
                        capture_output=True, text=True, check=True).stdout.strip()
    return f"ws://{ip}:{node_port}"


def cmd_images(args: argparse.Namespace) -> int:
    """Print the catalog's images for this channel and check the GPU node.

    Also the way to submit the workflow by hand: the last line is the
    --set-string a bare `osmo workflow submit` needs, since the workflow file
    names no image of its own."""
    name, refs = pinned_images()
    report = verify_images(refs)
    print(f"image set {name!r} from {IMAGE_SET_FILE}")
    for var, row in report.items():
        print(f"  {var:22} {'ok  ' if row['ok'] else 'DRIFT'} {row['pinned']}")
        if not row["ok"]:
            print(f"  {'':22}       {row['problem']}")
    print("--set-string " + " ".join(f"{v}={r['submitted']}" for v, r in report.items()))
    return 0 if all(r["ok"] for r in report.values()) else 1


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
        valid, failed = read_validation(bundle)
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


def cmd_registry(args: argparse.Namespace) -> int:
    """`registry sync`: write what runs/ holds into the run registry.

    The registry is fed live by `run`; this is how it is filled for runs
    flown before it existed, or after it was down, or on a new cluster. Every
    run bundle's run.json names its workflow, so an attempt the registry
    already has is updated in place and one it lacks is added after the ones
    it holds. Nothing is re-flown and nothing on disk changes, except that a
    run flown before the verdict task wrote verdict.json gets one, from the
    same verdict.py run here."""
    reg = Registry()
    if not reg.on:
        return 42
    roots = ([CAMPAIGNS_HOST / args.campaign] if args.campaign
             else sorted(p.parent for p in CAMPAIGNS_HOST.glob("*/campaign_manifest.json")))
    total = 0
    for root in roots:
        manifest_file = root / "campaign_manifest.json"
        if not manifest_file.exists():
            print(f"[registry] no manifest in {root}", file=sys.stderr)
            continue
        manifest = json.loads(manifest_file.read_text())
        spec_file = Path(manifest["spec"])
        campaign = (yaml.safe_load(spec_file.read_text()) if spec_file.exists()
                    else {"id": manifest["campaign_id"], "name": manifest.get("name"),
                          "scenario": manifest.get("scenario"),
                          "evaluation": manifest.get("evaluation")})
        register_campaign(reg, root.name, campaign, spec_file, manifest.get("platform"))
        rows = {r.get("run_key"): r for r in manifest.get("runs", [])}
        for meta_file in sorted((root / "runs").glob("*/run.json")):
            meta = json.loads(meta_file.read_text())
            bundle, wf = meta_file.parent, meta.get("workflow_id") or meta.get("run_id")
            if not wf:
                continue
            reg.adopt(root.name, meta["run_key"], wf, variant=meta.get("variant"),
                      repeat=meta.get("repeat", 1), seed=meta.get("seed"),
                      scenario_id=meta.get("scenario_id"), vehicle=meta.get("vehicle"),
                      run_dir=str(bundle))
            row = rows.get(meta["run_key"]) or {}
            same = row.get("run_id") == wf
            reg.submitted(root.name, meta["run_key"], _attempt_of(reg, wf), wf,
                          images=meta.get("images"), viz=(meta.get("viz") or {}).get("foxglove"),
                          at=row.get("started_at") if same else None)
            tasks = meta.get("tasks") or {}
            flight, error = judge_flight(meta.get("workflow_status", ""), tasks, bundle)
            ended = row.get("finished_at") if same else None
            ended = ended or datetime.fromtimestamp(meta_file.stat().st_mtime, timezone.utc).isoformat()
            valid, failed = read_validation(bundle)
            register_run(reg, wf, bundle, flight, error, meta.get("workflow_status", ""), tasks,
                         meta.get("gates") or gates(campaign), valid, failed, ended)
            total += 1
    print(f"[registry] {total} run(s) synced")
    return 0 if reg.on else 42


def _attempt_of(reg: Registry, workflow_ref: str) -> int | None:
    key = reg._key(workflow_ref)
    return key[2] if key else None


def cmd_evaluate(args: argparse.Namespace) -> int:
    """Score an existing manifest again -- after a scorer fix, or a rebuild."""
    campaign_file = find_campaign(args.campaign)
    campaign = yaml.safe_load(campaign_file.read_text())
    root = CAMPAIGNS_HOST / str(campaign["id"])
    return 0 if evaluate(root, campaign, Registry()) in (0, 1) else 1


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
    p.add_argument("--viz", action=argparse.BooleanOptionalAction, default=None,
                   help="the live Foxglove task on (--viz) or off (--no-viz) for every run; "
                        "default: each run's runtime.features.foxglove_bridge, else off")
    p.add_argument("--viz-hold", type=int, default=0, metavar="SEC",
                   help="with --viz: keep a run up to SEC seconds after its recording ends while "
                        "someone is watching (released 60 s after the last viewer leaves). "
                        "Default 0: a viewer never delays the campaign")
    p.add_argument("--allow-image-drift", action="store_true",
                   help="fly even if a node's image is not the one the catalog pins "
                        "(each run.json records what flew)")
    p.add_argument("--chase-cam", action="store_true",
                   help="with --viz: a third-person camera behind the drone. Thins the "
                        "stereo pair's rate, so the run is for looking, not scoring")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("watch", help="print the address of a running workflow's live Foxglove view")
    p.add_argument("workflow", help="workflow id, as `run` printed it")
    p.add_argument("--tunnel", action="store_true",
                   help="kubectl port-forward to localhost instead of a NodePort (held until the run ends)")
    p.add_argument("--port", type=int, default=8765, help="local port with --tunnel (default 8765)")
    p.add_argument("--node-port", type=int, default=FOXGLOVE_NODE_PORT,
                   help=f"NodePort for the Service (default {FOXGLOVE_NODE_PORT}; 0 = any, "
                        "30000-32767)")
    p.add_argument("--wait", type=float, default=900.0, help="seconds to wait for the task to start")
    p.set_defaults(fn=cmd_watch)
    p = sub.add_parser("images", help="the images a run would fly, and whether the GPU node has them")
    p.set_defaults(fn=cmd_images)
    p = sub.add_parser("reindex", help="rebuild the manifest's runs from the evidence on disk")
    p.add_argument("campaign")
    p.set_defaults(fn=cmd_reindex)
    p = sub.add_parser("registry", help="the run registry (osmo/registry.py)")
    p.add_argument("action", choices=["sync"], help="sync: write runs/ into the registry")
    p.add_argument("campaign", nargs="?", help="campaign directory name (default: every one)")
    p.set_defaults(fn=cmd_registry)
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
