"""packs/channels.json must agree with the Makefile, which is the authority.

The dashboard's Content phase needs to list the channels without parsing Make
syntax, so the list is declared as data. That duplication is only safe if
something fails when the two drift, which is what this does.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools -p 'test_channels.py'
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def makefile_channels() -> dict[str, dict[str, str]]:
    """The CHANNEL_* assignments in each `ifeq ($(CHANNEL),<name>)` block."""
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    blocks = re.findall(
        r"(?:else )?ifeq \(\$\(CHANNEL\),(\w+)\)\n(.*?)(?=(?:else )?ifeq \(\$\(CHANNEL\)|\nelse\n)",
        text, re.S)
    channels = {}
    for name, body in blocks:
        values = dict(re.findall(r"^(CHANNEL_\w+) := ?(.*)$", body, re.M))
        channels[name] = {k: v.strip() for k, v in values.items()}
    return channels


class ChannelsDeclarationMatchesTheMakefile(unittest.TestCase):
    def setUp(self):
        self.declared = json.loads((ROOT / "packs" / "channels.json").read_text(encoding="utf-8"))
        self.by_name = {c["name"]: c for c in self.declared["channels"]}
        self.makefile = makefile_channels()

    def test_the_same_channels_exist_on_both_sides(self):
        self.assertEqual(sorted(self.by_name), sorted(self.makefile))

    def test_the_default_channel_matches(self):
        match = re.search(r"^CHANNEL \?= (\w+)$", (ROOT / "Makefile").read_text(), re.M)
        self.assertEqual(self.declared["default"], match.group(1))

    def test_every_path_matches(self):
        keys = {"lock": "CHANNEL_LOCK", "contract": "CHANNEL_CONTRACT",
                "store": "CHANNEL_STORE", "authoring_data": "CHANNEL_DATA",
                "image_set": "CHANNEL_IMAGE_SET"}
        for name, declared in self.by_name.items():
            for field, variable in keys.items():
                self.assertEqual(declared[field], self.makefile[name][variable],
                                 f"{name}.{field} disagrees with {variable}")

    def test_the_authoring_contract_matches_including_when_absent(self):
        for name, declared in self.by_name.items():
            expected = self.makefile[name].get("CHANNEL_AUTHORING_CONTRACT", "") or None
            self.assertEqual(declared["authoring_contract"], expected, name)

    def test_seeding_matches(self):
        for name, declared in self.by_name.items():
            expected = self.makefile[name]["CHANNEL_SEED_DEFAULTS"] == "1"
            self.assertEqual(declared["seed_authoring_defaults"], expected, name)

    def test_every_declared_file_exists(self):
        for name, declared in self.by_name.items():
            for field in ("lock", "contract"):
                self.assertTrue((ROOT / declared[field]).is_file(),
                                f"{name}.{field}: {declared[field]} does not exist")


if __name__ == "__main__":
    unittest.main()
