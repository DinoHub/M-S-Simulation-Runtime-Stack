"""ATE of est.tum against gt.tum: nearest-stamp association, Umeyama SE3 and Sim3."""
import sys
import numpy as np


def load(path):
    a = np.loadtxt(path, ndmin=2)
    return a[np.argsort(a[:, 0])]


def umeyama(src, dst, with_scale):
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    cov = xd.T @ xs / len(src)
    u, d, vt = np.linalg.svd(cov)
    s = np.eye(3)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        s[2, 2] = -1
    r = u @ s @ vt
    c = (d * np.diag(s)).sum() / xs.var(0).sum() if with_scale else 1.0
    t = mu_d - c * r @ mu_s
    return r, t, c


def main(d, max_dt=0.02):
    est, gt = load(f"{d}/est.tum"), load(f"{d}/gt.tum")
    idx = np.searchsorted(gt[:, 0], est[:, 0]).clip(1, len(gt) - 1)
    prev = idx - 1
    pick = np.where(abs(gt[prev, 0] - est[:, 0]) < abs(gt[idx, 0] - est[:, 0]), prev, idx)
    ok = abs(gt[pick, 0] - est[:, 0]) < max_dt
    e, g = est[ok, 1:4], gt[pick[ok], 1:4]
    res = {"n": int(ok.sum()), "est_total": len(est),
           "duration_s": round(float(est[ok, 0][-1] - est[ok, 0][0]), 1) if ok.any() else 0,
           "path_gt_m": round(float(np.linalg.norm(np.diff(g, axis=0), axis=1).sum()), 1),
           "path_est_m": round(float(np.linalg.norm(np.diff(e, axis=0), axis=1).sum()), 1)}
    for name, scale in (("se3", False), ("sim3", True)):
        r, t, c = umeyama(e, g, scale)
        err = np.linalg.norm((c * (r @ e.T)).T + t - g, axis=1)
        res[f"ate_{name}_rmse"] = round(float(np.sqrt((err ** 2).mean())), 3)
        res[f"ate_{name}_max"] = round(float(err.max()), 3)
        if scale:
            res["sim3_scale"] = round(float(c), 3)
    return res


if __name__ == "__main__":
    print(main(sys.argv[1]))
