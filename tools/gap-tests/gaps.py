#!/usr/bin/env python3
"""gaps.py OUT_DIR --site lat,lon,doy,tz --times 7,9,12,...

Measure the three open sun gaps on a live fisheye stack (vehicle on the ground):

  1. Sun disc against sky: HDR radiance of the disc vs the sky 5 deg away.
  2. Daylight range: scene radiance (sunlit ground, sky) against sun elevation.
  3. Dusk exposure: the auto exposure's EV and output level as the sun goes down.

Radiance comes from raw captures (TonemapMode 2: linear, value/255 = scene luminance x 2^-EV,
before any lens or sensor effect: flare, veil, bloom, noise and relative illumination off),
bracketed over EV and merged per pixel, so neither the 8-bit output nor the auto exposure
limits the range. Scene colour is UE's absolute luminance (cd/m2 with physical light units).
"""
import argparse, json, math, os, sys, time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lighting_sweep as L  # noqa: E402
from rpc import Rpc  # noqa: E402

RAW = {"FisheyeAutoExposure": False, "TonemapMode": 2, "HDRBloomEnabled": False, "VeilingGlareEnabled": False,
       "FisheyeSensorNoise": False, "FisheyeRelativeIlluminationEdge": 1.0}
NORMAL = {"FisheyeAutoExposure": True, "TonemapMode": 1, "HDRBloomEnabled": True, "VeilingGlareEnabled": True,
          "FisheyeSensorNoise": True, "FisheyeRelativeIlluminationEdge": 0.7}
EVS = list(range(-9, 34, 3))


def real_sun(site, h):
    lat, lon, doy, tz = site
    B = math.radians(360 / 365 * (doy - 81))
    eot = 9.87 * math.sin(2 * B) - 7.53 * math.cos(B) - 1.5 * math.sin(B)
    sh = h + (4 * (lon - tz * 15) + eot) / 60
    dec = math.radians(23.45 * math.sin(math.radians(360 / 365 * (284 + doy))))
    H, La = math.radians(15 * (sh - 12)), math.radians(lat)
    el = math.degrees(math.asin(math.sin(La) * math.sin(dec) + math.cos(La) * math.cos(dec) * math.cos(H)))
    az = (math.degrees(math.atan2(math.sin(H), math.cos(H) * math.sin(La) - math.tan(dec) * math.cos(La))) + 540) % 360
    return az, el


def hdr(r, settle_s=1.8):
    """Merge a raw EV bracket into radiance per camera (float, raw units)."""
    acc = {c: None for c in L.CAMS}
    for ev in EVS:
        L.set_sensor(r, dict(RAW, ExposureCompensation=float(ev)))
        time.sleep(settle_s)
        ims, _ = L.grab(r)
        ims, _ = L.grab(r)
        for c in L.CAMS:
            v = cv2.cvtColor(ims[c], cv2.COLOR_BGR2GRAY).astype(np.float32)
            rad = v / 255.0 * (2.0 ** ev)  # raw: value/255 = scene x 2^-EV
            ok = (v > 12) & (v < 240)
            if acc[c] is None:
                acc[c] = np.full(v.shape, np.nan, np.float32)
                acc[c + "_sat"] = np.zeros(v.shape, bool)
            # lowest EV that is neither crushed nor clipped wins (least quantised)
            fill = ok & np.isnan(acc[c])
            acc[c][fill] = rad[fill]
            acc[c + "_sat"] |= v >= 250
    return acc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--site", default="1.3521,103.8198,271,8")
    ap.add_argument("--weather", default="clear")
    ap.add_argument("--times", default="7,8,9,10,12,14,16,17,17.5,18,18.5,18.75,19,19.25,19.5,20,21")
    a = ap.parse_args()
    site = tuple(float(x) for x in a.site.split(","))
    os.makedirs(a.out, exist_ok=True)
    r = Rpc()
    rig = L.Rig(r)
    L.flare(r, False)
    if a.weather:
        L.set_weather(r, a.weather)
    rows = []
    for t in [float(x) for x in a.times.split(",")]:
        r("simSetSolarTime", t)
        L.set_sensor(r, NORMAL)
        L.settle(r, min_s=8.0)
        ims, _ = L.grab(r)
        ev_ae = json.loads(r("simGetSensorSettings", L.VEHICLE, L.CAMS[0])).get("CurrentEV")
        mean_ae = float(np.mean([cv2.cvtColor(ims[c], cv2.COLOR_BGR2GRAY)[rig.valid[c]].mean() for c in L.CAMS]))
        fast_ae = float(np.mean([L.score(ims[c], rig.valid[c])[0]["fast"] for c in L.CAMS]))
        az, el = real_sun(site, t)
        H = hdr(r)
        rec = {"clock": t, "sun_az": round(az, 1), "sun_el": round(el, 1), "ae_ev": ev_ae, "ae_mean": round(mean_ae, 1), "ae_fast": round(fast_ae)}
        # ground: rays 20-80 deg below the horizon, outside the airframe; sky: 20-60 deg above
        g, s = [], []
        for c in L.CAMS:
            k = rig.cal[c]
            hgt, wid = H[c].shape
            yy, xx = np.mgrid[0:hgt, 0:wid]
            th = np.hypot(xx - k["cx"], yy - k["cy"]) / k["fx"]
            ph = np.arctan2(yy - k["cy"], xx - k["cx"])
            cam = np.stack([np.cos(th), np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph)], -1)
            wd = cam @ (rig.R_wb @ L.quat_rot(k["body_T_camera"]["orientation"])).T
            elev = np.degrees(np.arcsin(np.clip(-wd[..., 2], -1, 1)))
            m_air = rig.valid[c] | (th > math.radians(85))
            gm = (elev < -20) & (elev > -80) & rig.valid[c]
            sm = (elev > 20) & (elev < 60) & (th < math.radians(90))
            g.append(H[c][gm]); s.append(H[c][sm])
        g = np.concatenate(g); s = np.concatenate(s)
        rec["ground_rad"] = float(np.nanmedian(g)) if np.isfinite(g).any() else None
        rec["sky_rad"] = float(np.nanmedian(s)) if np.isfinite(s).any() else None
        # sun disc: nearest camera to the real sun direction
        d = np.array([math.cos(math.radians(el)) * math.cos(math.radians(az)), math.cos(math.radians(el)) * math.sin(math.radians(az)), -math.sin(math.radians(el))])
        best = min(L.CAMS, key=lambda c: rig.world_to_pixel(c, d)[2])
        x, y, thd = rig.world_to_pixel(best, d)
        rec["sun_cam"], rec["sun_theta"] = best, round(thd, 1)
        if el > 1 and thd < 85:
            k = rig.cal[best]
            hgt, wid = H[best].shape
            yy, xx = np.mgrid[0:hgt, 0:wid]
            rr = np.hypot(xx - x, yy - y)
            disc = H[best][rr <= max(2.0, k["fx"] * math.radians(0.6))]
            ring = H[best][np.abs(rr - k["fx"] * math.radians(5)) < 1.5]
            rec["disc_rad"] = float(np.nanmax(disc)) if np.isfinite(disc).any() else None
            rec["disc_saturated_all_ev"] = bool(H[best + "_sat"][int(round(y)), int(round(x))]) and not np.isfinite(H[best][int(round(y)), int(round(x))])
            rec["ring_rad"] = float(np.nanmedian(ring)) if np.isfinite(ring).any() else None
            if rec["disc_rad"] and rec["ring_rad"]:
                rec["disc_over_sky"] = rec["disc_rad"] / rec["ring_rad"]
        np.save(os.path.join(a.out, f"hdr_{t:05.2f}.npy"), np.stack([np.nan_to_num(H[c], nan=-1) for c in L.CAMS]))
        rows.append(rec)
        print(json.dumps(rec), flush=True)
        json.dump(rows, open(os.path.join(a.out, "gaps.json"), "w"), indent=1)
    L.set_sensor(r, NORMAL)
    L.flare(r, True)


if __name__ == "__main__":
    main()
