"""Autopilot profile lookup."""
from __future__ import annotations

from .ardupilot import ArduPilotProfile
from .base import AutopilotProfile
from .px4 import Px4Profile
from ..errors import SimstackError


_PROFILES: dict[str, AutopilotProfile] = {
    profile.type_name: profile
    for profile in (
        ArduPilotProfile(),
        Px4Profile(),
    )
}


def supported_autopilots() -> tuple[str, ...]:
    return tuple(sorted(_PROFILES))


def get_autopilot_profile(name: str) -> AutopilotProfile:
    key = name.lower()
    try:
        return _PROFILES[key]
    except KeyError as e:
        supported = ", ".join(supported_autopilots())
        raise SimstackError(f"autopilot.type must be one of: {supported}") from e
