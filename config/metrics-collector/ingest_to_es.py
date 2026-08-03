#!/usr/bin/env python3
"""
Elasticsearch Ingestion Script for Run Summaries

Merges metrics.json and evaluated.json into a single document
and indexes it to Elasticsearch for cross-run analysis.

Fault-tolerant: always exits 0. If ES is unreachable, logs a warning and skips.
Idempotent: uses run_id as ES document _id so re-runs upsert.
"""

import csv
import glob
import json
import math
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone


def load_json(path):
    """Load a JSON file, return None if missing or invalid."""
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[ES-INGEST] WARNING: Cannot load {path}: {e}", file=sys.stderr)
        return None


INDEX_NAME = "run-summaries"

INDEX_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0
    },
    "mappings": {
        "properties": {
            "run_id": {"type": "keyword"},
            "scenario_id": {"type": "keyword"},
            "local_planner": {"type": "keyword"},
            "vehicle": {"type": "keyword"},
            "timestamp": {"type": "date"},
            "start_time_sim": {"type": "float"},
            "end_time_sim": {"type": "float"},
            "totals": {
                "properties": {
                    "path_length_m": {"type": "float"},
                    "travel_time_sec": {"type": "float"},
                    "wall_travel_time_sec": {"type": "float"}
                }
            },
            "vehicles": {
                "type": "nested",
                "properties": {
                    "name": {"type": "keyword"},
                    "path_length_m": {"type": "float"},
                    "travel_time_sec": {"type": "float"},
                    "goal_reached": {"type": "boolean"},
                    "collisions": {"type": "integer"},
                    "odom_messages": {"type": "integer"},
                    "ate": {"type": "float"},
                    "rpe": {"type": "float"},
                    "ate_mean": {"type": "float"},
                    "ate_max": {"type": "float"},
                    "ate_std": {"type": "float"},
                    "ate_median": {"type": "float"},
                    "ate_num_samples": {"type": "integer"},
                    "rpe_translation_mean": {"type": "float"},
                    "rpe_translation_max": {"type": "float"},
                    "rpe_rotation_rmse_deg": {"type": "float"},
                    "rpe_rotation_mean_deg": {"type": "float"}
                }
            },
            "ate": {"type": "float"},
            "rpe": {"type": "float"},
            "collisions": {"type": "integer"},
            "goal_reached": {"type": "boolean"},
            "failure_reasons": {"type": "keyword"},
            "controller_failure_reason": {"type": "keyword"},
            "failed_waypoint_index": {"type": "integer"},
            "straight_line_distance_m": {"type": "float"},
            "max_speed": {"type": "float"},
            "speed_std": {"type": "float"},
            "zero_speed_pct": {"type": "float"},
            "final_speed": {"type": "float"},
            "failure_moment_sec": {"type": "float"},
            "verdict": {"type": "keyword"},
            "pass_flag": {"type": "integer"},
            "exit_code": {"type": "integer"},
            "checks": {
                "properties": {
                    "path_length": {
                        "properties": {
                            "passed": {"type": "boolean"},
                            "value": {"type": "float"},
                            "limit": {"type": "float"}
                        }
                    },
                    "travel_time": {
                        "properties": {
                            "passed": {"type": "boolean"},
                            "value": {"type": "float"},
                            "limit": {"type": "float"}
                        }
                    }
                }
            }
        }
    }
}


TRAJECTORY_INDEX = "run-trajectories"

TRAJECTORY_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0
    },
    "mappings": {
        "properties": {
            "run_id": {"type": "keyword"},
            "vehicle": {"type": "keyword"},
            "timestamp": {"type": "date"},
            "timestamp_sec": {"type": "float"},
            "x": {"type": "float"},
            "y": {"type": "float"},
            "z": {"type": "float"},
            "vx": {"type": "float"},
            "vy": {"type": "float"},
            "vz": {"type": "float"},
            "speed": {"type": "float"}
        }
    }
}


def build_document(metrics, evaluated, controller_result=None):
    """
    Merge metrics.json, evaluated.json, and controller_result.json into a single ES document.

    Fields are sourced from:
    - Environment variables: run_id, scenario_id, local_planner, vehicle
    - metrics.json: totals, vehicles, timing
    - evaluated.json: verdict, exit_code, checks
    - controller_result.json: failure reason, failed waypoint index
    """
    # Prefer the run_id/scenario_id carried in metrics.json (set per-run by the
    # /run_state trigger) over the container-fixed env vars, which are empty in
    # run_state mode and would index the doc as ''. Fall back to env, then default.
    metrics = metrics or {}
    doc = {
        "run_id": metrics.get("run_id") or os.environ.get("RUN_ID") or "unknown",
        "scenario_id": metrics.get("scenario_id") or os.environ.get("SCENARIO_ID") or "unknown",
        "local_planner": os.environ.get("LOCAL_PLANNER", "unknown"),
        "vehicle": os.environ.get("VEHICLE", "unknown"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # From metrics.json
    if metrics:
        doc["start_time_sim"] = metrics.get("start_time_sim")
        doc["end_time_sim"] = metrics.get("end_time_sim")

        totals = metrics.get("totals", {})
        doc["totals"] = {
            "path_length_m": totals.get("path_length_m"),
            "travel_time_sec": totals.get("travel_time_sec"),
            "wall_travel_time_sec": totals.get("wall_travel_time_sec"),
        }

        # Build vehicles array from the vehicles dict
        vehicles_dict = metrics.get("vehicles", {})
        vehicles_list = []
        for vname, vdata in vehicles_dict.items():
            entry = {
                "name": vname,
                "path_length_m": vdata.get("path_length_m"),
                "travel_time_sec": vdata.get("travel_time_sec"),
                "goal_reached": vdata.get("goal_reached", False),
                "collisions": vdata.get("collisions", 0),
                "odom_messages": vdata.get("odom_messages", 0),
            }
            vehicles_list.append(entry)
        doc["vehicles"] = vehicles_list

    # From evaluated.json
    if evaluated:
        doc["verdict"] = evaluated.get("verdict", "UNKNOWN")
        doc["exit_code"] = evaluated.get("exit_code")

        checks = evaluated.get("checks", {})
        doc_checks = {}
        for check_name in ("path_length", "travel_time"):
            if check_name in checks:
                doc_checks[check_name] = {
                    "passed": checks[check_name].get("passed"),
                    "value": checks[check_name].get("value"),
                    "limit": checks[check_name].get("limit"),
                }
        doc["checks"] = doc_checks

        # Extract failure_reasons from checks
        failure_reasons = []
        for check_name, check_data in checks.items():
            if check_name in ("path_length", "travel_time"):
                if not check_data.get("passed", True):
                    val = check_data.get("value", "?")
                    lim = check_data.get("limit", "?")
                    label = check_name.replace("_", " ").title()
                    failure_reasons.append(
                        f"{label}: {val} exceeds limit {lim}"
                    )
            else:
                # Vehicle-level checks
                vname = check_name
                vcheck = check_data if isinstance(check_data, dict) else {}
                gr = vcheck.get("goal_reached", {})
                if isinstance(gr, dict) and not gr.get("passed", True):
                    failure_reasons.append(f"{vname}: Goal not reached")
                col = vcheck.get("collisions", {})
                if isinstance(col, dict) and not col.get("passed", True):
                    col_val = col.get("value", "?")
                    col_lim = col.get("limit", "?")
                    failure_reasons.append(
                        f"{vname}: {col_val} collisions exceeds limit {col_lim}"
                    )
                te = vcheck.get("trajectory_errors", {})
                if isinstance(te, dict) and not te.get("passed", True):
                    for f_str in te.get("failures", []):
                        failure_reasons.append(f"{vname}: {f_str}")
        doc["failure_reasons"] = failure_reasons

        # Merge trajectory metrics (ATE/RPE) into vehicle entries
        traj_metrics = evaluated.get("trajectory_metrics", {})
        if traj_metrics and "vehicles" in doc:
            for vehicle_entry in doc["vehicles"]:
                vname = vehicle_entry["name"]
                if vname in traj_metrics:
                    traj = traj_metrics[vname]
                    ate_data = traj.get("ate")
                    if isinstance(ate_data, dict):
                        vehicle_entry["ate"] = ate_data.get("rmse")
                        vehicle_entry["ate_mean"] = ate_data.get("mean")
                        vehicle_entry["ate_max"] = ate_data.get("max")
                        vehicle_entry["ate_std"] = ate_data.get("std")
                        vehicle_entry["ate_median"] = ate_data.get("median")
                        vehicle_entry["ate_num_samples"] = ate_data.get("num_samples")
                    else:
                        vehicle_entry["ate"] = ate_data
                    rpe_data = traj.get("rpe")
                    if isinstance(rpe_data, dict):
                        vehicle_entry["rpe"] = rpe_data.get("translation_rmse")
                        vehicle_entry["rpe_translation_mean"] = rpe_data.get("translation_mean")
                        vehicle_entry["rpe_translation_max"] = rpe_data.get("translation_max")
                        vehicle_entry["rpe_rotation_rmse_deg"] = rpe_data.get("rotation_rmse_deg")
                        vehicle_entry["rpe_rotation_mean_deg"] = rpe_data.get("rotation_mean_deg")
                    else:
                        vehicle_entry["rpe"] = rpe_data

    # Top-level convenience fields for Grafana tables (avoids nested array display issues)
    if doc.get("vehicles"):
        first_vehicle = doc["vehicles"][0]
        doc["ate"] = first_vehicle.get("ate")
        doc["rpe"] = first_vehicle.get("rpe")
        doc["collisions"] = sum(v.get("collisions", 0) for v in doc["vehicles"])
        doc["goal_reached"] = all(v.get("goal_reached", False) for v in doc["vehicles"])

    # Merge controller result (failure reason from scenario_controller.py)
    if controller_result and not controller_result.get("success", True):
        reason = controller_result.get("failure_reason", "unknown")
        details = controller_result.get("details", "")
        doc["controller_failure_reason"] = reason

        wp_idx = controller_result.get("failed_waypoint_index")
        if wp_idx is not None:
            doc["failed_waypoint_index"] = wp_idx

        # Prepend controller failure to failure_reasons array
        label = f"controller: {reason}"
        if details:
            label += f" ({details})"
        failure_reasons = doc.get("failure_reasons", [])
        failure_reasons.insert(0, label)
        doc["failure_reasons"] = failure_reasons

    # Numeric pass flag for Grafana avg-based pass rate calculations
    doc["pass_flag"] = 1 if doc.get("verdict") == "PASS" else 0

    return doc


def ingest_trajectories(es, output_dir, run_id, run_timestamp):
    """Ingest trajectory CSV files into the run-trajectories index.

    Downsamples from 50Hz to 2Hz (every 25th row), computes velocity
    via finite differences, and bulk-indexes to Elasticsearch.

    Returns a dict of per-vehicle stability metrics, or None if no data.
    """
    csv_pattern = os.path.join(output_dir, "*_trajectory.csv")
    csv_files = glob.glob(csv_pattern)
    if not csv_files:
        print("[ES-INGEST] No trajectory CSV files found, skipping trajectory ingestion")
        return None

    # Ensure trajectory index exists
    if not es.indices.exists(index=TRAJECTORY_INDEX):
        es.indices.create(index=TRAJECTORY_INDEX, body=TRAJECTORY_MAPPING)
        print(f"[ES-INGEST] Created index '{TRAJECTORY_INDEX}'")

    # Parse anchor timestamp
    if isinstance(run_timestamp, str):
        anchor = datetime.fromisoformat(run_timestamp.replace("Z", "+00:00"))
    else:
        anchor = run_timestamp

    from elasticsearch.helpers import bulk

    total_indexed = 0
    stability_metrics = {}

    for csv_path in csv_files:
        filename = os.path.basename(csv_path)
        # Extract vehicle name: "{vehicle}_trajectory.csv"
        vehicle = filename.replace("_trajectory.csv", "")

        try:
            with open(csv_path, "r") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        except Exception as e:
            print(f"[ES-INGEST] WARNING: Cannot read {csv_path}: {e}",
                  file=sys.stderr)
            continue

        if not rows:
            continue

        # Downsample: every 25th row (50Hz → 2Hz), always include last point
        sampled_indices = list(range(0, len(rows), 25))
        if sampled_indices[-1] != len(rows) - 1:
            sampled_indices.append(len(rows) - 1)
        sampled = [rows[i] for i in sampled_indices]

        # Parse positions
        points = []
        for row in sampled:
            try:
                points.append({
                    "t": float(row["timestamp_sec"]),
                    "x": float(row["x"]),
                    "y": float(row["y"]),
                    "z": float(row["z"]),
                })
            except (KeyError, ValueError):
                continue

        if not points:
            continue

        # Compute velocities via finite differences
        actions = []
        all_speeds = []
        for i, pt in enumerate(points):
            if i < len(points) - 1:
                dt = points[i + 1]["t"] - pt["t"]
                if dt > 0:
                    vx = (points[i + 1]["x"] - pt["x"]) / dt
                    vy = (points[i + 1]["y"] - pt["y"]) / dt
                    vz = (points[i + 1]["z"] - pt["z"]) / dt
                else:
                    vx = vy = vz = 0.0
            else:
                # Last point: use previous velocity
                vx = vy = vz = 0.0
                if i > 0:
                    dt = pt["t"] - points[i - 1]["t"]
                    if dt > 0:
                        vx = (pt["x"] - points[i - 1]["x"]) / dt
                        vy = (pt["y"] - points[i - 1]["y"]) / dt
                        vz = (pt["z"] - points[i - 1]["z"]) / dt

            speed = math.sqrt(vx * vx + vy * vy + vz * vz)
            all_speeds.append(speed)
            abs_time = anchor + timedelta(seconds=pt["t"])

            doc_id = f"{run_id}_{vehicle}_{i}"
            actions.append({
                "_index": TRAJECTORY_INDEX,
                "_id": doc_id,
                "_source": {
                    "run_id": run_id,
                    "vehicle": vehicle,
                    "timestamp": abs_time.isoformat(),
                    "timestamp_sec": round(pt["t"], 4),
                    "x": round(pt["x"], 4),
                    "y": round(pt["y"], 4),
                    "z": round(pt["z"], 4),
                    "vx": round(vx, 4),
                    "vy": round(vy, 4),
                    "vz": round(vz, 4),
                    "speed": round(speed, 4),
                },
            })

        # Compute per-vehicle stability metrics
        if all_speeds:
            max_speed = max(all_speeds)
            speed_std = statistics.stdev(all_speeds) if len(all_speeds) > 1 else 0.0
            zero_count = sum(1 for s in all_speeds if s < 0.05)
            zero_speed_pct = zero_count / len(all_speeds) * 100
            final_speed = all_speeds[-1]

            first_pt = points[0]
            last_pt = points[-1]
            final_distance_to_start = math.sqrt(
                (last_pt["x"] - first_pt["x"]) ** 2
                + (last_pt["y"] - first_pt["y"]) ** 2
                + (last_pt["z"] - first_pt["z"]) ** 2
            )

            # Detect stuck: first occurrence of 3+ consecutive points with speed < 0.05
            failure_moment_sec = None
            consecutive = 0
            for idx, s in enumerate(all_speeds):
                if s < 0.05:
                    consecutive += 1
                    if consecutive >= 3:
                        start_idx = idx - consecutive + 1
                        failure_moment_sec = round(points[start_idx]["t"], 2)
                        break
                else:
                    consecutive = 0

            stability_metrics[vehicle] = {
                "max_speed": round(max_speed, 4),
                "speed_std": round(speed_std, 4),
                "zero_speed_pct": round(zero_speed_pct, 2),
                "final_speed": round(final_speed, 4),
                "final_distance_to_start": round(final_distance_to_start, 4),
                "failure_moment_sec": failure_moment_sec,
            }

        if actions:
            indexed, errors = bulk(es, actions, raise_on_error=False)
            total_indexed += indexed
            if errors:
                for err in errors[:3]:
                    print(f"[ES-INGEST] Trajectory bulk error: {err}",
                          file=sys.stderr)

        print(f"[ES-INGEST] Trajectory '{vehicle}': {len(actions)} points indexed")

    print(f"[ES-INGEST] Total trajectory points indexed: {total_indexed}")
    return stability_metrics if stability_metrics else None


def ensure_index(es):
    """Create the index with mapping if it doesn't exist."""
    if not es.indices.exists(index=INDEX_NAME):
        es.indices.create(index=INDEX_NAME, body=INDEX_MAPPING)
        print(f"[ES-INGEST] Created index '{INDEX_NAME}'")


def main():
    output_dir = os.environ.get("OUTPUT_DIR", "/metrics/outputs")
    es_host = os.environ.get("ELASTICSEARCH_HOST", "elasticsearch")
    es_port = int(os.environ.get("ELASTICSEARCH_PORT", "9200"))

    metrics_path = os.path.join(output_dir, "metrics.json")
    evaluated_path = os.path.join(output_dir, "evaluated.json")

    # Load data files
    metrics = load_json(metrics_path)
    if metrics is None:
        print("[ES-INGEST] No metrics.json found, skipping ingestion")
        return 0

    evaluated = load_json(evaluated_path)
    if evaluated is None:
        print("[ES-INGEST] WARNING: No evaluated.json, ingesting metrics only")

    controller_result_path = os.path.join(output_dir, "controller_result.json")
    controller_result = load_json(controller_result_path)

    # Skip ES ingestion for pre-flight failures (drone never airborne)
    PRE_FLIGHT_REASONS = {
        'service_unavailable',
        'connection_timeout',
        'set_mode_failed',
        'arm_failed',
        'altitude_timeout',
    }
    if controller_result and not controller_result.get("success", True):
        reason = controller_result.get("failure_reason", "")
        if reason in PRE_FLIGHT_REASONS:
            print(f"[ES-INGEST] Pre-flight failure ({reason}), skipping ES ingestion")
            return 0

    # Build the document
    doc = build_document(metrics, evaluated, controller_result)
    run_id = doc["run_id"]

    # Connect to Elasticsearch
    try:
        from elasticsearch import Elasticsearch
    except ImportError:
        print("[ES-INGEST] WARNING: elasticsearch package not installed, skipping",
              file=sys.stderr)
        return 0

    es_url = f"http://{es_host}:{es_port}"
    print(f"[ES-INGEST] Connecting to {es_url}...")

    try:
        es = Elasticsearch(es_url, request_timeout=10)

        if not es.ping():
            print(f"[ES-INGEST] WARNING: Cannot reach Elasticsearch at {es_url}, skipping")
            return 0

        ensure_index(es)

        # Index the document (upsert via doc _id)
        es.index(index=INDEX_NAME, id=run_id, document=doc)
        print(f"[ES-INGEST] Indexed run '{run_id}' to '{INDEX_NAME}'")

        # Ingest trajectory CSV files and compute stability metrics
        stability = ingest_trajectories(es, output_dir, run_id, doc["timestamp"])
        if stability:
            doc["stability"] = stability
            # Top-level convenience fields (first vehicle)
            first_v = next(iter(stability.values()))
            doc["max_speed"] = first_v["max_speed"]
            doc["speed_std"] = first_v["speed_std"]
            doc["zero_speed_pct"] = first_v["zero_speed_pct"]
            doc["final_speed"] = first_v["final_speed"]
            doc["failure_moment_sec"] = first_v["failure_moment_sec"]
            # Re-index with stability metrics
            es.index(index=INDEX_NAME, id=run_id, document=doc)
            print(f"[ES-INGEST] Updated run '{run_id}' with stability metrics")

    except Exception as e:
        print(f"[ES-INGEST] WARNING: ES ingestion failed: {e}", file=sys.stderr)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
