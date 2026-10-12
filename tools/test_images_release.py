"""The release guard in tools/images.py (`verify --release`): which rows it
checks, and the tag and digest rules it applies to them. No docker, no
registry: synthetic catalogs only.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools -p 'test_images_release.py'
"""
from __future__ import annotations

import unittest

import images

DIGEST = "sha256:" + "0" * 64


def row(tag, channel="pinned", digest=DIGEST, repo="dhdevspace/auto_mns"):
    return {"repo": repo, "tag": tag, "digest": digest, "channel": channel, "purpose": "fixture"}


def catalog(**extra_rows):
    """A catalog that can ship: one channel row, one image-set row, one
    upstream dashboard row on its upstream's own tag. Rows passed in are
    added (or replace these)."""
    rows = {"v1_stacks": row("mns-stacks-v1.0.0"),
            "qgroundcontrol": row("airsim-qgc-x11-v1.0.0"),
            "dashboard_lichtblick": row("latest", channel="upstream",
                                        repo="ghcr.io/lichtblick-suite/lichtblick"),
            **extra_rows}
    return {"schema": "mns.images.v1",
            "images": rows,
            "consumers": {
                "product_env": {},
                "compose_env": {"dashboard": {"DASHBOARD_LICHTBLICK_IMAGE": "dashboard_lichtblick"}},
                "release_channels": {"v1": {"emits": "does-not-exist.env", "image_set": "v1",
                                            "vars": {"MNS_STACKS_IMAGE": "v1_stacks"}}},
                "image_sets": {"v1": {"pull_policy": "missing",
                                      "images": {"qgroundcontrol": "qgroundcontrol"}}}}}


class ReleaseGuard(unittest.TestCase):
    def problems(self, cat):
        images._validate_catalog(cat)
        return images.release_problems(cat)

    def test_a_shippable_catalog_has_no_problems(self):
        self.assertEqual(self.problems(catalog()), [])

    def test_a_latest_tag_in_an_image_set_fails(self):
        found = self.problems(catalog(qgroundcontrol=row("airsim-qgc-x11-latest", channel="moving")))
        self.assertEqual(len(found), 1, found)
        self.assertIn("images.qgroundcontrol (image_sets.v1.qgroundcontrol)", found[0])
        self.assertIn("not a release tag", found[0])

    def test_an_rc_tag_in_an_image_set_fails(self):
        cat = catalog(sim_real_eval=row("sim-real-eval-worker-v1.0.0-rc"))
        cat["consumers"]["image_sets"]["v1"]["images"]["sim_real_eval"] = "sim_real_eval"
        found = self.problems(cat)
        self.assertEqual(len(found), 1, found)
        self.assertIn("images.sim_real_eval (image_sets.v1.sim_real_eval)", found[0])
        self.assertIn("release-candidate tag", found[0])

    def test_nested_rows_of_every_image_set_are_checked(self):
        # a set no channel selects, inheriting the channel's set, adds a
        # nested role of its own
        cat = catalog(v1_px4=row("px4-airsim-px4-latest", channel="moving"))
        cat["consumers"]["image_sets"]["other"] = {
            "inherits": "v1", "pull_policy": "missing",
            "images": {"autopilots": {"px4": "v1_px4"}}}
        found = self.problems(cat)
        self.assertEqual(len(found), 1, found)
        self.assertIn("images.v1_px4 (image_sets.other.autopilots.px4)", found[0])

    def test_an_image_set_row_with_no_digest_fails(self):
        found = self.problems(catalog(qgroundcontrol=row("airsim-qgc-x11-v1.0.0",
                                                         channel="unpublished", digest=None)))
        self.assertEqual(len(found), 1, found)
        self.assertIn("has no digest", found[0])

    def test_a_pending_row_is_reported_once(self):
        cat = catalog(qgroundcontrol={**row("airsim-qgc-x11-v1.0.0", digest=None),
                                      "pending": "phase 6"})
        found = self.problems(cat)
        self.assertEqual(len(found), 1, found)
        self.assertIn("still pending", found[0])

    def test_the_estimator_may_be_tagged_with_its_upstream_commit(self):
        cat = catalog(vio_estimator_openvins=row("vio-estimator-openvins-69488123"),
                      sim_real_eval=row("sim-real-eval-worker-69488123"))
        cat["consumers"]["image_sets"]["v1"]["images"].update(
            vio_estimator="vio_estimator_openvins", sim_real_eval="sim_real_eval")
        found = self.problems(cat)
        self.assertEqual(len(found), 1, found)
        self.assertIn("images.sim_real_eval", found[0])

    def test_upstream_dashboard_rows_keep_their_own_tags(self):
        self.assertEqual(self.problems(catalog()), [])
        # ...but an image we publish there is held to the release tags
        cat = catalog(dashboard_extra=row("tevv-jsonl-ingest-latest", channel="moving"))
        cat["consumers"]["compose_env"]["dashboard"]["X_IMAGE"] = "dashboard_extra"
        found = self.problems(cat)
        self.assertEqual(len(found), 1, found)
        self.assertIn("compose_env.dashboard.X_IMAGE", found[0])

    def test_an_upstream_row_in_an_image_set_is_held_to_release_tags(self):
        cat = catalog(zenoh=row("latest", channel="upstream", repo="eclipse/zenoh-bridge-ros2dds"))
        cat["consumers"]["image_sets"]["v1"]["images"]["zenoh"] = "zenoh"
        self.assertEqual(len(self.problems(cat)), 1)

    def test_a_row_is_reported_once_however_often_it_is_referenced(self):
        cat = catalog(qgroundcontrol=row("airsim-qgc-x11-latest", channel="moving"))
        cat["consumers"]["release_channels"]["v1"]["vars"]["QGC_IMAGE"] = "qgroundcontrol"
        found = self.problems(cat)
        self.assertEqual(len(found), 1, found)
        self.assertIn("(QGC_IMAGE)", found[0])

    def test_channel_rows_are_still_checked(self):
        found = self.problems(catalog(v1_stacks=row("mns-stacks-v1.0.0-rc.services.28")))
        self.assertEqual(len(found), 1, found)
        self.assertIn("(MNS_STACKS_IMAGE)", found[0])


if __name__ == "__main__":
    unittest.main()
