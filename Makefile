# M&S Simulation Runtime Stack — dev convenience wrapper around ./launch.sh.
#
# Scenario targets (each wraps `./launch.sh <scenario> <flags>`):
#   make ardupilot-xfs | px4-xfs | px4-condo | ardupilot-condo
#
# Flag vars -> launch.sh flags (set to `true` to enable):
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

HEADLESS        ?= false
AGENT_EXTERNAL  ?= false
PIXEL_STREAMING ?= false
MONITORING      ?= false
METRICS         ?= false
ALL             ?= false
# `make generate SCENARIO=px4-xfs` limits the generator; empty = all scenarios.
# Also forwarded to `make stop` (stop.sh auto-detects when empty).
SCENARIO        ?=
SCENARIO_SPEC   ?=
STACK           ?= generated/scenariospec

LAUNCH_FLAGS :=
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

SCENARIOS := ardupilot-xfs px4-xfs px4-condo ardupilot-condo px4-safticity

.PHONY: help $(SCENARIOS) scenariospec scenariospec-generate scenariospec-stop scenariospec-logs attach teleop stop logs ps generate check self-test

help:
	@echo "Scenario targets (wrap ./launch.sh):"
	@echo "  make ardupilot-xfs | px4-xfs | px4-condo | ardupilot-condo | px4-safticity"
	@echo "Flag vars (=true): HEADLESS AGENT_EXTERNAL PIXEL_STREAMING MONITORING METRICS ALL"
	@echo "Utility targets:"
	@echo "  attach     tmux dev session: rviz2|teleop side by side + sim/per-drone log windows"
	@echo "  teleop     WASD keyboard flight via mavros_dN [DRONE=1 AUTOPILOT=px4|ardupilot]"
	@echo "  scenariospec          generate + run ScenarioSpec [SCENARIO_SPEC=/path STACK=generated/name]"
	@echo "  scenariospec-generate generate image-only ScenarioSpec stack only"
	@echo "  scenariospec-stop     ./stop.sh --stack $(STACK)"
	@echo "  scenariospec-logs     ./logs.sh stack $(STACK) -f"
	@echo "  stop                  ./stop.sh [SCENARIO=name]"
	@echo "  logs                  ./logs.sh"
	@echo "  ps         running containers (name/status/image)"
	@echo "  generate   render compose files from templates [SCENARIO=name]"
	@echo "  check      exit nonzero when rendered files drift from .env+templates"
	@echo "  self-test  generator invariant checks"
	@echo "Current flags: $(if $(LAUNCH_FLAGS),$(LAUNCH_FLAGS),(none))"

$(SCENARIOS):
	./launch.sh $@ $(LAUNCH_FLAGS)

scenariospec:
	@if [ -z "$(SCENARIO_SPEC)" ]; then echo "SCENARIO_SPEC=/path/to/ScenarioSpec is required"; exit 1; fi
	./launch.sh --scenario-spec "$(SCENARIO_SPEC)" --stack-output "$(STACK)" -d

scenariospec-generate:
	@if [ -z "$(SCENARIO_SPEC)" ]; then echo "SCENARIO_SPEC=/path/to/ScenarioSpec is required"; exit 1; fi
	./launch.sh --scenario-spec "$(SCENARIO_SPEC)" --stack-output "$(STACK)" --generate-only

scenariospec-stop:
	./stop.sh --stack "$(STACK)" --remove-orphans

scenariospec-logs:
	./logs.sh stack "$(STACK)" -f

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
