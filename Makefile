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

SCENARIOS := ardupilot-xfs px4-xfs px4-condo ardupilot-condo

.PHONY: help $(SCENARIOS) stop logs ps generate check self-test

help:
	@echo "Scenario targets (wrap ./launch.sh):"
	@echo "  make ardupilot-xfs | px4-xfs | px4-condo | ardupilot-condo"
	@echo "Flag vars (=true): HEADLESS AGENT_EXTERNAL PIXEL_STREAMING MONITORING METRICS ALL"
	@echo "Utility targets:"
	@echo "  stop       ./stop.sh [SCENARIO=name]"
	@echo "  logs       ./logs.sh"
	@echo "  ps         running containers (name/status/image)"
	@echo "  generate   render compose files from templates [SCENARIO=name]"
	@echo "  check      exit nonzero when rendered files drift from .env+templates"
	@echo "  self-test  generator invariant checks"
	@echo "Current flags: $(if $(LAUNCH_FLAGS),$(LAUNCH_FLAGS),(none))"

$(SCENARIOS):
	./launch.sh $@ $(LAUNCH_FLAGS)

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
