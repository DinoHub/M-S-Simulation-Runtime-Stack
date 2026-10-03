"""Reading the platform's stack contract (mns.stack.contract.v1)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class ContractError(RuntimeError):
    pass


def load(path: str | None = None) -> dict[str, Any]:
    path = path or os.environ.get("MNS_CONTRACT", "/cfg/stack/contract.json")
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise ContractError(f"cannot read the stack contract at {path}: {exc}") from exc
    if doc.get("schema") != "mns.stack.contract.v1":
        raise ContractError(f"{path} is not a mns.stack.contract.v1 contract")
    return doc


def vehicle(contract: dict[str, Any], index: int | None = None) -> dict[str, Any]:
    index = int(index if index is not None else os.environ.get("MNS_VEHICLE_INDEX", 1))
    for v in contract.get("vehicles", []):
        if v.get("index") == index:
            return v
    raise ContractError(f"the contract has no vehicle {index}")


def stereo_pair(vehicle_doc: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """(pair, left camera, right camera) for the vehicle's primary stereo pair."""
    roles = vehicle_doc["roles"]
    name = roles.get("stereo_primary")
    if not name:
        cams = ", ".join(roles.get("cameras") or {}) or "none"
        raise ContractError(f"mac-vo needs a stereo pair; this scenario has cameras: {cams}, and no "
                            "two of them form a rectified pair (same size, FOV and orientation, "
                            "offset sideways)")
    pair = roles["stereo"][name]
    cams = roles["cameras"]
    return pair, cams[pair["left"]], cams[pair["right"]]
