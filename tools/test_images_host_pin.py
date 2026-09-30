"""The host-pin label checks in tools/images.py (kit-only model), and how
`verify --release` treats them. No docker: labels come from a fake.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools -p 'test_images_host_pin.py'
"""
from __future__ import annotations

import argparse
import contextlib
import io
import unittest
from unittest import mock

import images

KIT = "sha256:" + "1" * 64
OTHER = "sha256:" + "2" * 64


def catalog():
    base = {"repo": "r/i", "channel": "pinned", "purpose": "fixture"}
    return {"schema": "mns.images.v1",
            "images": {"host": {**base, "tag": "h", "digest": "sha256:" + "0" * 64},
                       "kit": {**base, "tag": "k", "digest": KIT},
                       "auth": {**base, "tag": "a", "digest": "sha256:" + "3" * 64}},
            "consumers": {"product_env": {}, "compose_env": {}, "image_sets": {},
                          "release_channels": {"v1": {
                              "emits": "x.env", "vars": {"H": "host"},
                              "host_pin": {"host": "host", "kit": "kit", "authoring": "auth"}}}}}


def fake_labels(cat, host, auth):
    table = {images.image_ref(cat["images"], "host"): host,
             images.image_ref(cat["images"], "auth"): auth}
    return lambda ref: (table.get(ref), "" if table.get(ref) is not None else "not local")


HOST = {images.LABEL_HOST_KIT_IMAGE: f"r/i@{KIT}", images.LABEL_HOST_SHARED_SET: "tree"}
AUTH = {images.LABEL_AUTHORING_KIT_IMAGE: f"r/i:k@{KIT}", images.LABEL_AUTHORING_SHARED_SET: "tree"}


class HostPin(unittest.TestCase):
    def findings(self, host, auth):
        cat = catalog()
        return images.host_pin_findings(cat, remote=False, labels_of=fake_labels(cat, host, auth))

    def warnings(self, host, auth):
        return [m for level, m in self.findings(host, auth) if level == "WARNING"]

    def test_one_kit_everywhere_is_silent(self):
        self.assertEqual(self.findings(HOST, AUTH), [])

    def test_the_authoring_kit_must_be_the_pinned_kit(self):
        found = self.warnings(HOST, {**AUTH, images.LABEL_AUTHORING_KIT_IMAGE: OTHER})
        self.assertEqual(len(found), 1)
        self.assertIn("--kit r/i:k@" + KIT, found[0])
        self.assertIn("--strict", found[0])

    def test_the_host_kit_must_be_the_pinned_kit(self):
        found = self.warnings({**HOST, images.LABEL_HOST_KIT_IMAGE: OTHER}, AUTH)
        self.assertEqual(len(found), 1)
        self.assertIn("pin the kit the host was built with", found[0])

    def test_shared_sets_must_match(self):
        found = self.warnings(HOST, {**AUTH, images.LABEL_AUTHORING_SHARED_SET: "other"})
        self.assertEqual(len(found), 1)
        self.assertIn("shared_set_id", found[0])

    def test_dev_builds_are_flagged(self):
        self.assertEqual(len(self.warnings(HOST, {**AUTH, images.LABEL_AUTHORING_PLUGINS_SOURCE: "/src"})), 1)
        self.assertEqual(len(self.warnings(HOST, {**AUTH, images.LABEL_AUTHORING_CHECK_WAIVED: "1"})), 1)
        self.assertEqual(self.warnings(HOST, {**AUTH, images.LABEL_AUTHORING_CHECK_WAIVED: "false"}), [])

    def test_an_image_from_before_the_kit_labels_says_so(self):
        found = self.warnings(HOST, {images.LABEL_AUTHORING_HOST_IMAGE_OLD: "r/i:h@x",
                                     images.LABEL_AUTHORING_SHARED_SET: "tree"})
        self.assertEqual(len(found), 1)
        self.assertIn("predates the kit labels", found[0])
        self.assertNotIn("runtime-host.lock.json", found[0])

    def test_missing_images_are_notes_never_warnings(self):
        levels = {level for level, _ in self.findings(None, None)}
        self.assertEqual(levels, {"NOTE"})


class ReleaseTurnsWarningsIntoFailures(unittest.TestCase):
    def run_verify(self, findings, release):
        with mock.patch.object(images, "run_selftest"), \
                mock.patch.object(images, "load_catalog", return_value=catalog()), \
                mock.patch.object(images, "assert_invariants"), \
                mock.patch.object(images, "pack_lock_drift", return_value=[]), \
                mock.patch.object(images, "render_all", return_value={}), \
                mock.patch.object(images, "release_problems", return_value=[]), \
                mock.patch.object(images, "host_pin_findings", return_value=findings), \
                mock.patch.dict(images.os.environ, {"GITHUB_BASE_REF": ""}), \
                contextlib.redirect_stderr(io.StringIO()) as err, \
                contextlib.redirect_stdout(io.StringIO()):
            code = images.cmd_verify(argparse.Namespace(release=release))
        return code, err.getvalue()

    def test_a_warning_passes_verify_and_fails_a_release(self):
        findings = [("WARNING", "v1: auth was built against kit x")]
        self.assertEqual(self.run_verify(findings, release=False)[0], 0)
        code, err = self.run_verify(findings, release=True)
        self.assertEqual(code, 1)
        self.assertIn("RELEASE: host pin: v1: auth was built against kit x", err)

    def test_a_note_never_fails_a_release(self):
        self.assertEqual(self.run_verify([("NOTE", "v1: host checks skipped")], release=True)[0], 0)


if __name__ == "__main__":
    unittest.main()
