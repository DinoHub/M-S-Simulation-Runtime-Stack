"""Offline checks for tools/install_demo_packs.py: lock/contract agreement,
per-pack release URLs, and the --check / --missing state report against a
store. Nothing here touches the network or docker.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools -p 'test_install_demo_packs.py'
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import install_demo_packs as installer

HOST = "ue-5.8.2-cl56702186-linux-development-vulkan-sm6-iostore-v2"
D1, D2 = "sha256:" + "a" * 64, "sha256:" + "b" * 64


def _lock(**overrides):
    lock = {
        "schema": "mns.pack_release_lock.v1",
        "release": {"repository": "DinoHub/M-S-Simulation-Runtime-Stack", "tag": "lock-release"},
        "capability_id": HOST,
        "required_images": {"product_shell": "local/shell:1"},
        "packs": [
            {"selection": "condo", "kind": "level", "id": "condo-level", "display_name": "Condo",
             "version": "1.0.0", "asset_name": "condo_level-1_0_0.mnslevelpack", "size_bytes": 10,
             "sha256": "0" * 64, "artifact_digest": D1,
             "release": {"repository": "DinoHub/TEVV-Airsim", "tag": "pack-level-condo-level-1.0.0"}},
            {"selection": "office-props", "kind": "asset", "id": "office-props", "display_name": "Office Props",
             "version": "1.0.1", "asset_name": "office-props-1.0.1.mnsassetpack", "size_bytes": 20,
             "sha256": "1" * 64, "artifact_digest": D2},
        ],
    }
    lock.update(overrides)
    return lock


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.lock_path = root / "lock.json"
        self.lock_path.write_text(json.dumps(_lock()))
        self.contract = root / "contract.json"
        self.contract.write_text(json.dumps({"id": HOST}))
        self.store = root / "store"
        self.env = patch.dict(os.environ, {
            "MNS_DEMO_PACK_LOCK": str(self.lock_path),
            "MNS_RUNTIME_HOST_COMPATIBILITY_CONTRACT": str(self.contract),
            "MNS_PACK_STORE_ROOT": str(self.store),
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _run(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = installer.main(list(argv))
        return code, out.getvalue()

    def test_pack_release_overrides_lock_release(self):
        lock = _lock()
        self.assertEqual(
            installer.pack_url(lock, lock["packs"][0]),
            "https://github.com/DinoHub/TEVV-Airsim/releases/download/pack-level-condo-level-1.0.0/condo_level-1_0_0.mnslevelpack")
        self.assertEqual(
            installer.pack_url(lock, lock["packs"][1]),
            "https://github.com/DinoHub/M-S-Simulation-Runtime-Stack/releases/download/lock-release/office-props-1.0.1.mnsassetpack")

    def test_lock_and_contract_must_agree(self):
        self.contract.write_text(json.dumps({"id": HOST.replace("5.8.2", "5.5.4")}))
        with self.assertRaisesRegex(RuntimeError, "would not mount"):
            self._run("--check", "--all")

    def test_selections_come_from_the_lock(self):
        code, out = self._run("--check", "--all")
        self.assertEqual(code, 1)                       # nothing installed yet
        self.assertIn("missing:   condo-level@1.0.0 (condo)", out)
        self.assertIn("missing:   office-props@1.0.1 (office-props)", out)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self._run("--check", "--pendleton")        # a 5.5.4-only flag is not in this lock

    def test_check_and_missing_read_the_store_by_digest(self):
        self.store.mkdir()
        (self.store / "index.json").write_text(json.dumps({"packs": [
            {"kind": "level", "id": "condo-level", "version": "1.0.0", "digest": D1}]}))
        code, out = self._run("--check", "--condo")
        self.assertEqual(code, 0)
        self.assertIn("installed: condo-level@1.0.0", out)
        code, out = self._run("--check", "--all")
        self.assertEqual(code, 1)
        # --missing with everything present downloads nothing but still
        # re-stages (a store can be full while ScenarioLab's index is stale)
        with patch.object(installer, "stage", return_value=0) as staged:
            code, out = self._run("--missing", "--condo")
        self.assertEqual(code, 0)
        self.assertIn("already installed", out)
        staged.assert_called_once()
        self.assertEqual(staged.call_args.args[0], "local/shell:1")
        # ... unless it is a dry run, which touches nothing
        with patch.object(installer, "stage", return_value=0) as staged:
            code, out = self._run("--missing", "--condo", "--dry-run")
        staged.assert_not_called()
        # --objects selects only asset packs
        code, out = self._run("--check", "--objects")
        self.assertIn("missing:   office-props@1.0.1", out)
        self.assertNotIn("condo-level", out)

    def test_dry_run_lists_release_urls_without_side_effects(self):
        code, out = self._run("--dry-run", "--all")
        self.assertEqual(code, 0)
        self.assertIn("DinoHub/TEVV-Airsim@pack-level-condo-level-1.0.0", out)
        self.assertIn("DinoHub/M-S-Simulation-Runtime-Stack@lock-release", out)
        self.assertFalse(self.store.exists())

    def test_disk_guard_counts_archive_plus_store_copy(self):
        class Usage:
            free = 100
        with patch.object(installer.shutil, "disk_usage", return_value=Usage()):
            with self.assertRaisesRegex(RuntimeError, "not enough free space"):
                installer.check_disk_space(Path(self.tmp.name), _lock()["packs"])
        Usage.free = 10 ** 12
        with patch.object(installer.shutil, "disk_usage", return_value=Usage()):
            installer.check_disk_space(Path(self.tmp.name), _lock()["packs"])

if __name__ == "__main__":
    unittest.main()
