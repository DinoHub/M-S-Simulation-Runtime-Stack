# M&S Simulation Runtime Stack — dev convenience wrapper around ./launch.sh.
#
# Scenario targets (each wraps `./launch.sh <scenario> <flags>`):
#   make ardupilot-xfs | px4-xfs | px4-condo | ardupilot-condo
#
# Flag vars -> launch.sh flags (set to `true` to enable):
#   EDITOR=true            -> --editor                (skip sim container; run AirSim in UE editor)
#   HEADLESS=true          -> --headless              (AirSim -RenderOffScreen)
#   AGENT_EXTERNAL=true    -> --with-agent-external   (per-drone zenoh bridges)
#   PIXEL_STREAMING=true   -> --with-pixel-streaming  (UE5 signalling sidecar)
#   MONITORING=true        -> --with-monitoring       (grafana/prometheus)
#   METRICS=true           -> --with-metrics          (metrics stack)
#   ALL=true               -> --all                   (monitoring + metrics)
# e.g.  make ardupilot-xfs HEADLESS=true AGENT_EXTERNAL=true
#
# Scenario shape (NUM_DRONES etc.) lives in .env — single source of truth.
# Generated compose files are regenerated automatically on drift by launch.sh;
# `make generate` / `make check` / `make self-test` drive the generator directly.

EDITOR          ?= false
HEADLESS        ?= false
AGENT_EXTERNAL  ?= false
PIXEL_STREAMING ?= false
MONITORING      ?= false
METRICS         ?= false
ALL             ?= false
# `make generate SCENARIO=px4-xfs` limits the generator; empty = all scenarios.
# Also forwarded to `make stop` (stop.sh auto-detects when empty).
SCENARIO        ?=

LAUNCH_FLAGS :=
ifeq ($(EDITOR),true)
LAUNCH_FLAGS += --editor
endif
ifeq ($(HEADLESS),true)
LAUNCH_FLAGS += --headless
endif
ifeq ($(AGENT_EXTERNAL),true)
LAUNCH_FLAGS += --with-agent-external
endif
ifeq ($(PIXEL_STREAMING),true)
LAUNCH_FLAGS += --with-pixel-streaming
endif
ifeq ($(MONITORING),true)
LAUNCH_FLAGS += --with-monitoring
endif
ifeq ($(METRICS),true)
LAUNCH_FLAGS += --with-metrics
endif
ifeq ($(ALL),true)
LAUNCH_FLAGS += --all
endif

SCENARIOS := ardupilot-xfs px4-xfs px4-condo ardupilot-condo

.PHONY: help $(SCENARIOS) dev attach teleop stop logs ps generate check self-test

dashboard:  ## TEVV Web Dashboard (browser entry point) on :3001; DB=true adds telemetry DB
	# Create these as the HOST user first, the way product.sh does. The backend
	# container runs as root, so if it mkdir's generated/ itself the directory
	# lands root-owned — and the generator image runs --user $$(id -u):$$(id -g),
	# so it then fails with "PermissionError: [Errno 13] ... generated/<name>"
	# surfacing as a 422 from /api/scenario/generate.
	@mkdir -p generated scenarios
	# Fail on an unreachable daemon or an occupied port here, with the occupant
	# named, instead of mid-`up` — or, for the host-net ros2-node, not at all.
	@. ./tools/check_docker.sh; check_docker || exit 1; \
	check_registry DASHBOARD_PULL_POLICY || true; \
	check_ports 3001:airsim-dashboard-frontend:frontend \
	            8001:airsim-dashboard-api:backend \
	            $(or $(DASHBOARD_LICHTBLICK_PORT),8082):dashboard-lichtblick:Lichtblick \
	            $(or $(FOXGLOVE_BRIDGE_PORT),8765):ros2-node:"Foxglove websocket" || exit 1
	@set -a; . ./product-images.env; set +a; \
	MSRS_ROOT=$$(pwd) HOST_UID=$$(id -u) HOST_GID=$$(id -g) \
	INIT_DB_SCHEMA=$(if $(filter true,$(DB)),true,false) \
	docker compose -f docker-compose-dashboard.yml $(if $(filter true,$(DB)),--profile db,) up -d
	@echo "Dashboard: http://localhost:3001 (backend :8001, lichtblick :$(or $(DASHBOARD_LICHTBLICK_PORT),8082))"

dashboard-down:
	# --profile db unconditionally: without it `down` skips profiled services and
	# leaves postgres-telemetry running as an orphan after a `DB=true` session.
	# Naming a profile that was never up is a no-op, so this is safe either way.
	@set -a; . ./product-images.env; set +a; \
	MSRS_ROOT=$$(pwd) docker compose -f docker-compose-dashboard.yml --profile db down

help:
	@echo "Scenario targets (wrap ./launch.sh):"
	@echo "  make ardupilot-xfs | px4-xfs | px4-condo | ardupilot-condo"
	@echo "Flag vars (=true): EDITOR HEADLESS AGENT_EXTERNAL PIXEL_STREAMING MONITORING METRICS ALL"
	@echo "  EDITOR=true skips containerized AirSim (run it from the Unreal editor on host)"
	@echo "Utility targets:"
	@echo "  dev        launch scenario (SCENARIO=name, default px4-xfs) + flags, then attach tmux"
	@echo "  attach     tmux dev session: rviz2|teleop side by side + sim/per-drone log windows"
	@echo "  teleop     WASD keyboard flight via mavros_dN [DRONE=1 AUTOPILOT=px4|ardupilot]"
	@echo "  scenariospec          generate + run ScenarioSpec [SCENARIO_SPEC=/path STACK=generated/name]"
	@echo "  scenariospec-generate generate image-only ScenarioSpec stack only"
	@echo "  stop                  ./stop.sh [SCENARIO=name]"
	@echo "  logs                  ./logs.sh"
	@echo "  ps         running containers (name/status/image)"
	@echo "  generate   render compose files from templates [SCENARIO=name]"
	@echo "  check      exit nonzero when rendered files drift from .env+templates"
	@echo "  self-test  generator invariant checks"
	@echo "Current flags: $(if $(LAUNCH_FLAGS),$(LAUNCH_FLAGS),(none))"

$(SCENARIOS):
	./launch.sh $@ $(LAUNCH_FLAGS)

# One-shot dev UX: bring a scenario up (autopilot SITL + AirSim + per-drone
# bridges + QGroundControl) with the usual flag vars, then attach the tmux
# dashboard. QGC is part of every scenario's compose, so it comes up here too.
# launch.sh uses `up -d`, so this returns before attaching; detaching the tmux
# session (Ctrl-b d) leaves the whole stack running.
#   make dev                                 # default scenario px4-xfs
#   make dev SCENARIO=ardupilot-xfs HEADLESS=true AGENT_EXTERNAL=true
#   make dev EDITOR=true                      # AirSim from the Unreal editor on host
DEV_SCENARIO ?= $(if $(SCENARIO),$(SCENARIO),px4-xfs)
dev:
	./launch.sh $(DEV_SCENARIO) $(LAUNCH_FLAGS)
	./tools/attach-session.sh

# tmux dev-session UX on top of the detached stack (bridge-repo `make dev`
# style). Focus window "dev": rviz2 (left, GUI on $DISPLAY) | teleop (right),
# each dropping to a shell in its container when the process exits. Plus sim
# logs and per-drone bridge|mavros log windows. Detach with Ctrl-b d —
# containers keep running either way.
attach:
	./tools/attach-session.sh

# WASD keyboard teleop over MAVROS — exec into the running mavros_dN, which
# already carries the right ROS_DOMAIN_ID for its drone. Run from any
# terminal while the stack is up. Keys: wasd move, r/f up/down, q/e yaw,
# 1 mode, 2 arm, 3 takeoff, 4 land, 0 disarm, space stop, x quit.
#   make teleop                    # drone 1, autopilot auto-detected
#   make teleop DRONE=2            # px4-xfs drone 2
#   make teleop AUTOPILOT=ardupilot VEHICLE=Copter1
DRONE     ?= 1
VEHICLE   ?= Copter$(DRONE)
AUTOPILOT ?= $(shell docker ps --format '{{.Names}}' | grep -q '^ardupilot' && echo ardupilot || echo px4)
teleop:
	docker exec -it mavros_d$(DRONE) bash -lc 'ros2 run airsim_mavros_bringup mavros_teleop_keyboard.py \
		--ros-args -p vehicle:=$(VEHICLE) -p autopilot:=$(AUTOPILOT)'

stop:
	./stop.sh $(SCENARIO)

logs:
	./logs.sh

ps:
	@docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'

generate:
	python3 tools/generate_scenario.py $(if $(SCENARIO),--scenario $(SCENARIO))

check:
	python3 tools/generate_scenario.py --check $(if $(SCENARIO),--scenario $(SCENARIO))

self-test:
	python3 tools/generate_scenario.py --self-test
