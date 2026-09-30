"""Compare the non-image inputs the estimator was fed, bag by bag."""
import sys, json, pathlib, numpy as np
from rosbags.highlevel import AnyReader

def stamp(m): return m.header.stamp.sec + m.header.stamp.nanosec * 1e-9

def analyse(bag):
    out = {}
    with AnyReader([pathlib.Path(bag)]) as r:
        cons = {c.topic: c for c in r.connections}
        data = {t: [] for t in cons}
        clock = []
        for con, t_log, raw in r.messages():
            msg = r.deserialize(raw, con.msgtype)
            if con.topic == "/clock":
                clock.append((t_log * 1e-9, msg.clock.sec + msg.clock.nanosec * 1e-9))
            elif con.topic in ("/imu/data", "/camera/front/camera_info", "/camera/front_right/camera_info"):
                data[con.topic].append((t_log * 1e-9, stamp(msg), msg))
            elif con.topic == "/tf_static" and "tf_static" not in out:
                out["tf_static"] = {f"{tr.header.frame_id}->{tr.child_frame_id}":
                                    [round(v, 4) for v in (tr.transform.translation.x, tr.transform.translation.y, tr.transform.translation.z,
                                                           tr.transform.rotation.x, tr.transform.rotation.y, tr.transform.rotation.z, tr.transform.rotation.w)]
                                    for tr in msg.transforms}
    c = np.array(clock)
    out["rtf"] = round(float((c[-1, 1] - c[0, 1]) / (c[-1, 0] - c[0, 0])), 3)
    # sim time at a wall instant, from the clock samples
    sim_at = lambda w: np.interp(w, c[:, 0], c[:, 1])
    for topic, rows in data.items():
        if not rows or topic not in ("/imu/data", "/camera/front/camera_info", "/camera/front_right/camera_info"):
            continue
        w = np.array([x[0] for x in rows]); s = np.array([x[1] for x in rows])
        ds = np.diff(s); med = np.median(ds)
        lag = sim_at(w) - s          # how old the stamp is when it arrives, in sim seconds
        o = {"n": len(s), "rate_hz": round(1 / med, 1) if med > 0 else None,
             "dt_p50_ms": round(med * 1e3, 2), "dt_p99_ms": round(np.percentile(ds, 99) * 1e3, 1),
             "gaps_gt_2x": int((ds > 2 * med).sum()), "dup_or_back": int((ds <= 0).sum()),
             "lag_p50_ms": round(float(np.median(lag)) * 1e3, 1), "lag_p95_ms": round(float(np.percentile(lag, 95)) * 1e3, 1)}
        m0 = rows[0][2]
        if topic.endswith("camera_info"):
            o["K"] = [round(v, 2) for v in m0.k[[0, 2, 4, 5]]]; o["D"] = [round(v, 4) for v in m0.d][:5]; o["size"] = [m0.width, m0.height]
            o["frame"] = m0.header.frame_id
        else:
            a = np.array([[x[2].linear_acceleration.x, x[2].linear_acceleration.y, x[2].linear_acceleration.z] for x in rows])
            g = np.array([[x[2].angular_velocity.x, x[2].angular_velocity.y, x[2].angular_velocity.z] for x in rows])
            park = slice(0, min(len(a), 2000))   # first ~10 s: parked
            o["acc_norm_parked"] = round(float(np.linalg.norm(a[park], axis=1).mean()), 3)
            o["acc_std_parked"] = [round(v, 4) for v in a[park].std(0)]
            o["gyro_std_parked"] = [round(v, 5) for v in g[park].std(0)]
            o["gyro_mean_parked"] = [round(v, 5) for v in g[park].mean(0)]
            o["frame"] = m0.header.frame_id
        out[topic] = o
    L = data.get("/camera/front/camera_info"); R = data.get("/camera/front_right/camera_info")
    if L and R:
        rs = np.array([x[1] for x in R]); ls = np.array([x[1] for x in L])
        near = np.abs(ls[:, None] - rs[None, :]).min(1) if len(ls) * len(rs) < 2e7 else None
        if near is not None:
            out["stereo_unpaired_pct"] = round(float((near > 1e-4).mean()) * 100, 1)
    return out

res = {"/".join(pathlib.Path(b).parts[-4:-1]): analyse(b) for b in sys.argv[1:]}
print(json.dumps(res, indent=1))
