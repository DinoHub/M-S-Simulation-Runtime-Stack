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
    """WORKSPACE_HOST comes from the kind cluster config, a path on the
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
        with mock.patch.dict(campaign.os.environ, {}, clear=False):
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
                "--out", str(stack.resolve())))

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


if __name__ == "__main__":
    unittest.main()
