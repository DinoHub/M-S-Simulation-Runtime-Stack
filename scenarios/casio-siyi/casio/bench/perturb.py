#!/usr/bin/env python3
"""Change the sim's weather and time of day over AirSim RPC, to stress casio.

  perturb.py [--fog F] [--rain R] [--dust D] [--snow S] [--time YYYY-MM-DDTHH:MM:SS]
  perturb.py --reset          clear weather, back to midday

Intensities are 0..1. --time moves the sun (UE5 sun/sky) to that local time
at the level's OriginGeopoint; --tz is the UTC offset used for it. Prints
what it applied as one JSON line, so square-flight.sh can keep it with the
run. Needs python3 + msgpack (the ros2 bridge image), like gt_objects.py.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gt_objects import Rpc  # noqa: E402

WEATHER = {"rain": 0, "roadwetness": 1, "snow": 2, "roadsnow": 3, "dust": 6, "fog": 7}
MIDDAY = "2026-09-30 12:00:00"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="host.docker.internal")
    ap.add_argument("--port", type=int, default=41451)
    for name in ("fog", "rain", "dust", "snow"):
        ap.add_argument(f"--{name}", type=float)
    ap.add_argument("--time", help="local date-time for the sun, e.g. 2026-09-30T21:30:00")
    ap.add_argument("--tz", type=float, default=-7.0, help="UTC offset for --time (the level sits in Idaho)")
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    rpc = Rpc(a.host, a.port)
    nan = float("nan")
    applied = {}

    if a.reset:
        rpc.call("simEnableWeather", True)
        for p in WEATHER.values():
            rpc.call("simSetWeatherParameter", p, 0.0)
        rpc.call("simEnableWeather", False)
        rpc.call("simSetTimeOfDay", True, MIDDAY, False, 1.0, 60.0, True, nan, nan, a.tz)
        applied = {"reset": True, "time": MIDDAY}
    else:
        levels = {k: getattr(a, k) for k in ("fog", "rain", "dust", "snow") if getattr(a, k) is not None}
        if levels:
            rpc.call("simEnableWeather", True)
            for k, v in levels.items():
                rpc.call("simSetWeatherParameter", WEATHER[k], float(v))
                if k in ("rain", "snow"):
                    rpc.call("simSetWeatherParameter", WEATHER["road" + ("wetness" if k == "rain" else "snow")], float(v))
            applied.update(levels)
        if a.time:
            a.time = a.time.replace("T", " ")
            # celestial clock 1x and sun updates every 60 s: the light holds for a flight
            rpc.call("simSetTimeOfDay", True, a.time, False, 1.0, 60.0, True, nan, nan, a.tz)
            applied["time"] = a.time
    print(json.dumps(applied))


if __name__ == "__main__":
    main()
