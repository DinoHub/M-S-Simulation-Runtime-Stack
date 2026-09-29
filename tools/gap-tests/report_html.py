#!/usr/bin/env python3
"""report_html.py SWEEP_DIR[,SWEEP_DIR...] OUT_DIR [--vio VIO_DIR --cases CASES]

Builds OUT_DIR/index.html + OUT_DIR/strips/*.jpg from lighting_sweep.py outputs (and,
optionally, lighting_vio.sh results): sun track on a sky plot, per-camera image metrics
against the sky clock, image strips for every condition (full model / flare off / veil off /
sun probe), and the VIO scorecard.
"""
import argparse, glob, html, json, math, os, statistics

import cv2
import numpy as np

CAMS = ["fisheye_front", "fisheye_right", "fisheye_back", "fisheye_left"]
SHORT = {c: c.split("_")[1] for c in CAMS}
VARIANTS = [("on", "Full model"), ("off", "Flare off"), ("noveil", "Flare and veil off"), ("sunprobe", "Sun probe")]
CAM_COLORS = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)"]


def strip(d, variant, out_path, px=200):
    if variant == "sunprobe":
        p = os.path.join(d, "sunprobe.png")
        if not os.path.exists(p):
            return False
        a = cv2.imread(p)
        ims = [a[:, i * a.shape[1] // 4:(i + 1) * a.shape[1] // 4] for i in range(4)]
    else:
        ps = [os.path.join(d, f"{variant}_{c}.png") for c in CAMS]
        if not all(os.path.exists(p) for p in ps):
            return False
        ims = [cv2.imread(p) for p in ps]
    row = np.hstack([cv2.resize(i, (px, px), interpolation=cv2.INTER_AREA) for i in ims])
    cv2.imwrite(out_path, row, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return True


def load(dirs):
    recs = []
    for dn in dirs:
        for p in sorted(glob.glob(os.path.join(dn, "*", "metrics.json"))):
            r = json.load(open(p))
            r["_dir"] = os.path.dirname(p)
            recs.append(r)
    return recs


# ---------------------------------------------------------------- SVG charts
def sky_plot(recs, size=360, site=None):
    """Polar sky: centre = zenith, rim = horizon, up = +X (north, NED). One dot per clock hour."""
    cx = cy = size / 2
    R = size / 2 - 34
    s = [f'<svg viewBox="0 0 {size} {size}" class="sky" role="img" aria-label="Sun position by sky clock hour">']
    for el in (0, 30, 60):
        rr = R * (90 - el) / 90
        s.append(f'<circle cx="{cx}" cy="{cy}" r="{rr:.1f}" fill="none" stroke="var(--grid)" stroke-width="1"/>')
        s.append(f'<text x="{cx + 3}" y="{cy - rr - 3:.1f}" class="tick">{el}°</text>')
    for az, lab in ((0, "N +X"), (90, "E"), (180, "S"), (270, "W")):
        a = math.radians(az)
        x, y = cx + (R + 16) * math.sin(a), cy - (R + 16) * math.cos(a)
        s.append(f'<line x1="{cx}" y1="{cy}" x2="{cx + R * math.sin(a):.1f}" y2="{cy - R * math.cos(a):.1f}" stroke="var(--grid)" stroke-width="1"/>')
        s.append(f'<text x="{x:.1f}" y="{y + 4:.1f}" class="tick" text-anchor="middle">{lab}</text>')
    pts, dropped = [], []
    for r in recs:
        sn = r.get("sun")
        if not sn or r["weather"] != "clear" or (r.get("current_ev") or 0) >= 13.9:
            continue  # at the exposure limit (night) the "disc" is sensor noise
        if site:
            ea, ee = real_sun(site, r["solar_time"])
            v1 = sph(sn["az_deg"], sn["el_deg"]); v2 = sph(ea, ee)
            if math.degrees(math.acos(max(-1, min(1, sum(x * y for x, y in zip(v1, v2)))))) > 10:
                dropped.append(r["solar_time"])
                continue
        rr = R * (90 - max(sn["el_deg"], -5)) / 90
        a = math.radians(sn["az_deg"])
        pts.append((r["solar_time"], cx + rr * math.sin(a), cy - rr * math.cos(a)))
    pts.sort()
    if site:
        # Real sun at the site for the same clock hours (NOAA-style approximation).
        lat, lon, doy, tz = site
        ref = []
        for h10 in range(0, 240):
            h = h10 / 10.0
            B = math.radians(360 / 365 * (doy - 81))
            eot = 9.87 * math.sin(2 * B) - 7.53 * math.cos(B) - 1.5 * math.sin(B)
            sh = h + (4 * (lon - tz * 15) + eot) / 60
            dec = math.radians(23.45 * math.sin(math.radians(360 / 365 * (284 + doy))))
            H, La = math.radians(15 * (sh - 12)), math.radians(lat)
            el = math.degrees(math.asin(math.sin(La) * math.sin(dec) + math.cos(La) * math.cos(dec) * math.cos(H)))
            az = (math.degrees(math.atan2(math.sin(H), math.cos(H) * math.sin(La) - math.tan(dec) * math.cos(La))) + 540) % 360
            if el > -1:
                rr = R * (90 - el) / 90
                ref.append((cx + rr * math.sin(math.radians(az)), cy - rr * math.cos(math.radians(az))))
        if len(ref) > 1:
            s.append('<polyline fill="none" stroke="var(--sky)" stroke-width="6" stroke-opacity=".25" stroke-linecap="round" points="'
                     + " ".join(f"{x:.1f},{y:.1f}" for x, y in ref) + '"/>')
    if len(pts) > 1:
        s.append('<polyline fill="none" stroke="var(--accent)" stroke-width="1.5" stroke-dasharray="3 3" points="'
                 + " ".join(f"{x:.1f},{y:.1f}" for _, x, y in pts) + '"/>')
    for t, x, y in pts:
        s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="var(--accent)"/>')
        s.append(f'<text x="{x + 7:.1f}" y="{y - 6:.1f}" class="lab">{int(t):02d}</text>')
    s.append("</svg>")
    note = (f"<p class='meta'>Left out: {', '.join(f'{int(t):02d}:{int(round(t % 1 * 60)):02d}' for t in dropped)}, where the finder's "
            "hit is more than 10° from the real sun (the sun is hidden and the brightest thing is something else).</p>") if dropped else ""
    return "\n".join(s) + note


def sph(az, el):
    a, e = math.radians(az), math.radians(el)
    return (math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e))


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


def line_chart(series, title, ylab, xmax=24, ymin=0, ymax=None, w=560, h=220, fmt="{:.0f}"):
    """series: list of (label, color, dash, [(x, y)])."""
    L, Rm, T, B = 48, 12, 14, 30
    ys = [y for _, _, _, pts in series for _, y in pts if y is not None]
    if not ys:
        return ""
    if ymax is None:
        ymax = max(ys) * 1.08 or 1
    X = lambda x: L + (w - L - Rm) * x / xmax
    Y = lambda y: T + (h - T - B) * (1 - (y - ymin) / (ymax - ymin))
    s = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="{html.escape(title)}">']
    for i in range(5):
        v = ymin + (ymax - ymin) * i / 4
        s.append(f'<line x1="{L}" x2="{w - Rm}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="var(--grid)"/>')
        s.append(f'<text x="{L - 6}" y="{Y(v) + 4:.1f}" class="tick" text-anchor="end">{fmt.format(v)}</text>')
    for x in range(0, xmax + 1, 3):
        s.append(f'<text x="{X(x):.1f}" y="{h - 10}" class="tick" text-anchor="middle">{x:02d}</text>')
    s.append(f'<text x="{L}" y="{T - 3}" class="tick">{html.escape(ylab)}</text>')
    for lab, col, dash, pts in series:
        pts = [(x, y) for x, y in sorted(pts) if y is not None]
        if not pts:
            continue
        s.append(f'<polyline fill="none" stroke="{col}" stroke-width="1.8" {"stroke-dasharray=%s" % chr(34) + dash + chr(34) if dash else ""} points="'
                 + " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in pts) + '"/>')
        for x, y in pts:
            s.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="2.2" fill="{col}"/>')
    s.append("</svg>")
    return "\n".join(s)


def legend(items):
    return '<div class="legend">' + "".join(
        f'<span><i style="background:{c};{"opacity:.55" if d else ""}"></i>{html.escape(l)}</span>' for l, c, d in items) + "</div>"


# ---------------------------------------------------------------- page
CSS = """
:root{--bg:#EEF1F2;--panel:#FFFFFF;--ink:#16202A;--muted:#5B6873;--grid:#D5DCE0;--accent:#B86A00;--sky:#2B6A94;
--c1:#B86A00;--c2:#2B6A94;--c3:#6E7F2A;--c4:#8C3F6E;--good:#2F7D4F;--warn:#A86A00;--bad:#B23A3A;--chip:#E3E8EA}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0F1519;--panel:#172027;--ink:#E3E9ED;--muted:#9AA8B2;--grid:#2A363F;
--accent:#F0A43A;--sky:#76B1DA;--c1:#F0A43A;--c2:#76B1DA;--c3:#A9BE5A;--c4:#D586B7;--good:#63C18A;--warn:#E7B04F;--bad:#EE7B7B;--chip:#22303A;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#0F1519;--panel:#172027;--ink:#E3E9ED;--muted:#9AA8B2;--grid:#2A363F;--accent:#F0A43A;--sky:#76B1DA;
--c1:#F0A43A;--c2:#76B1DA;--c3:#A9BE5A;--c4:#D586B7;--good:#63C18A;--warn:#E7B04F;--bad:#EE7B7B;--chip:#22303A;color-scheme:dark}
body{background:var(--bg);color:var(--ink);font:15px/1.55 "Public Sans",system-ui,sans-serif;padding-inline:20px;padding-block:28px 64px}
main{max-width:1180px;margin:0 auto;display:flex;flex-direction:column;gap:36px}
h1,h2,h3{font-family:"Archivo","Public Sans",sans-serif;text-wrap:balance;margin:0;letter-spacing:-.01em}
h1{font-size:34px;font-weight:700;font-stretch:88%}
h2{font-size:22px;font-weight:650;font-stretch:90%}
h3{font-size:15px;font-weight:650}
p{margin:0;max-width:72ch}
.eyebrow{font:600 12px/1 "IBM Plex Mono",monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--accent)}
header{display:flex;flex-direction:column;gap:10px}
.meta{font:13px/1.5 "IBM Plex Mono",monospace;color:var(--muted)}
section{display:flex;flex-direction:column;gap:16px}
.findings{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.finding{background:var(--panel);border-radius:10px;padding:16px 18px;display:flex;flex-direction:column;gap:6px;border:1px solid var(--grid)}
.finding b{font-family:"Archivo",sans-serif;font-size:16px}
.two{display:grid;grid-template-columns:minmax(0,380px) minmax(0,1fr);gap:24px;align-items:start}
@media (max-width:820px){.two{grid-template-columns:1fr}}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr));gap:18px}
figure{margin:0;display:flex;flex-direction:column;gap:6px}
figcaption{font-size:13px;color:var(--muted)}
svg{width:100%;height:auto;display:block}
svg .tick{font:11px "IBM Plex Mono",monospace;fill:var(--muted)}
svg .lab{font:600 11px "IBM Plex Mono",monospace;fill:var(--ink)}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12.5px;color:var(--muted)}
.legend i{display:inline-block;width:14px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}
.seg{display:flex;flex-wrap:wrap;gap:6px}
.seg button{font:600 13px "Public Sans",sans-serif;padding:7px 12px;border-radius:999px;border:1px solid var(--grid);background:var(--panel);color:var(--ink);cursor:pointer}
.seg button[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.seg button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.rows{display:flex;flex-direction:column;gap:10px}
.row{display:grid;grid-template-columns:150px minmax(0,1fr);gap:14px;align-items:center;background:var(--panel);border:1px solid var(--grid);border-radius:10px;padding:10px}
@media (max-width:640px){.row{grid-template-columns:1fr}}
.row img{width:100%;border-radius:6px;background:#000}
.rl{display:flex;flex-direction:column;gap:3px;font:12.5px/1.4 "IBM Plex Mono",monospace;color:var(--muted)}
.rl strong{font:650 15px "Archivo",sans-serif;color:var(--ink)}
.chip{display:inline-block;background:var(--chip);border-radius:999px;padding:1px 8px;font-size:11.5px;color:var(--ink)}
.tbl{overflow-x:auto;background:var(--panel);border:1px solid var(--grid);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13.5px;font-variant-numeric:tabular-nums}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--grid);white-space:nowrap}
th{font:600 12px "IBM Plex Mono",monospace;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
td.n{text-align:right}
tr:last-child td{border-bottom:0}
.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}
code{font:13px "IBM Plex Mono",monospace;background:var(--chip);padding:1px 5px;border-radius:4px}
.camkey{display:flex;gap:4px;font:11px "IBM Plex Mono",monospace;color:var(--muted)}
.camkey span{flex:1;text-align:center}
@media (prefers-reduced-motion:no-preference){.row img{transition:opacity .15s}}
.moments{display:flex;flex-direction:column;gap:12px}
.moment{display:grid;grid-template-columns:170px minmax(0,1fr);gap:14px;background:var(--panel);border:1px solid var(--grid);border-radius:10px;padding:10px}
@media (max-width:760px){.moment{grid-template-columns:1fr}}
.mgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:10px}
.mgrid img{width:100%;height:auto;border-radius:6px;background:#000}
.nochase{aspect-ratio:1.9;display:flex;align-items:center;justify-content:center;border-radius:6px;background:var(--chip);color:var(--muted);font-size:13px;text-align:center;padding:12px}
"""

JS = """
const btns=[...document.querySelectorAll('.seg button')];
function show(v){btns.forEach(b=>b.setAttribute('aria-pressed',b.dataset.v===v));
document.querySelectorAll('img[data-on]').forEach(i=>{const s=i.dataset[v];if(s){i.src=s;i.alt=i.dataset.base+' '+v;}});
try{localStorage.setItem('variant',v)}catch(e){}}
btns.forEach(b=>b.addEventListener('click',()=>show(b.dataset.v)));
let v0='on';try{v0=localStorage.getItem('variant')||'on'}catch(e){}
show(v0);
"""


def cam_series(recs, key, variant="off", weather="clear"):
    out = []
    for i, c in enumerate(CAMS):
        pts = [(r["solar_time"], r["cams"][c].get(variant, {}).get(key)) for r in recs if r["weather"] == weather]
        out.append((SHORT[c], CAM_COLORS[i], "", pts))
    return out


def mean_over_cams(r, key, variant):
    v = [r["cams"][c].get(variant, {}).get(key) for c in CAMS]
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sweeps")
    ap.add_argument("out")
    ap.add_argument("--vio")
    ap.add_argument("--cases")
    ap.add_argument("--findings", help="JSON list of [title, body] for the summary cards")
    ap.add_argument("--image", default="tevv-runtime-host-v1.0.0-bloomfix.15")
    ap.add_argument("--site", help="lat,lon,day_of_year,tz_hours: draw the real sun track on the sky plot")
    a = ap.parse_args()
    site = tuple(float(x) for x in a.site.split(",")) if a.site else None
    recs = load(a.sweeps.split(","))
    os.makedirs(os.path.join(a.out, "strips"), exist_ok=True)
    weathers = list(dict.fromkeys(r["weather"] for r in recs))

    # Strips
    rows_html = []
    for r in sorted(recs, key=lambda r: (weathers.index(r["weather"]), r["solar_time"])):
        attrs = {}
        for v, _ in VARIANTS:
            rel = f"strips/{r['id']}_{v}.jpg"
            if strip(r["_dir"], v, os.path.join(a.out, rel)):
                attrs[v] = rel
        if "on" not in attrs:
            attrs["on"] = attrs.get("off", "")
        sn = r.get("sun")
        night = (r.get("current_ev") or 0) >= 13.9
        sun = ("night: exposure at its limit" if night else
               f"sun {sn['az_deg']:.0f}° az, {sn['el_deg']:.0f}° el<br>seen by {SHORT[sn['cam']]}" if sn else "sun not seen")
        fm = mean_over_cams(r, "fast", "on")
        fo = mean_over_cams(r, "fast", "off")
        fn = mean_over_cams(r, "fast", "noveil")
        feat = f"corners {fo:.0f} off / {fm:.0f} flare" + (f" / {fn:.0f} no veil" if fn else "") if fm and fo else ""
        data = " ".join(f'data-{k}="{html.escape(v)}"' for k, v in attrs.items())
        rows_html.append(
            f'<div class="row"><div class="rl"><strong>{int(r["solar_time"]):02d}:{int(round((r["solar_time"] % 1) * 60)):02d}</strong>'
            f'<span class="chip">{html.escape(r["weather"])}</span><span>{sun}</span><span>EV {r.get("current_ev") or 0:.2f}</span><span>{feat}</span></div>'
            f'<div><img loading="lazy" src="{attrs["on"]}" {data} data-base="{html.escape(r["id"])}" alt="{html.escape(r["id"])}" width="800" height="200"></div></div>')

    clear_all = [r for r in recs if r["weather"] == "clear"]
    # Night hours (exposure at its limit) are left out of the corner and contrast charts:
    # their counts are sensor noise and would flatten the daylight detail.
    clear = [r for r in clear_all if (r.get("current_ev") or 0) < 13.9]
    charts = []
    if clear:
        charts.append(("Brightness each camera delivers (flare off)", legend([(SHORT[c], CAM_COLORS[i], "") for i, c in enumerate(CAMS)]),
                       line_chart(cam_series(clear_all, "mean"), "mean", "mean grey level, VIO region", ymax=160)))
        charts.append(("FAST corners (OpenVINS threshold 12, histogram-equalised)", legend([(SHORT[c], CAM_COLORS[i], "") for i, c in enumerate(CAMS)]),
                       line_chart(cam_series(clear, "fast"), "fast", "corners per image")))
        ser = [("full model", "var(--accent)", "", [(r["solar_time"], mean_over_cams(r, "fast", "on")) for r in clear]),
               ("flare off", "var(--sky)", "", [(r["solar_time"], mean_over_cams(r, "fast", "off")) for r in clear]),
               ("flare and veil off", "var(--c3)", "", [(r["solar_time"], mean_over_cams(r, "fast", "noveil")) for r in clear])]
        charts.append(("What the flare and the veil cost in corners (mean of 4 cameras)", legend([(l, c, d) for l, c, d, _ in ser]),
                       line_chart(ser, "fast by variant", "corners per image")))
        ser = [("full model", "var(--accent)", "", [(r["solar_time"], mean_over_cams(r, "rms_contrast", "on")) for r in clear]),
               ("flare off", "var(--sky)", "", [(r["solar_time"], mean_over_cams(r, "rms_contrast", "off")) for r in clear]),
               ("flare and veil off", "var(--c3)", "", [(r["solar_time"], mean_over_cams(r, "rms_contrast", "noveil")) for r in clear])]
        charts.append(("Image contrast (RMS grey level, mean of 4 cameras)", legend([(l, c, d) for l, c, d, _ in ser]),
                       line_chart(ser, "contrast", "RMS contrast")))
        ser = [(SHORT[c], CAM_COLORS[i], "", [(r["solar_time"], (r["cams"][c].get("flare_new_corners") or 0) - (r["cams"][c].get("noise_new_corners") or 0)) for r in clear])
               for i, c in enumerate(CAMS)]
        charts.append(("Corners the flare adds, above the sensor-noise floor", legend([(SHORT[c], CAM_COLORS[i], "") for i, c in enumerate(CAMS)]),
                       line_chart(ser, "flare corners", "new corners vs a second flare-off frame", ymin=min(0, min((y for *_, p in ser for _, y in p), default=0)))))
    wx = [w for w in weathers if w != "clear"]
    if wx:
        by_w = []
        for i, w in enumerate(weathers):
            by_w.append((w, [CAM_COLORS[0], CAM_COLORS[1], CAM_COLORS[2], CAM_COLORS[3], "var(--muted)"][i % 5], "",
                         [(r["solar_time"], mean_over_cams(r, "fast", "on")) for r in recs if r["weather"] == w and (r.get("current_ev") or 0) < 13.9]))
        charts.append(("FAST corners by weather (full model, mean of 4 cameras)", legend([(l, c, d) for l, c, d, _ in by_w]),
                       line_chart(by_w, "weather", "corners per image")))

    vio_html = ""
    if a.vio and os.path.exists(os.path.join(a.vio, "results.tsv")):
        lines = [l.rstrip("\n").split("\t") for l in open(os.path.join(a.vio, "results.tsv")) if l.strip()]
        hdr, body = lines[0], lines[1:]
        trs = []
        for row in body:
            tag = row[0]
            snap = os.path.join(a.vio, "snap", tag)
            img = ""
            if os.path.isdir(snap) and all(os.path.exists(os.path.join(snap, f"{c}.png")) for c in CAMS):
                rel = f"strips/vio_{tag}.jpg"
                ims = [cv2.resize(cv2.imread(os.path.join(snap, f"{c}.png")), (96, 96), interpolation=cv2.INTER_AREA) for c in CAMS]
                cv2.imwrite(os.path.join(a.out, rel), np.hstack(ims), [cv2.IMWRITE_JPEG_QUALITY, 80])
                img = f'<img src="{rel}" alt="{html.escape(tag)} at takeoff" width="384" height="96" style="width:260px;border-radius:4px">'
            tds = "".join(f'<td class="{"n" if j >= 6 else ""}">{html.escape(v)}</td>' for j, v in enumerate(row))
            trs.append(f"<tr>{tds}<td>{img}</td></tr>")
        vio_html = ('<div class="tbl"><table><thead><tr>' + "".join(f"<th>{html.escape(h)}</th>" for h in hdr)
                    + "<th>cameras at takeoff (front, right, back, left)</th></tr></thead><tbody>" + "".join(trs) + "</tbody></table></div>")

    findings = json.load(open(a.findings)) if a.findings else []
    fhtml = "".join(f'<div class="finding"><b>{html.escape(t)}</b><p>{b}</p></div>' for t, b in findings)
    n_img = sum(1 for _ in glob.glob(os.path.join(a.out, "strips", "*.jpg")))

    page = f"""<title>Fisheye Sun Sweep</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,400..800&family=IBM+Plex+Mono:wght@400;600&family=Public+Sans:wght@400;600&display=swap">
<style>{CSS}</style>
<main>
<header>
<span class="eyebrow">XFS yard · 4 × 190° fisheye · sensor model + sun flare</span>
<h1>Fisheye Sun Sweep</h1>
<p>How the sky clock, the weather and the lens model change what the four fisheye cameras deliver, and whether that moves OpenVINS. Vehicle parked at the yard spawn for the image sweep; the reference route is flown once per lighting case for the VIO check.</p>
<p class="meta">host {html.escape(a.image)} · {len(recs)} conditions · {n_img} image strips · 512 × 512 per camera, VIO region = airframe mask and θ &lt; 85°</p>
</header>
{"<section><h2>What the sweep found</h2><div class='findings'>" + fhtml + "</div></section>" if fhtml else ""}
<section><h2>Where the sun is</h2>
<div class="two"><figure>{sky_plot(recs, site=site)}<figcaption>Sun found in the images (clear sky), one dot per clock hour; the pale band is the real sun at the site on the same date. Centre is straight up, the rim is the horizon, up is +X (north).</figcaption></figure>
<div class="rl" style="font-size:13.5px;gap:8px"><p>The sun is located from the pictures, not from the clock: each condition takes one extra frame with the auto-exposure target at 0.06 and the veil and bloom off, finds the disc by its contrast against the sky 5° away, and turns that pixel into a world direction with the sim's own calibration (<code>simGetCameraCalibration</code>) and the vehicle pose.</p>
<p>Hours with no dot are night, or the disc was hidden.</p></div></div>
</section>
<section><h2>What the cameras deliver across the day</h2><div class="charts">
{"".join(f'<figure><h3>{html.escape(t)}</h3>{lg}{c}<figcaption>x axis: sky-clock hour, clear sky. Night hours are left out of the corner and contrast charts.</figcaption></figure>' for t, lg, c in charts)}
</div></section>
{f'<section><h2>Does it move VIO?</h2><p>Reference route (xfs_yard_box, ~50 m) flown once per case on a fresh stack, lighting set before takeoff; OpenVINS (front + back fisheye, t2 config) replayed on the bag three times, median reported. Replays of one bag are not deterministic, so small differences are noise.</p>{vio_html}</section>' if vio_html else ""}
<section><h2>Every condition</h2>
<div class="seg" role="group" aria-label="Image variant">{"".join(f'<button type="button" data-v="{v}" aria-pressed="false">{l}</button>' for v, l in VARIANTS)}</div>
<div class="camkey" style="margin-left:164px"><span>front</span><span>right</span><span>back</span><span>left</span></div>
<div class="rows">{"".join(rows_html)}</div>
</section>
</main>
<script>{JS}</script>
"""
    open(os.path.join(a.out, "index.html"), "w").write(page)
    print(f"wrote {a.out}/index.html ({len(recs)} conditions, {n_img} strips)")


if __name__ == "__main__":
    main()
