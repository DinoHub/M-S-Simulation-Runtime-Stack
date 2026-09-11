import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_ue_candidate as candidate


class CandidateTests(unittest.TestCase):
    def selection(self, workspace, store):
        self.lock["host_contract"] = "runtime.json"
        self.lock["authoring_host_contract"] = "authoring.json"
        lock = workspace / "candidate.json"
        lock.write_text(json.dumps(self.lock))
        for name in ("runtime.json", "authoring.json"):
            (workspace / name).write_text(json.dumps({
                "schema": "mns.host_compatibility.v1", "id": self.host,
            }))
        selection = candidate.candidate_selection(workspace, store, lock, self.lock)
        staged = Path(selection["MNS_AUTHORING_DATA_ROOT"]) / "ResolvedPacks/index.json"
        staged.parent.mkdir(parents=True)
        staged.write_text(json.dumps({
            "cook_capability_id": self.host,
            "level_packs": [dict(pack) for pack in self.lock["packs"]],
            "asset_packs": [],
        }))
        return selection

    def setUp(self):
        self.host = "ue-5.8.2-cl123456-linux-development-vulkan-sm6-iostore-v2"
        self.lock = {"schema": "mns.pack_release_lock.v1", "capability_id": self.host,
                     "required_images": {role: f"example.invalid/{role}:candidate@sha256:" + "a" * 64
                                         for role in candidate.IMAGE_ROLES},
                     "packs": [{"kind": "level", "id": "fixture", "version": "1.0.0",
                                "artifact_digest": "sha256:" + "b" * 64}]}

    def test_engine_is_read_from_build_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version = root / "Engine/Build/Build.version"
            version.parent.mkdir(parents=True)
            version.write_text(json.dumps(dict(MajorVersion=5, MinorVersion=8, PatchVersion=2, Changelist=123456)))
            self.assertEqual(candidate.host_id_from_engine(root, "5.8.2"), self.host)
            renderer_id = self.host + "-render-35e7a9a52ee2ef15"
            self.assertEqual(candidate.host_id_from_engine(root, "5.8.2", renderer_id), renderer_id)
            with self.assertRaises(candidate.CandidateError):
                candidate.host_id_from_engine(root, "5.8.2", renderer_id.replace("123456", "999999"))
            with self.assertRaisesRegex(candidate.CandidateError, "expected 5.8.1"):
                candidate.host_id_from_engine(root, "5.8.1")

    def test_old_packs_and_mutable_or_missing_images_fail(self):
        candidate.validate_lock(self.lock, self.host)
        for mutation in (lambda lock: lock.update(capability_id=self.host.replace("5.8.2", "5.5.4")),
                         lambda lock: lock["required_images"].update(authoring="example.invalid/authoring:latest"),
                         lambda lock: lock["required_images"].pop("dashboard_frontend"),
                         lambda lock: lock.update(packs=[])):
            lock = copy.deepcopy(self.lock)
            mutation(lock)
            with self.assertRaises(candidate.CandidateError):
                candidate.validate_lock(lock, self.host)

    def test_image_only_candidate_accepts_renderer_identity_and_rejects_invalid_suffix(self):
        for suffix in ("", "-render-35e7a9a52ee2ef15"):
            metadata = [{"Config": {"Labels": {candidate.HOST_LABEL: self.host + suffix}}}]
            with patch.object(candidate, "docker_json", return_value=metadata):
                self.assertEqual(candidate.host_id_from_image("runtime"), self.host + suffix)
        metadata[0]["Config"]["Labels"][candidate.HOST_LABEL] = self.host + "-render-invalid"
        with patch.object(candidate, "docker_json", return_value=metadata):
            with self.assertRaises(candidate.CandidateError):
                candidate.host_id_from_image("runtime")

    def test_local_mode_requires_unique_non_latest_tags_and_exact_ids(self):
        lock = copy.deepcopy(self.lock)
        lock["required_images"]["product_shell"] = "dhdevspace/auto_mns:mns-product-shell-ue582-local.1"
        lock["required_images"]["authoring"] = "local/mns-authoring:20260907.1"
        lock["required_image_ids"] = {role: "sha256:" + "c" * 64 for role in lock["required_images"]}
        candidate.validate_lock(lock, self.host, local_images=True)
        with self.assertRaisesRegex(candidate.CandidateError, "registry digest"):
            candidate.validate_lock(lock, self.host)

        for mutate, message in (
            (lambda value: value["required_images"].update(authoring="local/mns-authoring:latest"), "non-latest"),
            (lambda value: value["required_images"].update(authoring=value["required_images"]["product_shell"]), "reused"),
            (lambda value: value["required_image_ids"].pop("authoring"), "exactly match"),
            (lambda value: value["required_image_ids"].update(authoring="not-an-id"), "sha256"),
        ):
            changed = copy.deepcopy(lock)
            mutate(changed)
            with self.assertRaisesRegex(candidate.CandidateError, message):
                candidate.validate_lock(changed, self.host, local_images=True)

    def test_local_image_id_is_checked_before_use(self):
        images = {"authoring": "local/mns-authoring:ue582.1"}
        expected = {"authoring": "sha256:" + "c" * 64}
        metadata = [{"Id": expected["authoring"], "Config": {"Labels": {candidate.HOST_LABEL: self.host}}}]
        with patch.object(candidate, "docker_json", return_value=metadata):
            self.assertEqual(candidate.verify_images(images, self.host, expected)["authoring"]["image_id"],
                             expected["authoring"])
            metadata[0]["Id"] = "sha256:" + "d" * 64
            with self.assertRaisesRegex(candidate.CandidateError, "local image ID"):
                candidate.verify_images(images, self.host, expected)

    def test_unreal_images_need_matching_built_host_label(self):
        metadata = [{"Id": "sha256:image", "Config": {"Labels": {candidate.HOST_LABEL: self.host}}}]
        with patch.object(candidate, "docker_json", return_value=metadata):
            self.assertEqual(len(candidate.verify_images(self.lock["required_images"], self.host)), 8)
            metadata[0]["Config"]["Labels"][candidate.HOST_LABEL] = self.host.replace("5.8.2", "5.5.4")
            with self.assertRaisesRegex(candidate.CandidateError, "authoring image"):
                candidate.verify_images(self.lock["required_images"], self.host)

    def test_dashboard_wrong_workspace_or_override_fails(self):
        images = self.lock["required_images"]
        metadata = [{"Image": "sha256:backend", "State": {"Running": True}, "Config": {"Env": [
            "MNS_WORKSPACE_ROOT=/correct", "MNS_AUTHORING_IMAGE=" + images["authoring"],
            "MNS_STACK_GENERATOR_IMAGE=" + images["stack_generator"]]}}]
        receipts = {"dashboard_backend": {"image_id": "sha256:backend"}}
        with patch.object(candidate, "docker_json", return_value=metadata):
            candidate.verify_dashboard("dashboard", Path("/correct"), images, receipts)
            with self.assertRaisesRegex(candidate.CandidateError, "MNS_WORKSPACE_ROOT"):
                candidate.verify_dashboard("dashboard", Path("/wrong-worktree"), images, receipts)
            metadata[0]["Config"]["Env"][1] = "MNS_AUTHORING_IMAGE=old:latest"
            with self.assertRaisesRegex(candidate.CandidateError, "MNS_AUTHORING_IMAGE"):
                candidate.verify_dashboard("dashboard", Path("/correct"), images, receipts)

    def test_pack_receipt_mismatch_fails_after_checksum_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            (store / "blobs/sha256" / ("b" * 64)).mkdir(parents=True)
            result = {"kind": "level", "id": "fixture", "version": "1.0.0", "digest": "sha256:" + "b" * 64}
            with patch.object(candidate, "docker_json", return_value=result) as docker:
                self.assertEqual(candidate.verify_packs(self.lock, store, self.host), [result])
                self.assertIn("--network=none", docker.call_args.args)
                self.assertIn("--host", docker.call_args.args)
                result["digest"] = "sha256:" + "c" * 64
                with self.assertRaisesRegex(candidate.CandidateError, "receipt"):
                    candidate.verify_packs(self.lock, store, self.host)
                result["digest"] = "sha256:" + "b" * 64
                candidate.verify_packs(self.lock, store, self.host, local_images=True)
                self.assertIn("--pull=never", docker.call_args.args)

    def test_launch_overrides_stale_env_and_uses_only_candidate_runtime_slots(self):
        images = self.lock["required_images"]
        for role in ("ardupilot", "px4", "qgroundcontrol", "sim_real_eval", "lichtblick"):
            images[role] = f"example.invalid/{role}:candidate@sha256:" + "c" * 64
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            store = workspace / ".mns/pack-store"
            store.mkdir(parents=True)
            selection = self.selection(workspace, store)
            with patch.dict(os.environ, {"MNS_AUTHORING_IMAGE": "old:latest"}), \
                 patch.object(candidate.subprocess, "run") as run:
                candidate.start_dashboard(workspace, store, images, selection=selection)
            calls = run.call_args_list
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0].kwargs["env"]["MNS_AUTHORING_IMAGE"], images["authoring"])
            self.assertEqual(calls[1].kwargs["env"]["MSRS_ROOT"], str(workspace))
            for call in calls:
                for key, value in selection.items():
                    self.assertEqual(call.kwargs["env"][key], value)
            self.assertEqual(calls[1].kwargs["env"]["MNS_SKIP_PACK_STAGING"], "0")
            self.assertEqual(calls[1].kwargs["env"]["DASHBOARD_TIMESCALEDB_IMAGE"],
                             images["timescaledb"])
            self.assertEqual(calls[1].args[0][-2:], ["--pull", "never"])
            overlay = json.loads((workspace / ".mns/ue-candidate/image-set.json").read_text())
            selected = overlay["image_sets"]["published"]["images"]
            self.assertIn(calls[1].kwargs["env"]["MNS_IMAGE_SET"], overlay["image_sets"])
            self.assertEqual(selected["simulators"]["tevv_runtime_host"], images["runtime_host"])
            self.assertEqual(selected["autopilots"]["ardupilot"], images["ardupilot"])
            self.assertEqual(selected["ros2_bridge"], images["ros2_bridge"])

    def test_isolated_launch_uses_project_and_distinct_container_names(self):
        images = dict(self.lock["required_images"])
        for role in ("ardupilot", "px4", "qgroundcontrol", "sim_real_eval", "lichtblick"):
            images[role] = f"example.invalid/{role}:candidate@sha256:" + "c" * 64
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            store = workspace / ".mns/pack-store"
            store.mkdir(parents=True)
            selection = self.selection(workspace, store)
            with patch.object(candidate.subprocess, "run") as run:
                candidate.start_dashboard(workspace, store, images, selection=selection,
                                          compose_project="origin-e2e")
            command = run.call_args.args[0]
            self.assertEqual(command[command.index("-p") + 1], "origin-e2e")
            self.assertEqual(run.call_args.kwargs["env"]["DASHBOARD_CONTAINER_PREFIX"], "origin-e2e-")
            with patch.object(candidate.subprocess, "run") as run:
                with self.assertRaisesRegex(candidate.CandidateError, "compose project"):
                    candidate.start_dashboard(workspace, store, images, selection=selection,
                                              compose_project="../wrong")
                run.assert_not_called()

    def test_local_launch_persists_pull_never_override_and_receipts(self):
        images = self.lock["required_images"]
        for role in ("ardupilot", "px4", "qgroundcontrol", "sim_real_eval", "lichtblick"):
            images[role] = f"example.invalid/{role}:candidate@sha256:" + "c" * 64
        images["product_shell"] = "dhdevspace/auto_mns:mns-product-shell-ue582-local.1"
        expected_ids = {role: "sha256:" + "d" * 64 for role in images}
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            store = workspace / ".mns/pack-store"
            store.mkdir(parents=True)
            selection = self.selection(workspace, store)
            with patch.dict(os.environ, {"DISPLAY": ":1", "XAUTHORITY": "/run/user/1000/gdm/Xauthority"}), \
                 patch.object(candidate.subprocess, "run") as run:
                    override = candidate.start_dashboard(
                        workspace, store, images, local_images=True, expected_ids=expected_ids,
                        selection=selection,
                    )
            self.assertEqual(run.call_args_list[0].kwargs["env"]["MNS_PRODUCT_SHELL_IMAGE"],
                             expected_ids["product_shell"])
            self.assertEqual(run.call_args_list[0].kwargs["env"]["MNS_IMAGE_PULL_POLICY"], "never")
            self.assertEqual(run.call_args_list[-1].kwargs["env"]["MNS_PRODUCT_SHELL_IMAGE"],
                             images["product_shell"])
            self.assertEqual(run.call_args_list[-1].args[0][-2:], ["--pull", "never"])
            overlay = json.loads((workspace / ".mns/ue-candidate/image-set.json").read_text())
            self.assertEqual(overlay["image_sets"]["published"]["pull_policy"], "never")
            contents = override.read_text()
            self.assertIn("MNS_PRODUCT_SHELL_IMAGE=dhdevspace/auto_mns:mns-product-shell-ue582-local.1", contents)
            self.assertIn("DISPLAY=:1", contents)
            self.assertIn("XAUTHORITY=/run/user/1000/gdm/Xauthority", contents)
            self.assertIn(expected_ids["product_shell"], contents)
            self.assertEqual(override.stat().st_mode & 0o777, 0o600)

            rerun = workspace / ".mns/ue-candidate/rerun.sh"
            candidate.write_local_rerun(
                rerun, Path("/opt/Unreal-5.8.2"), "5.8.2", workspace / ".mns/lock.json",
                store, workspace,
                selection,
            )
            rerun_contents = rerun.read_text()
            self.assertIn("--engine-root /opt/Unreal-5.8.2", rerun_contents)
            self.assertIn("--lock", rerun_contents)
            self.assertIn("--authoring-host-contract", rerun_contents)
            self.assertIn("--runtime-host-contract", rerun_contents)
            self.assertIn("--authoring-data-root", rerun_contents)
            self.assertIn("--local-images --start-dashboard", rerun_contents)
            self.assertIn("set -a", rerun_contents)
            self.assertIn("source ", rerun_contents)
            self.assertIn("local-images.env", rerun_contents)
            self.assertNotIn("make dashboard", rerun_contents)
            self.assertEqual(rerun.stat().st_mode & 0o777, 0o700)

    def test_pack_staging_honors_validated_pull_policy(self):
        source = candidate.ROOT / "tools/stage-authoring-packs.sh"
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            script = workspace / "tools/stage-authoring-packs.sh"
            script.parent.mkdir(parents=True)
            script.write_text(source.read_text())
            script.chmod(0o755)
            # The script's default store is the default channel's (UE 5.8.2).
            (workspace / ".mns/ue582/pack-store").mkdir(parents=True)
            (workspace / ".mns/ue582/pack-store/index.json").write_text("{}\n")
            bin_dir = workspace / "bin"
            bin_dir.mkdir()
            docker = bin_dir / "docker"
            docker.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$DOCKER_ARGS"\n')
            docker.chmod(0o755)
            docker_args = workspace / "docker.args"
            environment = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "DOCKER_ARGS": str(docker_args),
                "MNS_PRODUCT_SHELL_IMAGE": "sha256:" + "d" * 64,
                "MNS_IMAGE_PULL_POLICY": "never",
            }
            subprocess.run([str(script)], env=environment, check=True, capture_output=True, text=True)
            arguments = docker_args.read_text().splitlines()
            self.assertEqual(arguments[:3], ["run", "--pull", "never"])

            environment["MNS_IMAGE_PULL_POLICY"] = "sometimes"
            failed = subprocess.run(
                [str(script)], env=environment, check=False, capture_output=True, text=True,
            )
            self.assertEqual(failed.returncode, 2)
            self.assertIn("must be always, missing, or never", failed.stderr)

    def test_launch_requires_support_images_instead_of_inheriting_catalog_defaults(self):
        with self.assertRaisesRegex(candidate.CandidateError, "ardupilot"):
            candidate.dashboard_configuration(self.lock["required_images"])

        images = copy.deepcopy(self.lock["required_images"])
        for role in ("ardupilot", "px4", "qgroundcontrol", "sim_real_eval", "lichtblick"):
            images[role] = f"example.invalid/{role}:candidate@sha256:" + "c" * 64
        images.pop("timescaledb")
        with self.assertRaisesRegex(candidate.CandidateError, "timescaledb"):
            candidate.dashboard_configuration(images)

    def test_selection_overrides_stale_environment_and_accepts_channel_store(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            store = workspace / ".mns/ue582/pack-store"
            store.mkdir(parents=True)
            selection = self.selection(workspace, store)
            images = self.lock["required_images"]
            for role in ("ardupilot", "px4", "qgroundcontrol", "sim_real_eval", "lichtblick"):
                images[role] = "example.invalid/" + role + ":candidate@sha256:" + "a" * 64
            stale = {key: "stale" for key in selection}
            stale["MNS_SKIP_PACK_STAGING"] = "1"
            with patch.dict(os.environ, stale), patch.object(candidate.subprocess, "run") as run:
                candidate.start_dashboard(workspace, store, images, selection=selection)
            for call in run.call_args_list:
                for key, value in selection.items():
                    self.assertEqual(call.kwargs["env"][key], value)
            self.assertEqual(selection["MNS_SEED_AUTHORING_DEFAULTS"], "0")

    def test_selection_rejects_missing_wrong_or_outside_contracts(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            store = workspace / "store"
            self.selection(workspace, store)
            lock_path = workspace / "candidate.json"
            self.lock.pop("authoring_host_contract")
            with self.assertRaisesRegex(candidate.CandidateError, "authoring host contract"):
                candidate.candidate_selection(workspace, store, lock_path, self.lock)
            self.lock["authoring_host_contract"] = "authoring.json"
            (workspace / "authoring.json").write_text('{"schema":"mns.host_compatibility.v1","id":"old"}')
            with self.assertRaisesRegex(candidate.CandidateError, "does not match"):
                candidate.candidate_selection(workspace, store, lock_path, self.lock)
            with self.assertRaisesRegex(candidate.CandidateError, "inside the selected workspace"):
                candidate.candidate_selection(workspace, store, lock_path, self.lock,
                                              runtime_contract=workspace / "../outside.json")

    def test_stale_staging_is_rejected_before_dashboard_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            store = workspace / "store"
            selection = self.selection(workspace, store)
            index = Path(selection["MNS_AUTHORING_DATA_ROOT"]) / "ResolvedPacks/index.json"
            index.write_text(json.dumps({"cook_capability_id": self.host, "level_packs": []}))
            with self.assertRaisesRegex(candidate.CandidateError, "not staged"):
                candidate.verify_staged_selection(selection)
            index.write_text('{"cook_capability_id":"old"}')
            with self.assertRaisesRegex(candidate.CandidateError, "capability"):
                candidate.verify_staged_selection(selection)

    def test_running_dashboard_must_use_the_verified_selection(self):
        metadata = [{"Image": "backend", "State": {"Running": True}, "Config": {"Env": [
            "MNS_WORKSPACE_ROOT=/correct", "MNS_AUTHORING_IMAGE=author", "MNS_STACK_GENERATOR_IMAGE=gen",
            "MNS_IMAGE_SET=ue582"]}}]
        with patch.object(candidate, "docker_json", return_value=metadata):
            with self.assertRaisesRegex(candidate.CandidateError, "MNS_IMAGE_SET"):
                candidate.verify_dashboard("dashboard", Path("/correct"),
                    {"authoring": "author", "stack_generator": "gen"},
                    {"dashboard_backend": {"image_id": "backend"}}, {"MNS_IMAGE_SET": "published"})


if __name__ == "__main__":
    unittest.main()
