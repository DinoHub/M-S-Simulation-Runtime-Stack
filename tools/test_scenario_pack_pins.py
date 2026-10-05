"""Every pack a shipped scenario or OSMO file pins is in the pack release lock.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools -p 'test_scenario_pack_pins.py'

The lock holds one version per pack. A scenario that still names a version
the lock dropped generates fine and then fails at stage time on a machine
that installed only the lock, so the pins are checked here, offline.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any, Iterator

import yaml

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "packs" / "v1.0.0.lock.json"
PINNED_DIRS = ("scenarios", "osmo")
ARTIFACT_DIGEST_RE = re.compile(r"artifact_digest[\"']?\s*:\s*[\"']?(sha256:[0-9a-f]{64})")


def pins(node: Any, where: str) -> Iterator[tuple[str, dict]]:
    """Every mapping that names a pack: an `id` plus a `version` or `artifact_digest`."""
    if isinstance(node, dict):
        if isinstance(node.get("id"), str) and ("version" in node or "artifact_digest" in node):
            yield where, node
        for key, value in node.items():
            yield from pins(value, f"{where}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from pins(value, f"{where}[{i}]")


def pinned_files() -> list[Path]:
    return sorted(p for d in PINNED_DIRS for p in (ROOT / d).rglob("*")
                  if p.suffix in {".yaml", ".yml", ".json"} and p.is_file())


class ScenarioPackPins(unittest.TestCase):
    lock = {p["id"]: p for p in json.loads(LOCK.read_text(encoding="utf-8"))["packs"]}

    def test_the_lock_holds_one_version_per_pack(self):
        packs = json.loads(LOCK.read_text(encoding="utf-8"))["packs"]
        ids = [(p["kind"], p["id"]) for p in packs]
        self.assertEqual(len(ids), len(set(ids)), ids)

    def test_every_pinned_pack_is_in_the_lock(self):
        checked = 0
        for path in pinned_files():
            text = path.read_text(encoding="utf-8")
            try:
                docs = [json.loads(text)] if path.suffix == ".json" else list(yaml.safe_load_all(text))
            except (ValueError, yaml.YAMLError):
                # A Jinja template (the OSMO workflow) or OpenCV YAML (OpenVINS
                # calibration); the raw-text test below still reads its digests.
                continue
            for where, pin in pins(docs, path.relative_to(ROOT).as_posix()):
                locked = self.lock.get(pin["id"])
                if locked is None:
                    continue  # an id that is not a pack (a vehicle, a sensor)
                checked += 1
                with self.subTest(pin=where):
                    if "version" in pin:
                        self.assertEqual(str(pin["version"]), locked["version"],
                                         f"{where}: {pin['id']} is not the locked version")
                    if "artifact_digest" in pin:
                        self.assertEqual(pin["artifact_digest"], locked["artifact_digest"],
                                         f"{where}: {pin['id']} is not the locked artifact")
        self.assertGreater(checked, 0, "no pack pins found; the walk is broken")

    def test_every_artifact_digest_in_the_text_is_locked(self):
        locked = {p["artifact_digest"] for p in self.lock.values()}
        for path in pinned_files():
            for digest in ARTIFACT_DIGEST_RE.findall(path.read_text(encoding="utf-8")):
                with self.subTest(path=path.relative_to(ROOT).as_posix()):
                    self.assertIn(digest, locked)


if __name__ == "__main__":
    unittest.main()
