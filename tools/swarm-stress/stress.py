#!/usr/bin/env python3
"""Swarm stress test through the dashboard API (PX4 or ArduPilot).

    stress.py <stack-name> <n-drones> [hover_s]

The stack must be launching or up (run_level.sh launches it). Waits for every
vehicle (Copter1..CopterN) to report ready, measures the sim's speed against
the wall clock from /clock on vehicle 1's bridge and the stack's CPU and
memory, takes every vehicle off at once (the dashboard's auto-takeoff), polls
each one through the hover, then lands them all. Progress lines first, one
JSON summary on the last line.

DASHBOARD_API overrides http://localhost:8001.
"""
import concurrent.futures as cf
import json
import subprocess
import sys
import time
import os
import urllib.request

API = os.environ.get("DASHBOARD_API", "http://localhost:8001")
STACK, N = sys.argv[1], int(sys.argv[2])
HOVER_S = float(sys.argv[3]) if len(sys.argv) > 3 else 120.0
NAMES = [f"Copter{i}" for i in range(1, N + 1)]


def call(method, path, timeout=60):
    req = urllib.request.Request(API + path, method=method, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def status(name):
    code, d = call("GET", f"/api/flight-controls/{name}/status", timeout=15)
    return d.get("state") if code == 200 else None


def statuses():
    with cf.ThreadPoolExecutor(16) as ex:
        return dict(zip(NAMES, ex.map(status, NAMES)))


def project():
    out = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout
    sim = [n for n in out.split() if n.endswith("-unreal-airsim") and STACK in n]
    return sim[0][: -len("-unreal-airsim")] if sim else None


PROJ = None


def sim_time():
    """Sim seconds from /clock on drone 1's bridge."""
    names = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
    bridge = next((n for n in names if n.startswith(f"{PROJ}-airsim-bridge-") and n[-1] == "1" and not n[-2].isdigit()), None)
    if not bridge:
        return None
    cmd = ["docker", "exec", bridge, "bash", "-lc",
           "source /opt/ros/humble/setup.bash >/dev/null 2>&1; "
           "timeout 8 ros2 topic echo /clock --once --field clock 2>/dev/null"]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    sec = nsec = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("sec:"):
            sec = int(line.split()[1])
        elif line.startswith("nanosec:"):
            nsec = int(line.split()[1])
    return None if sec is None else sec + (nsec or 0) * 1e-9


def rtf(window=10.0):
    a_wall, a_sim = time.time(), sim_time()
    time.sleep(window)
    b_wall, b_sim = time.time(), sim_time()
    if a_sim is None or b_sim is None:
        return None
    return round((b_sim - a_sim) / (b_wall - a_wall), 3)


def resources():
    out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.CPUPerc}} {{.MemUsage}}"],
                         capture_output=True, text=True).stdout
    cpu = mem = 0.0
    per = {}
    for line in out.splitlines():
        name, c, m = line.split(" ", 2)
        if PROJ and name.startswith(PROJ):
            cv = float(c.rstrip("%"))
            used = m.split("/")[0].strip()
            mv = float(used[:-3]) * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}.get(used[-3:], 1)
            cpu += cv
            mem += mv
            kind = name[len(PROJ) + 1:].rstrip("0123456789").rstrip("-d").rstrip("-")
            per.setdefault(kind, [0.0, 0])
            per[kind][0] += cv
            per[kind][1] += 1
    gpu = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout.strip()
    load = open("/proc/loadavg").read().split()[0]
    return {"cpu_pct_sum": round(cpu), "mem_mib": round(mem), "gpu": gpu, "load1": float(load),
            "cpu_by_kind": {k: round(v[0]) for k, v in per.items()}}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


t0 = time.time()
while not PROJ and time.time() - t0 < 600:
    PROJ = project()
    time.sleep(3)
summary = {"stack": STACK, "n": N, "project": PROJ}
deadline = t0 + 600
while True:
    st = statuses()
    ready = [n for n, s in st.items() if s and s.get("ready") and not s.get("stale")]
    if len(ready) == N or time.time() > deadline:
        break
    log(f"ready {len(ready)}/{N}")
    time.sleep(10)
summary["ready"] = len(ready)
summary["not_ready"] = {n: (s or {}).get("ready_message") for n, s in st.items() if n not in ready}
summary["ready_wait_s"] = round(time.time() - t0)
log(f"ready {len(ready)}/{N} after {summary['ready_wait_s']}s")

# POST_READY_CMD runs once every vehicle is ready, before anything is
# measured; {project} is the stack's compose project (e.g. trim_streams.sh).
hook = os.environ.get("POST_READY_CMD")
if hook:
    subprocess.run(hook.replace("{project}", PROJ or ""), shell=True)
    summary["post_ready_cmd"] = hook
    time.sleep(10)
summary["rtf_idle"] = rtf()
summary["res_idle"] = resources()
log(f"idle rtf={summary['rtf_idle']} {summary['res_idle']}")

log("auto-takeoff all")
t1 = time.time()
with cf.ThreadPoolExecutor(N) as ex:
    res = dict(zip(NAMES, ex.map(lambda n: call("POST", f"/api/flight-controls/{n}/auto-takeoff?timeout_sec=60", timeout=120), NAMES)))
summary["takeoff_ok"] = sum(1 for c, d in res.values() if c == 200 and d.get("success", True))
summary["takeoff_fail"] = {n: (c, str(d)[:200]) for n, (c, d) in res.items() if not (c == 200 and d.get("success", True))}
summary["takeoff_s"] = round(time.time() - t1)
log(f"takeoff ok {summary['takeoff_ok']}/{N} in {summary['takeoff_s']}s")

samples = []
t2 = time.time()
while time.time() - t2 < HOVER_S:
    st = statuses()
    air = sum(1 for s in st.values() if s and s.get("armed") and not s.get("landed"))
    stale = sum(1 for s in st.values() if not s or s.get("stale"))
    modes = {}
    for s in st.values():
        m = (s or {}).get("mode", "?")
        modes[m] = modes.get(m, 0) + 1
    r = rtf(5.0)
    samples.append({"t": round(time.time() - t2), "airborne": air, "stale": stale, "modes": modes, "rtf": r})
    log(f"hover t={samples[-1]['t']}s airborne={air}/{N} stale={stale} rtf={r} modes={modes}")
summary["hover"] = samples
summary["hover_min_airborne"] = min(s["airborne"] for s in samples) if samples else 0
rtfs = [s["rtf"] for s in samples if s["rtf"] is not None]
summary["rtf_hover_min"] = min(rtfs) if rtfs else None
summary["rtf_hover_mean"] = round(sum(rtfs) / len(rtfs), 3) if rtfs else None
summary["res_hover"] = resources()
log(f"hover resources {summary['res_hover']}")

log("land all")
with cf.ThreadPoolExecutor(N) as ex:
    list(ex.map(lambda n: call("POST", f"/api/flight-controls/{n}/auto-land", timeout=120), NAMES))
t3 = time.time()
while time.time() - t3 < 300:
    st = statuses()
    down = sum(1 for s in st.values() if s and (s.get("landed") or not s.get("armed")))
    if down == N:
        break
    time.sleep(5)
summary["landed"] = down
summary["land_s"] = round(time.time() - t3)
log(f"landed {down}/{N} in {summary['land_s']}s")
print(json.dumps(summary))
