# SUPER as a component (work in progress)

Image: `dhdevspace/auto_mns:super_runtime-ms.1`, a local build of the
local-planner adaptor scaffold's SUPER bundle (super_planner@development
26f56b6) with the scaffold manifest's external topics set to the M&S stack
contract: odometry `/ground_truth/odom`, ArduPilot, setpoints
`/Drone1/mavros/setpoint_raw/local`. Built with `run_dev.sh --bootstrap`
(needs the GitHub host key in the dev container's `~/.ssh/known_hosts` and a
pty), `build_adaptor.sh`, then `docker build -f .devcontainer/Dockerfile.runtime`
(the scaffold's `bake_runtime.sh` insists on a bag test whose data is on
Google Drive).

## Status (3 Oct 2026)

SUPER starts in the air (files/wait_airborne.py), receives the course's goal,
plans and flies, but slowly: every replan takes 2.5 to 9 s of wall time at
3 to 17 % CPU, its main thread mostly in a futex wait. With the upstream
`replan_forward_dt: 0.1` every replan is discarded ("Replan over time") and
the vehicle never moves; raised to 3.0 it crept 20 m toward a goal in 60 s.

Changed from the upstream config, each commented in files/config/click.yaml:
- `rog_map/resolution` 0.2 (0.1 held 21 GB for the 51 x 51 x 16 m window);
- `virtual_ground_height` -2.2 (heights are in the odometry frame anchored
  at the spawn, 2.3 m above the yard ground);
- `point_filt_num` 2;
- DEBUG only: `replan_forward_dt` 3.0, `print_log`, `detailed_log_en`.

Next: find what SUPER waits on during a replan (gdb from a sidecar sharing
its pid namespace with SYS_PTRACE attached, but resolved no symbols yet), or
ask the SUPER maintainers what replan time to expect on a 50 m outdoor map.
