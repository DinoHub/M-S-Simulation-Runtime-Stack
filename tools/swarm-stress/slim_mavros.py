#!/usr/bin/env python3
"""slim_mavros.py <generated stack dir>

Mount mavros_px4_slim.yaml into every AirSim bridge of a generated stack and
select it (MAVROS_CONFIG in the stack's .env). Edits the generated compose
file; regenerating the stack undoes it.
"""
import sys, shutil, yaml
from pathlib import Path
stack = Path(sys.argv[1])
cfg = stack / "config" / "mavros_px4_slim.yaml"
shutil.copy(Path(__file__).with_name("mavros_px4_slim.yaml"), cfg)
c = yaml.safe_load((stack / "docker-compose.yml").read_text())
target = "/ws/install/airsim_mavros_bringup/share/airsim_mavros_bringup/config/mavros_px4_slim.yaml"
n = 0
for name, s in c["services"].items():
    if name.startswith("airsim_bridge_"):
        vols = s.setdefault("volumes", [])
        if not any(target in str(v) for v in vols):
            vols.append(f"./config/mavros_px4_slim.yaml:{target}:ro")
        n += 1
(stack / "docker-compose.yml").write_text(yaml.safe_dump(c, sort_keys=False))
env = stack / ".env"
lines = [l for l in env.read_text().splitlines() if not l.startswith("MAVROS_CONFIG=")]
env.write_text("\n".join(lines + ["MAVROS_CONFIG=mavros_px4_slim.yaml"]) + "\n")
print(f"patched {n} bridges")
