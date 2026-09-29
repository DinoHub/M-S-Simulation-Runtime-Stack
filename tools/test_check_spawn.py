"""tools/check_spawn.py: the no-floor-at-the-origin warning.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools -p 'test_check_spawn.py'
"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_spawn  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
XFS_ENV = {"id": "xfs-level", "version": "1.0.1"}


def spec(start: dict, env: dict | None = None, **extra) -> dict:
    return {"schema": "mns.scenario.v1", "environment": dict(env or XFS_ENV),
            "vehicles": [{"id": "drone_1", "start": start}], **extra}


class Check(unittest.TestCase):
    hints = check_spawn.load_hints()

    def warnings(self, doc: dict) -> list[str]:
        return check_spawn.check(doc, self.hints, "t")

    def test_the_shipped_hints_name_xfs(self):
        self.assertFalse(self.hints["xfs-level"]["origin_has_floor"])

    def test_origin_spawn_on_xfs_warns_with_the_known_start(self):
        (line,) = self.warnings(spec({"x": 0, "y": 0, "z": 0}))
        self.assertIn("no floor", line)
        self.assertIn("x: 423, y: -906, z: -21.0", line)

    def test_a_missing_start_is_the_origin(self):
        doc = spec({})
        del doc["vehicles"][0]["start"]
        self.assertEqual(len(self.warnings(doc)), 1)

    def test_origin_spawn_at_any_height_warns(self):
        self.assertEqual(len(self.warnings(spec({"x": 0.4, "y": -0.3, "z": -50}))), 1)

    def test_the_measured_start_is_quiet(self):
        self.assertEqual(self.warnings(spec({"x": 423, "y": -906, "z": -21.0})), [])

    def test_other_levels_are_quiet(self):
        self.assertEqual(self.warnings(spec({"x": 0, "y": 0, "z": 0},
                                            {"id": "warehouse", "version": "1.0.1"})), [])

    def test_an_unmeasured_version_is_quiet(self):
        self.assertEqual(self.warnings(spec({"x": 0, "y": 0, "z": 0},
                                            {"id": "xfs-level", "version": "9.0.0"})), [])

    def test_an_explicit_scenario_origin_is_quiet(self):
        origin = {"frame": "unreal_world", "unit": "centimeter",
                  "position": {"x": 42300, "y": -90600, "z": 1900},
                  "rotation": {"pitch": 0, "roll": 0, "yaw": 0}}
        doc = spec({"x": 0, "y": 0, "z": 0}, coordinate_frame={"origin": origin})
        self.assertEqual(self.warnings(doc), [])

    def test_an_identity_scenario_origin_still_warns(self):
        origin = {"position": {"x": 0, "y": 0, "z": 0}, "rotation": {"pitch": 0, "roll": 0, "yaw": 0}}
        doc = spec({"x": 0, "y": 0, "z": 0}, coordinate_frame={"origin": origin})
        self.assertEqual(len(self.warnings(doc)), 1)


class Files(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def write(self, rel: str, text: str) -> Path:
        path = self.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
        return path

    def run_main(self, *argv: str) -> tuple[int, str]:
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rc = check_spawn.main(list(argv))
        return rc, err.getvalue()

    def test_a_scenariolab_style_split_export_is_read(self):
        self.write("s/ScenarioSpec.yaml", """\
            schema: mns.scenario.v1
            includes:
              environment: Environment.yaml
              vehicles: Vehicles/
            """)
        self.write("s/Environment.yaml", """\
            environment: {id: xfs-level, version: 1.0.1}
            coordinate_frame: {convention: ros2_flu}
            """)
        self.write("s/Vehicles/DroneA.yaml", "id: DroneA\nstart: {frame: ros2_flu, x: 0, y: 0, z: 0.3}\n")
        rc, err = self.run_main(str(self.dir / "s"))
        self.assertEqual(rc, 0)
        self.assertIn("vehicle DroneA", err)

    def test_a_campaign_checks_its_base_scenario(self):
        self.write("c/ScenarioSpec.yaml", """\
            schema: mns.scenario.v1
            environment: {id: xfs-level, version: 1.0.1}
            vehicles: [{id: d1, start: {x: 0, y: 0, z: 0}}]
            """)
        campaign = self.write("c/CampaignSpec.yaml", "id: c\nscenario: ./ScenarioSpec.yaml\n")
        rc, err = self.run_main("campaign", "run", str(campaign), "--only", "calm-r1")
        self.assertEqual(rc, 0)
        self.assertIn("campaign c: vehicle d1", err)

    def test_a_variant_that_moves_to_xfs_is_checked(self):
        self.write("v/ScenarioSpec.yaml", """\
            schema: mns.scenario.v1
            environment: {id: warehouse, version: 1.0.1}
            vehicles: [{id: d1, start: {x: 0, y: 0, z: 0}}]
            """)
        self.write("v/CampaignSpec.yaml", """\
            id: v
            scenario: ./ScenarioSpec.yaml
            variants:
              - id: calm
              - id: outdoors
                overrides: {environment: {id: xfs-level, version: 1.0.1}}
            """)
        rc, err = self.run_main("campaign", "validate", str(self.dir / "v"))
        self.assertIn("variant outdoors", err)
        self.assertEqual(err.count("WARNING"), 1)

    def test_other_campaign_subcommands_and_bad_input_are_silent(self):
        self.assertEqual(self.run_main("campaign", "init", "x", "--from", "vio-reference"), (0, ""))
        self.assertEqual(self.run_main(str(self.dir / "missing")), (0, ""))
        self.write("bad/ScenarioSpec.yaml", "vehicles: [unclosed\n")
        self.assertEqual(self.run_main(str(self.dir / "bad")), (0, ""))

    def test_the_shipped_scenarios_are_quiet(self):
        for folder in sorted((ROOT / "scenarios").glob("vio-*")):
            if (folder / "ScenarioSpec.yaml").is_file():
                with self.subTest(folder.name):
                    self.assertEqual(self.run_main(str(folder)), (0, ""))
            if (folder / "CampaignSpec.yaml").is_file():
                with self.subTest(folder.name + " campaign"):
                    self.assertEqual(self.run_main("campaign", "run", str(folder)), (0, ""))


if __name__ == "__main__":
    unittest.main()
