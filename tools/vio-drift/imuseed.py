"""Is the IMU noise the same sequence every run? Cross-correlate parked gyro noise between bags."""
import sys, pathlib, numpy as np
from rosbags.highlevel import AnyReader
from rosbags.typesys import Stores, get_typestore
TS = get_typestore(Stores.ROS2_HUMBLE)
def gyro(bag, n=1500):
    out = []
    with AnyReader([pathlib.Path(bag)], default_typestore=TS) as r:
        cons = [c for c in r.connections if c.topic == "/imu/data"]
        for con, t, raw in r.messages(connections=cons):
            m = r.deserialize(raw, con.msgtype)
            out.append((m.header.stamp.sec + m.header.stamp.nanosec * 1e-9, m.angular_velocity.x, m.angular_velocity.y, m.angular_velocity.z))
            if len(out) >= n: break
    return np.array(out)
series = {b: gyro(b) for b in sys.argv[1:]}
names = list(series)
ref = series[names[0]]
for b in names[1:]:
    s = series[b]
    x = ref[:, 1] - ref[:, 1].mean(); y = s[:, 1] - s[:, 1].mean()
    best = (0, 0)
    for lag in range(-len(x) // 2, len(x) // 2):
        a = x[max(0, lag):len(x) + min(0, lag)]; c = y[max(0, -lag):len(y) - max(0, lag)][:len(a)]; a = a[:len(c)]
        if len(a) > 300:
            r = float(np.corrcoef(a, c)[0, 1])
            if abs(r) > abs(best[0]): best = (r, lag)
    exact = None
    print(f"{names[0][-40:]} vs {b[-40:]}: best |corr| {abs(best[0]):.3f} at lag {best[1]}  (1.0 = same noise sequence; ~0.1 = independent)")
print("self-check noise std:", {pathlib.Path(k).parts[-2]: round(float(v[:, 1].std()), 5) for k, v in series.items()})
