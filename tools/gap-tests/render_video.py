#!/usr/bin/env python3
"""render_video.py FLIGHT_DIR TAG OUT.mp4 [--title TEXT] [--fps 15]

One mp4 of a VIO test flight, 1920x1080:
  left   chase view of the sim window (screen capture), time-aligned to sim time
  right  OpenVINS track history (front | back fisheye) and a top-down plot of truth
         vs the aligned estimate
  bottom position error over the flight, with a cursor
Sim time <-> wall time comes from the flight bag: each /clock message's receive time.
"""
import argparse, bisect, glob, os, subprocess

import cv2
import numpy as np
from mcap_ros2.reader import read_ros2_messages

W, H = 1920, 1080
BG = (24, 20, 18)
INK = (235, 232, 228)
MUTED = (150, 145, 140)
TRUTH = (200, 200, 200)
EST = (40, 150, 245)   # BGR orange
ERR = (90, 200, 250)


def clock_map(bag):
    sim, wall = [], []
    files = sorted(glob.glob(os.path.join(bag, "*.mcap")))
    for f in files:
        for m in read_ros2_messages(f, topics=["/clock"]):
            sim.append(m.ros_msg.clock.sec + m.ros_msg.clock.nanosec * 1e-9)
            wall.append(m.log_time_ns * 1e-9)
    order = np.argsort(sim)
    return np.array(sim)[order], np.array(wall)[order]


def put(img, text, org, scale=0.6, color=INK, thick=1):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("flight"); ap.add_argument("tag"); ap.add_argument("out")
    ap.add_argument("--title", default="VIO test flight")
    ap.add_argument("--fps", type=float, default=15.0)
    ap.add_argument("--ffmpeg", default=None)
    a = ap.parse_args()
    F = a.flight
    ffmpeg = a.ffmpeg or __import__("imageio_ffmpeg").get_ffmpeg_exe()

    sim, wall = clock_map(os.path.join(F, "bag"))
    chase_t0 = float(open(os.path.join(F, "chase_start_wall.txt")).read())
    cap = cv2.VideoCapture(os.path.join(F, "chase.mp4"))
    chase_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_chase = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    mcsv = glob.glob(os.path.join(F, "results", a.tag, "*", "matched.csv"))[0]
    M = np.loadtxt(mcsv, delimiter=",", skiprows=1)
    mt, gt, est, err = M[:, 0], M[:, 1:4], M[:, 4:7], M[:, 7]
    summ = open(glob.glob(os.path.join(F, "results", a.tag, "*", "summary.json"))[0]).read()
    import json
    S = json.loads(summ)
    ate = S["ate_position_m"]["rmse"]; path = S["matched_truth_path_length_m"]

    tracks = sorted(glob.glob(os.path.join(F, f"tracks_{a.tag}", "*.jpg")))
    tr_t = [int(os.path.basename(p)[:-4]) * 1e-9 for p in tracks]

    t_start, t_end = mt[0] - 2.0, mt[-1] + 1.0
    # top-down plot extents (ENU x/y of truth and estimate)
    # extents from the truth path: a diverged estimate must not shrink the route to a dot
    lo, hi = gt[:, :2].min(0) - 15, gt[:, :2].max(0) + 15
    span = max(hi - lo)
    PW, PH = 640, 400

    def to_px(xy):
        u = (xy[..., 0] - lo[0]) / span * (PW - 40) + 20
        v = PH - 20 - (xy[..., 1] - lo[1]) / span * (PH - 40)
        return np.stack([u, v], -1).astype(np.int32)

    proc = subprocess.Popen([ffmpeg, "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                             "-r", str(a.fps), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "21",
                             "-pix_fmt", "yuv420p", "-movflags", "+faststart", a.out], stdin=subprocess.PIPE)
    last_chase_idx, chase_frame = -1, np.zeros((720, 1280, 3), np.uint8)
    t = t_start
    while t <= t_end:
        fr = np.full((H, W, 3), BG, np.uint8)
        # chase
        wt = np.interp(t, sim, wall)
        idx = int(round((wt - chase_t0) * chase_fps))
        if 0 <= idx < n_chase:
            if idx != last_chase_idx + 1:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, img = cap.read()
            if ok:
                chase_frame = cv2.resize(img, (1280, 720), interpolation=cv2.INTER_AREA)
            last_chase_idx = idx
        fr[40:760, 0:1280] = chase_frame
        # track history
        k = bisect.bisect_right(tr_t, t) - 1
        if k >= 0:
            timg = cv2.imread(tracks[k])
            s = 640 / timg.shape[1]
            timg = cv2.resize(timg, (640, int(timg.shape[0] * s)), interpolation=cv2.INTER_AREA)[:320]
            fr[40:40 + timg.shape[0], 1280:1920] = timg
        put(fr, "OpenVINS feature tracks: front | back fisheye", (1290, 380), 0.5, MUTED)
        # top-down
        plot = np.full((PH, PW, 3), (34, 30, 28), np.uint8)
        cv2.polylines(plot, [to_px(gt[:, :2])], False, (80, 80, 80), 1, cv2.LINE_AA)
        j = np.searchsorted(mt, t)
        if j > 1:
            cv2.polylines(plot, [to_px(gt[:j, :2])], False, TRUTH, 2, cv2.LINE_AA)
            ep = np.clip(to_px(est[:j, :2]), [-1, -1], [PW, PH])
            cv2.polylines(plot, [ep], False, EST, 2, cv2.LINE_AA)
            cv2.circle(plot, tuple(int(v) for v in ep[-1]), 5, EST, -1, cv2.LINE_AA)
        put(plot, "top-down, 10 m grid", (12, 22), 0.5, MUTED)
        g0 = np.ceil(lo / 10) * 10
        for gx in np.arange(g0[0], hi[0], 10):
            u = to_px(np.array([gx, lo[1]]))[0]; cv2.line(plot, (u, 0), (u, PH), (48, 44, 42), 1)
        for gy in np.arange(g0[1], hi[1], 10):
            v = to_px(np.array([lo[0], gy]))[1]; cv2.line(plot, (0, v), (PW, v), (48, 44, 42), 1)
        cv2.line(plot, (PW - 200, PH - 18), (PW - 170, PH - 18), TRUTH, 2); put(plot, "truth", (PW - 162, PH - 13), 0.5, TRUTH)
        cv2.line(plot, (PW - 110, PH - 18), (PW - 80, PH - 18), EST, 2); put(plot, "OpenVINS", (PW - 72, PH - 13), 0.5, EST)
        fr[400:400 + PH, 1280:1920] = plot
        # error strip
        ex0, ey0, ew, eh = 60, 800, 1800, 220
        cv2.rectangle(fr, (ex0, ey0), (ex0 + ew, ey0 + eh), (34, 30, 28), -1)
        emax = max(1.0, float(np.ceil(err.max())))
        X = lambda tt: int(ex0 + (tt - t_start) / (t_end - t_start) * ew)
        Y = lambda e: int(ey0 + eh - e / emax * (eh - 20))
        for e in np.arange(0, emax + 1e-6, max(0.5, emax / 4)):
            cv2.line(fr, (ex0, Y(e)), (ex0 + ew, Y(e)), (48, 44, 42), 1); put(fr, f"{e:.1f} m", (8, Y(e) + 5), 0.45, MUTED)
        pts = np.stack([[X(x) for x in mt], [Y(e) for e in err]], -1).astype(np.int32)
        cv2.polylines(fr, [pts], False, (70, 70, 70), 1, cv2.LINE_AA)
        if j > 1:
            cv2.polylines(fr, [pts[:j]], False, ERR, 2, cv2.LINE_AA)
        cv2.line(fr, (X(t), ey0), (X(t), ey0 + eh), INK, 1)
        put(fr, "position error, OpenVINS vs truth (aligned)", (ex0 + 8, ey0 + 18), 0.5, MUTED)
        # HUD
        put(fr, a.title, (12, 28), 0.8, INK, 2)
        cur = err[j - 1] if j > 0 else 0.0
        put(fr, f"t {t - mt[0]:5.1f} s   error {cur:6.2f} m   run ATE {ate:.2f} m / {path:.0f} m", (1290, 28), 0.6, ERR, 2)
        proc.stdin.write(fr.tobytes())
        t += 1.0 / a.fps
    proc.stdin.close(); proc.wait()
    print("wrote", a.out)


if __name__ == "__main__":
    main()
