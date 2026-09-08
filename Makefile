# M&S Simulation Runtime Stack — dev convenience wrapper around ./launch.sh.
#
# Scenario targets (each wraps `./launch.sh <scenario> <flags>`):
#   make ardupilot-xfs | ardupilot-urbansim | px4-xfs | px4-condo | ardupilot-condo
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

# Transitional image workflow: development is local-first and tag-only;
# production keeps the immutable catalog pins.
IMAGE_MODE ?= development
# Which standalone-v2 release channel the dashboard runs (images/catalog.yaml
# consumers.release_channels). Each channel owns a pack lock, a runtime-host
# capability contract, and its own pack store + authoring data root under
# .mns/, because packs cooked for one engine line never mount on the other
# and the product shell stages everything in its store for ONE contract.
#   v2     UE 5.5.4 (default): packs/standalone-v2-review.1.lock.json, .mns/{pack-store,authoring-data}
#   ue582  UE 5.8.2 candidate: packs/standalone-v2-ue582.lock.json,      .mns/ue582/{pack-store,authoring-data}
CHANNEL ?= v2
# Standalone-v2 demo packs `make dashboard` guarantees are installed before
# ScenarioLab opens, as tools/install-demo-packs.sh selections. Only packs
# MISSING from .mns/pack-store are downloaded (--missing), so a re-run costs one
# offline lock/index comparison. The full set is ~2.6 GB (XFS 1.17 GB, SAFTI
# 0.83 GB, Pendleton 0.6 GB, Condo 9 MB, three object packs ~30 MB).
#   make dashboard MNS_DEMO_PACKS="--condo --objects"   # a small subset
#   make dashboard MNS_SKIP_PACK_INSTALL=1             # offline / v1-only
MNS_DEMO_PACKS ?= --all
MNS_SKIP_PACK_INSTALL ?= 0
# Fixed rather than derived from the checkout directory. The dashboard services
# carry daemon-global container_names (airsim-dashboard-api, ...), so two
# projects could never run side by side anyway; a stable project name at least
# makes `dashboard-down` find them from a worktree or a renamed clone. The cost
# is a one-time migration: a dashboard started BEFORE this change lives under
# the directory-derived project, so dashboard-down tears that one down as well
# (see LEGACY_DASHBOARD_PROJECT below) or its containers would be orphaned and
# the next `up` would fail with `Conflict. The container name
# "/airsim-dashboard-api" is already in use`.
DASHBOARD_COMPOSE_PROJECT_NAME ?= m-s-simulation-runtime-stack
# Compose's own normalisation of the directory name: lowercased, restricted to
# [a-z0-9_-]. This is the project name a checkout used before the line above.
LEGACY_DASHBOARD_PROJECT = $(shell basename "$(CURDIR)" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')
# auto  - recreate ros2-tools only when the selected bridge image changed
# always- always recreate (the pre-existing behaviour)
# never - never touch it
RECREATE_ROS2_TOOLS ?= auto
ifeq ($(CHANNEL),v2)
CHANNEL_NAME := standalone_v2
CHANNEL_ENV := images/standalone-v2-images.generated.env
CHANNEL_DEV_ENV := images/standalone-v2-development.generated.env
CHANNEL_IMAGE_SET := published
CHANNEL_LOCK := packs/standalone-v2-review.1.lock.json
CHANNEL_CONTRACT := packs/runtime-host-compatibility.json
CHANNEL_STORE := .mns/pack-store
CHANNEL_DATA := .mns/authoring-data
# Neither standalone-v2 authoring image ships the mns_vehicle_models /
# scenario_runtime_basic default asset packs; seed them from the v1 image
# (see tools/stage-authoring-packs.sh).
CHANNEL_SEED_DEFAULTS := 1
else ifeq ($(CHANNEL),ue582)
CHANNEL_NAME := standalone_v2_ue582
CHANNEL_ENV := images/standalone-v2-ue582.generated.env
# No -latest aliases exist on this line yet: development and production
# source the same pinned/local file.
CHANNEL_DEV_ENV := images/standalone-v2-ue582.generated.env
CHANNEL_IMAGE_SET := ue582
CHANNEL_LOCK := packs/standalone-v2-ue582.lock.json
CHANNEL_CONTRACT := packs/runtime-host-compatibility.ue582.json
CHANNEL_STORE := .mns/ue582/pack-store
CHANNEL_DATA := .mns/ue582/authoring-data
# The 5.5.4-cooked vehicle-model pak mounts in the 5.8.2 ScenarioLab (verified
# 2026-09-09: "Loaded asset pack: mns_vehicle_models (0 asset(s), 6 vehicle
# model(s))"), so the same seed serves this channel until an authoring image
# ships its own.
CHANNEL_SEED_DEFAULTS := 1
else
$(error CHANNEL must be v2 or ue582)
endif

# Exported to every script on the dashboard chain and interpolated by
# docker-compose-dashboard.yml, so the installer, the staging step, the
# backend and the nested generator all resolve packs from one place.
CHANNEL_ENV_EXPORTS := MNS_CHANNEL=$(CHANNEL) MNS_IMAGE_SET=$(CHANNEL_IMAGE_SET) \
	MNS_DEMO_PACK_LOCK=$(CURDIR)/$(CHANNEL_LOCK) \
	MNS_RUNTIME_HOST_COMPATIBILITY_CONTRACT=$(CURDIR)/$(CHANNEL_CONTRACT) \
	MNS_PACK_STORE_ROOT=$(CURDIR)/$(CHANNEL_STORE) \
	MNS_AUTHORING_DATA_ROOT=$(CURDIR)/$(CHANNEL_DATA) \
	MNS_SEED_AUTHORING_DEFAULTS=$(CHANNEL_SEED_DEFAULTS)

ifeq ($(IMAGE_MODE),development)
DASHBOARD_IMAGE_SET_FILE := images/image-set.development.generated.yaml
ENSURE_IMAGES_FLAG := --development --channel $(CHANNEL_NAME)
LOAD_DASHBOARD_IMAGES := export $(CHANNEL_ENV_EXPORTS); load_images_env ./$(CHANNEL_DEV_ENV); load_images_env ./images/standalone-v2-development.generated.env
else ifeq ($(IMAGE_MODE),production)
DASHBOARD_IMAGE_SET_FILE := images/image-set.generated.yaml
ENSURE_IMAGES_FLAG := --production --channel $(CHANNEL_NAME)
LOAD_DASHBOARD_IMAGES := export $(CHANNEL_ENV_EXPORTS); load_images_env ./$(CHANNEL_ENV); load_images_env ./product-images.env; load_images_env ./images/platform-images.generated.env
else
$(error IMAGE_MODE must be development or production)
endif

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

SCENARIOS := ardupilot-xfs ardupilot-urbansim px4-xfs px4-condo ardupilot-condo

.PHONY: help $(SCENARIOS) dev attach teleop stop logs ps generate check self-test topics verify-images pull-images ensure-images ensure-demo-packs stage-authoring-packs dashboard dashboard-down

ensure-images:  ## Use local image tags; pull only those that are missing
	./tools/ensure-images.sh $(ENSURE_IMAGES_FLAG)

# The product-shell image comes from the selected IMAGE_MODE's env (the
# -latest alias in development, the digest pin in production), not from the
# pack lock's pin, so install and the staging step right after it use ONE
# shell image and stage-authoring-packs.sh's .staged-with stamp stays current.
ensure-demo-packs: ensure-images  ## Install any standalone-v2 demo packs missing from .mns/pack-store
	@if [ "$(MNS_SKIP_PACK_INSTALL)" = "1" ]; then \
	  echo "MNS_SKIP_PACK_INSTALL=1: not installing demo packs."; \
	else \
	  . ./tools/load-images-env.sh; $(LOAD_DASHBOARD_IMAGES); \
	  ./tools/install-demo-packs.sh --missing $(MNS_DEMO_PACKS); \
	fi

stage-authoring-packs: ensure-demo-packs  ## Refresh ScenarioLab's view of installed immutable packs
	@. ./tools/load-images-env.sh; \
	$(LOAD_DASHBOARD_IMAGES); \
	./tools/stage-authoring-packs.sh

dashboard: stage-authoring-packs  ## TEVV Web Dashboard (browser entry point) on :3001; DB=true adds telemetry DB
	# Create these as the HOST user first, the way product.sh does. The backend
	# container runs as root, so if it mkdirs generated/ itself the directory
	# lands root-owned and the generator image cannot write into it.
	@mkdir -p generated scenarios
	# Fail early on Docker, X11, or host-port problems.
	@. ./tools/check_docker.sh; check_docker || exit 1; \
	. ./tools/load-images-env.sh; \
	$(LOAD_DASHBOARD_IMAGES); \
	check_images "$$MNS_STACK_GENERATOR_IMAGE" "$$MNS_AUTHORING_IMAGE" || true; \
	check_x11 || true; \
	check_ports 3001:airsim-dashboard-frontend:frontend \
	            8001:airsim-dashboard-api:backend \
	            $(or $(DASHBOARD_LICHTBLICK_PORT),8082):dashboard-lichtblick:Lichtblick \
	            $(or $(FOXGLOVE_BRIDGE_PORT),8764):ros2-tools:"Foxglove websocket" || exit 1
	@. ./tools/compose_retry.sh; . ./tools/load-images-env.sh; \
	$(LOAD_DASHBOARD_IMAGES); \
	COMPOSE_PROJECT_NAME=$(DASHBOARD_COMPOSE_PROJECT_NAME) MNS_IMAGE_SET_FILE=$$(pwd)/$(DASHBOARD_IMAGE_SET_FILE) \
	MSRS_ROOT=$$(pwd) HOST_UID=$$(id -u) HOST_GID=$$(id -g) \
	compose_retry -f docker-compose-dashboard.yml $(if $(filter true,$(DB)),--profile db,) up -d
	# ros2-tools is created outside Compose by the backend. It serves the
	# Foxglove websocket Lichtblick renders from and is what services/
	# ros2_recorder.py execs into for bag recording, so removing it
	# unconditionally destroyed an in-progress recording and dropped the viz
	# connection every time `make dashboard` was re-run against a live stack.
	# Recreate it only when the selected bridge image actually changed —
	# RECREATE_ROS2_TOOLS=always restores the old behaviour, =never skips it.
	@. ./tools/load-images-env.sh; $(LOAD_DASHBOARD_IMAGES); \
	desired="$${MNS_ROS2_BRIDGE_IMAGE:-}"; \
	current=$$(docker inspect -f '{{.Config.Image}}' ros2-tools 2>/dev/null || true); \
	if [ "$(RECREATE_ROS2_TOOLS)" = "never" ]; then \
	  echo "RECREATE_ROS2_TOOLS=never: leaving ros2-tools as it is."; \
	elif [ "$(RECREATE_ROS2_TOOLS)" = "always" ] || [ -z "$$current" ] || [ "$$current" != "$$desired" ]; then \
	  [ -n "$$current" ] && [ "$$current" != "$$desired" ] && echo "ros2-tools image changed ($$current -> $$desired); recreating."; \
	  docker rm -f ros2-tools >/dev/null 2>&1 || true; \
	else \
	  echo "ros2-tools already running $$desired; leaving it (Foxglove :8764 and any bag recording stay up)."; \
	fi; \
	docker restart airsim-dashboard-api >/dev/null
	@$(if $(filter true,$(DB)),. ./tools/load-images-env.sh; $(LOAD_DASHBOARD_IMAGES); COMPOSE_PROJECT_NAME=$(DASHBOARD_COMPOSE_PROJECT_NAME) MNS_IMAGE_SET_FILE=$$(pwd)/$(DASHBOARD_IMAGE_SET_FILE) MSRS_ROOT=$$(pwd) docker compose -f docker-compose-dashboard.yml --profile db restart dashboard-backend >/dev/null && echo "Telemetry pool reconnected.",true)
	@echo "Dashboard: http://localhost:3001 (backend :8001, lichtblick :$(or $(DASHBOARD_LICHTBLICK_PORT),8082), image mode: $(IMAGE_MODE))"

dashboard-down:
	@. ./tools/load-images-env.sh; \
	$(LOAD_DASHBOARD_IMAGES); \
	COMPOSE_PROJECT_NAME=$(DASHBOARD_COMPOSE_PROJECT_NAME) MNS_IMAGE_SET_FILE=$$(pwd)/$(DASHBOARD_IMAGE_SET_FILE) \
	MSRS_ROOT=$$(pwd) docker compose -f docker-compose-dashboard.yml --profile db down
	# Also tear down the pre-fixed-name project, or a dashboard started before
	# DASHBOARD_COMPOSE_PROJECT_NAME existed is left running and its
	# daemon-global container names block the next `up`.
	@if [ "$(LEGACY_DASHBOARD_PROJECT)" != "$(DASHBOARD_COMPOSE_PROJECT_NAME)" ]; then \
	  . ./tools/load-images-env.sh; $(LOAD_DASHBOARD_IMAGES); \
	  COMPOSE_PROJECT_NAME=$(LEGACY_DASHBOARD_PROJECT) MNS_IMAGE_SET_FILE=$$(pwd)/$(DASHBOARD_IMAGE_SET_FILE) \
	  MSRS_ROOT=$$(pwd) docker compose -f docker-compose-dashboard.yml --profile db down >/dev/null 2>&1 || true; \
	fi

help:
	@echo "Scenario targets (wrap ./launch.sh):"
	@echo "  make ardupilot-xfs | ardupilot-urbansim | px4-xfs | px4-condo | ardupilot-condo"
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
	@echo "  topics     ROS 2 topics a stack will publish, before starting it"
	@echo "             [SCENARIO=name | STACK=generated/name]"
	@echo "  generate   render compose files from templates [SCENARIO=name]"
	@echo "  check      exit nonzero when rendered files drift from .env+templates"
	@echo "  self-test  generator invariant checks"
	@echo "  verify-images  CI gate: images/catalog.yaml matches generated artifacts"
	@echo "  ensure-images  use local tags and pull only missing images [IMAGE_MODE=development|production]"
	@echo "  pull-images    explicitly refresh every exact published remote image pin"
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

# What a stack will publish, without starting it. Resolves settings.json
# sensors/cameras + topic_names.yaml renames + topic_prefix using the bridge
# image's own launch code, so it cannot drift from what the bridge really does.
#   make topics                          # default scenario
#   make topics SCENARIO=ardupilot-xfs
#   make topics STACK=generated/xfs-fisheye
topics:
	@python3 tools/preview_topics.py $(or $(STACK),$(SCENARIO),$(DEV_SCENARIO))

generate:
	python3 tools/generate_scenario.py $(if $(SCENARIO),--scenario $(SCENARIO))

check:
	python3 tools/generate_scenario.py --check $(if $(SCENARIO),--scenario $(SCENARIO))

self-test:
	python3 tools/generate_scenario.py --self-test

# CI gate for the image catalog (images/catalog.yaml): regenerates
# product-images.env / images/*.generated.* into a temp location and diffs
# against the committed copies. Offline — no registry calls. See
# docs/adr/0002-one-image-catalog.md.
verify-images:
	./tools/images.sh verify

pull-images:
	./tools/pull-all-images.sh
