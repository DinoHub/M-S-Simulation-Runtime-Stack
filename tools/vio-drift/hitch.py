"""hitch.py SIMLOG GT_TUM: engine hitches (wall gaps between capture services) relative to takeoff."""
import re, sys, datetime, numpy as np
log, tum = sys.argv[1], sys.argv[2]
pts = []
for line in open(log, errors="ignore"):
    m = re.match(r"\[(\d{4})\.(\d\d)\.(\d\d)-(\d\d)\.(\d\d)\.(\d\d):(\d{3})\]\[\s*(\d+)\]", line)
    if m and "Processing" in line:
        y, mo, d, H, M, S, ms, fr = map(int, m.groups())
        pts.append(datetime.datetime(y, mo, d, H, M, S, ms * 1000, tzinfo=datetime.timezone.utc).timestamp())
t = np.array(sorted(pts))
g = np.loadtxt(tum); t0 = g[0, 0]            # ground_truth.tum starts at the flight window
rel = t - t0; gaps = np.diff(t) * 1000
def win(a, b):
    w = (rel[1:] >= a) & (rel[1:] < b)
    gg = gaps[w]
    return f"n={w.sum():4d} p50={np.median(gg):5.1f}ms p99={np.percentile(gg,99):6.1f} max={gg.max():6.1f} >100ms:{(gg>100).sum():3d}" if w.any() else "none"
print(f"{log.split('/')[-3][:28]:28} cruise 5-18s: {win(5,18)} | corner 18-32s: {win(18,32)}")
