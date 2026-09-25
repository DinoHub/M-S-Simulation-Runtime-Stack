# MnS User Guide

MnS is a containerised simulation environment for testing drone autonomy. You
describe a scenario and MnS turns it into a running simulation that you can
fly, record and evaluate. The simulation runs a PX4 or ArduPilot SITL, an
Unreal Engine 5.8 world with Cosys-AirSim sensors, and ROS 2 Humble topics.
Everything runs in Docker, and you drive it from a browser dashboard.

This guide assumes you know ROS 2, PX4/ArduPilot and simulation, but have
never used MnS. It takes you from a fresh machine to a recorded rosbag. You do
not need to edit any configuration files.

---

## Quick start: your first rosbag

The first run takes about 30 minutes of downloads, then about 15 minutes of
clicking. Section 5 explains each step in full.

**On the command line** (once):

```bash
git clone https://github.com/DinoHub/M-S-Simulation-Runtime-Stack.git
cd M-S-Simulation-Runtime-Stack
./setup.sh           # checks the machine, logs in to Docker Hub, pulls the images
./download-packs.sh  # downloads the starter levels, ~0.8 GB (logs in to GitHub)
make dashboard       # run from a terminal on the desktop, not over SSH
```

Both scripts stop and tell you what to fix if anything is missing. Fix it and
run the same script again; it skips whatever is already done.

**In Chrome or Chromium** (the 3D viewer does not run in Firefox), at
<http://localhost:3001> → **Scenario Configuration**:

1. **Content**: **Continue to Author**.
2. **Author**: **Launch editor**. ScenarioLab opens in its own window.
3. In ScenarioLab:
   1. Type a name in **Scenario** and press Enter.
   2. **Environment** → **Level Pack** `warehouse` → **Apply Level Pack**.
   3. **Runtime** → Autopilot `px4`.
   4. **Vehicles**: aim the screen centre at the floor → **Add Drone**.
   5. **Validate** → **Validate**, then **Export**. *Export is the only save.*
4. Back in the dashboard, click your scenario under **Authored scenarios**.
5. **Generate**: **Generate stack**.
6. **ROS 2** → **Bag capture**: tick `/ground_truth/odom`, `/imu/data` and
   `/gps/fix` → **Save & continue to metrics**. Then **Metrics** → **Save &
   apply**.
7. **Launch**: pick your stack → **Launch**. Wait for *Visualization ready* →
   **Go to Monitor**.
8. **Monitor**: the **Bag recording** panel shows **REC mm:ss**; that's your bag
   recording. Fly with **Teleop (WASD)** or **Quick Mission Launch**. *PX4 needs
   1–2 minutes after spawn before it will arm.*
9. **Launch** → **Stop**. This finalizes the bag.
10. Your bag is in `runs/<scenario>_<timestamp>/bag/`. **Replay → Bags**
    lists and plays it; with ROS 2 on the host, `ros2 bag info` reads it too.

Starting again from scratch on a machine that has run MnS before? Stop what
is running first:

```bash
make dashboard-down                                                    # in the old checkout
docker rm -f ros2-tools mns-replay-bridge mns-scenariolab-editor 2>/dev/null
docker ps -a --filter name=mns-recorder- -q | xargs -r docker rm -f
```

---

## Contents

1. [How MnS fits together](#1-how-mns-fits-together)
2. [Requirements](#2-requirements)
3. [Setup](#3-setup)
4. [Starting and stopping the dashboard](#4-starting-and-stopping-the-dashboard)
5. [Your first run: scenario to rosbag](#5-your-first-run-scenario-to-rosbag)
6. [Working with a running stack](#6-working-with-a-running-stack)
7. [Where everything is stored](#7-where-everything-is-stored)
8. [Optional configuration](#8-optional-configuration)
9. [Troubleshooting](#9-troubleshooting)
10. [Known limitations](#10-known-limitations)
11. [Quick reference](#11-quick-reference)

---

## 1. How MnS fits together

```
 ScenarioLab ──export──▶ ScenarioSpec ──generate──▶ Stack ──launch──▶ Simulation ──record──▶ rosbag
 (Unreal editor)          (YAML files)              (docker compose)   (sim + SITL + ROS 2)     (runs/)
         ▲                                                                   ▲
         └───────────────── all driven from the dashboard (localhost:3001) ──┘
```

| MnS term | What it is, in domain terms |
|---|---|
| **Level pack** | A pre-built Unreal map, the environment you fly in: Warehouse, Office Environment, Condo, XFS, Safti or Fisherman's Cabin. It is downloaded once and checksum-verified. ScenarioLab can open **Warehouse** and **Office Environment**; the other four are *runtime only* and fly from hand-written specs. |
| **Object pack** | Placeable props: office, warehouse and cabin props. The drone models come as their own pack, `mns_vehicle_models`. |
| **ScenarioLab** | An Unreal-based editor. You place vehicles, sensors and objects in a level, then **Export**. |
| **ScenarioSpec** | The exported scenario: a folder of YAML files covering the environment, vehicles, sensor profiles, objects and runtime settings. It is the single input to everything downstream. |
| **Stack** | A docker compose project generated from a ScenarioSpec. It contains the Unreal/AirSim runtime host, one SITL per vehicle, a ROS 2 bridge per vehicle, a Foxglove bridge and, optionally, QGroundControl. |
| **Run** | One launch of a stack. It gets a run id, an optional rosbag, and metrics events. |
| **Dashboard** | The browser UI that drives all of the above and records the run. |

Three things work differently from a typical ROS 2 sim setup:

- **One ROS domain per vehicle.** Each vehicle's `ROS_DOMAIN_ID` comes from its
  `ros_domain_id` in the scenario; ScenarioLab numbers them 1, 2, … by default.
  Topic names have **no vehicle namespace**, for example `/imu/data` rather than
  `/Copter1/imu/data`.
- **Everything is containerised on Docker bridge networks.** Your own nodes
  join the stack's network, as described in
  [Connecting your autonomy stack](#connecting-your-autonomy-stack).
- **The environment is exact.** A scenario pins a level pack by version *and*
  digest, and ScenarioLab and the simulator load the same artifact.

Which service does what, and which file each step leaves on disk, is in
[How it fits together](how-it-fits-together.md).

---

## 2. Requirements

**Hardware**
- An NVIDIA GPU with a current driver. Both the simulator and ScenarioLab
  render on it.
- **About 35 GB of free disk** for the first setup. `./download-packs.sh` works
  out the exact figure for your pack selection.
  - images: about 15 GB
  - content packs: about 8 GB to download, and briefly about twice that while
    they install
  - rosbags on top of that: hundreds of MB to several GB per minute, depending
    on the topics you record

**Software** (`./setup.sh` checks every item and prints the install command
for anything missing)
- Ubuntu 22.04 or later with a desktop session. ScenarioLab, the simulator and
  QGroundControl open windows.
- Docker Engine with the Compose v2 plugin, and your user in the `docker`
  group.
- NVIDIA Container Toolkit.
- `make`, `git`, `curl`, `python3` with `python3-yaml`.
- The GitHub CLI (`gh`), which is the easiest way to authorise pack downloads.
- **Google Chrome or Chromium** for the dashboard. Its 3D viewer (Lichtblick)
  does not run in Firefox.

**Accounts.** The images and packs are private. Ask your MnS contact for access
to:
- **Docker Hub**, for the `dhdevspace/auto_mns` images.
- **GitHub**, for the `DinoHub/TEVV-Airsim` releases that hold the content
  packs.

`./setup.sh` asks you to log in to Docker Hub, and `./download-packs.sh` to GitHub.

---

## 3. Setup

Setup is two scripts, run once from the repository root:

```bash
./setup.sh            # the machine, configuration, Docker Hub and images
./download-packs.sh   # the content packs
```

### `./setup.sh`

It works through four steps and prints a ✓ or ✗ for each check:

| Step | What it does |
|---|---|
| **1. Checking this machine** | Checks Docker, Compose, the GPU runtime, tools, Python modules, free disk and the desktop display. |
| **2. Local configuration** | Creates `.env` from `.env.example`, with defaults only; nothing needs editing. Also creates the working folders. |
| **3. Docker Hub account** | Checks your Docker Hub login, and runs `docker login` for you if it is missing. |
| **4. Container images** | Downloads the release images (several GB). Images you already have are kept. |

It ends with **Setup complete** and tells you to run `./download-packs.sh`.

If it stops early, it lists each problem with the command that fixes it, for
example `✗ not logged in to Docker Hub → docker login`. Fix the problems and
run it again. Work that is already done is skipped, so a re-run is quick.

`./setup.sh --check` only checks the machine; it changes and downloads nothing.

### `./download-packs.sh`

It downloads the level and object packs from GitHub, verifies their
checksums, installs them, and makes them visible to ScenarioLab. If you are
not logged in to GitHub, it runs `gh auth login` for you.

| Command | What it does |
|---|---|
| `./download-packs.sh --list` | Shows every pack: size, whether it is installed, and whether ScenarioLab can open it. |
| `./download-packs.sh` | Downloads the **starter set**: Warehouse (the level the walkthrough uses) and the vehicle models, about 0.8 GB. |
| `./download-packs.sh --all` | Downloads everything, about 8 GB. |
| `./download-packs.sh --office --xfs` | Downloads these as well; the vehicle models are always added. |
| `./download-packs.sh --objects` | Downloads every object pack. |

A dropped connection resumes where it stopped, and packs already installed
are skipped. So if it fails part-way, just run it again. You can also add packs
later from the dashboard's **Content** step.

`make dashboard` does not download packs. If none are installed, it warns you
and starts anyway.

**Display access.** `make dashboard` finds your X display cookie itself.
Start it from a terminal on the desktop, not over SSH. If you must use SSH,
first run:

```bash
export DISPLAY=:1      # run `echo $DISPLAY` in a desktop terminal to see which
export XAUTHORITY=$(ls -t /run/user/$(id -u)/.mutter-Xwaylandauth.* 2>/dev/null | head -1)
```

### Updating an existing checkout

To move a checkout you set up earlier onto a new release:

```bash
make dashboard-down
git pull
grep -n '_IMAGE=' .env        # delete any image lines this shows; they override the release
./product.sh pull-images      # refresh images whose tags were republished
./setup.sh                    # pulls any new images
./download-packs.sh           # installs any new packs
make dashboard
```

Scenarios in `scenarios/` and runs in `runs/` are kept.

Runs recorded before this release are in `~/tevv-runs`, the old default.
They stay there. To keep recording into that folder, add
`TEVV_RUNS_DIR=/home/<you>/tevv-runs` to `.env`; otherwise move them into
`runs/`.

---

## 4. Starting and stopping the dashboard

```bash
make dashboard         # start; prints "Dashboard: http://localhost:3001 ..."
make dashboard-down    # stop the dashboard
```

Open **<http://localhost:3001>**. The first start after setup takes a few
seconds, because everything is already downloaded.

The left sidebar has these pages:

| Page | Use it to |
|---|---|
| **Overview** | See system status at a glance. |
| **Scenario Configuration** | Work through the step-by-step flow, from content to launch. You spend most of your time here. |
| **Monitor** | Watch and fly the running stack: 3D view, cameras, plots, teleop and recording. |
| **Replay** | Play back recorded bags in the browser. |
| **Calibration** | Compare simulated runs with real flights, and download bag files. |
| **VIO Stress** | Visual-inertial odometry stress evaluation. |

The dashboard uses these host ports:

| Port | Service |
|---|---|
| 3001 | dashboard |
| 8001 | dashboard API |
| 8082 | Lichtblick (the viewer) |
| 8764 | ROS 2 tools |
| 8765, 8766, … | Foxglove bridge for vehicle 1, 2, … of the running stack |
| 41451 | AirSim RPC of the running stack |

If one of the first four is taken, `make dashboard` stops and names the
program using it.

`make dashboard-down` leaves a few helper containers that the dashboard starts
on demand. To remove them as well:

```bash
docker rm -f ros2-tools mns-replay-bridge mns-scenariolab-editor 2>/dev/null
docker ps -a --filter name=mns-recorder- -q | xargs -r docker rm -f
```

---

## 5. Your first run: scenario to rosbag

Open **Scenario Configuration**. A stepper runs across the top:

```
0 Content → 1a Author → 1b Generate → 1c ROS 2 → 1d Metrics → 2 Launch → 3 Runtime → 4 Analysis
```

Each step unlocks when the previous one is done and then shows a check mark.
The whole loop takes about 15 minutes the first time.

### Step 0: Content

This step shows what is installed.

- **Engine line** lists four roles: ScenarioLab, Runtime host, Stack
  generator and Product shell. ScenarioLab and the runtime host should say
  **matches**; the generator and shell say **present**.
- **Level packs** and **Object packs** should say **ready**.

`./download-packs.sh` has already done this work, so normally you just click
**Continue to Author**.

To add a pack later, tick it and click **Download & stage N**. The panel shows
the free space and how much the selection needs.

### Step 1a: Author the scenario in ScenarioLab

1. Check the header reads **environment ready**. If it doesn't, the failing
   checks are listed; see [Troubleshooting](#9-troubleshooting).
2. Click **Launch editor**. ScenarioLab opens in its own window after 30–90
   seconds.

**Moving around the viewport**

| Input | Action |
|---|---|
| **W A S D**, **Q/E** | Fly the camera; Q/E move down/up. Hold **Shift** for 4× speed. |
| **Right mouse** (hold) | Look around. |
| **Mouse wheel** | Move forward/back. |
| **Left click** | Select an object. **Tab** cycles through placed objects. |
| **1 / 2 / 3** | Move / Rotate / Scale mode, then drag the gizmo. |
| Arrow keys, **PgUp/PgDn** | Nudge the selection (25 cm or 5°; hold **Ctrl** for finer steps). |
| **N** | Add a drone where the screen centre points. |
| **P** | Export. |
| **F10** | Hide or show the side panel. |

**Building a scenario.** The side panel has a **Scenario** name field, then
these tabs: **Environment**, **Vehicles**, **Sensor Profiles**, **Objects**,
**Spawns**, **Degradation**, **Runtime** and **Validate**. Work through them in
this order:

1. **Scenario** (top field): type a name and press Enter. The name becomes the
   folder name `scenarios/<name>/`.
2. **Environment**: pick a **Level Pack** and click **Apply Level Pack**. The
   level loads.
   - For a first run, pick **Warehouse**. The list shows only levels
     ScenarioLab can open: Warehouse and Office Environment.
   - Do this **first**. Switching levels can restart ScenarioLab, and it
     discards everything placed so far.
   - Optional settings here: **Edit MnS Origin** (the ROS frame origin), time
     of day, and a weather preset.
3. **Runtime**: choose the **Autopilot** (`px4` or `ardupilot`; it applies to
   the whole scenario). Leave Endpoint on `docker`. Tick the extras you want:
   QGroundControl, the ROS 2 bridge, MAVROS.
4. **Vehicles**: aim the screen centre at the ground and click **Add Drone**,
   or press **N**.
   - Choose a **Vehicle type** (default `quadrotor_small`) and a **Sensor
     profile**.
   - Adjust the drone with the gizmo or the **Position m / Rotation deg**
     fields. These are metres, ROS FLU, relative to the origin. **Snap Floor**
     puts the drone on the ground.
5. **Sensor Profiles**: edit the sensors a profile carries.
   - Sensors: IMU, GPS, barometer and magnetometer (each with noise and
     timing), lidar or GPU lidar, and RGB, depth or fisheye cameras.
   - Camera settings: intrinsics, noise and mount pose.
   - Click **Save Profile** after editing. Unsaved profile edits are not
     exported.
   - **Assign** gives the profile to a drone.
6. **Objects** *(optional)*: pick a pack and asset, then **Place Selected
   Asset**.
7. **Spawns** / **Degradation** *(optional)*: add random-clutter volumes, and
   zones that degrade sensors or weather (GPS jamming or spoofing, urban
   canyon, fog, gale, …).
8. **Validate**: click **Validate** and fix anything it lists until it reads
   *Validation OK*.
9. Click **Export** (or press **P**). The status line reads
   `Exported ScenarioSpec: …/scenarios/<name>/ScenarioSpec.yaml`.

**Export is the only save.** Closing ScenarioLab without exporting loses your
work.

Back in the dashboard, your scenario appears under **Authored scenarios**
within about 5 seconds. **Click it** to move to Generate.

Each row in **Authored scenarios** has three icons:
- ✏️ reopens the scenario in ScenarioLab.
- ⧉ copies it into a new scenario.
- 🗑 deletes its spec and stack. Recorded runs are kept.

> **No GPU desktop available for ScenarioLab?** **Skip ScenarioLab — write
> the spec by hand** gives you a minimal template spec to edit in Generate.

### Step 1b: Generate the stack

This step validates your ScenarioSpec and turns it into a runnable stack.

- **Spec source** should read *Authored in ScenarioLab: \<name\>*.
- **Files in this spec** lists the exported YAML files. Click one to read it.
- **Edit YAML** lets you change the root spec by hand. This is optional;
  ScenarioLab is the normal way to edit.

Click **Generate stack**. After a few seconds the dashboard moves straight on
to **ROS 2**, which means it worked. The stack is written to
`generated/<name>/`.

The badge (*working: authored*, *modified from authored*, …) shows whether the
spec still matches your export. **Restore authored** returns to the export, and
**Save as new…** forks the spec under a new name.

### Step 1c: ROS 2 settings and what to record

| Sub-step | What you set |
|---|---|
| **Network** | `ROS_DOMAIN_ID`, a network preset, and optionally a CycloneDDS configuration. The defaults work on one machine. |
| **Topic names** | An optional topic prefix and renames. |
| **Sensors** | Tweaks to the stack's sensor settings. |
| **Bag capture** | Which topics to record. |

On **Bag capture**:

1. Leave **Start recording when the stack launches** ticked. Recording then
   starts automatically.
2. Tick the topics to record, for example `/ground_truth/odom`, `/imu/data` and
   `/gps/fix`. The summary line at the top shows the count, e.g. *bag 3/12*.
   - Before launch the list is worked out from the stack's files, so camera
     images (`/<camera>/image_raw`) and `/clock` are not offered yet. They
     appear here once the stack is running, and the list then reads *live from
     ros2-tools*.
   - If you tick nothing, **everything** is recorded, which with cameras is
     gigabytes per minute.
3. Click **Save & continue to metrics**.

### Step 1d: Metrics

Optionally tick **Measure this run** and choose a preset. Event detectors then
log to `generated/<name>/outputs/metrics/<run>/events.jsonl`. Click **Save &
apply**.

### Step 2: Launch

1. Under **Generated stack**, select your stack and click **Launch**. Use
   **Refresh stacks** if it isn't listed.
2. Each service moves to **running · healthy**. The simulator is last, because
   loading the level takes 1–3 minutes.
3. When you see **Visualization ready — the viewer is answering**, click
   **Go to Monitor**.
4. On **Monitor**, the **Bag recording** panel shows **REC mm:ss** and the MB
   written, which confirms the bag is recording.
   - The Launch step can also show a red *Recording failed to start:* with
     no reason after it, even when recording is running. Trust the Monitor
     panel. If it says *idle*, click **Start** there.

The simulator window, and QGroundControl if you enabled it, also open on your
desktop.

### Step 3: Fly it (Monitor)

The **Monitor** page has these areas:

- **Viewers → Lichtblick**: live 3D view, camera images and plots. **Viewers →
  Sim** shows the simulator view.
- **Mission Control** (right-hand panel):
  - pick a vehicle
  - **Quick Mission Launch** and **Flight patterns** for scripted flights
  - **Teleop (WASD)** for keyboard flight
  - **Bag recording**: start and stop by hand; it shows **REC mm:ss** and the
    size written so far
  - compact telemetry
- **Run events** / **Grafana**: the live metrics stream.
- **Controls**: evaluation settings and live sensor controls.

**PX4 needs 1–2 minutes after spawn** before its estimator accepts arming
(`Preflight Fail: ekf2 missing data`). If the first arm is refused, wait and
try again.

You can also fly from your own autonomy stack; see
[section 6](#connecting-your-autonomy-stack).

### Step 4: Stop, which finalizes the bag

Go back to **Launch** and click **Stop**. The dashboard stops the recorder
cleanly, writing the bag's `metadata.yaml`, and then shuts the stack down.
**Analysis** then unlocks.

Always stop through the dashboard. A bag killed mid-write has no
`metadata.yaml`, so it cannot be replayed or analysed.

### Step 5: Get your rosbag

Everything a run produces is inside the folder you cloned
(`M-S-Simulation-Runtime-Stack/`). Each run gets its own folder in `runs/`,
named after the scenario and the time the stack started, in **UTC**. The
newest run is the last one listed:

```bash
cd M-S-Simulation-Runtime-Stack
ls -t runs | head -1              # your latest run
```

```
runs/<scenario>_<YYYYmmdd_HHMMSS>/
  run.json          # run id, stack, bag path
  bag/
    metadata.yaml   # rosbag2 metadata
    bag_0.db3       # rosbag2 sqlite3 storage (zstd per message); large runs add bag_1.db3, …
```

It is a standard ROS 2 Humble bag:

```bash
ros2 bag info runs/<run>/bag
ros2 bag play runs/<run>/bag
```

You can also get it from the browser:
- **Download**: **Calibration** → choose the run as *Sim run (candidate)* →
  **⬇ View bag files**.
- **Replay**: **Replay → Bags** lists each run with its duration, topics and
  message count, and plays it in the browser.
- **Hand-off bundle**: **Analysis → Record integration bundle →
  Download bundle** packages the run for another team.

---

## 6. Working with a running stack

### What runs in a stack

`generated/<name>/docker-compose.yml` contains:

| Service | Role |
|---|---|
| `unreal-airsim` | Unreal Engine 5.8 runtime host with Cosys-AirSim, loading your level pack. Publishes AirSim RPC on host port **41451**. |
| `px4-drone-N` / `ardupilot-drone-N` | SITL autopilot, one per vehicle. |
| `airsim_bridge_…` | ROS 2 Humble bridge: sensors and ground truth out, commands in. It also runs MAVROS if enabled. |
| `foxglove_bridge_…` | Foxglove websocket for vehicle N on host port **8765 + N − 1**. |
| `qgroundcontrol-x11` | QGroundControl, if enabled. |
| `iceoryx-init`, `sim-real-eval-worker`, `vio_estimator_…` | Helpers. The last two appear only when the scenario enables them. |

The runtime defaults:
- **ROS 2 Humble**, `rmw_fastrtps_cpp` over UDPv4.
- `use_sim_time:=true`; `/clock` comes from the simulator.
- `ROS_DOMAIN_ID` is the vehicle's `ros_domain_id` from the scenario. Check it with
  `grep ROS_DOMAIN_ID generated/<name>/docker-compose.yml`.

### Topics

Before launching, list exactly what a stack will publish:

```bash
make topics STACK=generated/<name>
```

These are the defaults for one vehicle on its own domain:

| Topic | Type |
|---|---|
| `/ground_truth/odom` | `nav_msgs/Odometry` (`odom` → `base_link`) |
| `/pose` | `geometry_msgs/PoseStamped` |
| `/imu/data`, `/imu/mag`, `/air_pressure`, `/gps/fix` | IMU, magnetometer, barometer, `NavSatFix` |
| `/<camera>/image_raw`, `/<camera>/camera_info`, `/<camera>/camera_pose` | per camera, named after the camera in the sensor profile, e.g. `/front_rgb/image_raw` (`bgra8`) |
| `/lidar/points` | `PointCloud2` |
| `/clock`, `/tf`, `/tf_static` | |

Commands go in through the bridge as services (`/takeoff`, `/land`, `/reset`,
`/gps_waypoint`, …) and velocity topics (`/vel_cmd_body_frame`,
`/vel_cmd_world_frame`). With MAVROS enabled, its topics sit under
`/<vehicle>/mavros/…`.

**Frames.** AirSim works in NED/FRD. The bridge publishes REP-105 ENU/FLU
(`map → odom → base_link`).

**Cameras.** Images travel from the simulator to the bridge over shared
memory (iceoryx), and the bridge publishes them as `/<camera>/image_raw`.
`make topics` currently lists such a camera under *NOT on ROS — camera rides
the iceoryx SHM path*. That line is out of date: the image topic is there once
the stack runs.

How the names are resolved, and what `make topics` can and cannot see, is in
[What will this stack publish?](topics.md).

### Connecting your autonomy stack

**Over ROS 2.** The stack's ROS traffic lives on Docker bridge networks, one
per vehicle, named `<stack>_agent_internal-N`. Run your nodes in a container
attached to that network with the stack's domain, RMW, user and shared memory:

```bash
docker network ls | grep agent_internal                       # the network name
grep -m1 ROS_DOMAIN_ID generated/<name>/docker-compose.yml    # the vehicle's domain
docker run --rm -it \
  --network <stack>_agent_internal-1 \
  --ipc host -v /dev/shm:/dev/shm -u $(id -u):$(id -g) -e HOME=/tmp \
  -e ROS_DOMAIN_ID=<domain> -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
  -e FASTDDS_BUILTIN_TRANSPORTS=UDPv4 -e ROS_LOCALHOST_ONLY=0 \
  <your-ros2-humble-image> ros2 topic list
```

If you see only `/rosout` and `/parameter_events`, the domain is wrong or the
bridge is still starting.

Use the same user id as the stack. A mismatched user can subscribe, but
receives nothing over shared memory. The dashboard's own recorder attaches
exactly this way.

Alternatively, add your service to `generated/<name>/docker-compose.yml` on
that network. Regenerating the stack overwrites the file, so keep a copy.

**Over MAVLink.** In the default `docker` endpoint mode, no autopilot port is
published on the host.
- **PX4:** mavlink-router streams GCS traffic on UDP **14550** (to
  QGroundControl, or to the host when QGC is off). MAVROS connects to
  `px4-drone-1:14580`.
- **ArduPilot:** MAVROS connects to `tcp://ardupilot-drone-0:5760`, and QGC
  uses UDP **14550**.

To reach the autopilot from your container, join the vehicle's network as
above.

**Over AirSim RPC.** Connect to `localhost:41451` from the host.

**Visualisation.** Connect Foxglove or Lichtblick to `ws://localhost:8765`
(vehicle 1).

### Running stacks without the dashboard

The same generate, run and stop steps are available as commands, for scripts,
CI, or a machine where you only need the simulation. Paths are inside the
repository, which is mounted at `/workspace`:

```bash
./product.sh cli runtime --scenario /workspace/scenarios/<name> \
                         --out /workspace/generated/<name> --no-run   # validate + generate
./product.sh cli run-stack --stack /workspace/generated/<name> --detach
./product.sh cli status    --stack /workspace/generated/<name>
./product.sh cli logs      --stack /workspace/generated/<name>
./product.sh cli stop      --stack /workspace/generated/<name>
```

Nothing records a bag automatically this way. Record from your own container
as shown in [Connecting your autonomy stack](#connecting-your-autonomy-stack),
for example:

```bash
docker run --rm --network <stack>_agent_internal-1 --ipc host -v /dev/shm:/dev/shm \
  -u $(id -u):$(id -g) -e HOME=/tmp -e ROS_DOMAIN_ID=<domain> \
  -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp -e FASTDDS_BUILTIN_TRANSPORTS=UDPv4 \
  -v "$PWD/runs":/out --entrypoint bash \
  dhdevspace/auto_mns:tevv-airsim-ros2-bridge-humble-v1.0.0 -lc \
  'source /opt/ros/humble/setup.bash && mkdir -p /out/my_run && ros2 bag record \
   -o /out/my_run/bag -s sqlite3 /ground_truth/odom /imu/data /front_rgb/image_raw /clock'
```

Press Ctrl-C to stop recording. The bag is finalized when `ros2 bag record`
exits cleanly.

Every `product.sh` command, including setup and the image cache, is in
[The product shell from a terminal](cli.md).

### Repeatable test campaigns

`make campaign` flies a *campaign*: a scored matrix of runs over one scenario,
for example 4 wind strengths × 3 repeats, each recorded, validity-gated and
scored. [Campaigns](campaigns.md) covers the commands, and
[`scenarios/vio-reference/README.md`](../scenarios/vio-reference/README.md) shows how to copy the reference
campaign and swap in your own estimator.

---

## 7. Where everything is stored

Everything lives inside the folder you cloned, `M-S-Simulation-Runtime-Stack/`.

**Your results**

| What | Where on disk | Where in the dashboard |
|---|---|---|
| Rosbag of a run | `runs/<scenario>_<time>/bag/` (`bag_0.db3` + `metadata.yaml`) | **Replay → Bags** (plays it); **Calibration** → *Sim run* → **⬇ View bag files** (downloads it) |
| Run record | `runs/<scenario>_<time>/run.json`: run id, stack, bag name | shown with the run in **Replay → Bags** |
| Metrics events | `generated/<scenario>/outputs/metrics/<run id>/events.jsonl` | **Monitor → Run events** while the stack runs |
| Calibration reports | `runs/_reports/` | **Calibration** |
| Hand-off bundle | downloaded by your browser | **Analysis → Record integration bundle → Download bundle** |

**Your inputs and the product's own files**

| Path | Contents |
|---|---|
| `scenarios/<name>/` | Your exported ScenarioSpec (YAML files). |
| `generated/<name>/` | The stack generated from it: `docker-compose.yml` and configs. |
| `.mns/v1/pack-store/` | Installed level and object packs. |
| `.env` | Local settings. Defaults only; see [section 8](#8-optional-configuration). |

To free disk, delete old runs from `runs/`, and old scenarios with the
🗑 icon in Author. `du -sh generated/ runs/` shows the usage.

---

## 8. Optional configuration

Nothing here is needed for a normal run.

**Settings in `.env`.** Uncomment the line you need in `.env`:

| Variable | Default | Change it to |
|---|---|---|
| `TEVV_RUNS_DIR` | `runs/` in the checkout | keep runs and bags on another disk (an absolute path) |
| `DASHBOARD_LICHTBLICK_PORT`, `FOXGLOVE_BRIDGE_PORT` | `8082`, `8764` | move a port that clashes |
| `GRAFANA_URL` | local Grafana | empty, to hide the Grafana embed |
| `DOCKER_CONFIG` | `~/.docker` | a non-default Docker login location |

**Options on the `make dashboard` command line**

| Option | Effect |
|---|---|
| `DB=true` | Adds the telemetry history database. |
| `IMAGE_MODE=production` | Uses only the exact, digest-pinned release images. |
| `MNS_DEMO_PACKS=--all` | Installs any missing packs before starting, like `./download-packs.sh --all`. |
| `CHANNEL=ue582` / `CHANNEL=v2` | Runs an older pre-release line (UE 5.8.2 review set / UE 5.5.4). Each has its own packs and data. |

**Evaluation and sim-to-real.** The dashboard’s **Scenario Configuration** tab authors a ScenarioSpec and
generates + launches stacks through the selected `MNS_STACK_GENERATOR_IMAGE`
(no source checkouts).
**Monitor → Controls** edits the evaluation files in the shared runs directory
(`TEVV_RUNS_DIR`, default `runs/` in this checkout; hot-reloaded). **Calibration**
shows the sim-to-real verdicts the `sim-real-eval` worker writes there
automatically after each recorded run (enable with
`runtime.features: { sim_real_eval: true }` in the scenario).

**Keep image variables (`*_IMAGE`) out of `.env`.** An image set there
overrides the release, so you would run a different image from everyone else.

---

## 9. Troubleshooting

**Setup**

| Symptom | Fix |
|---|---|
| `✗ Docker is not reachable` | Start Docker, then `sudo usermod -aG docker $USER` and log out and back in. |
| `✗ NVIDIA container runtime not usable` | Install the NVIDIA Container Toolkit. `./setup.sh` prints the exact commands. |
| `pull access denied for dhdevspace/auto_mns` | Your Docker Hub account lacks access. Ask your MnS contact. |
| Pack download fails with `404` | Your GitHub account cannot read the pack releases (they answer 404, not 401). Check `gh auth status` and ask for access. |
| `only N GB free` | Free disk, or install fewer packs: `./download-packs.sh --warehouse`. |
| Pack download stops part-way (`curl exit 92/18/56`) | A dropped connection; run `./download-packs.sh` again and it resumes. |
| No internet on this machine | Copy the `.mnslevelpack` / `.mnsassetpack` files into `.mns/v1/packs/`, then run `tools/pull-packs.sh --import`. |

**Dashboard and ScenarioLab**

| Symptom | Fix |
|---|---|
| `make dashboard` stops: *the runs directory … is not writable by you* | The runs folder (`runs/`, or your `TEVV_RUNS_DIR`) is owned by root, so recordings would fail. Run the `sudo chown` command it prints, once. |
| `make dashboard` stops: port in use | Another program holds the port and is named in the message. Stop it, or run `make dashboard-down`. |
| `Conflict … "/airsim-dashboard-api" is already in use` | Left over from an older install. Run `make dashboard-down`, then `make dashboard`. |
| Author: *not ready*, `display` / `xauthority` | Run `make dashboard` from a desktop terminal, or see Display access in [section 3](#3-setup). |
| Author: *not ready*, `pack_store` / `packs_staged` | In Content, click **Download & stage** or **Stage**. |
| *editor exited immediately* | Usually the GPU or the display. **Editor log** in Author shows Unreal's own error. |
| Generate prints *…different registry digests; proceeding without enforcing base-release compatibility* | Advisory only: the level pack was cooked against an earlier build of the same base release. Generation continues and the level loads. |
| A downloaded level (Condo, XFS, Safti, Fisherman's Cabin) is missing from ScenarioLab's list, or shows *runtime only* in Content | Expected: ScenarioLab cannot open those four, so it doesn't list them; see [Known limitations](#10-known-limitations). Author in Warehouse or Office Environment. |
| Scenario doesn't appear in Authored scenarios | It wasn't exported; closing ScenarioLab does not save. Check its status line for *Exported ScenarioSpec*. |
| Export succeeded but has a default drone at the origin | No drone was placed. Export adds a default one; run **Validate** first. |

**Running**

| Symptom | Fix |
|---|---|
| Launch never reaches *Visualization ready* | The level is still loading; wait up to 3 minutes and check the `unreal-airsim` row. Make sure nothing else holds port 8765, such as another stack or Foxglove bridge. |
| `unreal-airsim is unhealthy`, restarting in a loop | The display. Start `make dashboard` from a desktop terminal. |
| PX4 won't arm: `ekf2 missing data` | Wait 1–2 minutes after spawn. |
| Lichtblick shows *You're using an unsupported browser* | Open the dashboard in Chrome or Chromium. |
| Lichtblick shows no topics | ROS domain mismatch. Relaunch from the dashboard, and check the hint on Monitor. |
| Your node sees only `/rosout` and `/parameter_events` | Wrong `ROS_DOMAIN_ID`, or the bridge hasn't finished starting. Read the domain from `generated/<name>/docker-compose.yml`. |
| Your node sees topics but no data | Your container's user id differs from the stack's. Run it with `-u $(id -u):$(id -g)` and share `/dev/shm`. |
| Bag missing from Replay / Analysis | It was not finalized. Always stop from the dashboard. |
| Huge bag | No topics were selected, so everything was recorded. Pick topics in ROS 2 → Bag capture. |

**Logs**
- Dashboard: `docker logs airsim-dashboard-api`
- ScenarioLab: Author → **Editor log**
- Stack services: the log panel in **Launch**, or
  `docker compose --project-directory generated/<name> logs -f <service>`

---

## 10. Known limitations

- **PX4** refuses to arm for 1–2 minutes after spawn, while its EKF2
  initialises.
- **ArduPilot:** the dashboard's command link carries heartbeats only, so
  takeoff altitude is not reported and the vehicle stays armed after a
  descent "land". Disarm it explicitly.
- **Runtime-only levels.** ScenarioLab cannot open Condo, XFS, Safti or
  Fisherman's Cabin: its editor lacks plugins those levels need. They still
  mount and fly, but you author them by hand, starting from a Warehouse
  export. Set the spec's origin to the level's PlayerStart; with the default
  (0,0,0) origin the drone can spawn high above the level and fall.
- **Office Environment** has a low ceiling. PX4 takeoffs there can be slow, and
  flights fly low.
- **Bags are sqlite3 (`.db3`).** Convert with `ros2 bag convert` if you need
  MCAP.
- **ScenarioLab** has no mission or waypoint authoring. Fly missions from
  Monitor, QGroundControl or your own stack.

---

## 11. Quick reference

```bash
./setup.sh                      # once: check, log in, pull images
./download-packs.sh --list      # what packs exist; ./download-packs.sh to get them
make dashboard                  # start → http://localhost:3001
make topics STACK=generated/<name>    # what a stack publishes
ls runs/                 # runs; bag in <run>/bag/
make dashboard-down             # stop the dashboard
```

**The flow:** Content → Author (Launch editor · Environment → Runtime →
Vehicles → Sensor Profiles → Validate → **Export**) → **Generate stack** →
ROS 2 (tick bag topics) → Metrics → **Launch** → Monitor (fly) → Launch
**Stop** → `runs/<run>/bag/`
