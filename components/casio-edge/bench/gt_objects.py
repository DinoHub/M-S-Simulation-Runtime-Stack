#!/usr/bin/env python3
"""Log sim-truth poses of the drone and the level's people over AirSim RPC.

  gt_objects.py --out DIR [--host host.docker.internal] [--rate 2] [--pattern REGEX]
                [--wait-s 600] [--settle-s 0]
  gt_objects.py --snapshot [--pattern REGEX]     print the people once, as JSON

As the casio-edge component's truth sidecar it starts with the stack, before
the simulator answers: --wait-s keeps trying to connect, and --settle-s waits
for the drone to sit still that long before taking its pose as the origin.

Needs only python3 + msgpack (the ros2 bridge image has both). Positions are
ENU metres relative to where the drone rests when this starts (PX4's home),
the same axes and origin as casio's map frame. Everything, the drone
included, comes from simGetObjectPose (Unreal world); simGetVehiclePose is
relative to the spawn point instead, so the two are never mixed. Writes
gt_objects.csv (wall time, name, e, n, u) and gt_vehicle.csv until SIGTERM.
"""
import argparse
import csv
import itertools
import json
import re
import signal
import socket
import time

import msgpack

PEOPLE = r"Mannequin|DynamicObstaclePawn|Person|Pedestrian|Human|Character"


class Rpc:
    def __init__(self, host, port, wait_s=0.0):
        deadline = time.time() + wait_s
        while True:
            try:
                self.sock = socket.create_connection((host, port), timeout=30)
                break
            except OSError:
                if time.time() >= deadline:
                    raise
                time.sleep(2.0)
        self.ids = itertools.count()
        self.unpacker = msgpack.Unpacker(raw=False)

    def call(self, method, *args):
        mid = next(self.ids)
        self.sock.sendall(msgpack.packb([0, mid, method, list(args)]))
        while True:
            for msg in self.unpacker:
                if msg[1] == mid:
                    if msg[2]:
                        raise RuntimeError(f"{method}: {msg[2]}")
                    return msg[3]
            self.unpacker.feed(self.sock.recv(1 << 20))


def enu(pose, origin):
    p = pose["position"]
    return (p["y_val"] - origin[1], p["x_val"] - origin[0], -(p["z_val"] - origin[2]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--host", default="host.docker.internal")
    ap.add_argument("--port", type=int, default=41451)
    ap.add_argument("--vehicle", default="Drone1")
    ap.add_argument("--rate", type=float, default=2.0)
    ap.add_argument("--pattern", default=PEOPLE)
    ap.add_argument("--origin", help="world NED to use as origin (default: the drone, now)")
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--wait-s", type=float, default=0.0, help="keep trying to connect this long")
    ap.add_argument("--settle-s", type=float, default=0.0,
                    help="take the origin once the drone has moved < 2 cm for this long")
    a = ap.parse_args()

    rpc = Rpc(a.host, a.port, a.wait_s)
    if a.origin:
        origin = [float(v) for v in a.origin.split(",")]
    else:
        def drone():
            p = rpc.call("simGetObjectPose", a.vehicle, False)["position"]
            return [p["x_val"], p["y_val"], p["z_val"]]
        origin, still_since = drone(), time.time()
        while time.time() - still_since < a.settle_s:
            time.sleep(0.5)
            now = drone()
            if any(v != v for v in now) or max(abs(x - y) for x, y in zip(now, origin)) > 0.02:
                origin, still_since = now, time.time()
    names = sorted(n for n in rpc.call("simListSceneObjects", ".*") if re.search(a.pattern, n))

    if a.snapshot:
        print(json.dumps({n: enu(rpc.call("simGetObjectPose", n, False), origin) for n in names},
                         indent=1))
        return

    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    with open(f"{a.out}/gt_objects.csv", "w", newline="") as fo, \
            open(f"{a.out}/gt_vehicle.csv", "w", newline="") as fv:
        wo, wv = csv.writer(fo), csv.writer(fv)
        wo.writerow(["wall", "name", "e", "n", "u"])
        wv.writerow(["wall", "e", "n", "u", "qw", "qx", "qy", "qz"])
        period = 1.0 / a.rate
        while not stop:
            t = time.time()
            v = rpc.call("simGetObjectPose", a.vehicle, False)
            q = v["orientation"]
            wv.writerow([f"{t:.3f}", *(f"{x:.3f}" for x in enu(v, origin)),
                         q["w_val"], q["x_val"], q["y_val"], q["z_val"]])
            for n in names:
                wo.writerow([f"{t:.3f}", n, *(f"{x:.3f}" for x in enu(rpc.call("simGetObjectPose", n, False), origin))])
            fo.flush()
            fv.flush()
            time.sleep(max(0.0, period - (time.time() - t)))


if __name__ == "__main__":
    main()
