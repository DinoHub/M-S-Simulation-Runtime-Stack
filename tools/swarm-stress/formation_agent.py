"""One vehicle's part of a formation flight, run INSIDE its PX4 container.

formation.py pipes this to `python3 -` in every PX4 container at once
(pymavlink ships in the PX4 image). Over MAVLink only, through the
container's mavlink-router, it:

  1. reads where the vehicle is (GLOBAL_POSITION_INT),
  2. uploads a mission: take off to --alt, fly --route (north/east metre
     offsets from THIS vehicle's start, so every vehicle flies the same shape
     shifted by its place in the grid and the grid keeps its formation), then
     RETURN_TO_LAUNCH, which brings it back to its start and lands it,
  3. waits for the shared --start-at wall-clock instant, arms and starts the
     mission (all vehicles together),
  4. follows it to the landing and prints one JSON result line.

PX4: through the router's MAVROS endpoint (UDP 14555), which a stack without
MAVROS leaves free; the Control endpoint (14560) belongs to the dashboard's
flight controls. ArduPilot: formation.py passes the SITL port to use.

    python3 - --autopilot px4 --alt 25 --speed 4 --route "0,-60;40,-60;40,0" --start-at 1791300000 < formation_agent.py
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time

from pymavlink import mavutil

M = mavutil.mavlink
EARTH_R = 6378137.0
PX4_AUTO, PX4_AUTO_MISSION = 4, 4
COPTER_AUTO, COPTER_GUIDED = 3, 4
GLOBAL_POSITION_INT, EXTENDED_SYS_STATE = 33, 245


def say(msg: str) -> None:
    print(f"[agent] {msg}", file=sys.stderr, flush=True)


def heartbeats(link, stop: threading.Event) -> None:
    # The router forwards to a UDP server endpoint's last peer only while it
    # hears from it; a GCS heartbeat at 1 Hz keeps this link alive.
    while not stop.is_set():
        link.mav.heartbeat_send(M.MAV_TYPE_GCS, M.MAV_AUTOPILOT_INVALID, 0, 0, 0)
        stop.wait(1.0)


def offset(lat: float, lon: float, north: float, east: float) -> tuple[int, int]:
    dlat = north / EARTH_R
    dlon = east / (EARTH_R * math.cos(math.radians(lat)))
    return int((lat + math.degrees(dlat)) * 1e7), int((lon + math.degrees(dlon)) * 1e7)


def command(link, cmd: int, *params: float, timeout: float = 10.0) -> int:
    p = list(params) + [0.0] * (7 - len(params))
    link.mav.command_long_send(link.target_system, link.target_component, cmd, 0, *p)
    ack = link.recv_match(type="COMMAND_ACK", blocking=True, timeout=timeout,
                          condition=f"COMMAND_ACK.command=={cmd}")
    return ack.result if ack else -1


def stream(link, msg_id: int, hz: float) -> None:
    command(link, M.MAV_CMD_SET_MESSAGE_INTERVAL, msg_id, 1e6 / hz, timeout=3)


def position(link, wait_s: float = 120.0):
    """The vehicle's GLOBAL_POSITION_INT. Under load a link can go a while
    without one (two of 60 PX4s gave none in 30 s), so ask for the stream
    explicitly and keep asking."""
    deadline = time.time() + wait_s
    while time.time() < deadline:
        stream(link, GLOBAL_POSITION_INT, 5)
        pos = link.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=10)
        if pos is not None and (pos.lat or pos.lon):
            return pos
    raise RuntimeError(f"no GLOBAL_POSITION_INT in {wait_s:g}s")


def upload(link, items: list[dict]) -> None:
    link.mav.mission_clear_all_send(link.target_system, link.target_component, M.MAV_MISSION_TYPE_MISSION)
    link.recv_match(type="MISSION_ACK", blocking=True, timeout=5)
    link.mav.mission_count_send(link.target_system, link.target_component, len(items),
                                M.MAV_MISSION_TYPE_MISSION)
    deadline = time.time() + 30
    while time.time() < deadline:
        msg = link.recv_match(type=["MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"],
                              blocking=True, timeout=5)
        if msg is None:
            continue
        if msg.get_type() == "MISSION_ACK":
            if msg.type != M.MAV_MISSION_ACCEPTED:
                raise RuntimeError(f"mission rejected: {msg.type}")
            return
        it = items[msg.seq]
        link.mav.mission_item_int_send(
            link.target_system, link.target_component, msg.seq, it["frame"], it["cmd"],
            0, 1, *it["params"], it["x"], it["y"], it["z"], M.MAV_MISSION_TYPE_MISSION)
    raise RuntimeError("mission upload timed out")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--autopilot", choices=("px4", "ardupilot"), default="px4")
    ap.add_argument("--alt", type=float, required=True)
    ap.add_argument("--speed", type=float, required=True)
    ap.add_argument("--route", required=True, help='"n,e;n,e;..." metres from the start')
    ap.add_argument("--start-at", type=float, required=True)
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--url", default="udpout:127.0.0.1:14555")
    a = ap.parse_args()
    route = [tuple(float(v) for v in leg.split(",")) for leg in a.route.split(";") if leg]
    result = {"ok": False, "stage": "connect"}
    stop = threading.Event()
    try:
        link = mavutil.mavlink_connection(a.url, source_system=245, source_component=190)
        threading.Thread(target=heartbeats, args=(link, stop), daemon=True).start()
        if not link.wait_heartbeat(timeout=30):
            raise RuntimeError("no heartbeat from the autopilot")
        result["stage"] = "position"
        pos = position(link)
        stream(link, EXTENDED_SYS_STATE, 2)
        lat0, lon0 = pos.lat / 1e7, pos.lon / 1e7
        result["home"] = [round(lat0, 7), round(lon0, 7)]

        result["stage"] = "upload"
        # Come back at the formation's altitude rather than PX4's 60 m default.
        ardu = a.autopilot == "ardupilot"
        if ardu:
            link.mav.param_set_send(link.target_system, link.target_component, b"RTL_ALT",
                                    a.alt * 100, M.MAV_PARAM_TYPE_REAL32)  # centimetres
        else:
            link.mav.param_set_send(link.target_system, link.target_component, b"RTL_RETURN_ALT",
                                    a.alt, M.MAV_PARAM_TYPE_REAL32)
        rel = M.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT
        here = offset(lat0, lon0, 0, 0)
        # ArduPilot keeps item 0 for home and overwrites it; PX4 flies item 0.
        items = ([{"frame": M.MAV_FRAME_GLOBAL_INT, "cmd": M.MAV_CMD_NAV_WAYPOINT, "params": (0, 0, 0, 0),
                   "x": here[0], "y": here[1], "z": 0}] if ardu else []) + [
            {"frame": rel, "cmd": M.MAV_CMD_NAV_TAKEOFF, "params": (0, 0, 0, math.nan), "x": here[0], "y": here[1], "z": a.alt},
            {"frame": M.MAV_FRAME_MISSION, "cmd": M.MAV_CMD_DO_CHANGE_SPEED, "params": (1, a.speed, -1, 0), "x": 0, "y": 0, "z": 0},
        ]
        for north, east in route:
            x, y = offset(lat0, lon0, north, east)
            items.append({"frame": rel, "cmd": M.MAV_CMD_NAV_WAYPOINT, "params": (0, 1.0, 0, math.nan), "x": x, "y": y, "z": a.alt})
        items.append({"frame": M.MAV_FRAME_MISSION, "cmd": M.MAV_CMD_NAV_RETURN_TO_LAUNCH, "params": (0, 0, 0, 0), "x": 0, "y": 0, "z": 0})
        upload(link, items)
        last = len(items) - 1

        result["stage"] = "wait_start"
        while time.time() < a.start_at:
            link.recv_match(blocking=True, timeout=0.5)

        result["stage"] = "arm"
        t0 = time.time()
        flag = M.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
        if ardu:
            # Copter arms in GUIDED, not AUTO; switched to AUTO on the ground it
            # waits for MISSION_START (there is no throttle stick to raise).
            if command(link, M.MAV_CMD_DO_SET_MODE, flag, COPTER_GUIDED) != 0:
                raise RuntimeError("GUIDED refused")
        elif command(link, M.MAV_CMD_DO_SET_MODE, flag, PX4_AUTO, PX4_AUTO_MISSION) != 0:
            raise RuntimeError("AUTO.MISSION refused")
        for _ in range(10):
            if command(link, M.MAV_CMD_COMPONENT_ARM_DISARM, 1) == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("arming refused")
        if ardu and command(link, M.MAV_CMD_DO_SET_MODE, flag, COPTER_AUTO) != 0:
            raise RuntimeError("AUTO refused")
        if command(link, M.MAV_CMD_MISSION_START, 1 if ardu else 0, last) not in (0, -1):
            raise RuntimeError("MISSION_START refused")

        result["stage"] = "flying"
        flown, reached, deadline = False, -1, time.time() + a.timeout
        while time.time() < deadline:
            msg = link.recv_match(type=["MISSION_ITEM_REACHED", "EXTENDED_SYS_STATE", "HEARTBEAT",
                                        "GLOBAL_POSITION_INT"], blocking=True, timeout=2)
            if msg is None:
                continue
            kind = msg.get_type()
            down = False
            if kind == "MISSION_ITEM_REACHED" and msg.seq > reached:
                reached = msg.seq
            elif kind == "GLOBAL_POSITION_INT" and msg.relative_alt > 3000:
                flown = True
            elif kind == "EXTENDED_SYS_STATE":
                if msg.landed_state == M.MAV_LANDED_STATE_IN_AIR:
                    flown = True
                down = msg.landed_state == M.MAV_LANDED_STATE_ON_GROUND
            elif kind == "HEARTBEAT" and msg.type != M.MAV_TYPE_GCS:
                # Both autopilots disarm on their own once landed.
                down = not (msg.base_mode & M.MAV_MODE_FLAG_SAFETY_ARMED)
            if flown and down:
                result.update(ok=True, stage="landed", reached=reached, flight_s=round(time.time() - t0, 1))
                break
        else:
            result.update(stage="timeout", reached=reached)
    except Exception as exc:  # noqa: BLE001 - one result line whatever happens
        result["error"] = str(exc)
    finally:
        stop.set()
    print(json.dumps(result), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
