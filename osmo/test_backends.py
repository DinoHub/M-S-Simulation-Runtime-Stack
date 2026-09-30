"""The Argo backend's translation into OSMO's vocabulary, and what it reads from a stack."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backends  # noqa: E402
import campaign  # noqa: E402


def container(name, *, running=False, waiting=None, exit_code=None):
    if running:
        state = {"running": {}}
    elif waiting:
        state = {"waiting": {"reason": waiting}}
    else:
        state = {"terminated": {"exitCode": exit_code}}
    return {"name": name, "state": state}


def workflow(phase, nodes, message=""):
    return {"metadata": {"creationTimestamp": "2026-09-30T04:09:40Z"},
            "status": {"phase": phase, "message": message,
                       "startedAt": "2026-09-30T04:09:44Z", "finishedAt": "2026-09-30T04:12:54Z",
                       "nodes": {n["id"]: n for n in nodes}}}


def node(id_, template, phase, started="2026-09-30T04:09:44Z"):
    return {"id": id_, "type": "Pod", "templateName": template, "phase": phase,
            "startedAt": started}


def fly_pod(node_id, statuses):
    return {"metadata": {"name": "fly-pod", "annotations": {"workflows.argoproj.io/node-id": node_id}},
            "spec": {"containers": [{"name": s["name"]} for s in statuses]},
            "status": {"containerStatuses": statuses}}


FLOWN = [container("wait", exit_code=0), container("main", exit_code=0),
         container("pilot", exit_code=0), container("sim", exit_code=143),
         container("bridge", exit_code=143), container("vio", exit_code=137)]


class Translate(unittest.TestCase):
    def test_a_flown_run_reads_as_osmo_would_report_it(self):
        wf = workflow("Succeeded", [node("f", "fly", "Succeeded"),
                                    node("v", "verdict", "Succeeded"),
                                    node("s", "spawn-eval", "Succeeded")])
        status, tasks = backends.translate(wf, {"fly-pod": fly_pod("f", FLOWN)})
        self.assertEqual(status, "COMPLETED")
        self.assertEqual(tasks["recorder"], "COMPLETED")
        self.assertEqual(tasks["pilot"], "COMPLETED")
        # Stopped by Argo when the recorder exited: the pod ending as designed.
        self.assertEqual(tasks["sim"], "COMPLETED")
        self.assertEqual(tasks["vio"], "COMPLETED")
        self.assertNotIn("wait", tasks)
        self.assertEqual(tasks["verdict"], "COMPLETED")

    def test_the_judge_accepts_it(self):
        wf = workflow("Succeeded", [node("f", "fly", "Succeeded")])
        status, tasks = backends.translate(wf, {"fly-pod": fly_pod("f", FLOWN)})
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(campaign.judge_flight(status, tasks, Path(d)), ("done", None))

    def test_a_crashed_sidecar_is_a_failed_task(self):
        statuses = FLOWN[:2] + [container("pilot", exit_code=42), container("sim", exit_code=139)]
        wf = workflow("Failed", [node("f", "fly", "Failed")])
        _, tasks = backends.translate(wf, {"fly-pod": fly_pod("f", statuses)})
        self.assertEqual(tasks["pilot"], "FAILED")
        self.assertEqual(tasks["sim"], "FAILED")

    def test_a_recorder_killed_is_a_failure_even_by_sigterm(self):
        statuses = [container("main", exit_code=143), container("pilot", exit_code=0)]
        _, tasks = backends.translate(workflow("Failed", [node("f", "fly", "Failed")]),
                                      {"fly-pod": fly_pod("f", statuses)})
        self.assertEqual(tasks["recorder"], "FAILED")

    def test_the_last_retry_of_fly_is_the_one_read(self):
        wf = workflow("Running", [node("f0", "fly", "Failed", "2026-09-30T04:00:00Z"),
                                  node("f1", "fly", "Running", "2026-09-30T04:05:00Z")])
        pods = {"a": fly_pod("f0", [container("main", exit_code=42)]),
                "b": fly_pod("f1", [container("main", running=True)])}
        status, tasks = backends.translate(wf, pods)
        self.assertEqual(status, "RUNNING")
        self.assertEqual(tasks["recorder"], "RUNNING")

    def test_an_image_that_cannot_be_pulled(self):
        statuses = [container("main", running=True), container("sim", waiting="ImagePullBackOff")]
        wf = workflow("Failed", [node("f", "fly", "Failed")])
        status, tasks = backends.translate(wf, {"fly-pod": fly_pod("f", statuses)})
        self.assertEqual(tasks["sim"], "FAILED_IMAGE_PULL")
        self.assertEqual(status, "FAILED_IMAGE_PULL")

    def test_a_stopped_workflow_is_cancelled(self):
        wf = workflow("Failed", [], message="Stopped with strategy 'Stop'")
        self.assertEqual(backends.translate(wf, {})[0], "CANCELLED")

    def test_a_workflow_that_errored_is_an_infra_failure(self):
        status, _ = backends.translate(workflow("Error", []), {})
        self.assertEqual(campaign.registry_outcome("failed", None, status, {}, None)[0],
                         "infra_failed")

    def test_without_the_pod_only_the_recorder_is_known(self):
        wf = workflow("Succeeded", [node("f", "fly", "Succeeded")])
        _, tasks = backends.translate(wf, {})
        self.assertEqual(tasks, {"recorder": "COMPLETED"})

    def test_times_come_from_argo(self):
        wf = workflow("Succeeded", [node("f", "fly", "Succeeded", "2026-09-30T04:09:50Z"),
                                    node("v", "vio-eval", "Succeeded", "2026-09-30T04:12:34Z"),
                                    node("x", "verdict", "Succeeded", "2026-09-30T04:12:44Z")])
        self.assertEqual(backends.workflow_times(wf), {
            "submitted": "2026-09-30T04:09:40Z", "started": "2026-09-30T04:09:50Z",
            "evaluating": "2026-09-30T04:12:34Z", "ended": "2026-09-30T04:12:54Z"})


class FromTheStack(unittest.TestCase):
    def stack(self, env: str, chain: str | None, estimator: bool) -> Path:
        d = Path(tempfile.mkdtemp())
        (d / ".env").write_text(env)
        vio = d / "config" / "vio"
        vio.mkdir(parents=True)
        if chain is not None:
            (vio / "kalibr_imucam_chain.yaml").write_text(chain)
        if estimator:
            (vio / "estimator_config.yaml").write_text("{}")
        return d

    def test_a_fisheye_stack_relays_its_iceoryx2_images(self):
        stack = self.stack("ENABLE_VIO=true\n",
                           "cam0:\n  rostopic: /fisheye_front/image_raw_reliable\n"
                           "cam1:\n  rostopic: /fisheye_back/image_raw_reliable\n", True)
        spec = {"extensions": {"mns.vio_estimator": {"launch_args": {"use_stereo": "false"}}}}
        self.assertEqual(backends.run_parameters(stack, spec, {}), {
            "enable_vio": "true", "estimator": "true",
            "relay_topics": "/fisheye_front/image_raw,/fisheye_back/image_raw",
            "vio_use_stereo": "false",
            "camera_info_topics": "/fisheye_front/camera_info,/fisheye_back/camera_info"})

    def test_a_pinhole_stereo_stack_needs_no_relay(self):
        # As the generator writes it: OpenCV YAML, which a YAML parser refuses.
        stack = self.stack("ENABLE_VIO=false\n",
                           "%YAML:1.0\n---\ncam0:\n  camera_model: pinhole\n"
                           "  rostopic: /camera/front/image_raw\n"
                           "cam1:\n  # a comment\n  rostopic: /camera/front_right/image_raw\n", True)
        params = backends.run_parameters(stack, {}, {})
        self.assertEqual(params["enable_vio"], "false")
        self.assertEqual(params["relay_topics"], "")
        self.assertEqual(params["vio_use_stereo"], "true")
        self.assertEqual(params["camera_info_topics"],
                         "/camera/front/camera_info,/camera/front_right/camera_info")

    def test_no_estimator_takes_camera_info_from_the_recording_list(self):
        stack = self.stack("", None, False)
        campaign_doc = {"recording": {"topics": ["/imu/data", "/camera/front/camera_info"]}}
        params = backends.run_parameters(stack, {}, campaign_doc)
        self.assertEqual(params["estimator"], "false")
        self.assertEqual(params["camera_info_topics"], "/camera/front/camera_info")

    def test_a_route_is_never_cut_short_by_the_runners_tail(self):
        self.assertEqual(backends.record_cap({"mission": {"timeout_s": 10}}, "e30="), 900)
        self.assertEqual(backends.record_cap({"mission": {"timeout_s": 1200}}, "e30="), 1200)
        self.assertEqual(backends.record_cap({"mission": {"timeout_s": 120}}, ""), 120)


class Submit(unittest.TestCase):
    IMAGES = {"runtime_host_image": "host:1", "bridge_image": "bridge:1", "px4_image": "px4:1",
              "ardupilot_image": "ap:1", "vio_estimator_image": "ov:1", "sim_real_eval_image": "sre:1"}

    def params(self, autopilot: str) -> dict[str, str]:
        stack = campaign.WORKSPACE_HOST / "generated" / "campaigns" / "c" / "stacks" / "k"
        bundle = campaign.WORKSPACE_HOST / "generated" / "campaigns" / "c" / "runs" / "k"
        return campaign.ArgoRun().submit_params(
            stack_dir=stack, bundle=bundle, spec={},
            campaign={"mission": {"autopilot": autopilot, "timeout_s": 10}},
            run_gates={"ate_rmse_m": {"max": 1.0}}, route_b64="e30=", images=self.IMAGES,
            vehicle="Drone1")

    def test_the_autopilot_picks_its_image(self):
        self.assertEqual(self.params("px4")["autopilot_image"], "px4:1")
        self.assertEqual(self.params("ardupilot")["autopilot_image"], "ap:1")

    def test_paths_are_the_nodes_view(self):
        p = self.params("px4")
        self.assertEqual(p["stack_dir"], "/workspace/generated/campaigns/c/stacks/k")
        self.assertEqual(p["export_dir"], "/workspace/generated/campaigns/c/runs/k")
        self.assertEqual(p["gates_json"], '{"ate_rmse_m": {"max": 1.0}}')
        self.assertEqual(p["record_sec"], "900")

    def test_every_parameter_is_one_the_template_declares(self):
        import yaml
        doc = yaml.safe_load(backends.TEMPLATE.read_text())
        declared = {p["name"] for p in doc["spec"]["arguments"]["parameters"]}
        self.assertLessEqual(set(self.params("px4")), declared)

    def test_both_backends_offer_the_same_calls(self):
        for cls in campaign.BACKENDS.values():
            for call in ("prepare", "submit_run", "query", "times", "fetch", "logs"):
                self.assertTrue(callable(getattr(cls, call)), f"{cls.__name__}.{call}")


if __name__ == "__main__":
    unittest.main()
