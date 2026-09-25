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


if __name__ == "__main__":
    unittest.main()
