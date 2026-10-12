"""A local tag must name the image the catalog pins, not only the digest.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools -p 'test_image_tags.py'

The -v1.0.0 tags were republished in place on 2026-10-07. On a machine that
pulled them before, `docker pull repo:tag@digest` fetched the new images
without moving the tags, ensure-images --development saw the tags and called
them LOCAL, doctor checked by digest and said Ready, and development mode
(the default) ran the bare tags: the old images. These tests run
tools/ensure-images.sh, tools/pull-all-images.sh and tools/doctor.sh against
the real catalog with a fake docker on PATH that keeps an image store in a
JSON file. Nothing here touches the network or a Docker daemon.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import images  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOST_TAG = "dhdevspace/auto_mns:tevv-runtime-host-v1.0.0"
HOST_REPO = "dhdevspace/auto_mns"
OLD = "sha256:f540c4fb" + "0" * 56

# A fake docker over a JSON image store:
#   images: {id: [digest, ...]}   (RepoDigests, as repo@digest)
#   tags:   {repo:tag: id}
#   offline: true makes every pull fail
# and every call appended to `calls` as an argv list.
FAKE_DOCKER = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys
    path = os.environ["FAKE_DOCKER_STATE"]
    state = json.load(open(path))
    argv = sys.argv[1:]
    state["calls"].append(argv)

    def repo_of(ref):
        name = ref.split("@")[0]
        last = name.rsplit("/", 1)[-1]
        return name.rsplit(":", 1)[0] if ":" in last else name

    def resolve(ref):
        if "@" in ref:
            want = repo_of(ref) + "@" + ref.split("@", 1)[1]
            return next((i for i, d in state["images"].items() if want in d), None)
        return state["tags"].get(ref)

    def done(code=0, out=""):
        json.dump(state, open(path, "w"))
        sys.stdout.write(out)
        sys.exit(code)

    if argv[:1] == ["version"] or argv[:2] == ["compose", "version"] or argv[:1] == ["info"]:
        done(0, "29.0.0\\n")
    if argv[:2] == ["image", "inspect"]:
        ref = argv[-1]
        image = resolve(ref)
        if image is None:
            sys.stderr.write("Error: No such image: " + ref + "\\n")
            done(1)
        out = "".join(d + "\\n" for d in state["images"][image]) if "-f" in argv else "[{}]\\n"
        done(0, out)
    if argv[:1] == ["pull"]:
        ref = argv[1]
        if state.get("offline") or "@" not in ref:
            sys.stderr.write("pull failed: " + ref + "\\n")
            done(1)
        digest = ref.split("@", 1)[1]
        entry = repo_of(ref) + "@" + digest
        image = resolve(ref) or "img-" + digest[7:15]
        state["images"].setdefault(image, [])
        if entry not in state["images"][image]:
            state["images"][image].append(entry)
        done(0)
    if argv[:1] == ["tag"]:
        image = resolve(argv[1])
        if image is None:
            done(1)
        state["tags"][argv[2]] = image
        done(0)
    sys.stderr.write("fake docker: unhandled " + " ".join(argv) + "\\n")
    done(2)
''')


def _pins() -> list[tuple[str, str]]:
    return images.tag_pins(images.load_catalog())


class FakeDocker:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.bin = workspace / "bin"
        self.bin.mkdir()
        docker = self.bin / "docker"
        docker.write_text(FAKE_DOCKER)
        docker.chmod(0o755)
        self.state_path = workspace / "docker.json"
        self.state = {"images": {}, "tags": {}, "calls": [], "offline": False}

    def at_pin(self, skip: tuple[str, ...] = ()) -> None:
        """Every pinned tag present and at its pin, except `skip`."""
        for ref, digest in _pins():
            if ref in skip or not digest:
                continue
            image = "img-" + digest[7:15]
            self.state["images"][image] = [f"{images_repo(ref)}@{digest}"]
            self.state["tags"][ref] = image

    def save(self) -> None:
        self.state_path.write_text(json.dumps(self.state))

    def load(self) -> dict:
        self.state = json.loads(self.state_path.read_text())
        return self.state

    def digests_of(self, ref: str) -> list[str]:
        state = self.load()
        image = state["tags"].get(ref)
        return [] if image is None else [d.split("@", 1)[1] for d in state["images"][image]]

    def calls(self, verb: str) -> list[list[str]]:
        return [c for c in self.load()["calls"] if c[:1] == [verb]]

    def run(self, *argv: str, **env: str) -> subprocess.CompletedProcess:
        self.save()
        environment = {
            **{k: v for k, v in os.environ.items() if k != "MNS_KEEP_LOCAL_TAGS"},
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FAKE_DOCKER_STATE": str(self.state_path),
            "MNS_AUTHORING_DATA_ROOT": str(self.workspace / "authoring-data"),
            "MNS_PACK_STORE_ROOT": str(self.workspace / "pack-store"),
            "MNS_PACKS_DIR": str(self.workspace / "packs"),
            "PYTHONDONTWRITEBYTECODE": "1",
            **env,
        }
        return subprocess.run([str(ROOT / argv[0]), *argv[1:]], env=environment,
                              capture_output=True, text=True, timeout=120)


def images_repo(ref: str) -> str:
    name = ref.split("@")[0]
    return name.rsplit(":", 1)[0] if ":" in name.rsplit("/", 1)[-1] else name


def _host_pin() -> str:
    return dict(_pins())[HOST_TAG]


class TagPins(unittest.TestCase):
    def test_every_v1_row_pins_its_development_tag(self):
        pins = _pins()
        self.assertIn(HOST_TAG, dict(pins))
        self.assertEqual(sorted(images.pullable_refs(images.load_catalog(), development=True)),
                         [ref for ref, _ in pins])
        for ref, digest in pins:
            self.assertTrue(digest.startswith("sha256:"), ref)

    def test_rows_nothing_pins_have_no_digest(self):
        catalog = images.load_catalog()
        catalog["images"]["v1_runtime_host"]["digest"] = None          # a pending: row
        catalog["images"]["v1_authoring"]["channel"] = "moving"        # a moving tag
        pins = dict(images.tag_pins(catalog))
        self.assertEqual(pins[HOST_TAG], "")
        self.assertEqual(pins["dhdevspace/auto_mns:mns-authoring-v1.0.0"], "")
        self.assertTrue(pins["dhdevspace/auto_mns:mns-stacks-v1.0.0"])


class EnsureImagesDevelopment(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.docker = FakeDocker(Path(self._tmp.name).resolve())

    def tearDown(self):
        self._tmp.cleanup()

    def _stale_host(self) -> None:
        self.docker.at_pin(skip=(HOST_TAG,))
        self.docker.state["images"]["img-old"] = [f"{HOST_REPO}@{OLD}"]
        self.docker.state["tags"][HOST_TAG] = "img-old"

    def test_a_stale_tag_is_pulled_by_digest_and_pointed_at_the_pin(self):
        self._stale_host()
        done = self.docker.run("tools/ensure-images.sh", "--development")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        pin = _host_pin()
        self.assertEqual(self.docker.calls("pull"), [["pull", f"{HOST_REPO}@{pin}"]])
        self.assertEqual(self.docker.calls("tag"), [["tag", f"{HOST_REPO}@{pin}", HOST_TAG]])
        self.assertEqual(self.docker.digests_of(HOST_TAG), [pin])
        self.assertIn(f"TAGGED  {HOST_TAG} -> {pin} (it was {OLD})", done.stdout)

    def test_offline_with_the_pin_present_it_retags_without_the_network(self):
        self._stale_host()
        pin = _host_pin()
        self.docker.state["images"]["img-pin"] = [f"{HOST_REPO}@{pin}"]
        self.docker.state["offline"] = True
        done = self.docker.run("tools/ensure-images.sh", "--development")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.docker.calls("pull"), [])
        self.assertEqual(self.docker.digests_of(HOST_TAG), [pin])

    def test_tags_already_at_their_pins_are_untouched(self):
        self.docker.at_pin()
        before = dict(self.docker.state["tags"])
        done = self.docker.run("tools/ensure-images.sh", "--development")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.docker.calls("pull"), [])
        self.assertEqual(self.docker.calls("tag"), [])
        self.assertEqual(self.docker.load()["tags"], before)
        self.assertNotIn("TAGGED", done.stdout)

    def test_an_absent_tag_gets_the_pinned_digest(self):
        self.docker.at_pin(skip=(HOST_TAG,))
        done = self.docker.run("tools/ensure-images.sh", "--development")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.docker.digests_of(HOST_TAG), [_host_pin()])

    def test_a_local_build_is_kept(self):
        # Classic image store: a build has no RepoDigests at all.
        self.docker.at_pin(skip=(HOST_TAG,))
        self.docker.state["images"]["img-built"] = []
        self.docker.state["tags"][HOST_TAG] = "img-built"
        done = self.docker.run("tools/ensure-images.sh", "--development")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.docker.load()["tags"][HOST_TAG], "img-built")
        self.assertIn("built here", done.stdout)

    def test_keep_local_tags_keeps_a_stale_tag(self):
        self._stale_host()
        done = self.docker.run("tools/ensure-images.sh", "--development", MNS_KEEP_LOCAL_TAGS="1")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.docker.calls("pull"), [])
        self.assertEqual(self.docker.load()["tags"][HOST_TAG], "img-old")

    def test_dry_run_reports_the_stale_tag_and_changes_nothing(self):
        self._stale_host()
        done = self.docker.run("tools/ensure-images.sh", "--development", "--dry-run")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn(f"STALE   {HOST_TAG}", done.stdout)
        self.assertEqual(self.docker.calls("pull") + self.docker.calls("tag"), [])

    def test_production_mode_is_unchanged(self):
        # Exact refs: the pinned image is present, so nothing is pulled or
        # tagged, even though the bare tag is stale.
        self._stale_host()
        pin = _host_pin()
        self.docker.state["images"]["img-pin"] = [f"{HOST_REPO}@{pin}"]
        done = self.docker.run("tools/ensure-images.sh", "--production")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.docker.calls("pull") + self.docker.calls("tag"), [])
        self.assertEqual(self.docker.load()["tags"][HOST_TAG], "img-old")


class PullAllImages(unittest.TestCase):
    def test_pulling_by_digest_points_the_tag_at_the_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            docker = FakeDocker(Path(directory).resolve())
            docker.at_pin(skip=(HOST_TAG,))
            docker.state["images"]["img-old"] = [f"{HOST_REPO}@{OLD}"]
            docker.state["tags"][HOST_TAG] = "img-old"
            done = docker.run("tools/pull-all-images.sh")
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
            pin = _host_pin()
            self.assertIn(["pull", f"{HOST_TAG}@{pin}"], docker.calls("pull"))
            self.assertEqual(docker.calls("tag"), [["tag", f"{HOST_REPO}@{pin}", HOST_TAG]])
            self.assertEqual(docker.digests_of(HOST_TAG), [pin])
            for ref, digest in _pins():
                self.assertEqual(docker.digests_of(ref), [digest], ref)


class Doctor(unittest.TestCase):
    def _run(self, *argv: str, stale: bool, **env: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as directory:
            docker = FakeDocker(Path(directory).resolve())
            docker.at_pin()
            if stale:
                pin = _host_pin()
                docker.state["images"]["img-old"] = [f"{HOST_REPO}@{OLD}"]
                docker.state["tags"][HOST_TAG] = "img-old"
                self.assertIn(f"{HOST_REPO}@{pin}", docker.state["images"]["img-" + pin[7:15]])
            done = docker.run("tools/doctor.sh", *argv, **env)
            self.assertEqual(docker.calls("pull") + docker.calls("tag"), [])
            return done

    def test_a_stale_tag_is_a_problem_with_the_fix(self):
        done = self._run("--development", stale=True)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn(f"STALE TAG: {HOST_TAG} is {OLD}, but the catalog pins {_host_pin()}", done.stdout)
        self.assertIn("make ensure-images", done.stderr)
        self.assertNotIn("Ready", done.stdout)

    def test_development_is_the_default(self):
        done = self._run(stale=True)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("STALE TAG", done.stdout)

    def test_tags_at_their_pins_are_ready(self):
        done = self._run("--development", stale=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("Ready", done.stdout)

    def test_production_checks_digests_only(self):
        done = self._run("--production", stale=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("Ready", done.stdout)


if __name__ == "__main__":
    unittest.main()
