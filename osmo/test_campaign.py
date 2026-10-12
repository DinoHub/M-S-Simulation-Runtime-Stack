"""Exit-code folding, host paths and the mns-stacks calls in osmo/campaign.py.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s osmo -p 'test_campaign.py'
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign  # noqa: E402


class WorseRc(unittest.TestCase):
    def test_pass_then_gate_failure_is_gate_failure(self):
        self.assertEqual(campaign.worse_rc(0, 1), 1)

    def test_evaluator_error_outranks_an_earlier_gate_failure(self):
        self.assertEqual(campaign.worse_rc(1, 2), 2)

    def test_first_evaluator_error_is_kept(self):
        self.assertEqual(campaign.worse_rc(2, 1), 2)
        self.assertEqual(campaign.worse_rc(2, 5), 2)

    def test_a_pass_changes_nothing(self):
        self.assertEqual(campaign.worse_rc(1, 0), 1)
        self.assertEqual(campaign.worse_rc(0, 0), 0)


class InAWorkspace(unittest.TestCase):
    """WORKSPACE_HOST comes from the running cluster (or its rendered config), a path on the
    machine the cluster runs on; the tests use a temporary one."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.workspace = Path(tmp.name).resolve()
        for name, value in (("WORKSPACE_HOST", self.workspace),
                            ("CAMPAIGNS_HOST", self.workspace / "generated" / "campaigns"),
                            ("STACKS_SH", self.workspace / "tools" / "mns-stacks.sh")):
            patcher = mock.patch.object(campaign, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class WorkspacePath(InAWorkspace):
    """mns-stacks sees the checkout at its own host path: no translation."""

    def test_a_repo_path_is_passed_as_the_host_path(self):
        path = campaign.WORKSPACE_HOST / "scenarios" / "x"
        self.assertEqual(campaign.workspace_path(path), str(path.resolve()))

    def test_a_path_outside_the_repo_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()) as err, \
                self.assertRaises(SystemExit) as exit_:
            campaign.workspace_path(Path("/definitely/not/the/repo"))
        self.assertEqual(exit_.exception.code, 2)
        self.assertIn("outside", err.getvalue())


def _done(stdout: str = "", code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, stdout, "")


class StacksCalls(InAWorkspace):
    """plan, generate and status go through tools/mns-stacks.sh."""

    def test_stacks_cli_runs_the_wrapper_with_the_pinned_image(self):
        with mock.patch.object(campaign, "sh", return_value=_done()) as sh, \
                mock.patch.dict(campaign.os.environ, {"MNS_STACKS_IMAGE": "local/mns-stacks:dev"}):
            campaign.stacks_cli("campaign", "status", "x")
        argv, kwargs = sh.call_args.args[0], sh.call_args.kwargs
        self.assertEqual(argv, [str(campaign.STACKS_SH), "campaign", "status", "x"])
        self.assertEqual(kwargs["env"]["MNS_STACKS_IMAGE"], "local/mns-stacks:dev")
        self.assertEqual(kwargs["cwd"], campaign.WORKSPACE_HOST)

    def test_the_image_defaults_to_the_channel_pin(self):
        # No ./.env: a local override in the checkout running the tests must
        # not change what "the pin" is.
        with mock.patch.object(campaign, "DOTENV_FILE", self.workspace / ".env"), \
                mock.patch.dict(campaign.os.environ, {}, clear=False):
            campaign.os.environ.pop("MNS_STACKS_IMAGE", None)
            image = campaign.stacks_image()
        pinned = [line.split("=", 1)[1] for line in
                  campaign.CHANNEL_ENV_FILE.read_text().splitlines()
                  if line.startswith("MNS_STACKS_IMAGE=")]
        self.assertEqual([image], pinned)
        self.assertIn("mns-stacks", image)

    def test_the_image_precedence_is_shell_then_dotenv_then_the_pin(self):
        dotenv = self.workspace / ".env"
        with mock.patch.object(campaign, "DOTENV_FILE", dotenv), \
                mock.patch.dict(campaign.os.environ, {}, clear=False):
            campaign.os.environ.pop("MNS_STACKS_IMAGE", None)
            pinned = campaign.stacks_image()
            dotenv.write_text('MNS_STACKS_IMAGE="mns-stacks:local-test"\n')
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(campaign.stacks_image(), "mns-stacks:local-test")
            self.assertIn("overrides the catalog pin", err.getvalue())
            campaign.os.environ["MNS_STACKS_IMAGE"] = "shell/mns-stacks:x"
            self.assertEqual(campaign.stacks_image(), "shell/mns-stacks:x")
        self.assertIn("mns-stacks", pinned)

    def test_plan_reads_the_json_run_list(self):
        spec = self.workspace / "generated" / "campaigns" / "c" / "calm-r1" / "ScenarioSpec.yaml"
        envelope = {"schema": "mns.stacks_cli.v1", "command": "campaign plan", "ok": True,
                    "exit_code": 0, "warnings": [], "error": None,
                    "result": {"campaign_id": "c", "dry_run": True, "runs": [
                        {"run_key": "calm-r1", "variant": "calm", "seed": None, "repeat": 1,
                         "spec": str(spec), "stack": "x", "bag_dir": "y", "command": []}]}}
        campaign_file = self.workspace / "scenarios" / "vio-reference" / "CampaignSpec.yaml"
        with mock.patch.object(campaign, "stacks_cli",
                               return_value=_done(json.dumps(envelope))) as cli:
            runs = campaign.materialise(campaign_file, campaign.CAMPAIGNS_HOST, ["calm-r1", "wind-r1"])
        self.assertEqual(runs, [("calm-r1", spec)])
        self.assertEqual(cli.call_args.args, (
            "campaign", "plan", str(campaign_file.resolve()),
            "--out", str(campaign.CAMPAIGNS_HOST.resolve()), "--only", "calm-r1", "wind-r1",
            "--json"))

    def test_a_failed_plan_exits_with_the_cli_message(self):
        envelope = {"schema": "mns.stacks_cli.v1", "command": "campaign plan", "ok": False,
                    "exit_code": 2, "result": None, "warnings": [],
                    "error": {"type": "StacksError", "message": "no CampaignSpec"}}
        with mock.patch.object(campaign, "stacks_cli", return_value=_done(json.dumps(envelope), 2)), \
                contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaisesRegex(SystemExit, "no CampaignSpec"):
            campaign.materialise(self.workspace / "x.yaml", campaign.CAMPAIGNS_HOST, [])

    def test_generate_writes_the_stack_and_starts_nothing(self):
        with tempfile.TemporaryDirectory(dir=self.workspace) as tmp:
            spec = Path(tmp) / "run" / "ScenarioSpec.yaml"
            stack = Path(tmp) / "stack"

            def fake(*args):
                stack.mkdir()
                (stack / "docker-compose.yml").write_text("services: {}\n")
                return _done()

            with mock.patch.object(campaign, "stacks_cli", side_effect=fake) as cli:
                campaign.generate_stack(spec, stack)
            self.assertEqual(cli.call_args.args, (
                "generate", str(spec.parent.resolve()), "--profile", "docker",
                "--no-topics", "--out", str(stack.resolve())))

    def test_a_generation_without_a_compose_file_fails(self):
        with tempfile.TemporaryDirectory(dir=self.workspace) as tmp, \
                mock.patch.object(campaign, "stacks_cli", return_value=_done("", 1)), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "stack generation failed"):
                campaign.generate_stack(Path(tmp) / "ScenarioSpec.yaml", Path(tmp) / "stack")

    def test_status_json_prints_the_result_out_of_the_envelope(self):
        card = {"campaign": "vio-reference", "runs": [{"run_key": "calm-r1", "status": "done"}]}
        envelope = {"schema": "mns.stacks_cli.v1", "command": "campaign status", "ok": True,
                    "exit_code": 0, "result": card, "warnings": [], "error": None}
        args = argparse.Namespace(campaign="vio-reference", json=True)
        with mock.patch.object(campaign, "stacks_cli", return_value=_done(json.dumps(envelope))) as cli, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = campaign.cmd_status(args)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), card)
        self.assertEqual(cli.call_args.args[:2], ("campaign", "status"))
        self.assertTrue(cli.call_args.args[2].endswith("campaign_manifest.json"))
        self.assertEqual(cli.call_args.args[3:], ("--json",))

    def test_status_json_keeps_a_blocking_exit_code(self):
        envelope = {"schema": "mns.stacks_cli.v1", "command": "campaign status", "ok": False,
                    "exit_code": 1, "result": {"runs": []}, "warnings": ["w"],
                    "error": {"type": "blocked", "message": "a gate failed"}}
        args = argparse.Namespace(campaign="vio-reference", json=True)
        with mock.patch.object(campaign, "stacks_cli", return_value=_done(json.dumps(envelope), 1)), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(campaign.cmd_status(args), 1)
        self.assertIn("a gate failed", err.getvalue())


class WorkspaceHost(unittest.TestCase):
    """Where the cluster's /workspace is: never a path typed into the repo."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name).resolve()
        env = mock.patch.dict(campaign.os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        campaign.os.environ.pop("MNS_OSMO_WORKSPACE", None)

    def no_cluster(self):
        return mock.patch.object(campaign, "_cluster_workspace_mount", return_value=None)

    def test_the_committed_template_names_no_host_path(self):
        text = (campaign.ROOT / "osmo" / "kind-osmo-cluster-config.gpu.yaml.tmpl").read_text()
        self.assertIn("hostPath: @MNS_OSMO_WORKSPACE@\n", text)
        self.assertNotIn("/home/", text)

    def test_the_environment_wins(self):
        campaign.os.environ["MNS_OSMO_WORKSPACE"] = str(self.dir)
        self.assertEqual(campaign.workspace_host(), (self.dir, "MNS_OSMO_WORKSPACE"))

    def test_the_running_cluster_is_read_back(self):
        mounts = json.dumps([{"Source": "/dev/null", "Destination": "/var/run/nvidia-container-devices/all"},
                             {"Source": str(self.dir), "Destination": "/workspace"}])
        answers = iter([_done("osmo-control-plane\nosmo-worker2\n"), _done("[]"), _done(mounts)])
        with mock.patch.object(campaign.subprocess, "run", side_effect=lambda *a, **k: next(answers)):
            host, source = campaign.workspace_host()
        self.assertEqual(host, self.dir)
        self.assertIn("running kind cluster", source)

    def test_no_docker_is_no_cluster(self):
        with mock.patch.object(campaign.subprocess, "run", side_effect=FileNotFoundError("docker")):
            self.assertIsNone(campaign._cluster_workspace_mount())

    def test_then_the_rendered_config_then_this_checkout(self):
        rendered = self.dir / "kind.yaml"
        rendered.write_text("nodes:\n- role: worker\n  extraMounts:\n"
                            "  - {hostPath: /srv/checkout, containerPath: /workspace}\n")
        with self.no_cluster(), mock.patch.object(campaign, "RENDERED_KIND_CONFIG", rendered), \
                mock.patch.object(campaign, "ROOT", self.dir):
            self.assertEqual(campaign.workspace_host()[0], Path("/srv/checkout"))
        with self.no_cluster(), mock.patch.object(campaign, "RENDERED_KIND_CONFIG", self.dir / "none"):
            self.assertEqual(campaign.workspace_host()[0], campaign.ROOT)

    def test_an_unusable_workspace_is_a_message_not_a_traceback(self):
        with mock.patch.object(campaign, "WORKSPACE_HOST", self.dir / "M-S-Simulation-Runtime-Stack"), \
                mock.patch.object(campaign, "WORKSPACE_SOURCE", "test"), \
                contextlib.redirect_stderr(io.StringIO()) as err, \
                self.assertRaises(SystemExit) as exit_:
            campaign.main(["run", "vio-osmo-condo"])
        self.assertEqual(exit_.exception.code, 2)
        message = err.getvalue()
        self.assertIn("does not exist on this machine", message)
        self.assertIn("MNS_OSMO_WORKSPACE", message)

    def test_a_permission_error_is_a_message_not_a_traceback(self):
        (self.dir / "tools").mkdir()
        (self.dir / "tools" / "mns-stacks.sh").write_text("")
        denied = PermissionError(13, "Permission denied", str(self.dir / "generated"))
        with mock.patch.object(campaign, "WORKSPACE_HOST", self.dir), \
                mock.patch.object(campaign, "cmd_run", side_effect=denied), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(campaign.main(["run", "x"]), 2)
        self.assertIn("Permission denied", err.getvalue())

    def test_a_failed_kubectl_is_exit_42_with_a_hint(self):
        (self.dir / "tools").mkdir()
        (self.dir / "tools" / "mns-stacks.sh").write_text("")
        failed = subprocess.CalledProcessError(1, ["kubectl", "get", "node"], "",
                                               'Error from server (NotFound): nodes "osmo-worker" not found')
        with mock.patch.object(campaign, "WORKSPACE_HOST", self.dir), \
                mock.patch.object(campaign, "cmd_run", side_effect=failed), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(campaign.main(["run", "x"]), 42)
        self.assertIn("NotFound", err.getvalue())
        self.assertIn("setup-local-osmo.sh", err.getvalue())


class CpuPlatformProblem(unittest.TestCase):
    """The evaluate and aggregate groups need platform cpu with a node behind it."""

    def run_with(self, returncode: int, stdout: str, stderr: str = ""):
        done = subprocess.CompletedProcess([], returncode, stdout, stderr)
        with mock.patch.object(campaign.subprocess, "run", return_value=done):
            return campaign.cpu_platform_problem()

    def test_a_listed_node_is_no_problem(self):
        self.assertIsNone(self.run_with(0, '{"resources": [{"hostname": "osmo-worker"}]}'))

    def test_no_node_names_the_setup_script(self):
        problem = self.run_with(0, '{"resources": []}')
        self.assertIn("osmo/cpu-platform.sh", problem)

    def test_a_failing_cli_names_the_setup_script(self):
        problem = self.run_with(1, "", "not logged in")
        self.assertIn("not logged in", problem)
        self.assertIn("osmo/cpu-platform.sh", problem)


class CameraInfoTopics(unittest.TestCase):
    """The recorder subscribes by name to what the stack's bridge will publish."""

    def run_with(self, returncode: int, stdout: str, stderr: str = ""):
        import subprocess
        from unittest import mock
        done = subprocess.CompletedProcess([], returncode, stdout, stderr)
        with mock.patch.object(campaign.subprocess, "run", return_value=done), \
                contextlib.redirect_stdout(io.StringIO()):
            return campaign.camera_info_topics(Path("/stack"))

    def test_only_camera_info_topics_of_every_bridge(self):
        out = ('[{"topics": [{"topic": "/camera/front/camera_info"}, {"topic": "/imu/data"},'
               ' {"topic": "/fisheye_front_Scene/camera_info"}]},'
               ' {"topics": [{"topic": "/camera/front/camera_info"}]}]')
        self.assertEqual(self.run_with(0, out),
                         ["/camera/front/camera_info", "/fisheye_front_Scene/camera_info"])

    def test_a_failed_preview_is_an_empty_list(self):
        self.assertEqual(self.run_with(1, "", "no compose file"), [])
        self.assertEqual(self.run_with(0, "not json"), [])


if __name__ == "__main__":
    unittest.main()
