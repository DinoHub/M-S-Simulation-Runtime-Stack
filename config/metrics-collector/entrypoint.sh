#!/bin/bash
# Metrics Collector Container Entrypoint

set -e

RUN_ID=${RUN_ID:-"run-$(date +%Y%m%d-%H%M%S)"}
SCENARIO_ID=${SCENARIO_ID:-"default"}
VEHICLE=${VEHICLE:-"Copter1"}

OUTPUT_DIR=${OUTPUT_DIR:-"/metrics/outputs"}
EVALUATION_CONFIG=${EVALUATION_CONFIG:-"/metrics/config/evaluation.yaml"}
MISSION_FILE=${MISSION_FILE:-"/metrics/mission/mission.json"}
SCENARIO_CONTROLLER_CONFIG=${SCENARIO_CONTROLLER_CONFIG:-"/metrics/config/scenario_controller.yaml"}

LOCAL_PLANNER=${LOCAL_PLANNER:-"unknown"}

# These are still used by metrics_collector_node directly
GOAL_TOPIC=${GOAL_TOPIC:-"/goal"}
GOAL_TOLERANCE=${GOAL_TOLERANCE:-"0.5"}
USE_2D_GOAL_DISTANCE=${USE_2D_GOAL_DISTANCE:-"true"}
ENABLE_COLLISION_DETECTION=${ENABLE_COLLISION_DETECTION:-"true"}
GROUND_TRUTH_ODOM_TOPIC_SUFFIX=${GROUND_TRUTH_ODOM_TOPIC_SUFFIX:-"ground_truth/odom"}
MISSION_TIMEOUT_SEC=${MISSION_TIMEOUT_SEC:-"600.0"}

echo "=========================================="
echo "  Metrics Collector Container"
echo "=========================================="
echo "RUN_ID:                        $RUN_ID"
echo "SCENARIO_ID:                   $SCENARIO_ID"
echo "VEHICLE:                       $VEHICLE"
echo "LOCAL_PLANNER:                 $LOCAL_PLANNER"
echo "MISSION_FILE:                  $MISSION_FILE"
echo "OUTPUT_DIR:                    $OUTPUT_DIR"
echo "EVALUATION_CONFIG:             $EVALUATION_CONFIG"
echo "SCENARIO_CONTROLLER_CONFIG:    $SCENARIO_CONTROLLER_CONFIG"
echo "GOAL_TOPIC:                    $GOAL_TOPIC"
echo "GOAL_TOLERANCE:                ${GOAL_TOLERANCE}m"
echo "USE_2D_GOAL_DISTANCE:          $USE_2D_GOAL_DISTANCE"
echo "DDS_TRANSPORT:                 ${FASTDDS_BUILTIN_TRANSPORTS:-UDPv4}"
echo "=========================================="

mkdir -p "$OUTPUT_DIR"
chmod a+rwx "$OUTPUT_DIR" 2>/dev/null || true

if ! touch "$OUTPUT_DIR/.write_test" 2>/dev/null; then
    echo "[METRICS] ERROR: Output directory not writable: $OUTPUT_DIR"
    echo "[METRICS] If using a bind mount, pre-create the host directory:"
    echo "[METRICS]   mkdir -p ./metrics_outputs"
    exit 1
fi
rm -f "$OUTPUT_DIR/.write_test"

if [ ! -f "$MISSION_FILE" ]; then
    echo "[METRICS] WARNING: Mission file not found: $MISSION_FILE"
    echo "[METRICS] Scenario controller will fail to load waypoints"
fi

if [ ! -f "$EVALUATION_CONFIG" ]; then
    echo "[METRICS] WARNING: Evaluation config not found: $EVALUATION_CONFIG"
    if [ -f "/metrics/config/evaluation.yaml.template" ]; then
        echo "[METRICS] Copying evaluation.yaml.template to evaluation.yaml"
        cp /metrics/config/evaluation.yaml.template "$EVALUATION_CONFIG"
    fi
fi

if [ ! -f "$SCENARIO_CONTROLLER_CONFIG" ]; then
    echo "[METRICS] ERROR: Scenario controller config not found: $SCENARIO_CONTROLLER_CONFIG"
    exit 2
fi

export FASTDDS_BUILTIN_TRANSPORTS=${FASTDDS_BUILTIN_TRANSPORTS:-UDPv4}

set +u
source /opt/ros/humble/setup.bash
source /opt/ros/airsim/setup.bash
set -u

METRICS_PID=""
CONTROLLER_PID=""
HEALTH_PID=""

cleanup() {
    echo "[METRICS] Received shutdown signal, stopping processes..."

    if [ -n "$CONTROLLER_PID" ] && kill -0 "$CONTROLLER_PID" 2>/dev/null; then
        kill -SIGTERM "$CONTROLLER_PID" 2>/dev/null || true
        wait "$CONTROLLER_PID" 2>/dev/null || true
    fi

    if [ -n "$METRICS_PID" ] && kill -0 "$METRICS_PID" 2>/dev/null; then
        kill -SIGTERM "$METRICS_PID" 2>/dev/null || true
        wait "$METRICS_PID" 2>/dev/null || true
    fi

    if [ -n "$HEALTH_PID" ] && kill -0 "$HEALTH_PID" 2>/dev/null; then
        kill -SIGTERM "$HEALTH_PID" 2>/dev/null || true
        wait "$HEALTH_PID" 2>/dev/null || true
    fi
}
trap cleanup SIGTERM SIGINT

echo "[METRICS] Starting health server on port 8888..."
python3 /metrics/scripts/health_server.py &
HEALTH_PID=$!
echo "[METRICS] Health server started with PID: $HEALTH_PID"

echo "[METRICS] Starting metrics_collector_node..."
/opt/ros/airsim/airsim_ros_pkgs/lib/airsim_ros_pkgs/metrics_collector_node \
    --ros-args \
    -p use_sim_time:=true \
    -p use_run_state_trigger:=${USE_RUN_STATE_TRIGGER:-false} \
    -p vehicles:="['$VEHICLE']" \
    -p goal_topic:="$GOAL_TOPIC" \
    -p goal_tolerance_m:="$GOAL_TOLERANCE" \
    -p auto_end_on_goal_reached:=false \
    -p enable_collision_detection:="$ENABLE_COLLISION_DETECTION" \
    -p output_dir:="$OUTPUT_DIR" \
    -p evaluation_config_path:="$EVALUATION_CONFIG" \
    -p run_id:="$RUN_ID" \
    -p scenario_id:="$SCENARIO_ID" \
    -p local_planner:="$LOCAL_PLANNER" \
    -p odom_topic_suffix:="$GROUND_TRUTH_ODOM_TOPIC_SUFFIX" \
    -p mission_timeout_sec:="$MISSION_TIMEOUT_SEC" \
    -p use_2d_goal_distance:="$USE_2D_GOAL_DISTANCE" &

METRICS_PID=$!
echo "[METRICS] Metrics collector started with PID: $METRICS_PID"

sleep 2
if ! kill -0 "$METRICS_PID" 2>/dev/null; then
    echo "[METRICS] ERROR: Metrics collector failed to start"
    exit 1
fi

set +e

if [ "${USE_RUN_STATE_TRIGGER:-false}" = "true" ]; then
    # Planner-agnostic (exploration) mode: run lifecycle is driven by /run_state,
    # NOT by scenario_controller goal-flying. Skip the controller entirely so it
    # can't throw and write a failing controller_result.json that poisons the
    # evaluation verdict. Clear any stale sidecar from a prior goal-mode run.
    rm -f "$OUTPUT_DIR/controller_result.json"
    echo "[METRICS] run_state mode: scenario_controller DISABLED (lifecycle = /run_state). Waiting on collector..."
    CONTROLLER_PID=$METRICS_PID
    wait "$CONTROLLER_PID"
    CONTROLLER_EXIT=$?
    echo "[METRICS] Metrics collector exited with code: $CONTROLLER_EXIT"
else
    echo "[METRICS] Starting scenario_controller.py..."
    python3 /metrics/scripts/scenario_controller.py \
        --ros-args \
        --params-file "$SCENARIO_CONTROLLER_CONFIG" \
        -p mission_file:="$MISSION_FILE" \
        -p output_dir:="$OUTPUT_DIR" &

    CONTROLLER_PID=$!
    echo "[METRICS] Scenario controller started with PID: $CONTROLLER_PID"

    wait "$CONTROLLER_PID"
    CONTROLLER_EXIT=$?
    echo "[METRICS] Scenario controller exited with code: $CONTROLLER_EXIT"
fi

echo "[METRICS] Stopping metrics collector..."
if [ -n "$METRICS_PID" ] && kill -0 "$METRICS_PID" 2>/dev/null; then
    kill -SIGTERM "$METRICS_PID" 2>/dev/null || true

    WAIT_COUNT=0
    while kill -0 "$METRICS_PID" 2>/dev/null && [ $WAIT_COUNT -lt 20 ]; do
        sleep 0.5
        WAIT_COUNT=$((WAIT_COUNT + 1))
    done

    if kill -0 "$METRICS_PID" 2>/dev/null; then
        echo "[METRICS] Forcing metrics collector shutdown..."
        kill -SIGKILL "$METRICS_PID" 2>/dev/null || true
    fi
fi

FINAL_EXIT_CODE=$CONTROLLER_EXIT

if [ -f "$OUTPUT_DIR/metrics.json" ]; then
    echo "[METRICS] Running offline evaluation..."

    if [ -x /metrics/evaluation/evaluate.py ] && [ -f "$EVALUATION_CONFIG" ]; then
        EVAL_CMD="python3 /metrics/evaluation/evaluate.py"
        EVAL_CMD="$EVAL_CMD --metrics \"$OUTPUT_DIR/metrics.json\""
        EVAL_CMD="$EVAL_CMD --config \"$EVALUATION_CONFIG\""
        EVAL_CMD="$EVAL_CMD --output \"$OUTPUT_DIR/evaluated.json\""
        EVAL_CMD="$EVAL_CMD --trajectories-dir \"$OUTPUT_DIR\""

        if [ -f "$MISSION_FILE" ]; then
            EVAL_CMD="$EVAL_CMD --mission \"$MISSION_FILE\""
            echo "[METRICS] Using mission file for ATE reference: $MISSION_FILE"
        fi

        TRAJ_COUNT=$(ls -1 "$OUTPUT_DIR"/*_trajectory.csv 2>/dev/null | wc -l || true)
        if [ "$TRAJ_COUNT" -gt 0 ]; then
            echo "[METRICS] Found $TRAJ_COUNT trajectory file(s) for ATE/RPE evaluation"
        fi

        eval $EVAL_CMD
        EVAL_EXIT_CODE=$?

        echo "[METRICS] Evaluation completed with code: $EVAL_EXIT_CODE"

        if [ $CONTROLLER_EXIT -ne 0 ]; then
            FINAL_EXIT_CODE=$CONTROLLER_EXIT
        else
            FINAL_EXIT_CODE=$EVAL_EXIT_CODE
        fi
    else
        echo "[METRICS] Evaluation script not available or config missing"
        echo "[METRICS] Using controller exit code: $CONTROLLER_EXIT"
    fi
else
    echo "[METRICS] WARNING: No metrics.json found at $OUTPUT_DIR/metrics.json"
    echo "[METRICS] Using controller exit code: $CONTROLLER_EXIT"
fi

if [ -f "$OUTPUT_DIR/metrics.json" ]; then
    echo "[METRICS] Ingesting run summary to Elasticsearch..."
    python3 /metrics/scripts/ingest_to_es.py || echo "[METRICS] WARNING: ES ingestion returned non-zero (non-fatal)"
fi

echo "=========================================="
echo "  Metrics Collection Complete"
echo "=========================================="
echo "Controller exit: $CONTROLLER_EXIT"
echo "Final exit code: $FINAL_EXIT_CODE"

if [ -f "$OUTPUT_DIR/metrics.json" ]; then
    echo ""
    echo "Metrics output: $OUTPUT_DIR/metrics.json"
    echo "---"
    cat "$OUTPUT_DIR/metrics.json"
    echo ""
fi

TRAJ_FILES=$(ls -1 "$OUTPUT_DIR"/*_trajectory.csv 2>/dev/null || true)
if [ -n "$TRAJ_FILES" ]; then
    echo ""
    echo "Trajectory files:"
    for f in $TRAJ_FILES; do
        LINE_COUNT=$(wc -l < "$f")
        echo "  - $f ($LINE_COUNT poses)"
    done
    echo ""
fi

if [ -f "$OUTPUT_DIR/evaluated.json" ]; then
    echo ""
    echo "Evaluation output: $OUTPUT_DIR/evaluated.json"
    echo "---"
    cat "$OUTPUT_DIR/evaluated.json"
    echo ""
fi

if [ -f "$OUTPUT_DIR/controller_result.json" ]; then
    echo ""
    echo "Controller result: $OUTPUT_DIR/controller_result.json"
    echo "---"
    cat "$OUTPUT_DIR/controller_result.json"
    echo ""
fi

echo "=========================================="

exit $FINAL_EXIT_CODE