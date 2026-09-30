import re, sys, datetime, numpy as np
for f in sys.argv[1:]:
    pts = []
    for line in open(f, errors="ignore"):
        m = re.match(r"\[(\d{4})\.(\d\d)\.(\d\d)-(\d\d)\.(\d\d)\.(\d\d):(\d{3})\]\[\s*(\d+)\]", line)
        if m and "Processing" in line:
            y, mo, d, H, M, S, ms, fr = map(int, m.groups())
            pts.append((datetime.datetime(y, mo, d, H, M, S, ms * 1000).timestamp(), fr))
    if len(pts) < 20: print(f[-60:], "too few"); continue
    a = np.array(pts); a = a[np.argsort(a[:, 0])]
    # engine frame counter wraps at 1000 in the log field? unwrap
    fr = a[:, 1].copy(); wraps = np.cumsum(np.r_[0, np.diff(fr) < -500]) * 1000; fr = fr + wraps
    t = a[:, 0] - a[0, 0]
    bins = []
    for s in np.arange(0, t[-1] - 5, 5):
        w = (t >= s) & (t < s + 5)
        if w.sum() > 3: bins.append((fr[w][-1] - fr[w][0]) / (t[w][-1] - t[w][0] + 1e-9))
    b = np.array(bins); caps = len(a) / (t[-1] + 1e-9)
    print(f"{f.split('/runs/')[-1][:46]:46} span {t[-1]:5.0f}s captures {caps:4.1f}/s engine fps p10 {np.percentile(b,10):5.1f} p50 {np.median(b):5.1f} min {b.min():5.1f} | per-5s: {' '.join(f'{x:.0f}' for x in b[:40])}")
