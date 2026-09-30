"""score_bag.py BAG [EST_TOPIC]: error growth of the estimate vs ground truth over the flight."""
import sys, pathlib, numpy as np
from rosbags.highlevel import AnyReader
from rosbags.typesys import Stores, get_typestore
TS = get_typestore(Stores.ROS2_HUMBLE)
bag, est = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "/ov_msckf/odomimu")
series = {"/ground_truth/odom": [], est: []}
with AnyReader([pathlib.Path(bag)], default_typestore=TS) as r:
    cons = [c for c in r.connections if c.topic in series]
    for con, t, raw in r.messages(connections=cons):
        m = r.deserialize(raw, con.msgtype)
        p = m.pose.pose.position
        series[con.topic].append((m.header.stamp.sec + m.header.stamp.nanosec * 1e-9, p.x, p.y, p.z))
g = np.array(series["/ground_truth/odom"]); e = np.array(series[est])
if len(e) == 0: sys.exit(f"{bag}: no {est}")
sp = np.linalg.norm(np.diff(g[:, 1:4], axis=0), axis=1) / np.maximum(np.diff(g[:, 0]), 1e-6)
moving = np.nonzero(sp > 0.2)[0]; t0, t1 = g[moving[0], 0], g[moving[-1], 0]
m = (e[:, 0] >= t0) & (e[:, 0] <= t1); te, pe = e[m, 0], e[m, 1:4]
G = np.stack([np.interp(te, g[:, 0], g[:, i]) for i in (1, 2, 3)], 1)
k = te < te[0] + 10
A, B = pe[k] - pe[k].mean(0), G[k] - G[k].mean(0)
U, S, Vt = np.linalg.svd(B.T @ A); D = np.eye(3); D[2, 2] = np.sign(np.linalg.det(U @ Vt)); R = U @ D @ Vt
err = np.linalg.norm((R @ pe.T).T + G[k].mean(0) - R @ pe[k].mean(0) - G, axis=1)
bins = " ".join(f"{np.median(err[(te - te[0] >= s) & (te - te[0] < s + 10)]):.2f}" for s in range(0, int(te[-1] - te[0]), 10))
over = np.nonzero(err > 3)[0]
print(f"{pathlib.Path(bag).name[:40]:40} flight {t1-t0:5.1f}s  err per 10 s: {bins}  | diverges at {te[over[0]]-te[0]:.1f}s" if len(over) else f"{pathlib.Path(bag).name[:40]:40} flight {t1-t0:5.1f}s  err per 10 s: {bins}  | no divergence")
