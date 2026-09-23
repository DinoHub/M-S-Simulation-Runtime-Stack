import json
import os
import sys

import numpy as np
from sim_real_eval.bagio import extract_odom, write_tum, OdomSeries
from sim_real_eval.trajectory import compare_trajectories

CLIMB_M = 0.3       # above the resting height, the vehicle is flying
LEAD_S = 3.0        # keep the run-up: the filter is moving before it lifts
TAIL_S = 1.0        # and the touchdown, but not the parking after it

def main(bag, out_dir, est_topic):
    est = extract_odom(bag, est_topic)
    gt = extract_odom(bag, "/ground_truth/odom")
    if len(est.stamp_ns) == 0:
        print("no estimate on %s; nothing to score" % est_topic, file=sys.stderr)
        return 1
    t0 = gt.stamp_ns[0]
    sec = lambda ns: (ns - t0) / 1e9

    # The resting height is read where the estimator starts, not at
    # the top of the bag. OpenVINS publishes only once it has
    # initialised on a still vehicle, so that moment is on the
    # ground by construction -- whereas the first seconds of a bag
    # need not be. On XFS under OSMO the vehicle fell through the
    # terrain before World Partition streamed it in and was put
    # back 47 m higher five seconds later; a rest height averaged
    # over those seconds made the whole recording look airborne.
    t_est = est.stamp_ns[0]
    after = gt.stamp_ns >= t_est
    if not after.any():
        print("ground truth ends before the estimate starts", file=sys.stderr)
        return 1
    rest = float(np.interp(t_est, gt.stamp_ns, gt.pos[:, 2]))
    up = np.where(after & (gt.pos[:, 2] > rest + CLIMB_M))[0]
    if len(up) < 2:
        print("the vehicle never left the ground; scoring the whole recording")
        lo, hi = sec(gt.stamp_ns[0]), sec(gt.stamp_ns[-1])
    else:
        lo = max(sec(gt.stamp_ns[up[0]]) - LEAD_S, sec(t_est))
        hi = sec(gt.stamp_ns[up[-1]]) + TAIL_S

    def clip(s):
        m = (sec(s.stamp_ns) >= lo) & (sec(s.stamp_ns) <= hi)
        return OdomSeries(stamp_ns=s.stamp_ns[m], pos=s.pos[m], quat=s.quat[m],
                          lin_vel=s.lin_vel[m], ang_vel=s.ang_vel[m])

    e, g = clip(est), clip(gt)
    print("flight window %.1f..%.1f s of a %.1f s recording: "
          "%d estimate samples, %d ground truth"
          % (lo, hi, sec(gt.stamp_ns[-1]), len(e.stamp_ns), len(g.stamp_ns)))
    if len(e.stamp_ns) < 20:
        print("the estimate does not cover the flight", file=sys.stderr)
        return 1

    os.makedirs(out_dir, exist_ok=True)
    write_tum(os.path.join(out_dir, "estimate.tum"), e)
    write_tum(os.path.join(out_dir, "ground_truth.tum"), g)

    window = {"flight_window_s": [round(lo, 1), round(hi, 1)],
              "recording_s": round(sec(gt.stamp_ns[-1]), 1),
              "airborne_s": round(hi - lo - LEAD_S - TAIL_S, 1),
              "path_m": round(float(np.linalg.norm(
                  np.diff(g.pos, axis=0), axis=1).sum()), 2)}
    # The same alignment over everything recorded, for contrast. Its
    # keys are deliberately not the gated names.
    try:
        whole = compare_trajectories(est, gt)
        window["whole_recording_ate_rmse_m"] = round(whole.ate_trans["rmse"], 3)
    except ValueError as exc:
        window["whole_recording_ate_rmse_m"] = None
        window["whole_recording_note"] = str(exc)
    with open(os.path.join(out_dir, "window.json"), "w") as f:
        json.dump(window, f, indent=2)
    print(json.dumps(window, indent=2))
    return 0

sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
