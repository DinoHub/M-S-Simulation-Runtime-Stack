# MnS product: the TEVV Web Dashboard, the same flows headless, and the
# content/image tooling.
#
#   make dashboard / make dashboard-down   the browser entry point (:3001)
#   make fly SCENARIO=<name> [RECORD=1]    fly one scenario headless for FLY_SECONDS (300; KEEP=1: leave it up)
#   make author [SCENARIO=<name>]          open ScenarioLab
#   make campaign [CAMPAIGN=<name>]        fly a scored run matrix
#   make topics STACK=generated/<name>     what a generated stack will publish
#   make help                              everything else
#
# One-time setup is ./setup.sh (machine, .env, images) then
# ./download-packs.sh (content packs); see docs/USER_GUIDE.md.
.DEFAULT_GOAL := help

# X11 cookie for ScenarioLab and the simulator window. A desktop terminal
# already exports it; SSH, tmux and some Wayland shells do not, and then
# Unreal containers crash-loop. Keep a valid XAUTHORITY, otherwise use the
# newest GNOME/Wayland (mutter) cookie, then the GDM one. Empty when none
# exists, and tools/check_docker.sh's check_x11 then says what to do.
XAUTHORITY := $(shell f="$$XAUTHORITY"; [ -f "$$f" ] && { echo "$$f"; exit; }; \
	for c in $$(ls -t /run/user/$$(id -u)/.mutter-Xwaylandauth.* 2>/dev/null) /run/user/$$(id -u)/gdm/Xauthority; do \
	[ -f "$$c" ] && { echo "$$c"; exit; }; done)
export XAUTHORITY

# Which refs of the catalog's pins run. development: the pinned tags alone,
# so a local build under the same tag wins and a missing tag is pulled;
# production: tag@digest, exactly what images/catalog.yaml pins.
IMAGE_MODE ?= development
# The release channel the dashboard runs (images/catalog.yaml
# consumers.release_channels; packs/channels.json lists it for the dashboard).
# A channel owns a pack lock, a runtime-host capability contract, and its own
# pack store + authoring data root under .mns/<channel>/, because packs cooked
# for one engine build never mount on another.
#   v1  MnS 1.0, UE 5.8.2 (the stable line): packs/v1.0.0.lock.json, .mns/v1/
CHANNEL ?= v1
# Content packs are downloaded by ./download-packs.sh (or the dashboard's
# Content phase), not by `make dashboard`: it stages whatever the channel's
# store holds and warns when that is nothing. Set MNS_DEMO_PACKS to have it
# install missing packs first, as tools/install-demo-packs.sh selections
# (--missing, so a re-run costs one offline lock/index comparison):
#   make dashboard MNS_DEMO_PACKS=--all
#   make dashboard MNS_DEMO_PACKS="--warehouse --office"
#   make dashboard MNS_SKIP_PACK_INSTALL=1             # skip the store check too
MNS_DEMO_PACKS ?=
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
# The HOST runs directory, resolved once: the shell's TEVV_RUNS_DIR, else
# ./.env's, else ./runs; ~ and relative paths made absolute. Exported, so the
# dashboard compose file (its /data/runs mount and the backend's
# TEVV_RUNS_DIR) and tools/mns-stacks.sh all get the same absolute host path.
TEVV_RUNS_DIR := $(shell r="$$TEVV_RUNS_DIR"; [ -n "$$r" ] || r=$$(sed -n 's/^[[:space:]]*TEVV_RUNS_DIR=//p' .env 2>/dev/null | tail -1 | tr -d "\"'"); \
	[ -n "$$r" ] || r="$(CURDIR)/runs"; r=$$(printf '%s' "$$r" | sed "s|^~|$$HOME|"); \
	case "$$r" in (/*) ;; (*) r="$(CURDIR)/$$r" ;; esac; printf '%s' "$$r")
export TEVV_RUNS_DIR
ifeq ($(CHANNEL),v1)
CHANNEL_NAME := v1
CHANNEL_ENV := images/v1.0.0.generated.env
CHANNEL_DEV_ENV := images/v1.0.0.generated.env
CHANNEL_IMAGE_SET := v1
CHANNEL_LOCK := packs/v1.0.0.lock.json
CHANNEL_CONTRACT := packs/runtime-host-compatibility.v1.json
CHANNEL_AUTHORING_CONTRACT := packs/authoring-host-compatibility.v1.json
CHANNEL_STORE := .mns/v1/pack-store
CHANNEL_DATA := .mns/v1/authoring-data
PACK_RELEASE_REPO := DinoHub/TEVV-Airsim
else
$(error CHANNEL must be v1)
endif

# Exported to every script on the dashboard chain and interpolated by
# docker-compose-dashboard.yml, so the installer, the staging step, the
# backend and the nested generator all resolve packs from one place.
CHANNEL_ENV_EXPORTS := MNS_CHANNEL=$(CHANNEL) MNS_IMAGE_SET=$(CHANNEL_IMAGE_SET) \
	MNS_DEMO_PACK_LOCK=$(CURDIR)/$(CHANNEL_LOCK) \
	MNS_RUNTIME_HOST_COMPATIBILITY_CONTRACT=$(CURDIR)/$(CHANNEL_CONTRACT) \
	MNS_AUTHORING_HOST_CONTRACT=$(if $(CHANNEL_AUTHORING_CONTRACT),$(CURDIR)/$(CHANNEL_AUTHORING_CONTRACT),) \
	MNS_PACK_STORE_ROOT=$(CURDIR)/$(CHANNEL_STORE) \
	MNS_AUTHORING_DATA_ROOT=$(CURDIR)/$(CHANNEL_DATA) \
	MNS_PACKS_DIR=$(CURDIR)/.mns/$(CHANNEL)/packs

ifeq ($(IMAGE_MODE),development)
DASHBOARD_IMAGE_SET_FILE := images/image-set.development.generated.yaml
ENSURE_IMAGES_FLAG := --development --channel $(CHANNEL_NAME)
LOAD_DASHBOARD_IMAGES := export $(CHANNEL_ENV_EXPORTS); load_images_env ./$(CHANNEL_DEV_ENV); load_images_env ./images/development.generated.env
NOTE_IMAGE_OVERRIDES := note_image_overrides ./$(CHANNEL_DEV_ENV) ./images/development.generated.env
else ifeq ($(IMAGE_MODE),production)
DASHBOARD_IMAGE_SET_FILE := images/image-set.generated.yaml
ENSURE_IMAGES_FLAG := --production --channel $(CHANNEL_NAME)
LOAD_DASHBOARD_IMAGES := export $(CHANNEL_ENV_EXPORTS); load_images_env ./$(CHANNEL_ENV); load_images_env ./images/platform-images.generated.env
NOTE_IMAGE_OVERRIDES := note_image_overrides ./$(CHANNEL_ENV) ./images/platform-images.generated.env
else
$(error IMAGE_MODE must be development or production)
endif

.PHONY: help ps topics fly evaluate stop author author-stop stacks campaign campaign-status doctor verify-images pull-images ensure-images ensure-demo-packs pack-status pack-lock stage-authoring-packs dashboard dashboard-down print-channel-env

ensure-images:  ## Use local image tags; pull only those that are missing
	./tools/ensure-images.sh $(ENSURE_IMAGES_FLAG)

# The mns-packs image comes from the selected IMAGE_MODE's env (the pinned
# tag in development, tag@digest in production), not from the pack lock's
# pin, so install and the staging step right after it use ONE mns-packs image
# and stage-authoring-packs.sh's .staged-with stamp stays current.
# --sync: the lock decides each pack's version. It adds the lock's version of
# the vehicle models and of every pack already installed in any version, so a
# lock bump is pulled on the next start; packs never chosen are never fetched.
# What --sync adds is best effort (offline or no credentials: a warning).
ensure-demo-packs: ensure-images  ## Bring installed packs (and MNS_DEMO_PACKS, if set) to the lock's versions
	@if [ "$(MNS_SKIP_PACK_INSTALL)" = "1" ]; then \
	  echo "MNS_SKIP_PACK_INSTALL=1: not installing demo packs."; \
	elif [ -n "$(MNS_DEMO_PACKS)" ]; then \
	  . ./tools/load-images-env.sh; $(LOAD_DASHBOARD_IMAGES); \
	  ./tools/install-demo-packs.sh --missing --sync $(MNS_DEMO_PACKS); \
	else \
	  . ./tools/load-images-env.sh; $(LOAD_DASHBOARD_IMAGES); \
	  ./tools/install-demo-packs.sh --missing --sync \
	    || echo "WARNING: could not bring the packs to the lock's versions (above); continuing with what is installed."; \
	  if ! ./tools/install-demo-packs.sh --check --all 2>/dev/null | grep -q 'installed:'; then \
	    echo "WARNING: no content packs are installed for channel $(CHANNEL). Run ./download-packs.sh,"; \
	    echo "         or install them from the dashboard's Content step."; \
	  fi; \
	fi

# The lock is a snapshot of what packaging had published when it was built;
# this catches it up (newest version of every pack cooked for the channel's
# host id), downloading and verifying anything new with the channel's mns-packs.
# Review the diff, commit, then `make dashboard` installs what is new.
# The script exits nonzero when its NEEDS YOU list is non-empty, so CI can gate
# on it the way it gates on `tools/images.sh verify`. That is a report, not a
# build failure, so this target swallows it: call the script directly to gate.
pack-status:  ## What is locked, what is installed, and what has been published since
	-@. ./tools/load-images-env.sh; $(LOAD_DASHBOARD_IMAGES); \
	tools/pull-packs.sh --status --json

pack-lock: ensure-images  ## Rebuild the channel's pack lock from every pack release cooked for its host id
	@. ./tools/load-images-env.sh; $(LOAD_DASHBOARD_IMAGES); \
	python3 tools/build_pack_lock.py --release-repo $(PACK_RELEASE_REPO) --discover \
	  --host-contract $(CHANNEL_CONTRACT) --images-env $(CHANNEL_ENV) \
	  --packs "$$MNS_PACKS_IMAGE" --cache .mns/downloads/pack-cache \
	  --output $(CHANNEL_LOCK) --lock-tag $(notdir $(basename $(basename $(CHANNEL_LOCK))))

# The channel's lock, contracts and pack roots as shell assignments, for
# scripts that must follow CHANNEL= exactly as the dashboard does
# (download-packs.sh):  eval "$(make -s print-channel-env)"
print-channel-env:
	@for kv in $(CHANNEL_ENV_EXPORTS); do printf 'export %s\n' "$$kv"; done

stage-authoring-packs: ensure-demo-packs  ## Refresh ScenarioLab's view of installed immutable packs
	@. ./tools/load-images-env.sh; \
	$(LOAD_DASHBOARD_IMAGES); \
	./tools/stage-authoring-packs.sh

dashboard: stage-authoring-packs  ## TEVV Web Dashboard (browser entry point) on :3001; DB=true adds telemetry DB
	@# Create these as the HOST user first. The backend container runs as
	@# root, so if it mkdirs generated/ itself the directory lands root-owned
	@# and mns-stacks generate (which runs as you) cannot write into it.
	@# The pack drop-in directory is a bind source too; a root-owned one
	@# would need sudo to drop a pack archive into.
	@mkdir -p generated scenarios "$(CURDIR)/.mns/$(CHANNEL)/packs"
	@# Same for the runs directory: compose would create a missing bind source
	@# as root, and the recorder (the host uid) then cannot write a bag into it
	@# ("Failed to create database directory"). TEVV_RUNS_DIR may come from the
	@# shell or ./.env, as it does for compose; the default is ./runs here.
	@runs="$(TEVV_RUNS_DIR)"; \
	mkdir -p "$$runs" 2>/dev/null; \
	if [ ! -w "$$runs" ]; then \
	  echo "ERROR: the runs directory $$runs is not writable by you (owner: $$(stat -c %U "$$runs" 2>/dev/null)),"; \
	  echo "       so recordings would fail. Fix it with:  sudo chown -R $$(id -un): $$runs"; \
	  exit 1; \
	fi
	@# Fail early on Docker, X11, or host-port problems.
	@. ./tools/check_docker.sh; check_docker || exit 1; \
	. ./tools/load-images-env.sh; \
	$(NOTE_IMAGE_OVERRIDES); \
	$(LOAD_DASHBOARD_IMAGES); \
	check_images "$${MNS_STACKS_IMAGE:-$$(dotenv_value MNS_STACKS_IMAGE)}" \
	             "$${MNS_PACKS_IMAGE:-$$(dotenv_value MNS_PACKS_IMAGE)}" \
	             "$${MNS_AUTHORING_IMAGE:-$$(dotenv_value MNS_AUTHORING_IMAGE)}" || true; \
	check_x11 || true; \
	check_ports 3001:airsim-dashboard-frontend:frontend \
	            8001:airsim-dashboard-api:backend \
	            $(or $(DASHBOARD_LICHTBLICK_PORT),8082):dashboard-lichtblick:Lichtblick \
	            $(or $(FOXGLOVE_BRIDGE_PORT),8764):ros2-tools:"Foxglove websocket" || exit 1
	@. ./tools/compose_retry.sh; . ./tools/load-images-env.sh; \
	$(LOAD_DASHBOARD_IMAGES); \
	COMPOSE_PROJECT_NAME=$(DASHBOARD_COMPOSE_PROJECT_NAME) MNS_IMAGE_SET_FILE="$$($(EFFECTIVE_IMAGE_SET))" \
	MSRS_ROOT=$$(pwd) HOST_UID=$$(id -u) HOST_GID=$$(id -g) \
	compose_retry -f docker-compose-dashboard.yml $(if $(filter true,$(DB)),--profile db,) up -d
	@# ros2-tools is created outside Compose by the backend. It serves the
	@# Foxglove websocket Lichtblick renders from, so removing it
	@# unconditionally dropped the viz connection every time `make dashboard`
	@# was re-run against a live stack.
	@# Recreate it only when the selected bridge image actually changed —
	@# RECREATE_ROS2_TOOLS=always restores the old behaviour, =never skips it.
	@. ./tools/load-images-env.sh; $(LOAD_DASHBOARD_IMAGES); \
	desired="$${MNS_ROS2_BRIDGE_IMAGE:-}"; \
	current=$$(docker inspect -f '{{.Config.Image}}' $(DASHBOARD_CONTAINER_PREFIX)ros2-tools 2>/dev/null || true); \
	if [ "$(RECREATE_ROS2_TOOLS)" = "never" ]; then \
	  echo "RECREATE_ROS2_TOOLS=never: leaving ros2-tools as it is."; \
	elif [ "$(RECREATE_ROS2_TOOLS)" = "always" ] || [ -z "$$current" ] || [ "$$current" != "$$desired" ]; then \
	  [ -n "$$current" ] && [ "$$current" != "$$desired" ] && echo "ros2-tools image changed ($$current -> $$desired); recreating."; \
	  docker rm -f $(DASHBOARD_CONTAINER_PREFIX)ros2-tools >/dev/null 2>&1 || true; \
	else \
	  echo "ros2-tools already running $$desired; leaving it (Foxglove :8764 stays up)."; \
	fi; \
	docker restart $(DASHBOARD_CONTAINER_PREFIX)airsim-dashboard-api >/dev/null
	@$(if $(filter true,$(DB)),. ./tools/load-images-env.sh; $(LOAD_DASHBOARD_IMAGES); COMPOSE_PROJECT_NAME=$(DASHBOARD_COMPOSE_PROJECT_NAME) MNS_IMAGE_SET_FILE=$$(pwd)/$(DASHBOARD_IMAGE_SET_FILE) MSRS_ROOT=$$(pwd) docker compose -f docker-compose-dashboard.yml --profile db restart dashboard-backend >/dev/null && echo "Telemetry pool reconnected.",true)
	@echo "Dashboard: http://localhost:3001 (backend :8001, lichtblick :$(or $(DASHBOARD_LICHTBLICK_PORT),8082), image mode: $(IMAGE_MODE))"

dashboard-down:
	@. ./tools/load-images-env.sh; \
	$(LOAD_DASHBOARD_IMAGES); \
	COMPOSE_PROJECT_NAME=$(DASHBOARD_COMPOSE_PROJECT_NAME) MNS_IMAGE_SET_FILE=$$(pwd)/$(DASHBOARD_IMAGE_SET_FILE) \
	MSRS_ROOT=$$(pwd) docker compose -f docker-compose-dashboard.yml --profile db down
	@# Also tear down the pre-fixed-name project, or a dashboard started before
	@# DASHBOARD_COMPOSE_PROJECT_NAME existed is left running and its
	@# daemon-global container names block the next `up`.
	@if [ "$(LEGACY_DASHBOARD_PROJECT)" != "$(DASHBOARD_COMPOSE_PROJECT_NAME)" ]; then \
	  . ./tools/load-images-env.sh; $(LOAD_DASHBOARD_IMAGES); \
	  COMPOSE_PROJECT_NAME=$(LEGACY_DASHBOARD_PROJECT) MNS_IMAGE_SET_FILE=$$(pwd)/$(DASHBOARD_IMAGE_SET_FILE) \
	  MSRS_ROOT=$$(pwd) docker compose -f docker-compose-dashboard.yml --profile db down >/dev/null 2>&1 || true; \
	fi

help:
	@echo "Product:"
	@echo "  dashboard        start the TEVV Web Dashboard on :3001 [CHANNEL= IMAGE_MODE= DB=true MNS_DEMO_PACKS=]"
	@echo "  dashboard-down   stop it"
	@echo "Headless (the same images the dashboard runs):"
	@echo "  fly              generate and fly one scenario, a FLY_SECONDS (300) run unless ARGS names --done [SCENARIO=name RECORD=1 KEEP=1 ARGS=...]"
	@echo "                   KEEP=1: bring it up and leave it up (make stop ends it)"
	@echo "  stop             stop a flown stack, finalize_metrics first [STACK=generated/name, default: the last fly]"
	@echo "  author           open ScenarioLab [SCENARIO=name]; author-stop closes it"
	@echo "  campaign         fly a run matrix over one scenario, scored [CAMPAIGN=name]"
	@echo "                   or any subcommand: ARGS=\"status vio-reference\""
	@echo "  campaign-status  one row per flight: recording verdict and accuracy [CAMPAIGN=name]"
	@echo "  stacks           any mns-stacks command: ARGS=\"status --stack generated/name --json\""
	@echo "  topics           ROS 2 topics a generated stack will publish [STACK=generated/name]"
	@echo "  ps               running containers (name/status/image)"
	@echo "Images and packs:"
	@echo "  doctor           is this machine ready: Docker, Compose, every pinned image present"
	@echo "  ensure-images    use local tags and pull only missing images [IMAGE_MODE=development|production]"
	@echo "  pull-images      explicitly refresh every exact published remote image pin"
	@echo "  verify-images    CI gate: images/catalog.yaml matches generated artifacts"
	@echo "  pack-status      what is locked, installed, and published since"
	@echo "  pack-lock        rebuild the channel's pack lock from published releases"
	@echo "Content packs are downloaded with ./download-packs.sh (--list shows them)."

ps:
	@docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'

# What a generated stack will publish, without starting it. Resolves
# settings.json sensors/cameras + topic_names.yaml renames + topic_prefix using
# the bridge image's own launch code, so it cannot drift from the bridge.
#   make topics STACK=generated/<name>
topics:
	@test -n "$(STACK)" || { echo "usage: make topics STACK=generated/<name>" >&2; exit 2; }
	@python3 tools/preview_topics.py $(STACK)

# Headless: the dashboard's flows from a terminal, through the same images.
# Every target runs mns-stacks (MNS_STACKS_IMAGE) as a sibling container with
# the checkout and the runs directory mounted at their host paths
# (tools/mns-stacks.sh); ScenarioLab runs exactly as the dashboard starts it
# (tools/author.sh).
#
#   make fly SCENARIO=my-scene RECORD=1             # generate, fly FLY_SECONDS (300), stop
#   make fly SCENARIO=my-scene KEEP=1               # bring it up and leave it up; make stop ends it
#   make fly SCENARIO=my-scene ARGS="--done topic:/x" # ARGS: extra `mns-stacks run` flags
#   make stop [STACK=generated/<name>]              # default: the last `make fly`
#   make author [SCENARIO=my-scene]                 # ScenarioLab; exports to scenarios/
#   make stacks ARGS="status --stack generated/my-scene --json"
#
# fly first brings the packs to the lock's versions (ensure-demo-packs), the
# check `make dashboard` runs; `mns-stacks run` then verifies the stack's
# resolved packs before anything starts.
SCENARIO ?=
RECORD ?=
KEEP ?=
STACK ?=
ARGS ?=
# The image-set file the dashboard's generated stacks run: the selected one,
# or a copy with the shell/./.env image overrides applied (MNS_RUNTIME_HOST_IMAGE,
# MNS_ROS2_BRIDGE_IMAGE), each with a NOTE. tools/mns-stacks.sh does the same
# for make fly / make campaign. Falls back to the selected file, with the
# error, when the overrides cannot be applied.
EFFECTIVE_IMAGE_SET = python3 tools/images.py effective-image-set --in "$(CURDIR)/$(DASHBOARD_IMAGE_SET_FILE)" \
	--out "$(CURDIR)/.mns/image-set.effective.yaml" --dotenv "$(CURDIR)/.env" || echo "$(CURDIR)/$(DASHBOARD_IMAGE_SET_FILE)"

MNS_STACKS_ENV = . ./tools/load-images-env.sh; $(LOAD_DASHBOARD_IMAGES); \
	export MNS_IMAGE_SET_FILE=$(CURDIR)/$(DASHBOARD_IMAGE_SET_FILE) MNS_HOST_UID=$$(id -u) MNS_HOST_GID=$$(id -g)

# Checked when the Makefile is read, so a missing SCENARIO fails before the
# pack and image prerequisites start pulling anything.
ifneq ($(filter fly,$(MAKECMDGOALS)),)
ifeq ($(strip $(SCENARIO)),)
$(error usage: make fly SCENARIO=<name under scenarios/> [RECORD=1] [KEEP=1] [ARGS=...])
endif
endif
fly: ensure-demo-packs  ## Generate and fly SCENARIO for FLY_SECONDS unless ARGS names --done (RECORD=1 records a bag; COMPONENTS="components/x ..." attaches packages)
	@$(MNS_STACKS_ENV); COMPONENTS="$(COMPONENTS)" ./tools/fly.sh "$(SCENARIO)" $(if $(filter 1 true yes,$(RECORD)),--record,) $(if $(filter 1 true yes,$(KEEP)),--keep,) -- $(ARGS)

evaluate: ensure-demo-packs  ## Fly SCENARIO end to end and print its verdict: record what scorers read, MISSION="<cmd>" ({stack} = the stack), score, report (COMPONENTS= as for fly)
	@$(MNS_STACKS_ENV); COMPONENTS="$(COMPONENTS)" ./tools/evaluate.sh "$(SCENARIO)" $(if $(MISSION),--mission "$(MISSION)",) -- $(ARGS)

stop:  ## Stop a flown stack (finalize_metrics, then compose down)
	@last=$$(cut -f1 .mns/last-stack 2>/dev/null); last_project=$$(cut -s -f2 .mns/last-stack 2>/dev/null); \
	stack="$(STACK)"; [ -n "$$stack" ] || stack="$$last"; \
	test -n "$$stack" || { echo "usage: make stop STACK=generated/<name>  (no make fly to default to)" >&2; exit 2; }; \
	case "$$stack" in /*) ;; *) stack="$(CURDIR)/$$stack" ;; esac; \
	project=""; [ "$$stack" = "$$last" ] && [ -n "$$last_project" ] && project="$$last_project"; \
	$(MNS_STACKS_ENV); ./tools/mns-stacks.sh stop --stack "$$stack" $${project:+--project "$$project"}

author: stage-authoring-packs  ## Open ScenarioLab (SCENARIO=<folder or ScenarioSpec> to open one)
	@. ./tools/check_docker.sh; check_x11 || true
	@$(MNS_STACKS_ENV); ./tools/author.sh $(if $(SCENARIO),"$(SCENARIO)",)

author-stop:  ## Close ScenarioLab
	@./tools/author.sh --stop

stacks:  ## Any mns-stacks command: make stacks ARGS="<command> ..."
	@test -n "$(ARGS)" || { echo 'usage: make stacks ARGS="<mns-stacks command> ..."   (ARGS=--help lists them)' >&2; exit 2; }
	@$(MNS_STACKS_ENV); ./tools/mns-stacks.sh $(ARGS)

# Campaigns: a run matrix over one scenario, flown, recorded, checked and
# scored by `mns-stacks campaign`, so it needs no platform checkout.
#
#   make campaign                                  # the reference campaign
#   make campaign CAMPAIGN=my-test                 # yours
#   make campaign ARGS="status vio-reference"      # any campaign subcommand
#
# A campaign is hours long and holds the GPU, the simulator ports and the X
# display, so only one runs at a time; it refuses to start a second.
CAMPAIGN ?= vio-reference
campaign: ensure-images  ## Fly a campaign (CAMPAIGN=<name>, or ARGS="<subcommand> ...")
	@mkdir -p generated scenarios
	@# Advisory: warns when the campaign's vehicle spawns where its level has no
	@# floor (packs/level-spawn-hints.json), before hours of runs fail to arm.
	@python3 tools/check_spawn.py campaign $(or $(ARGS),run $(CAMPAIGN)) || true
	@$(MNS_STACKS_ENV); ./tools/mns-stacks.sh campaign $(or $(ARGS),run $(CAMPAIGN))

campaign-status:  ## One row per flight of a campaign (CAMPAIGN=<name>)
	@$(MNS_STACKS_ENV); ./tools/mns-stacks.sh campaign status $(CAMPAIGN)

doctor:  ## Docker, Compose, and every pinned image present (changes nothing)
	@./tools/doctor.sh --channel $(CHANNEL_NAME)

# CI gate for the image catalog (images/catalog.yaml): regenerates
# product-images.env / images/*.generated.* into a temp location and diffs
# against the committed copies. Offline — no registry calls. See
# docs/adr/0002-one-image-catalog.md.
verify-images:
	./tools/images.sh verify

pull-images:
	./tools/pull-all-images.sh
