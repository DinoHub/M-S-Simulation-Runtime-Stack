#!/usr/bin/env python3
"""verify_sun.py [lat lon doy tz]: set clear sky at several clock times, find the sun in the
fisheye images, compare with the real sun at the site. Prints az/el measured vs expected."""
import math, sys, time
sys.path.insert(0, __import__("os").path.dirname(__file__))
import lighting_sweep as L
from rpc import Rpc
lat, lon, doy, tz = (float(x) for x in (sys.argv[1:5] or [42.764938, -115.579201, 271, -8]))

def real(h):
    B = math.radians(360 / 365 * (doy - 81))
    eot = 9.87 * math.sin(2 * B) - 7.53 * math.cos(B) - 1.5 * math.sin(B)
    sh = h + (4 * (lon - tz * 15) + eot) / 60
    dec = math.radians(23.45 * math.sin(math.radians(360 / 365 * (284 + doy))))
    H, La = math.radians(15 * (sh - 12)), math.radians(lat)
    el = math.degrees(math.asin(math.sin(La) * math.sin(dec) + math.cos(La) * math.cos(dec) * math.cos(H)))
    az = (math.degrees(math.atan2(math.sin(H), math.cos(H) * math.sin(La) - math.tan(dec) * math.cos(La))) + 540) % 360
    return az, el

r = Rpc(); rig = L.Rig(r)
L.set_weather(r, "clear"); L.flare(r, False)
for h in [float(x) for x in (sys.argv[5].split(",") if len(sys.argv) > 5 else "7,9,12,14,16,21".split(","))]:
    r("simSetSolarTime", h); L.settle(r, min_s=6.0)
    L.set_sensor(r, {"FisheyeAutoExposureTarget": 0.06, "FisheyeAutoExposureMinEV": -10.0, "VeilingGlareEnabled": False, "HDRBloomEnabled": False})
    L.settle(r, min_s=3.0); ims, _ = L.grab(r); s = L.find_sun(rig, ims)
    L.set_sensor(r, {"FisheyeAutoExposureTarget": 0.45, "FisheyeAutoExposureMinEV": -2.0, "VeilingGlareEnabled": True, "HDRBloomEnabled": True})
    ea, ee = real(h)
    got = f"az {s['az_deg']:5.1f} el {s['el_deg']:5.1f} (contrast {s['contrast']:.1f}, {s['cam'][8:]})" if s else "not seen"
    print(f"clock {h:5.2f}: expected az {ea:5.1f} el {ee:5.1f} | measured {got} | readback {r('simGetSolarTime'):.2f}", flush=True)
