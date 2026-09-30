"""frames.py BAG: per-camera frame freshness, brightness and sharpness over the flight."""
import sys, pathlib, numpy as np
from rosbags.highlevel import AnyReader
from rosbags.typesys import Stores, get_typestore
TS = get_typestore(Stores.ROS2_HUMBLE)
bag = pathlib.Path(sys.argv[1])
cams = ["/camera/front/image_raw", "/camera/front_right/image_raw"]
prev = {}; rows = {c: [] for c in cams}; gt = []
with AnyReader([bag], default_typestore=TS) as r:
    cons = [c for c in r.connections if c.topic in cams + ["/ground_truth/odom"]]
    for con, t, raw in r.messages(connections=cons):
        m = r.deserialize(raw, con.msgtype)
        st = m.header.stamp.sec + m.header.stamp.nanosec * 1e-9
        if con.topic == "/ground_truth/odom":
            p = m.pose.pose.position; gt.append((st, p.x, p.y, p.z)); continue
        ch = 3 if m.encoding in ("rgb8", "bgr8") else (4 if "a" in m.encoding else 1)
        img = np.frombuffer(m.data, np.uint8).reshape(m.height, m.width, -1)[..., :3].mean(-1)
        small = img[::4, ::4]
        d = float(np.abs(small - prev[con.topic]).mean()) if con.topic in prev else np.nan
        prev[con.topic] = small
        lap = small[1:-1, 1:-1] * 4 - small[:-2, 1:-1] - small[2:, 1:-1] - small[1:-1, :-2] - small[1:-1, 2:]
        rows[con.topic].append((st, d, float(img.mean()), float(lap.var()), hash(m.data[::997].tobytes())))
g = np.array(gt); sp = np.linalg.norm(np.diff(g[:, 1:4], axis=0), axis=1) / np.maximum(np.diff(g[:, 0]), 1e-6)
t0 = g[np.nonzero(sp > 0.2)[0][0], 0]
L, R = np.array([x[:4] for x in rows[cams[0]]]), np.array([x[:4] for x in rows[cams[1]]])
print(f"{bag.name}: takeoff at stamp {t0:.1f}; {len(L)} / {len(R)} frames")
print(" t(s)  L:n  L:stale  R:stale  both-fresh-pair%  L:bright  L:sharp  R:bright  R:sharp")
for s in range(-10, 70, 5):
    wl = (L[:, 0] >= t0 + s) & (L[:, 0] < t0 + s + 5); wr = (R[:, 0] >= t0 + s) & (R[:, 0] < t0 + s + 5)
    if not wl.any(): continue
    stale_l = (L[wl, 1] < 0.05).mean() * 100; stale_r = (R[wr, 1] < 0.05).mean() * 100
    # pairs: same stamp; one fresh one stale = mismatch
    lk = {round(x[0], 4): x[1] < 0.05 for x in L[wl]}; rk = {round(x[0], 4): x[1] < 0.05 for x in R[wr]}
    common = [k for k in lk if k in rk]; mism = np.mean([lk[k] != rk[k] for k in common]) * 100 if common else np.nan
    print(f"{s:5d} {wl.sum():5d} {stale_l:7.1f}% {stale_r:7.1f}%  mismatch {mism:5.1f}%   {L[wl,2].mean():6.1f} {L[wl,3].mean():8.1f} {R[wr,2].mean():6.1f} {R[wr,3].mean():8.1f}")
