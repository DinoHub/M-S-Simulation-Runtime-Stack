import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_ue_candidate as candidate


class CandidateTests(unittest.TestCase):
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

    def test_unreal_images_need_matching_built_host_label(self):
        metadata = [{"Id": "sha256:image", "Config": {"Labels": {candidate.HOST_LABEL: self.host}}}]
        with patch.object(candidate, "docker_json", return_value=metadata):
            self.assertEqual(len(candidate.verify_images(self.lock["required_images"], self.host)), 7)
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

    def test_launch_overrides_stale_env_and_uses_only_candidate_runtime_slots(self):
        images = self.lock["required_images"]
        for role in ("ardupilot", "px4", "qgroundcontrol", "sim_real_eval", "lichtblick"):
            images[role] = f"example.invalid/{role}:candidate@sha256:" + "c" * 64
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            store = workspace / ".mns/pack-store"
            store.mkdir(parents=True)
            with patch.dict(os.environ, {"MNS_AUTHORING_IMAGE": "old:latest"}), \
                 patch.object(candidate.subprocess, "run") as run:
                candidate.start_dashboard(workspace, store, images)
            calls = run.call_args_list
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0].kwargs["env"]["MNS_AUTHORING_IMAGE"], images["authoring"])
            self.assertEqual(calls[1].kwargs["env"]["MSRS_ROOT"], str(workspace))
            self.assertEqual(calls[1].args[0][-2:], ["--pull", "never"])
            overlay = json.loads((workspace / ".mns/ue-candidate/image-set.json").read_text())
            selected = overlay["image_sets"]["published"]["images"]
            self.assertEqual(selected["simulators"]["tevv_runtime_host"], images["runtime_host"])
            self.assertEqual(selected["autopilots"]["ardupilot"], images["ardupilot"])
            self.assertEqual(selected["ros2_bridge"], images["ros2_bridge"])

    def test_launch_requires_support_images_instead_of_inheriting_catalog_defaults(self):
        with self.assertRaisesRegex(candidate.CandidateError, "ardupilot"):
            candidate.dashboard_configuration(self.lock["required_images"])


if __name__ == "__main__":
    unittest.main()
