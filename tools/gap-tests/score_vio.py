#!/usr/bin/env python3
"""score_vio.py OUT CASES: one row per case from OUT/<tag>/results/r<n>/ (median of replays), as gap.sh cases writes it."""
import glob, json, os, re, statistics, sys

out, cases = sys.argv[1], sys.argv[2]
rows = []
for line in open(cases):
    f = line.split()
    if not f or f[0].startswith("#"):
        continue
    tag, t, w, fl, veil = (f + ["-"] * 5)[:5]
    ates, rpes, feats, inits, n = [], [], [], 0, 0
    for d in sorted(glob.glob(f"{out}/{tag}/results/r*")):
        n += 1
        L = open(f"{d}/openvins.log").read() if os.path.exists(f"{d}/openvins.log") else ""
        inits += "successful initialization" in L
        fs = [int(m) for m in re.findall(r"MSCKF update \((\d+) feats\)", L)]
        if fs:
            feats.append(sum(fs) / len(fs))
        for sj in glob.glob(f"{d}/*/summary.json"):
            s = json.load(open(sj))
            if s.get("status") == "measured":
                ates.append(s["ate_position_m"]["rmse"])
                rpes.append(s["rpe_translation_m"]["rmse"])
    snap = f"{out}/{tag}/snap/metrics.json"
    sun = ""
    if os.path.exists(snap):
        sn = json.load(open(snap)).get("sun")
        sun = f"az {sn['az_deg']:.0f} el {sn['el_deg']:.0f}" if sn else "not seen"
    med = lambda v: f"{statistics.median(v):.2f}" if v else "-"
    rng = f"{min(ates):.2f}-{max(ates):.2f}" if ates else "-"
    rows.append((tag, t, w, fl, veil, sun, f"{inits}/{n}", med(ates), rng, med(rpes), med(feats)))
hdr = ("case", "time", "weather", "flare", "veil", "sun at takeoff", "init", "ATE med m", "ATE range", "RPE med m", "feats/upd")
print("\t".join(hdr))
for r in rows:
    print("\t".join(r))
with open(f"{out}/results.tsv", "w") as fh:
    fh.write("\t".join(hdr) + "\n" + "\n".join("\t".join(r) for r in rows) + "\n")
