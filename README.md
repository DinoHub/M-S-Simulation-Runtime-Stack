# M-S-Simulation-Runtime-Stack

This repository provides the runtime environment used by the autonomy team.

It launches the following components:

- Simulation runtime
- Optional local planner
- Metrics collection / supervisor
- Monitoring stack

This repo **does not contain the planner implementation**.

---

## Quick Start

```bash
cp .env.example .env
./setup.sh
./launch.sh
```

Stop everything:

```bash
./stop.sh
```

View logs:

```bash
./logs.sh
```

---

## Before You Start

After copying `.env.example` to `.env`, update the fields relevant to your setup.

### Minimum fields to check

```env
CONFIG_ROOT=./config
LOCAL_PLANNER_MODE=external
```

### If the planner is started separately

Use this during active planner development when you already run the planner from its own repo:

```env
LOCAL_PLANNER_MODE=external
```

### If this runtime stack should start the planner

Update these fields:

```env
LOCAL_PLANNER_MODE=managed-script
LOCAL_PLANNER_DIR=/path/to/planner-repo
LOCAL_PLANNER_START_CMD="./run.sh"
LOCAL_PLANNER_STOP_CMD="make stop"
```

### If the planner has its own Docker Compose stack

Update these fields:

```env
LOCAL_PLANNER_MODE=managed-compose
LOCAL_PLANNER_DIR=/path/to/planner-repo
LOCAL_PLANNER_COMPOSE_FILE=docker-compose.yml
LOCAL_PLANNER_PROFILE=
```

---

## Typical Workflow

Autonomy engineers usually work in one of two ways.

### Option 1 — Planner started separately (recommended during development)

Run your planner from its own repository.

Then set in `.env`:

```env
LOCAL_PLANNER_MODE=external
```

Start the runtime stack:

```bash
./launch.sh
```

### Option 2 — Planner started by this runtime stack

If your planner repo has a startup script, this repo can launch it automatically.

Set in `.env`:

```env
LOCAL_PLANNER_MODE=managed-script
LOCAL_PLANNER_DIR=/path/to/planner-repo
LOCAL_PLANNER_START_CMD="./run.sh"
LOCAL_PLANNER_STOP_CMD="make stop"
```

Then run:

```bash
./launch.sh
```

---

## Local Planner Modes

Planner behavior is controlled using `LOCAL_PLANNER_MODE` in `.env`.

| Mode | Description | What to update in `.env` |
|------|-------------|--------------------------|
| `disabled` | No planner will be started | `LOCAL_PLANNER_MODE=disabled` |
| `external` | Planner is assumed to already be running | `LOCAL_PLANNER_MODE=external` |
| `managed-script` | Planner started using a script in its repository | `LOCAL_PLANNER_MODE`, `LOCAL_PLANNER_DIR`, `LOCAL_PLANNER_START_CMD`, `LOCAL_PLANNER_STOP_CMD` |
| `managed-compose` | Planner started using its own Docker Compose stack | `LOCAL_PLANNER_MODE`, `LOCAL_PLANNER_DIR`, `LOCAL_PLANNER_COMPOSE_FILE`, optionally `LOCAL_PLANNER_PROFILE` |

---

## Example `.env`

Most engineers only need to modify these fields.

```env
CONFIG_ROOT=./config

LOCAL_PLANNER_MODE=managed-script
LOCAL_PLANNER_DIR=/home/user/planner_repo
LOCAL_PLANNER_START_CMD="./run.sh"
LOCAL_PLANNER_STOP_CMD="make stop"
```

---

## Commands

Start runtime stack:

```bash
./launch.sh
```

Stop runtime stack:

```bash
./stop.sh
```

View logs from all services:

```bash
./logs.sh
```

View logs from a specific stack:

```bash
./logs.sh sim
./logs.sh monitoring
./logs.sh metrics
```

View logs from a specific service:

```bash
./logs.sh sim ros2-x11-node
./logs.sh metrics metrics-collector
```

Filter logs by text:

```bash
./logs.sh metrics metrics-collector ERROR
```

---

## Repository Structure

```text
sim-runtime-stack/
├── config/
│   └── runtime configuration
├── docker-compose-sim.yml
├── docker-compose-monitoring.yml
├── docker-compose-metrics.yml
├── launch.sh
├── stop.sh
├── logs.sh
├── setup.sh
├── .env.example
└── README.md
```

---

## Notes

- This repository only orchestrates the runtime environment.
- Planner code should live in its own repository.
- Planner startup behavior is controlled through `.env`.
- Engineers typically use `LOCAL_PLANNER_MODE=external` during active development.
