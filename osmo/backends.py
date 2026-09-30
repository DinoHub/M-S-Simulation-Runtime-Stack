"""Where a campaign's runs execute: the part of osmo/campaign.py that talks to a cluster.

    osmo/campaign.py run vio-reference --backend argo --only calm-r1

campaign.py does everything else -- materialise, generate, images, verdict, the run
registry, the manifest, the campaign-level evaluator -- the same way for every backend.
A backend does five things with one run, in OSMO's vocabulary, because that is what the
registry and the scorecard already read:

    submit(...)            -> a workflow reference
    query(ref)             -> (workflow status, {task: status}), e.g. ("COMPLETED",
                              {"recorder": "COMPLETED", "pilot": "COMPLETED", ...})
    times(ref)             -> {"submitted", "started", "evaluating", "ended"}, UTC ISO
    fetch(ref, bundle)     -> the evidence in runs/<key>/: bag/, mission.json, eval/<step>/
    logs(ref, tasks, bundle, since) -> every task's output, kept with a run that did not pass

OsmoBackend is in campaign.py, around the functions it always had. ArgoBackend is here:
one Argo Workflow per run from the WorkflowTemplate in argo/workflows/, the whole
real-time group in one pod (docs/design/2026-09-30-campaigns-openshift-argo.md).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable

import yaml

ROOT = Path(__file__).resolve().parents[1]
ARGO_DIR = ROOT / "argo"
TEMPLATE = ARGO_DIR / "workflows" / "tevv-campaign-run.yaml"
TEMPLATE_NAME = "tevv-campaign-run"
FILES_CONFIGMAP = "tevv-run-files"

# Argo's workflow phase -> OSMO's workflow status, which registry_outcome reads.
WORKFLOW_STATUS = {
    "": "PENDING",
    "Pending": "PENDING",
    "Running": "RUNNING",
    "Succeeded": "COMPLETED",
    "Failed": "FAILED",
    "Error": "FAILED_SERVER_ERROR",
}
TERMINAL = {"COMPLETED", "FAILED", "FAILED_SERVER_ERROR", "FAILED_IMAGE_PULL", "CANCELLED"}
EVAL_STEPS = ("vio-eval", "spawn-eval", "validate", "verdict")
# A sidecar Argo stops once the recorder exits ends on SIGTERM (143) or, past the
# grace period, SIGKILL (137). Either is the pod ending as designed, not a failure.
STOPPED = {0, 137, 143}
IMAGE_PULL = ("ErrImagePull", "ImagePullBackOff", "InvalidImageName")


def _run(argv: list[str], *, check: bool = True, input_: str | None = None,
         quiet: bool = False) -> subprocess.CompletedProcess:
    if not quiet:
        print("[campaign] $", shlex.join(argv if len(" ".join(argv)) < 400 else argv[:6] + ["..."]),
              flush=True)
    proc = subprocess.run(argv, capture_output=True, text=True, input=input_)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{argv[0]} {argv[1] if len(argv) > 1 else ''} failed: "
                           f"{(proc.stderr or proc.stdout).strip()[:400]}")
    return proc


# --------------------------------------------------------------------------
# What a run needs, read from the generated stack
# --------------------------------------------------------------------------
#
# The template's parameters describe the pod: whether the fisheye images go over
# iceoryx2, whether an estimator runs, which image topics it needs relayed. All of it
# is already decided by the generator for this very ScenarioSpec, so it is read back
# from the stack, never restated in the CampaignSpec or here.

def stack_env(stack: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    path = stack / ".env"
    if path.is_file():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.lstrip().startswith("#"):
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def estimator_topics(stack: Path) -> list[str]:
    """The image topics the stack's estimator subscribes to, in camera order.

    Read line by line: the chain is OpenCV YAML (a `%YAML:1.0` header), which a YAML
    parser rejects, and each camera's `rostopic:` is all this needs from it."""
    path = stack / "config" / "vio" / "kalibr_imucam_chain.yaml"
    if not path.is_file():
        return []
    return re.findall(r"^\s+rostopic:\s*(\S+)", path.read_text(), flags=re.M)


def run_parameters(stack: Path, spec: dict[str, Any], campaign: dict[str, Any]) -> dict[str, str]:
    """The template parameters this run's stack implies."""
    env = stack_env(stack)
    estimator = (stack / "config" / "vio" / "estimator_config.yaml").is_file()
    topics = estimator_topics(stack)
    # A topic the estimator reads as <topic>_reliable is one the relay must make:
    # the bridge publishes iceoryx2 images best-effort and OpenVINS subscribes reliable.
    relay = [t[: -len("_reliable")] for t in topics if t.endswith("_reliable")]
    cameras = [t[: -len("_reliable")] if t.endswith("_reliable") else t for t in topics]
    camera_info = [c.rsplit("/", 1)[0] + "/camera_info" for c in cameras]
    if not camera_info:
        camera_info = [t for t in ((campaign.get("recording") or {}).get("topics") or [])
                       if str(t).endswith("/camera_info")]
    extension = (spec.get("extensions") or {}).get("mns.vio_estimator") or {}
    launch = extension.get("launch_args") or {}
    params = {
        "enable_vio": "true" if env.get("ENABLE_VIO", "false").lower() == "true" else "false",
        "estimator": "true" if estimator else "false",
        "relay_topics": ",".join(relay),
        "vio_use_stereo": str(launch.get("use_stereo", "true")).lower(),
    }
    if camera_info:
        params["camera_info_topics"] = ",".join(dict.fromkeys(camera_info))
    return params


def record_cap(campaign: dict[str, Any], route_b64: str) -> int:
    """RECORD_SEC: how long the recorder waits for the pilot's done before it gives up.

    The bag closes a few seconds after the pilot announces the end of the flight, so
    this is a cap, not the flight's length. `mission.timeout_s` is the runner's tail
    after a flight (10 s in vio-reference); as a cap it would close the bag before
    takeoff. A declared route gets at least 15 minutes."""
    timeout = int((campaign.get("mission") or {}).get("timeout_s", 300))
    return max(timeout, 900) if route_b64 else timeout


# --------------------------------------------------------------------------
# Argo
# --------------------------------------------------------------------------

class ArgoBackend:
    name = "argo"

    def __init__(self, container_path: Callable[[Path], str], *,
                 namespace: str | None = None, argo: str | None = None) -> None:
        self.container_path = container_path
        self.ns = namespace or os.environ.get("TEVV_ARGO_NAMESPACE", "tevv-argo")
        self.argo = argo or os.environ.get("ARGO", "argo")
        self._prepared = False

    @property
    def workflow(self) -> str:
        return str(TEMPLATE)

    def _kubectl(self, *args: str, check: bool = True, input_: str | None = None,
                 quiet: bool = False) -> subprocess.CompletedProcess:
        return _run(["kubectl", "-n", self.ns, *args], check=check, input_=input_, quiet=quiet)

    def prepare(self) -> None:
        """The per-task scripts as a ConfigMap, and the WorkflowTemplate, from this checkout.
        Once per invocation: every run of it flies the same scripts."""
        if self._prepared:
            return
        cm = _run(["kubectl", "-n", self.ns, "create", "configmap", FILES_CONFIGMAP,
                   f"--from-file={ROOT / 'osmo' / 'files'}", f"--from-file={ARGO_DIR / 'files'}",
                   "--dry-run=client", "-o", "yaml"]).stdout
        self._kubectl("apply", "-f", "-", input_=cm)
        self._kubectl("apply", "-f", str(TEMPLATE))
        self._prepared = True

    def submit_params(self, *, stack_dir: Path, bundle: Path, spec: dict[str, Any],
                      campaign: dict[str, Any], run_gates: dict[str, Any], route_b64: str,
                      images: dict[str, str], vehicle: str | None) -> dict[str, str]:
        mission = campaign.get("mission") or {}
        inputs = (campaign.get("evaluation") or {}).get("inputs") or {}
        autopilot = str(mission.get("autopilot", "px4"))
        image_key = "px4_image" if autopilot == "px4" else "ardupilot_image"
        params = {
            "stack_dir": self.container_path(stack_dir),
            "export_dir": self.container_path(bundle),
            "fly": "true",
            "autopilot": autopilot,
            "vehicle": vehicle or "Drone1",
            "route_b64": route_b64,
            "record_sec": str(record_cap(campaign, route_b64)),
            "est_topic": str(inputs.get("est_topic", "/ov_msckf/odomimu")),
            "gates_json": json.dumps(run_gates),
            **run_parameters(stack_dir, spec, campaign),
            "runtime_host_image": images["runtime_host_image"],
            "bridge_image": images["bridge_image"],
            "autopilot_image": images[image_key],
            "vio_estimator_image": images["vio_estimator_image"],
            "sim_real_eval_image": images["sim_real_eval_image"],
        }
        return params

    def submit(self, params: dict[str, str], labels: dict[str, str]) -> str:
        self.prepare()
        argv = [self.argo, "submit", "-n", self.ns, "--from", f"workflowtemplate/{TEMPLATE_NAME}",
                "-o", "name", "--labels", ",".join(f"{k}={_label(v)}" for k, v in labels.items())]
        for key, value in params.items():
            argv += ["-p", f"{key}={value}"]
        name = _run(argv).stdout.strip().rsplit("/", 1)[-1]
        if not name:
            raise RuntimeError("argo submit printed no workflow name")
        return name

    def _workflow(self, ref: str) -> dict[str, Any] | None:
        proc = self._kubectl("get", "workflow", ref, "-o", "json", check=False, quiet=True)
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout)

    def _pods(self, ref: str) -> dict[str, dict[str, Any]]:
        proc = self._kubectl("get", "pods", "-l", f"workflows.argoproj.io/workflow={ref}",
                             "-o", "json", check=False, quiet=True)
        if proc.returncode != 0:
            return {}
        return {p["metadata"]["name"]: p for p in json.loads(proc.stdout).get("items", [])}

    def query(self, ref: str) -> tuple[str, dict[str, str]]:
        wf = self._workflow(ref)
        if wf is None:
            return "FAILED_SERVER_ERROR", {}
        return translate(wf, self._pods(ref))

    def times(self, ref: str) -> dict[str, str | None]:
        wf = self._workflow(ref)
        return workflow_times(wf) if wf else {}

    def fetch(self, ref: str, bundle: Path) -> None:
        """Nothing to download: the workflow's exit handler copied the evidence into
        runs/<key>/ through the node's /workspace. Only a missing copy is reported."""
        if not (bundle / "bag").is_dir():
            print(f"[campaign]   {ref}: no bag in {bundle} (the export step found none, or failed)")

    def logs(self, ref: str, tasks: dict[str, str], bundle: Path, since: str | None = None) -> None:
        """Every container's output, from the pods: a run that did not pass keeps its
        pods (podGC deletes only a success's)."""
        out = bundle / "logs" / ref
        out.mkdir(parents=True, exist_ok=True)
        for pod_name, pod in self._pods(ref).items():
            step = (pod["metadata"].get("annotations") or {}).get(
                "workflows.argoproj.io/node-name", pod_name).rsplit(".", 1)[-1]
            for c in pod["spec"].get("containers", []):
                if c["name"] == "wait":
                    continue
                proc = self._kubectl("logs", pod_name, "-c", c["name"], check=False, quiet=True)
                if proc.stdout.strip():
                    (out / f"{_file(step)}.{c['name']}.log").write_text(proc.stdout)
        print(f"[campaign]   container logs kept in {out}")


def _label(value: str) -> str:
    """A Kubernetes label value: 63 characters of [A-Za-z0-9._-], alphanumeric at both ends."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", str(value))[:63].strip("-._") or "none"


def _file(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def _container_state(status: dict[str, Any], main: bool) -> str:
    state = status.get("state") or {}
    if "running" in state:
        return "RUNNING"
    if "waiting" in state:
        reason = (state["waiting"] or {}).get("reason", "")
        return "FAILED_IMAGE_PULL" if reason in IMAGE_PULL else "INITIALIZING"
    term = state.get("terminated") or {}
    code = term.get("exitCode")
    if code == 0 or (not main and code in STOPPED):
        return "COMPLETED"
    return "FAILED"


def _last_attempt(nodes: list[dict[str, Any]], template: str) -> dict[str, Any] | None:
    pods = [n for n in nodes if n.get("type") == "Pod" and n.get("templateName") == template]
    return max(pods, key=lambda n: n.get("startedAt") or "") if pods else None


def translate(wf: dict[str, Any], pods: dict[str, dict[str, Any]]) -> tuple[str, dict[str, str]]:
    """An Argo Workflow in OSMO's words: its status, and one task per fly-pod container
    (recorder, sim, bridge, autopilot, vio, relay, pilot, ...) plus one per evaluate
    step. judge_flight and registry_outcome read these unchanged."""
    status = wf.get("status") or {}
    phase = status.get("phase", "")
    message = status.get("message", "") or ""
    wf_status = WORKFLOW_STATUS.get(phase, "RUNNING")
    if wf_status.startswith("FAILED") and "Stopped with strategy" in message:
        wf_status = "CANCELLED"
    nodes = list((status.get("nodes") or {}).values())
    tasks: dict[str, str] = {}

    fly = _last_attempt(nodes, "fly")
    pod = None
    if fly is not None:
        pod = next((p for p in pods.values()
                    if (p["metadata"].get("annotations") or {}).get("workflows.argoproj.io/node-id")
                    == fly.get("id")), None)
    if pod is not None:
        for c in (pod.get("status") or {}).get("containerStatuses") or []:
            if c["name"] == "wait":
                continue
            # Argo names the template's container `main`; it is the recorder.
            main = c["name"] == "main"
            tasks["recorder" if main else c["name"]] = _container_state(c, main)
    elif fly is not None:
        # The pod is gone (a success, after podGC's delay): the node's phase is all
        # there is, and it says nothing about the pilot.
        tasks["recorder"] = {"Succeeded": "COMPLETED", "Failed": "FAILED",
                             "Error": "FAILED"}.get(fly.get("phase", ""), "RUNNING")
    for step in EVAL_STEPS:
        node = _last_attempt(nodes, step)
        if node is None:
            continue
        tasks[step] = {"Succeeded": "COMPLETED", "Failed": "FAILED", "Error": "FAILED",
                       "Running": "RUNNING", "Pending": "INITIALIZING"}.get(node.get("phase", ""), "RUNNING")
    if any(s == "FAILED_IMAGE_PULL" for s in tasks.values()) and wf_status.startswith("FAILED"):
        wf_status = "FAILED_IMAGE_PULL"
    return wf_status, tasks


def workflow_times(wf: dict[str, Any]) -> dict[str, str | None]:
    """Argo's own clock for a workflow, as campaign.workflow_times gives OSMO's."""
    status = wf.get("status") or {}
    nodes = list((status.get("nodes") or {}).values())
    fly = _last_attempt(nodes, "fly")
    evals = [n.get("startedAt") for n in nodes
             if n.get("templateName") in EVAL_STEPS and n.get("startedAt")]
    return {"submitted": (wf.get("metadata") or {}).get("creationTimestamp"),
            "started": (fly or {}).get("startedAt") or status.get("startedAt"),
            "evaluating": min(evals) if evals else None,
            "ended": status.get("finishedAt")}
