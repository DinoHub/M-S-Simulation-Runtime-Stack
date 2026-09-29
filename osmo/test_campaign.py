"""Exit-code folding and path mapping in osmo/campaign.py."""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

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


class ContainerPath(unittest.TestCase):
    def test_a_repo_path_maps_under_workspace(self):
        path = campaign.WORKSPACE_HOST / "scenarios" / "x"
        self.assertEqual(campaign.container_path(path), "/workspace/scenarios/x")

    def test_a_path_outside_the_repo_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()) as err, \
                self.assertRaises(SystemExit) as exit_:
            campaign.container_path(Path("/definitely/not/the/repo"))
        self.assertEqual(exit_.exception.code, 2)
        self.assertIn("outside", err.getvalue())


class CpuPlatformProblem(unittest.TestCase):
    """The evaluate and aggregate groups need platform cpu with a node behind it."""

    def run_with(self, returncode: int, stdout: str, stderr: str = ""):
        import subprocess
        from unittest import mock
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


if __name__ == "__main__":
    unittest.main()
