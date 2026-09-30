#!/usr/bin/env python3
"""Sweep time of day, weather and the fisheye sun-flare model; save PNGs and image metrics.

Runs against a live fisheye stack (AirSim RPC on :41451), vehicle parked.

    lighting_sweep.py images --out DIR [--times 6,8,10] [--weather clear,fog] [--flare both]
    lighting_sweep.py set --time 10 --weather clear --flare on     # one condition, for a flight
    lighting_sweep.py report DIR                                   # summary.csv + index.html

Per condition it:
  1. sets weather (simEnableWeather + simSetWeatherParameter) and solar time (simSetSolarTime),
  2. waits for auto exposure to settle,
  3. with --sun-probe, drops the auto-exposure target so the sky is far below clipping and finds
     the sun disc by local contrast; the disc pixel is turned into a world direction with the
     sim's own calibration (simGetCameraCalibration) and the vehicle pose,
  4. grabs all four cameras with the flare off, again with the flare off (noise floor), and
     with the flare on, and scores each image the way the VIO front end sees it.

The sun is located from the images, not from the clock, so the sweep checks the sky itself.
Before TEVV-Airsim #216 the XFS sky ran a stylised arc (noon at clock 04:42, night 12:00-21:00);
with #216 the sky follows the real sun (verify_sun.py compares the two).
"""
import argparse, csv, json, math, os, sys, time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rpc import Rpc  # noqa: E402

CAMS = ["fisheye_front", "fisheye_right", "fisheye_back", "fisheye_left"]
VEHICLE = "Drone1"
# name -> weather parameters (WorldSimApiBase::WeatherParameter index: value). UDW cloud cover
# is max(rain, fog), so a cloudy sky needs one of them.
WEATHER = {
    "clear": {0: 0.0, 7: 0.0, 6: 0.0, 2: 0.0},
    "haze": {0: 0.0, 7: 0.2, 6: 0.0, 2: 0.0},
    "fog": {0: 0.0, 7: 0.6, 6: 0.0, 2: 0.0},
    "rain": {0: 0.6, 7: 0.0, 6: 0.0, 2: 0.0},
    "dust": {0: 0.0, 7: 0.0, 6: 0.5, 2: 0.0},
}
FAST_THRESHOLD = 12   # OpenVINS t2 config (fast_threshold)
GRID = 5              # OpenVINS grid_x / grid_y
MAX_THETA_DEG = 85.0  # VIO masks drop rays past 85 deg off axis
MASK_DIR = os.environ.get("LIGHT_MASK_DIR", "")


# ---------------------------------------------------------------- sim control
def flare(r, on):
    v = "-1" if on else "0"  # -1 = use the camera's settings (defaults 1.0), 0 = off
    r("simRunConsoleCommand", f"r.Fisheye.FlareGhostIntensity {v}")
    r("simRunConsoleCommand", f"r.Fisheye.FlareHaloIntensity {v}")


def set_weather(r, name):
    if name == "level":
        return  # leave the level's authored sky (only meaningful before any other weather)
    r("simEnableWeather", True)
    for p, v in WEATHER[name].items():
        r("simSetWeatherParameter", p, float(v))


def set_sensor(r, patch):
    for c in CAMS:
        r("simSetSensorSettings", VEHICLE, c, json.dumps(patch))


def grab(r):
    req = [{"camera_name": c, "image_type": 0, "pixels_as_float": False, "compress": False,
            "annotation_name": ""} for c in CAMS]
    res = r("simGetImages", req, VEHICLE)
    out = {}
    for c, x in zip(CAMS, res):
        a = np.frombuffer(x["image_data_uint8"], np.uint8).reshape(x["height"], x["width"], -1)
        out[c] = a[..., :3].copy()  # BGR
    return out, res[0]["time_stamp"]


def grab_fresh(r, last_stamp, timeout=5.0):
    t0 = time.time()
    while True:
        ims, st = grab(r)
        if st != last_stamp or time.time() - t0 > timeout:
            return ims, st
        time.sleep(0.1)


def settle(r, min_s=4.0, max_s=40.0, tol=0.02):
    """Wait until every camera's mean changes by < tol (relative) over two 2 s steps."""
    t0 = time.time()
    time.sleep(min_s)
    prev, calm = None, 0
    while time.time() - t0 < max_s:
        ims, _ = grab(r)
        m = np.array([float(ims[c].mean()) for c in CAMS])
        if prev is not None and np.all(np.abs(m - prev) <= tol * np.maximum(prev, 5.0)):
            calm += 1
            if calm >= 2:
                break
        else:
            calm = 0
        prev = m
        time.sleep(2.0)
    return time.time() - t0


# ---------------------------------------------------------------- geometry
def quat_rot(q):
    w, x, y, z = q["w_val"], q["x_val"], q["y_val"], q["z_val"]
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


class Rig:
    def __init__(self, r):
        self.cal = {c: r("simGetCameraCalibration", c, VEHICLE, 0) for c in CAMS}
        self.R_wb = quat_rot(r("simGetVehiclePose", VEHICLE)["orientation"])
        self.valid, self.sky = {}, {}
        for c in CAMS:
            k = self.cal[c]
            h, w = k["height"], k["width"]
            yy, xx = np.mgrid[0:h, 0:w]
            theta = np.hypot(xx - k["cx"], yy - k["cy"]) / k["fx"]
            v = theta < math.radians(MAX_THETA_DEG)
            mp = os.path.join(MASK_DIR, f"mask_{c}.png") if MASK_DIR else ""
            if mp and os.path.exists(mp):
                v &= cv2.imread(mp, 0) < 128  # OpenVINS masks: white = do not track
            self.valid[c] = v
            # Elevation of every pixel's ray (world NED), for the sun search: sky only.
            dx, dy = xx - k["cx"], yy - k["cy"]
            ph = np.arctan2(dy, dx)
            cvx, cvy, cvz = np.sin(theta) * np.cos(ph), np.sin(theta) * np.sin(ph), np.cos(theta)
            cam = np.stack([cvz, cvx, cvy], -1)
            R = self.R_wb @ quat_rot(k["body_T_camera"]["orientation"])
            wd = cam @ R.T
            air = cv2.imread(mp, 0) >= 128 if mp and os.path.exists(mp) else np.zeros_like(v)
            self.sky[c] = (np.degrees(np.arcsin(np.clip(-wd[..., 2], -1, 1))) > -3.0) & ~air & (theta < math.radians(93))

    def pixel_to_world(self, c, x, y):
        """Unit ray in world NED for pixel (x, y); equidistant Kannala-Brandt, zero k."""
        k = self.cal[c]
        dx, dy = x - k["cx"], y - k["cy"]
        th = math.hypot(dx, dy) / k["fx"]
        ph = math.atan2(dy, dx)
        cv = np.array([math.sin(th) * math.cos(ph), math.sin(th) * math.sin(ph), math.cos(th)])
        cam = np.array([cv[2], cv[0], cv[1]])  # CV (right, down, fwd) -> AirSim (fwd, right, down)
        R_bc = quat_rot(k["body_T_camera"]["orientation"])
        return self.R_wb @ R_bc @ cam

    def world_to_pixel(self, c, d):
        k = self.cal[c]
        R_bc = quat_rot(k["body_T_camera"]["orientation"])
        cam = (self.R_wb @ R_bc).T @ d
        cv = np.array([cam[1], cam[2], cam[0]])
        th = math.acos(max(-1.0, min(1.0, cv[2])))
        ph = math.atan2(cv[1], cv[0])
        rr = k["fx"] * th
        return k["cx"] + rr * math.cos(ph), k["cy"] + rr * math.sin(ph), math.degrees(th)


def srgb_to_linear(a8):
    a = a8.astype(np.float32) / 255.0
    return np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)


def find_sun(rig, ims):
    """Brightest compact disc by contrast against a ring ~5 deg out. Returns dict or None."""
    best = None
    for c in CAMS:
        k = rig.cal[c]
        lin = srgb_to_linear(ims[c]) @ np.array([0.0722, 0.7152, 0.2126], np.float32)  # BGR
        h, w = lin.shape
        yy, xx = np.mgrid[0:h, 0:w]
        inside = rig.sky[c]
        sm = cv2.GaussianBlur(lin, (0, 0), 1.2)
        sm[~inside] = 0
        y, x = np.unravel_index(int(np.argmax(sm)), sm.shape)
        ring_r = max(4, int(round(k["fx"] * math.radians(5))))
        ring = (np.abs(np.hypot(xx - x, yy - y) - ring_r) < 1.5) & inside
        if ring.sum() < 8:
            continue
        sky = float(np.median(lin[ring]))
        peak = float(sm[y, x])
        contrast = peak / max(sky, 1e-6)
        cand = {"cam": c, "x": int(x), "y": int(y), "peak_lin": peak, "sky_lin": sky,
                "contrast": contrast, "clipped": bool(ims[c][y, x].max() >= 254)}
        # The disc is the brightest thing in the sky; a small bright spot on the
        # horizon can out-contrast it against its dark surroundings, so rank by
        # brightness among candidates that stand out at all.
        if contrast >= 2.0 and (best is None or peak > best["peak_lin"]):
            best = cand
    if best is None:
        return None
    d = rig.pixel_to_world(best["cam"], best["x"], best["y"])
    best["az_deg"] = (math.degrees(math.atan2(d[1], d[0])) + 360.0) % 360.0  # from +X (NED north)
    best["el_deg"] = math.degrees(math.asin(max(-1.0, min(1.0, -d[2]))))
    best["dir_ned"] = [float(v) for v in d]
    return best


# ---------------------------------------------------------------- image metrics
_fast = cv2.FastFeatureDetector_create(threshold=FAST_THRESHOLD, nonmaxSuppression=True)


def corners(img, valid):
    g = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))  # OpenVINS histogram_method HISTOGRAM
    kps = _fast.detect(g, (valid * 255).astype(np.uint8))
    return np.array([k.pt for k in kps], np.float32).reshape(-1, 2)


def grid_coverage(pts, valid, n=GRID, need=3):
    h, w = valid.shape
    cells = used = 0
    for i in range(n):
        for j in range(n):
            ys, ye, xs, xe = i * h // n, (i + 1) * h // n, j * w // n, (j + 1) * w // n
            if valid[ys:ye, xs:xe].mean() < 0.25:
                continue
            cells += 1
            inn = ((pts[:, 0] >= xs) & (pts[:, 0] < xe) & (pts[:, 1] >= ys) & (pts[:, 1] < ye)).sum() if len(pts) else 0
            used += inn >= need
    return used / cells if cells else 0.0


def unmatched(a, b, tol=3.0):
    """Points in a with no point in b within tol px."""
    if len(a) == 0:
        return 0
    if len(b) == 0:
        return len(a)
    d = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1)).min(1)
    return int((d > tol).sum())


def score(img, valid):
    g = img.max(axis=2)
    luma = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    pts = corners(img, valid)
    v = valid
    return {
        "mean": round(float(luma[v].mean()), 2),
        "p5": float(np.percentile(luma[v], 5)), "p95": float(np.percentile(luma[v], 95)),
        "clip_frac": round(float((g[v] >= 254).mean()), 4),
        "crush_frac": round(float((g[v] <= 4).mean()), 4),
        "rms_contrast": round(float(luma[v].std()), 2),
        "fast": int(len(pts)),
        "grid_cov": round(grid_coverage(pts, valid), 3),
    }, pts


# ---------------------------------------------------------------- sweep
def condition_id(t, w):
    return f"{w}_t{t:05.2f}".replace(".", "h")


def run_condition(r, rig, out, t, w, flares, sun_probe, ae_target, veil_ab=True):
    cid = condition_id(t, w)
    d = os.path.join(out, cid)
    os.makedirs(d, exist_ok=True)
    set_weather(r, w)
    r("simSetSolarTime", float(t))
    rec = {"id": cid, "solar_time": t, "weather": w,
           "solar_readback": r("simGetSolarTime")}
    flare(r, False)
    rec["settle_s"] = round(settle(r, min_s=6.0), 1)
    if sun_probe:
        # Veil and bloom off for the probe: the veil is added in exposed units, so at low exposure
        # it would flood the frame and hide the disc.
        set_sensor(r, {"FisheyeAutoExposureTarget": 0.06, "FisheyeAutoExposureMinEV": -10.0,
                       "VeilingGlareEnabled": False, "HDRBloomEnabled": False})
        settle(r, min_s=3.0)
        ims, _ = grab(r)
        rec["sun"] = find_sun(rig, ims)
        cv2.imwrite(os.path.join(d, "sunprobe.png"), np.hstack([ims[c] for c in CAMS]))
        set_sensor(r, {"FisheyeAutoExposureTarget": ae_target, "FisheyeAutoExposureMinEV": -2.0,
                       "VeilingGlareEnabled": True, "HDRBloomEnabled": True})
        settle(r, min_s=3.0)
    if rec.get("sun"):
        dd = np.array(rec["sun"]["dir_ned"])
        rec["sun_in_cam"] = {c: dict(zip(("x", "y", "theta_deg"), [round(v, 1) for v in rig.world_to_pixel(c, dd)]))
                             for c in CAMS}
    ev = r("simGetSensorSettings", VEHICLE, CAMS[0])
    try:
        rec["current_ev"] = json.loads(ev).get("CurrentEV")
    except Exception:
        rec["current_ev"] = None
    shots = {}
    stamp = None
    seq = [("off", False), ("off2", False)] + ([("on", True)] if "on" in flares else [])
    for tag, on in seq:
        flare(r, on)
        if on:
            time.sleep(1.0)
        ims, stamp = grab_fresh(r, stamp)
        shots[tag] = ims
        for c in CAMS:
            cv2.imwrite(os.path.join(d, f"{tag}_{c}.png"), ims[c])
    if veil_ab:
        # Same scene with the veiling glare off (flare off), after auto exposure re-settles.
        flare(r, False)
        set_sensor(r, {"VeilingGlareEnabled": False})
        settle(r, min_s=3.0)
        ims, stamp = grab_fresh(r, stamp)
        shots["noveil"] = ims
        for c in CAMS:
            cv2.imwrite(os.path.join(d, f"noveil_{c}.png"), ims[c])
        set_sensor(r, {"VeilingGlareEnabled": True})
    rec["cams"] = {}
    for c in CAMS:
        cr = {}
        pts = {}
        for tag in shots:
            cr[tag], pts[tag] = score(shots[tag][c], rig.valid[c])
        diff = np.abs(shots["off2"][c].astype(np.int16) - shots["off"][c].astype(np.int16)).max(2)
        cr["noise_mad"] = round(float(diff[rig.valid[c]].mean()), 3)
        cr["noise_new_corners"] = unmatched(pts["off2"], pts["off"])
        if "on" in shots:
            fd = np.abs(shots["on"][c].astype(np.int16) - shots["off2"][c].astype(np.int16)).max(2)
            cr["flare_mad"] = round(float(fd[rig.valid[c]].mean()), 3)
            cr["flare_px_frac"] = round(float((fd[rig.valid[c]] > 12).mean()), 4)
            cr["flare_new_corners"] = unmatched(pts["on"], pts["off2"])
            cr["flare_lost_corners"] = unmatched(pts["off2"], pts["on"])
        rec["cams"][c] = cr
    flare(r, True)
    with open(os.path.join(d, "metrics.json"), "w") as f:
        json.dump(rec, f, indent=1)
    s = rec.get("sun")
    sun = f"sun {s['cam'][8:]} az {s['az_deg']:.0f} el {s['el_deg']:.0f} c{s['contrast']:.1f}" if s else "sun not seen"
    f0 = rec["cams"]["fisheye_front"]
    print(f"{cid}: settle {rec['settle_s']}s EV {rec['current_ev']} {sun} | "
          + " ".join(f"{c[8:]}:{rec['cams'][c]['off']['mean']:.0f}/{rec['cams'][c]['off']['fast']}"
                     f"{'+' + str(rec['cams'][c].get('flare_new_corners')) if 'on' in shots else ''}" for c in CAMS),
          flush=True)
    return rec


def cmd_images(a):
    r = Rpc(a.host)
    r("simRunConsoleCommand", "r.LumenScene.SurfaceCache.CardCapturesPerFrame 16")
    rig = Rig(r)
    os.makedirs(a.out, exist_ok=True)
    json.dump({"cal": rig.cal, "cams": CAMS, "weather": {k: {str(p): v for p, v in d.items()} for k, d in WEATHER.items()},
               "fast_threshold": FAST_THRESHOLD, "grid": GRID}, open(os.path.join(a.out, "rig.json"), "w"), indent=1)
    times = [float(x) for x in a.times.split(",")]
    for w in a.weather.split(","):
        for t in times:
            cid = condition_id(t, w)
            if a.resume and os.path.exists(os.path.join(a.out, cid, "metrics.json")):
                continue
            run_condition(r, rig, a.out, t, w, a.flare, not a.no_sun_probe, a.ae_target, not a.no_veil_ab)
    cmd_report(argparse.Namespace(dir=a.out))


def cmd_set(a):
    r = Rpc(a.host)
    r("simRunConsoleCommand", "r.LumenScene.SurfaceCache.CardCapturesPerFrame 16")
    if a.weather:
        set_weather(r, a.weather)
    if a.time is not None:
        r("simSetSolarTime", float(a.time))
    if a.flare:
        flare(r, a.flare == "on")
    if a.veil:
        set_sensor(r, {"VeilingGlareEnabled": a.veil == "on"})
    print(f"set: time {a.time} weather {a.weather} flare {a.flare} veil {a.veil}; solar readback {r('simGetSolarTime')}")


def cmd_snapshot(a):
    """Settle, find the sun, save the four cameras as they are now (flare/veil untouched)."""
    r = Rpc(a.host)
    rig = Rig(r)
    os.makedirs(a.out, exist_ok=True)
    rec = {"solar_readback": r("simGetSolarTime"), "settle_s": round(settle(r, min_s=6.0), 1)}
    set_sensor(r, {"FisheyeAutoExposureTarget": 0.06, "FisheyeAutoExposureMinEV": -10.0,
                   "VeilingGlareEnabled": False, "HDRBloomEnabled": False})
    settle(r, min_s=3.0)
    ims, _ = grab(r)
    rec["sun"] = find_sun(rig, ims)
    set_sensor(r, {"FisheyeAutoExposureTarget": a.ae_target, "FisheyeAutoExposureMinEV": -2.0,
                   "VeilingGlareEnabled": a.veil != "off", "HDRBloomEnabled": True})
    settle(r, min_s=3.0)
    ims, _ = grab(r)
    rec["cams"] = {}
    for c in CAMS:
        cv2.imwrite(os.path.join(a.out, f"{c}.png"), ims[c])
        rec["cams"][c] = score(ims[c], rig.valid[c])[0]
    json.dump(rec, open(os.path.join(a.out, "metrics.json"), "w"), indent=1)
    s = rec["sun"]
    print("snapshot:", f"sun {s['cam']} az {s['az_deg']:.0f} el {s['el_deg']:.0f}" if s else "sun not seen",
          " ".join(f"{c[8:]}:{rec['cams'][c]['mean']:.0f}/{rec['cams'][c]['fast']}" for c in CAMS))


# ---------------------------------------------------------------- report
def load(dirn):
    recs = []
    for e in sorted(os.listdir(dirn)):
        p = os.path.join(dirn, e, "metrics.json")
        if os.path.exists(p):
            recs.append(json.load(open(p)))
    return recs


def cmd_report(a):
    recs = load(a.dir)
    rows = []
    for rec in recs:
        s = rec.get("sun") or {}
        for c, m in rec["cams"].items():
            row = {"id": rec["id"], "weather": rec["weather"], "solar_time": rec["solar_time"],
                   "ev": rec.get("current_ev"), "sun_cam": s.get("cam", ""), "sun_az": s.get("az_deg"),
                   "sun_el": s.get("el_deg"), "sun_contrast": s.get("contrast"), "cam": c,
                   "sun_theta": (rec.get("sun_in_cam") or {}).get(c, {}).get("theta_deg")}
            for k, v in m["off"].items():
                row[k] = v
            for k in ("noise_mad", "noise_new_corners", "flare_mad", "flare_px_frac", "flare_new_corners", "flare_lost_corners"):
                row[k] = m.get(k)
            if "noveil" in m:
                row["fast_noveil"] = m["noveil"]["fast"]
                row["grid_cov_noveil"] = m["noveil"]["grid_cov"]
                row["mean_noveil"] = m["noveil"]["mean"]
                row["rms_contrast_noveil"] = m["noveil"]["rms_contrast"]
            if "on" in m:
                row["fast_flare_on"] = m["on"]["fast"]
                row["grid_cov_flare_on"] = m["on"]["grid_cov"]
            rows.append(row)
    if not rows:
        print("no conditions yet")
        return
    keys = list(rows[0].keys()) + [k for r_ in rows for k in r_ if k not in rows[0]]
    keys = list(dict.fromkeys(keys))
    with open(os.path.join(a.dir, "summary.csv"), "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=keys)
        wr.writeheader()
        wr.writerows(rows)
    print(f"{len(recs)} conditions -> {a.dir}/summary.csv")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    sp = p.add_subparsers(dest="cmd", required=True)
    i = sp.add_parser("images")
    i.add_argument("--out", required=True)
    i.add_argument("--times", default="6,8,10,12,14,16,18")
    i.add_argument("--weather", default="clear,haze,fog,rain")
    i.add_argument("--flare", default="both", choices=["both", "off"], help="both = off + on frames")
    i.add_argument("--ae-target", type=float, default=0.45)
    i.add_argument("--no-sun-probe", action="store_true")
    i.add_argument("--no-veil-ab", action="store_true", help="skip the veiling-glare-off frame")
    i.add_argument("--resume", action="store_true")
    s = sp.add_parser("set")
    s.add_argument("--time", type=float)
    s.add_argument("--weather", choices=list(WEATHER) + ["level"])
    s.add_argument("--flare", choices=["on", "off"])
    s.add_argument("--veil", choices=["on", "off"])
    n = sp.add_parser("snapshot")
    n.add_argument("--out", required=True)
    n.add_argument("--veil", choices=["on", "off"], default="on")
    n.add_argument("--ae-target", type=float, default=0.45)
    rp = sp.add_parser("report")
    rp.add_argument("dir")
    a = p.parse_args()
    if a.cmd == "images":
        a.flare = "on" if a.flare == "both" else ""
    {"images": cmd_images, "set": cmd_set, "snapshot": cmd_snapshot, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
