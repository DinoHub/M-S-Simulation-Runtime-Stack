"""Turn the evaluators' reports into one exit code.

Reads every eval/*.json the evaluate group produced and applies the
run's gates. This is the only place a pass/fail decision is made:
evaluators measure, the aggregate judges. Adding an evaluator means
adding a gate here, not changing what any evaluator does.

Exit codes are the platform's, and the three are not
interchangeable:
  0   gates met
  1   a real verdict -- the stack ran and was wrong. Never retried.
  42  no evidence to judge. The platform failed, so reschedule.
"""
import json, os, pathlib, sys

ALIASES = {"ate_rmse_m": "ate_trans_m.rmse", "ate_max_m": "ate_trans_m.max",
           "rpe_rmse_m": "rpe_trans_m.rmse"}
measured = set()

def main():
    reports_dir = pathlib.Path(os.environ["REPORT_DIR"])
    reports = sorted(reports_dir.rglob("*.json"))
    if not reports:
        print("NO EVIDENCE: no *.json under %s" % reports_dir, file=sys.stderr)
        return 42

    status = 0
    for path in reports:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print("FAIL: %s unreadable (%s)" % (path.name, exc))
            status = 1
            continue
        print("--- %s" % path.name)
        print(json.dumps(data, indent=2)[:2000])

        # Was the run physically valid? A vehicle that fell out of
        # the world produces a trajectory error that says nothing
        # about the estimator, so this is checked before the number
        # is believed -- and failing here is a verdict on the
        # scenario, not on the thing under test.
        spawn = data.get("spawn")
        if isinstance(spawn, dict):
            if spawn.get("spawn_ok"):
                print("PASS: spawn -- %s" % spawn.get("reason"))
            else:
                print("FAIL: spawn -- %s" % spawn.get("reason"))
                print("      every other number from this run is "
                      "measuring the fall, not the estimator")
                status = 1
            continue

        # The gates on the thing under test. Each names a metric
        # in the platform's vocabulary (the scorecard's columns:
        # ate_rmse_m, ate_max_m, rpe_rmse_m) and a bound; the
        # trajectory evaluator spells the same numbers as nested
        # paths, so both are looked for. A metric that no report
        # measured fails at the end, below: run 26 passed with a
        # gate on a number that did not exist.
        gates = json.loads(os.environ.get("GATES") or "{}")
        for metric, bound in gates.items():
            value = _find(data, (metric, ALIASES.get(metric, metric)))
            if value is None:
                continue
            measured.add(metric)
            if "max" in bound and value > float(bound["max"]):
                print("FAIL: %s %.3f over the %.3f gate" % (metric, value, float(bound["max"])))
                status = 1
            elif "min" in bound and value < float(bound["min"]):
                print("FAIL: %s %.3f under the %.3f gate" % (metric, value, float(bound["min"])))
                status = 1
            else:
                print("PASS: %s %.3f within %s" % (metric, value, bound))

    for metric in json.loads(os.environ.get("GATES") or "{}"):
        if metric not in measured:
            print("FAIL: gate %r -- no report measured it (the estimate "
                  "never published, or the evaluator crashed)" % metric)
            status = 1

    print("verdict rc=%d" % status)
    return status

def _find(node, names):
    """Depth-first search for the first numeric field with one of
    these names. Evaluator report shapes are not yet a contract, so
    this looks rather than assumes. A dotted name ("ate_trans_m.rmse")
    walks that path from the first matching parent."""
    for name in names:
        if "." in name:
            head, _, rest = name.partition(".")
            parent = _find(node, (head,)) if not isinstance(node, dict) or head not in node else node[head]
            if isinstance(parent, dict):
                found = _find(parent, (rest,))
                if found is not None:
                    return found
    if isinstance(node, dict):
        for key, value in node.items():
            if key.lower() in names and isinstance(value, (int, float)):
                return float(value)
            if key.lower() in names and isinstance(value, dict) and any("." in n for n in names):
                return value
        for value in node.values():
            found = _find(value, names)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find(value, names)
            if found is not None:
                return found
    return None

sys.exit(main())
