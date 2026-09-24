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


class ResumeDownloadTests(unittest.TestCase):
    """A dropped connection resumes; an HTTP error does not retry."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp.name) / "pack.mnslevelpack"
        self.calls = []
        url = mock.patch.object(installer, "asset_api_url", return_value="https://api.example/asset/1")
        nap = mock.patch.object(installer.time, "sleep")
        url.start(); nap.start()
        self.addCleanup(url.stop); self.addCleanup(nap.stop); self.addCleanup(self.tmp.cleanup)

    def _curl(self, *exit_codes):
        codes = list(exit_codes)
        def fake_run(command, **_kwargs):
            self.calls.append(command)
            with self.target.open("ab") as out:
                out.write(b"x" * 10)
            code = codes.pop(0)
            if code:
                raise installer.subprocess.CalledProcessError(code, command)
        return mock.patch.object(installer, "run", fake_run)

    def test_a_dropped_stream_resumes_from_the_bytes_on_disk(self):
        with self._curl(92, 18, 0), contextlib.redirect_stderr(io.StringIO()) as err:
            installer._download_named_asset({}, "pack.mnslevelpack", "t", self.target)
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(all("--continue-at" in c for c in self.calls))
        self.assertIn("resuming (attempt 2 of", err.getvalue())
        self.assertEqual(self.target.stat().st_size, 30)

    def test_an_http_error_is_not_retried(self):
        with self._curl(installer.CURL_HTTP_ERROR, 0):
            with self.assertRaises(installer.subprocess.CalledProcessError):
                installer._download_named_asset({}, "pack.mnslevelpack", "t", self.target)
        self.assertEqual(len(self.calls), 1)

    def test_it_gives_up_after_the_last_attempt(self):
        with self._curl(*([56] * installer.DOWNLOAD_ATTEMPTS)), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(installer.subprocess.CalledProcessError):
                installer._download_named_asset({}, "pack.mnslevelpack", "t", self.target)
        self.assertEqual(len(self.calls), installer.DOWNLOAD_ATTEMPTS)

    def test_the_hint_separates_network_from_access_failures(self):
        network = installer._hint_for(installer.subprocess.CalledProcessError(92, ["curl"]))
        access = installer._hint_for(installer.subprocess.CalledProcessError(22, ["curl"]))
        self.assertIn("network problem", network)
        self.assertIn("404", access)



class StagingStaysInItsOwnChannel(unittest.TestCase):
    """A store and an authoring data root are two halves of one channel."""

    def _staged_env(self, store: Path, environ: dict) -> dict:
        captured = {}

        def fake_run(argv, **kwargs):
            captured.update(kwargs["env"])
            return None

        with patch.dict(installer.os.environ, environ, clear=True), \
                patch.object(installer.subprocess, "run", fake_run):
            installer.stage("shell:image", store)
        return captured

    def test_a_custom_store_stages_beside_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "origin" / "pack-store"
            env = self._staged_env(store, {})
            self.assertEqual(env["MNS_PACK_STORE_ROOT"], str(store))
            self.assertEqual(env["MNS_AUTHORING_DATA_ROOT"],
                             str(Path(tmp) / "origin" / "authoring-data"))

    def test_it_never_falls_back_to_the_default_channel(self):
        """The bug: installing into any other store rewrote ue582's index."""
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "origin" / "pack-store"
            env = self._staged_env(store, {})
            self.assertNotIn("ue582", env["MNS_AUTHORING_DATA_ROOT"])

    def test_an_explicit_authoring_root_still_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "origin" / "pack-store"
            chosen = Path(tmp) / "somewhere-else"
            env = self._staged_env(store, {"MNS_AUTHORING_DATA_ROOT": str(chosen)})
            self.assertEqual(env["MNS_AUTHORING_DATA_ROOT"], str(chosen))


class UnpublishedLockEntries(unittest.TestCase):
    """A lock entry with a digest but no release can never be installed."""

    def test_pack_release_says_why_instead_of_raising_keyerror(self):
        lock = {"packs": []}
        pack = {"id": "mns_vehicle_models", "version": "1.0.2"}
        with self.assertRaises(RuntimeError) as caught:
            installer.pack_release(lock, pack)
        self.assertIn("declares no release", str(caught.exception))
        self.assertIn("mns_vehicle_models", str(caught.exception))

    def test_a_lock_level_release_still_covers_a_pack_without_one(self):
        lock = {"release": {"repository": "DinoHub/M-S-Simulation-Runtime-Stack", "tag": "v1"}}
        self.assertEqual(installer.pack_release(lock, {"id": "x"})["tag"], "v1")


DIGEST = "sha256:" + "b" * 64
OTHER = "sha256:" + "c" * 64


def _pack_store(root: Path, *digests: str) -> Path:
    """A pack store holding one blob dir per digest."""
    store = root / "pack-store"
    (store / "blobs" / "sha256").mkdir(parents=True)
    packs = []
    for digest in digests:
        blob = f"blobs/sha256/{digest.removeprefix('sha256:')}"
        (store / blob).mkdir(parents=True)
        (store / blob / "payload.bin").write_bytes(b"x" * 2048)
        packs.append({"blob": blob, "digest": digest, "id": digest[7:11], "kind": "level",
                      "version": "1.0.0"})
    (store / "index.json").write_text(json.dumps({"schema": "mns.pack_store.v1", "packs": packs}))
    return store


def _removal_lock(*digests: str) -> dict:
    return {"packs": [{"selection": d[7:11], "id": d[7:11], "kind": "level", "version": "1.0.0",
                       "artifact_digest": d, "release": {"repository": "o/r", "tag": "t"}}
                      for d in digests],
            "required_images": {"product_shell": "shell:image"}}


def _staged_data(root: Path, *digests: str) -> Path:
    data = root / "authoring-data"
    (data / "ResolvedPacks").mkdir(parents=True)
    (data / "ResolvedPacks" / "index.json").write_text(json.dumps(
        {"level_packs": [{"id": d[7:11], "artifact_digest": d} for d in digests],
         "asset_packs": []}))
    return data


class PackRemoval(unittest.TestCase):
    """Removing a pack must not break something that still needs it."""

    def test_an_unreferenced_pack_is_removed_and_the_index_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, data = _pack_store(root, DIGEST, OTHER), _staged_data(root)
            removed, warnings = installer.remove_packs(
                [DIGEST[7:11]], _removal_lock(DIGEST, OTHER), store, data, workspace=root)
            self.assertEqual([r["digest"] for r in removed], [DIGEST])
            self.assertFalse((store / "blobs/sha256" / DIGEST.removeprefix("sha256:")).exists())
            index = json.loads((store / "index.json").read_text())
            self.assertEqual([p["digest"] for p in index["packs"]], [OTHER])
            self.assertEqual(warnings, [])

    def test_it_refuses_while_a_generated_stack_uses_the_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, data = _pack_store(root, DIGEST), _staged_data(root)
            resolved = root / "generated" / "condo-px4" / "config" / "content-packs"
            resolved.mkdir(parents=True)
            (resolved / "resolved-pack-set.json").write_text(json.dumps(
                {"environment": {"artifact_digest": DIGEST}, "asset_packs": []}))
            with self.assertRaises(RuntimeError) as caught:
                installer.remove_packs([DIGEST[7:11]], _removal_lock(DIGEST), store, data, workspace=root)
            self.assertIn("condo-px4", str(caught.exception))
            self.assertTrue((store / "blobs/sha256" / DIGEST.removeprefix("sha256:")).exists())

    def test_it_refuses_while_staged_unless_unstage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, data = _pack_store(root, DIGEST), _staged_data(root, DIGEST)
            with self.assertRaises(RuntimeError) as caught:
                installer.remove_packs([DIGEST[7:11]], _removal_lock(DIGEST), store, data, workspace=root)
            self.assertIn("--unstage", str(caught.exception))
            removed, _ = installer.remove_packs(
                [DIGEST[7:11]], _removal_lock(DIGEST), store, data, unstage=True, workspace=root)
            self.assertEqual(len(removed), 1)

    def test_an_authored_scenario_warns_but_does_not_block(self):
        """A spec is a record of what was authored, not something running."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, data = _pack_store(root, DIGEST), _staged_data(root)
            spec = root / "scenarios" / "my-scenario"
            spec.mkdir(parents=True)
            (spec / "ScenarioSpec.yaml").write_text(f"environment:\n  artifact_digest: {DIGEST}\n")
            removed, warnings = installer.remove_packs(
                [DIGEST[7:11]], _removal_lock(DIGEST), store, data, workspace=root)
            self.assertEqual(len(removed), 1)
            self.assertTrue(any("my-scenario" in w for w in warnings))

    def test_an_asset_pack_in_a_split_spec_is_found_too(self):
        """A split spec keeps its asset packs in a sibling AssetPacks.yaml."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = root / "scenarios" / "split"
            spec.mkdir(parents=True)
            (spec / "AssetPacks.yaml").write_text(f"asset_packs:\n  - artifact_digest: {DIGEST}\n")
            found = installer.pack_references(DIGEST, root / "nowhere", workspace=root)
            self.assertEqual(found["scenario_files"], ["scenarios/split/AssetPacks.yaml"])

    def test_orphan_blobs_are_reported_and_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = _pack_store(root, DIGEST)
            orphan = store / "blobs" / "sha256" / ("d" * 64)
            orphan.mkdir()
            (orphan / "payload.bin").write_bytes(b"y" * 4096)
            found = installer.remove_orphan_blobs(store)
            self.assertEqual([o["digest"] for o in found], ["sha256:" + "d" * 64])
            self.assertFalse(orphan.exists())
            self.assertTrue((store / "blobs/sha256" / DIGEST.removeprefix("sha256:")).exists())


class PackStatus(unittest.TestCase):
    """The join of locked, installed and published."""

    def test_it_reports_a_newer_published_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = _pack_store(root, DIGEST)
            lock = _removal_lock(DIGEST)
            with patch.object(installer, "installed_digests", lambda _: {DIGEST}), \
                    patch("build_pack_lock.discover_latest",
                          lambda *a, **k: {("level", DIGEST[7:11]): {"version": "2.0.0", "tag": "t2"}}):
                status = installer.pack_status(lock, Path("lock.json"), store,
                                               "host-id", "o/r", root)
            row = status["packs"][0]
            self.assertTrue(row["update_available"])
            self.assertEqual(row["latest_version"], "2.0.0")
            self.assertTrue(status["remote_checked"])

    def test_offline_says_so_rather_than_implying_currency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = installer.pack_status(_removal_lock(DIGEST), Path("lock.json"),
                                           _pack_store(root, DIGEST), "host", "o/r", root, offline=True)
            self.assertFalse(status["remote_checked"])
            self.assertFalse(status["packs"][0]["update_available"])

    def test_an_installed_pack_outside_the_lock_is_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = installer.pack_status(_removal_lock(DIGEST), Path("lock.json"),
                                           _pack_store(root, DIGEST, OTHER), "host", "o/r", root,
                                           offline=True)
            self.assertEqual(status["installed_not_locked"], [OTHER])

if __name__ == "__main__":
    unittest.main()
