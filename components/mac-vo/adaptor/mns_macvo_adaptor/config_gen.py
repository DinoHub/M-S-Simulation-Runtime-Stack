"""`mns-macvo-adaptor config`: write MAC-VO's config for this stack.

MAC-VO reads one YAML: its Odometry pipeline plus a Camera block (fx, fy, cx,
cy and the stereo baseline bl) for the images it is fed. The pipeline comes
from the package's template (MAC-VO-ROS2's own config/zedcam_macvo.yaml); the
Camera block comes from the stack contract's primary stereo pair. MAC-VO
scales and crops the intrinsics itself when it resizes to 320x320
(SmartResizeFrame), so the native values go in.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import yaml

from . import contract as contract_mod

TEMPLATE = os.environ.get("MACVO_TEMPLATE", "/cfg/component/macvo-template")
OUT_DIR = Path(os.environ.get("MACVO_WORK", "/work"))


def camera_block(left: dict, pair: dict) -> dict:
    if not left.get("rectified"):
        raise contract_mod.ContractError(f"camera {pair['left']} is not rectified; mac-vo expects "
                                         "a rectified pinhole stereo pair")
    k = left.get("K")
    if not k:
        raise contract_mod.ContractError(f"camera {pair['left']} has no pinhole intrinsics")
    return {"fx": float(k[0]), "fy": float(k[4]), "cx": float(k[2]), "cy": float(k[5]),
            "bl": float(pair["baseline_m"])}


def build_config(template: dict, contract: dict, index: int | None = None) -> tuple[dict, dict]:
    vehicle = contract_mod.vehicle(contract, index)
    pair, left, right = contract_mod.stereo_pair(vehicle)
    config = dict(template)
    config["Camera"] = camera_block(left, pair)
    check = {
        "left": {"topic": left["image"], "encoding": left.get("encoding"),
                 "size": [left.get("width"), left.get("height")]},
        "right": {"topic": right["image"], "encoding": right.get("encoding")},
        "baseline_m": pair["baseline_m"],
        "camera": config["Camera"],
    }
    return config, check


def main() -> int:
    try:
        contract = contract_mod.load()
        template = yaml.safe_load(Path(TEMPLATE).read_text()) or {}
        config, check = build_config(template, contract)
    except (contract_mod.ContractError, OSError, yaml.YAMLError) as exc:
        print(f"mac-vo config: {exc}", file=sys.stderr)
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_DIR / "macvo.yaml.tmp"
    tmp.write_text(yaml.safe_dump(config, sort_keys=False))
    tmp.replace(OUT_DIR / "macvo.yaml")  # atomic: macvo waits for this file
    (OUT_DIR / "contract-check.json").write_text(json.dumps(check, indent=2) + "\n")
    print(f"mac-vo config: {json.dumps(check['camera'])} from {check['left']['topic']} + "
          f"{check['right']['topic']}")
    return 0
