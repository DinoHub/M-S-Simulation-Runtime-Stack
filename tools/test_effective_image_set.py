"""The shell/./.env image overrides reach the image set generated stacks run.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools -p 'test_effective_image_set.py'

Generated stacks (make fly, make campaign, the dashboard) take their images
from the image-set file only, so `MNS_RUNTIME_HOST_IMAGE=...` in ./.env used
to be printed as an override and then not run. tools/images.py
effective-image-set renders a copy of the selected file with the overrides
applied; these tests pin which variables count, what counts as an override,
and that nothing changes without one. They run against the real catalog.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import images  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEV_SET = ROOT / "images" / "image-set.development.generated.yaml"
PROD_SET = ROOT / "images" / "image-set.generated.yaml"
CHANNEL_ENV = ROOT / "images" / "v1.0.0.generated.env"


def _pins() -> dict[str, str]:
    return images._dotenv(CHANNEL_ENV)


class ImageSetSlots(unittest.TestCase):
    def test_the_channel_vars_that_generated_stacks_use(self):
        slots = images.image_set_slots(images.load_catalog(), "v1")
        self.assertEqual(slots, {
            "MNS_RUNTIME_HOST_IMAGE": [("simulators", "tevv_runtime_host")],
            "MNS_ROS2_BRIDGE_IMAGE": [("ros2_bridge",)],
        })


class EffectiveImageSet(unittest.TestCase):
    def run_cmd(self, src: Path, environ: dict[str, str], dotenv_text: str = ""):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        dotenv = tmp / ".env"
        dotenv.write_text(dotenv_text)
        out = tmp / "effective.yaml"
        args = images.argparse.Namespace(src=str(src), out=str(out), image_set="v1",
                                         dotenv=str(dotenv))
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, environ, clear=True), redirect_stdout(stdout), redirect_stderr(stderr):
            rc = images.cmd_effective_image_set(args)
        self.assertEqual(rc, 0)
        return Path(stdout.getvalue().strip()), stderr.getvalue(), out

    def test_no_override_uses_the_selected_file_as_it_is(self):
        path, err, out = self.run_cmd(DEV_SET, {})
        self.assertEqual(path, DEV_SET)
        self.assertEqual(err, "")
        self.assertFalse(out.exists())

    def test_the_pins_make_exports_are_not_overrides(self):
        """make exports the channel pins (tag@digest) into the shell before
        calling this, and the development set holds the bare tags."""
        for src in (DEV_SET, PROD_SET):
            path, err, _ = self.run_cmd(src, _pins())
            self.assertEqual((path, err), (src, ""), src)
        bare = {k: v.split("@", 1)[0] for k, v in _pins().items()}
        self.assertEqual(self.run_cmd(PROD_SET, {}, "".join(f"{k}={v}\n" for k, v in bare.items()))[0],
                         PROD_SET)

    def test_a_dotenv_host_override_reaches_the_image_set(self):
        path, err, out = self.run_cmd(DEV_SET, _pins() | {"MNS_RUNTIME_HOST_IMAGE": ""},
                                      "# local host\nMNS_RUNTIME_HOST_IMAGE='tevv-runtime-host:local-test'\n")
        self.assertEqual(path, out)
        doc = yaml.safe_load(out.read_text())
        want = yaml.safe_load(DEV_SET.read_text())
        want["image_sets"]["v1"]["images"]["simulators"]["tevv_runtime_host"] = "tevv-runtime-host:local-test"
        self.assertEqual(doc, want)  # only that slot changed
        self.assertIn("NOTE: MNS_RUNTIME_HOST_IMAGE from ./.env overrides the image set's "
                      "simulators.tevv_runtime_host", err)
        self.assertIn("generated stacks run tevv-runtime-host:local-test", err)

    def test_the_shell_wins_over_dotenv_and_the_bridge_is_covered(self):
        path, err, out = self.run_cmd(
            PROD_SET, {"MNS_ROS2_BRIDGE_IMAGE": "bridge:shell"}, "MNS_ROS2_BRIDGE_IMAGE=bridge:dotenv\n")
        self.assertEqual(path, out)
        self.assertEqual(yaml.safe_load(out.read_text())["image_sets"]["v1"]["images"]["ros2_bridge"],
                         "bridge:shell")
        self.assertIn("MNS_ROS2_BRIDGE_IMAGE from the environment", err)

    def test_images_sh_exposes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if not k.endswith("_IMAGE")}
            env["MNS_RUNTIME_HOST_IMAGE"] = "tevv-runtime-host:local-test"
            proc = subprocess.run(
                [str(ROOT / "tools" / "images.sh"), "effective-image-set", "--in", str(DEV_SET),
                 "--out", f"{tmp}/e.yaml", "--dotenv", f"{tmp}/none"],
                capture_output=True, text=True, env=env, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), f"{tmp}/e.yaml")
            self.assertIn("NOTE: MNS_RUNTIME_HOST_IMAGE", proc.stderr)


if __name__ == "__main__":
    unittest.main()
