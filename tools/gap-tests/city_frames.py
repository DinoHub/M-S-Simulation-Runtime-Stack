#!/usr/bin/env python3
"""city_frames.py FLIGHT_ONBOARD FLIGHT_CHASE OUT_DIR

Pick route moments from ground truth and save, per moment: the onboard front/back fisheye
frames (from FLIGHT_ONBOARD's bag) and the chase-view frame (from FLIGHT_CHASE's screen capture,
matched by position on the same route). Writes OUT_DIR/moments.json.
"""
import glob, json, os, sys

import cv2
import numpy as np
from mcap_ros2.reader import read_ros2_messages

MOMENTS = [  # name, target ENU (x, y) or None, phase filter
    ("Parked at the spawn", None, "parked"),
    ("Climbing to 3 m", None, "climb"),
    ("South-east street, halfway", (23.0, -19.3), "out1"),
    ("South-east street, far end", (45.96, -38.57), "out1"),
    ("Back over the start", (0.0, 0.0), "mid"),
    ("North-north-west street, far end", (-21.13, 45.32), "out2"),
    ("Landed", None, "landed"),
]


def read(bag, topics):
    out = {t: [] for t in topics}
    for f in sorted(glob.glob(os.path.join(bag, "*.mcap"))):
        for m in read_ros2_messages(f, topics=topics):
            out[m.channel.topic].append(m)
    return out


def truth_track(msgs):
    T, P = [], []
    for m in msgs:
        h = m.ros_msg.header.stamp
        T.append(h.sec + h.nanosec * 1e-9)
        p = m.ros_msg.pose.pose.position
        P.append((p.x, p.y, p.z))
    return np.array(T), np.array(P)


def pick_times(T, P):
    z0 = P[:50, 2].mean()
    up = np.where(P[:, 2] > z0 + 1.0)[0]
    t_take, t_land = T[up[0]], T[up[-1]]
    out = {}
    # route phases by time between takeoff and landing, split at the far ends
    d_se = np.hypot(P[:, 0] - 45.96, P[:, 1] + 38.57)
    d_nw = np.hypot(P[:, 0] + 21.13, P[:, 1] - 45.32)
    i_se = int(np.argmin(d_se)); i_nw = int(np.argmin(d_nw))
    for name, xy, phase in MOMENTS:
        if phase == "parked":
            t = T[0] + 3.0
        elif phase == "climb":
            t = t_take + 1.5
        elif phase == "landed":
            t = t_land + 4.0
        else:
            if phase == "out1":
                sel = (T > t_take) & (T <= T[i_se])
            elif phase == "mid":
                sel = (T > T[i_se]) & (T < T[i_nw])
            else:
                sel = (T > T[i_se]) & (T <= T[i_nw] + 0.5)
            idx = np.where(sel)[0]
            k = idx[np.argmin(np.hypot(P[idx, 0] - xy[0], P[idx, 1] - xy[1]))]
            t = T[k]
        out[name] = float(t)
    return out


def main():
    onboard, chase, out = sys.argv[1:4]
    os.makedirs(out, exist_ok=True)
    # onboard frames: truth first, then stream images keeping the nearest per moment
    M = read(os.path.join(onboard, "bag"), ["/ground_truth/odom"])
    T, P = truth_track(M["/ground_truth/odom"])
    times = pick_times(T, P)
    best = {}
    for f in sorted(glob.glob(os.path.join(onboard, "bag", "*.mcap"))):
        for m in read_ros2_messages(f, topics=["/fisheye_front/image_raw", "/fisheye_back/image_raw"]):
            cam = "front" if "front" in m.channel.topic else "back"
            h = m.ros_msg.header.stamp; st = h.sec + h.nanosec * 1e-9
            for i, (name, _, _) in enumerate(MOMENTS):
                d = abs(st - times[name])
                if d < best.get((i, cam), (1e9, None))[0]:
                    r = m.ros_msg
                    best[(i, cam)] = (d, np.frombuffer(bytes(r.data), np.uint8).reshape(r.height, r.width, -1)[..., :3].copy())
    res = []
    for i, (name, _, _) in enumerate(MOMENTS):
        t = times[name]
        k = int(np.argmin(np.abs(T - t)))
        rec = {"name": name, "t_rel": round(t - T[0], 1), "pos": [round(float(v), 1) for v in P[k]], "t_sim": t}
        for cam in ("front", "back"):
            p = os.path.join(out, f"m{i}_{cam}.jpg")
            cv2.imwrite(p, best[(i, cam)][1], [cv2.IMWRITE_JPEG_QUALITY, 85])
            rec[cam] = os.path.basename(p)
        res.append(rec)
    # chase frames: same route, matched by position (and phase) on the chase flight
    C = read(os.path.join(chase, "bag"), ["/ground_truth/odom", "/clock"])
    Tc, Pc = truth_track(C["/ground_truth/odom"])
    sim = np.array([m.ros_msg.clock.sec + m.ros_msg.clock.nanosec * 1e-9 for m in C["/clock"]])
    wall = np.array([m.log_time_ns * 1e-9 for m in C["/clock"]])
    o = np.argsort(sim); sim, wall = sim[o], wall[o]
    tc = pick_times(Tc, Pc)
    t0 = float(open(os.path.join(chase, "chase_start_wall.txt")).read())
    cap = cv2.VideoCapture(os.path.join(chase, "chase.mp4"))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    for i, rec in enumerate(res):
        w = np.interp(tc[rec["name"]], sim, wall)
        cap.set(cv2.CAP_PROP_POS_FRAMES, int((w - t0) * fps))
        ok, img = cap.read()
        if ok:
            p = os.path.join(out, f"m{i}_chase.jpg")
            cv2.imwrite(p, cv2.resize(img, (960, int(img.shape[0] * 960 / img.shape[1]))), [cv2.IMWRITE_JPEG_QUALITY, 82])
            rec["chase"] = os.path.basename(p)
    json.dump(res, open(os.path.join(out, "moments.json"), "w"), indent=1)
    for r in res:
        print(r["name"], r["t_rel"], r["pos"], r.get("chase"))


if __name__ == "__main__":
    main()
