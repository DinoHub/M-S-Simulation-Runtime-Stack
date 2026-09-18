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

from unittest import mock

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
            installer.pack_release(lock, lock["packs"][0]),
            {"repository": "DinoHub/TEVV-Airsim", "tag": "pack-level-condo-level-1.0.0"})
        self.assertEqual(
            installer.pack_release(lock, lock["packs"][1])["tag"], "lock-release")

    def test_download_needs_a_token_and_says_so(self):
        """The releases are private, and GitHub answers 404 (not 401) to an
        anonymous client -- so a credential-less run must fail by name, before
        it fetches anything, rather than surfacing an unexplained 404."""
        with mock.patch.object(installer, "github_token", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "no GitHub credentials"):
                self._run("--all")

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


class PullAndImportTests(InstallerTests):
    """--release-tag / --import / --list-remote and split-part reassembly."""

    def test_download_asset_reassembles_parts_in_order(self):
        target = Path(self.tmp.name) / "big.mnslevelpack"
        pack = {"asset_name": "big.mnslevelpack",
                "parts": ["big.mnslevelpack.part-000", "big.mnslevelpack.part-001"]}
        calls = []

        def fake_named(release, name, token, path):
            calls.append(name)
            path.write_bytes(b"AAA" if name.endswith("000") else b"BB")

        with patch.object(installer, "_download_named_asset", side_effect=fake_named):
            installer.download_asset({"repository": "r", "tag": "t"}, pack, "tok", target)
        self.assertEqual(target.read_bytes(), b"AAABB")
        self.assertEqual(calls, pack["parts"])
        self.assertFalse(list(Path(self.tmp.name).glob("*.download")))

    def test_pack_entry_from_release_reads_artifact_and_parts(self):
        assets = [
            {"name": "artifact.json", "size": 10},
            {"name": "mns_level_pack.json", "size": 10},
            {"name": "blocks-1.0.1.mnslevelpack.sha256", "size": 90},
            {"name": "blocks-1.0.1.mnslevelpack.part-001", "size": 5},
            {"name": "blocks-1.0.1.mnslevelpack.part-000", "size": 7},
        ]
        artifact = {"kind": "level", "pack": {"id": "blocks", "version": "1.0.1"},
                    "variants": [{"host_compatibility_id": HOST}]}

        def fake_named(release, name, token, path):
            if name == "artifact.json":
                path.write_text(json.dumps(artifact))
            else:
                path.write_text("f" * 64 + "  blocks-1.0.1.mnslevelpack\n")

        release = {"repository": "DinoHub/TEVV-Airsim", "tag": "pack-level-blocks-1.0.1"}
        with patch.object(installer, "release_assets", return_value=assets), \
                patch.object(installer, "_download_named_asset", side_effect=fake_named):
            entry = installer.pack_entry_from_release(release, "tok", HOST, Path(self.tmp.name) / "scratch")
        self.assertEqual(entry["asset_name"], "blocks-1.0.1.mnslevelpack")
        self.assertEqual(entry["parts"], ["blocks-1.0.1.mnslevelpack.part-000", "blocks-1.0.1.mnslevelpack.part-001"])
        self.assertEqual(entry["size_bytes"], 12)
        self.assertEqual(entry["sha256"], "f" * 64)
        self.assertEqual(entry["artifact_digest"], "")
        # a release cooked for another host is refused before any download
        artifact["variants"] = [{"host_compatibility_id": "other"}]
        with patch.object(installer, "release_assets", return_value=assets), \
                patch.object(installer, "_download_named_asset", side_effect=fake_named):
            with self.assertRaisesRegex(RuntimeError, "not cooked for the selected runtime host"):
                installer.pack_entry_from_release(release, "tok", HOST, Path(self.tmp.name) / "scratch2")

    def test_release_tag_dry_run_lists_the_release(self):
        entry = {"selection": "blocks", "kind": "level", "id": "blocks", "display_name": "blocks",
                 "version": "1.0.1", "asset_name": "blocks-1.0.1.mnslevelpack", "parts": ["a", "b"],
                 "size_bytes": 12, "sha256": "f" * 64, "artifact_digest": "",
                 "release": {"repository": "DinoHub/TEVV-Airsim", "tag": "pack-level-blocks-1.0.1"}}
        with patch.object(installer, "github_token", return_value="tok"), \
                patch.object(installer, "pack_entry_from_release", return_value=entry):
            code, out = self._run("--release-tag", "pack-level-blocks-1.0.1", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("blocks: 2 parts from DinoHub/TEVV-Airsim@pack-level-blocks-1.0.1 (digest recorded at install)", out)
        with patch.object(installer, "github_token", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "--release-tag needs GitHub credentials"):
                self._run("--release-tag", "pack-level-blocks-1.0.1")

    def test_import_installs_archives_from_the_pack_mount_directory(self):
        packs_dir = Path(self.tmp.name) / "packs"
        packs_dir.mkdir()
        (packs_dir / "blocks-1.0.1.mnslevelpack").write_bytes(b"zip")
        (packs_dir / "notes.txt").write_text("ignored")
        with patch.dict(os.environ, {"MNS_PACKS_DIR": str(packs_dir)}):
            code, out = self._run("--import", "--dry-run")
            self.assertEqual(code, 0)
            self.assertIn("import: blocks-1.0.1.mnslevelpack", out)
            self.assertNotIn("notes.txt", out)
            with patch.object(installer, "ensure_image"), \
                    patch.object(installer, "install_archive", return_value=D1) as install, \
                    patch.object(installer, "stage", return_value=0) as staged:
                code, out = self._run("--import")
        self.assertEqual(code, 0)
        self.assertEqual(install.call_args.args[1], packs_dir / "blocks-1.0.1.mnslevelpack")
        self.assertIsNone(install.call_args.args[2])          # digest comes from the shell
        self.assertIn(f"Installed blocks-1.0.1.mnslevelpack as {D1}.", out)
        staged.assert_called_once()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self._run("--import", "--check")

    def test_install_archive_accepts_the_shell_digest_when_none_is_expected(self):
        class Result:
            stdout = json.dumps({"digest": D2})
        with patch.object(installer, "run", return_value=Result()), \
                patch.object(installer, "container_path", return_value="/workspace/.mns/store"), \
                patch.object(installer, "host_user", return_value="1000:1000"):
            self.assertEqual(installer.install_archive("img", Path(self.tmp.name) / "a.mnsassetpack", None, self.store), D2)
            with self.assertRaisesRegex(RuntimeError, "but the lock expects"):
                installer.install_archive("img", Path(self.tmp.name) / "a.mnsassetpack", D1, self.store)
