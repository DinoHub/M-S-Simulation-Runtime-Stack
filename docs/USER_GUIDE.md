# MnS Product — User Guide

This guide takes you from a fresh machine to a recorded rosbag. You author a
scenario, generate a simulation stack from it, fly it, and record it, all from
the browser dashboard.

The [README](../README.md) is the operator reference: image pins, channels,
CLI flags and the legacy stacks. This guide covers only what you need to use
the product.

---

## 1. What you are running

| Piece | What it is | Where you see it |
|---|---|---|
| **TEVV Web Dashboard** | The browser entry point. It walks you through every step and records the run. | <http://localhost:3001> |
| **ScenarioLab** | An Unreal editor window where you place vehicles, sensors, zones and obstacles in a level. The dashboard launches it. | A separate window on your desktop |
| **Stack generator** | Turns the exported `ScenarioSpec.yaml` into a runnable Docker Compose stack. | `generated/<scenario>/` |
| **Generated stack** | The simulation itself: one TEVVRuntimeHost (Unreal + AirSim), an autopilot SITL per vehicle, ROS 2 bridges, a Foxglove bridge and QGroundControl. | The dashboard's **Monitor** page |
| **Level / object packs** | Pre-cooked Unreal content: environments and placeable props. ScenarioLab and the runtime host load the same checksum-verified artifact. | `.mns/v1/pack-store/` |

You never build source code. Everything ships as pinned Docker images and
downloadable packs.

---

## 2. Requirements

### Hardware

- An NVIDIA GPU with a recent driver. The runtime host and ScenarioLab both
  need GPU passthrough.
- At least **50 GB free disk** before the first run:
  - The MnS 1.0 packs are about 15 GB to download, and Electric Dreams alone is
    12.3 GB. During install the archive and the store copy exist side by side,
    so plan for about twice that. Staging the packs for ScenarioLab and for
    generated stacks uses hard links and costs no extra space.
  - The images take several GB more.
  - Rosbags grow quickly, from hundreds of MB to several GB per minute
    depending on the topics you record.

### Software

| Requirement | Check |
|---|---|
| Linux with a desktop session (X11, or GNOME/Wayland with XWayland) | `echo $DISPLAY` prints something like `:0` or `:1` |
| Docker Engine and the Compose v2 plugin | `docker compose version` |
| Your user in the `docker` group | `docker ps` works without `sudo` |
| NVIDIA Container Toolkit | `docker run --rm --gpus all ubuntu nvidia-smi` |
| Python 3 with `jinja2`, `python-dotenv` and `pyyaml` | `pip install -r tools/requirements.txt`, or on Ubuntu `sudo apt install python3-jinja2 python3-dotenv python3-yaml` |
| `make`, `git`, `curl` | |

### Access

You need two sets of credentials. Ask your MnS contact for access if you have
neither.

1. **Docker Hub**, able to pull the private `dhdevspace/auto_mns` images:
   ```bash
   docker login
   ```
2. **GitHub**, able to read the private pack releases (`DinoHub/TEVV-Airsim`).
   Either log in with the GitHub CLI:
   ```bash
   gh auth login
   ```
   or export a token that has read access:
   ```bash
   export GH_TOKEN=<token>
   ```
   If you have neither, the pack download stops before it fetches anything and
   says so. A `404` during the pack download almost always means a missing or
   expired token, not a network problem.

---

## 3. One-time setup

```bash
git clone https://github.com/DinoHub/M-S-Simulation-Runtime-Stack.git
cd M-S-Simulation-Runtime-Stack

./setup.sh            # host checks (Docker, NVIDIA, Python deps); creates .env from the template
./product.sh setup    # pulls the release images and creates the local pack store
./product.sh doctor   # confirms every pinned image is present; no network needed
```

**Fix every warning that `./setup.sh` prints before you continue.** It checks
Docker access, the NVIDIA runtime and the Python modules.

`./product.sh setup` is the one step that downloads the full image set. It
retries each pull if Docker Hub is flaky. Re-run it only when you want to
refresh to newly published images.

### Display access (GNOME / Wayland hosts)

ScenarioLab and the simulator open windows on your desktop, so they need your X
authority cookie. On GNOME/Wayland (the Ubuntu default), set it in **the
terminal you run `make dashboard` from**:

```bash
export XAUTHORITY=$(ls -t /run/user/$(id -u)/.mutter-Xwaylandauth.* 2>/dev/null | head -1)
xhost +si:localuser:$USER
```

You can add both lines to `~/.bashrc`.

Without this, Unreal containers start, fail to open a display, and restart in a
loop. The only visible error is
`dependency failed to start: container …-unreal-airsim is unhealthy`, which
does not mention X11.

### Configuration (`.env`)

**Nothing needs configuring for a standard run.** Every setting has a working
default.

`./setup.sh` creates `./.env` from [`.env.example`](../.env.example). It is
local to your machine and not tracked by git. The top section of
`.env.example`, *Dashboard (make dashboard)*, lists the settings you might
change, commented out, with their default values. The rest of the file is for
the legacy `./launch.sh` stacks, which you can ignore.

Settings that go in `./.env`:

| Variable | Default | Change it when |
|---|---|---|
| `TEVV_RUNS_DIR` | `~/tevv-runs` | You want runs and rosbags on another disk |
| `DOCKER_CONFIG` | `~/.docker` | Your `docker login` is stored somewhere else |
| `DASHBOARD_LICHTBLICK_PORT` | `8082` | The port is already in use |
| `FOXGLOVE_BRIDGE_PORT` | `8764` | The port is already in use |
| `GRAFANA_URL` | the local Grafana on :3000 | Set it empty to hide the Grafana embed when monitoring is not running |
| `DASHBOARD_PULL_POLICY`, `MNS_IMAGE_PULL_POLICY` | `missing` | You want `always`, which re-checks the registry on every start |

Settings you pass on the command line or export in your shell. These are
**not** read from `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `XAUTHORITY`, `DISPLAY` | from your desktop session | X11 access for Unreal windows. See [Display access](#display-access-gnome--wayland-hosts). |
| `GH_TOKEN` | `gh auth token` | GitHub credentials for pack downloads |
| `CHANNEL` | `v1` | Release line: `v1` (MnS 1.0, UE 5.8.2). The older `ue582` and `v2` lines are for comparison only. |
| `IMAGE_MODE` | `development` | `production` for the exact digest-pinned release |
| `DB` | off | `DB=true` adds the telemetry history database |
| `MNS_DEMO_PACKS` | `--all` | Install only some packs, for example `"--blocks --condo"` |
| `MNS_SKIP_PACK_INSTALL` | off | `1` skips the pack check, for offline use |
| `MNS_DEMO_PACK_DOWNLOAD_DIR` | a temp folder | Where pack downloads are staged; use a larger disk |

For example:

```bash
make dashboard IMAGE_MODE=production DB=true
```

**Keep image variables (`*_IMAGE`) out of `.env`.** An image key set there
overrides the release pins. For example, a leftover `MNS_AUTHORING_IMAGE=`
line makes ScenarioLab launch an old image. Only set one when you deliberately
want to run a local build.

---

## 4. Start the dashboard

```bash
make dashboard
```

The first run downloads and stages the demo packs (about 15 GB). Later runs
only check that nothing is missing and start in seconds. When the command
finishes it prints:

```
Dashboard: http://localhost:3001 (backend :8001, lichtblick :8082, image mode: development)
```

Open **<http://localhost:3001>**.

### Variants

| Command | Use it when |
|---|---|
| `make dashboard` | Normal use: the MnS 1.0 release (channel `v1`, Unreal Engine 5.8.2). Local images are kept; only missing ones are pulled. |
| `make dashboard IMAGE_MODE=production` | You want the exact, digest-pinned release images. |
| `make dashboard MNS_DEMO_PACKS="--blocks --condo --xfs"` | You want only some packs, for example to skip the 12 GB Electric Dreams. The names are `--blocks --condo --electric_dreams --pendleton --safticity --xfs --mns_vehicle_models`. |
| `make dashboard MNS_SKIP_PACK_INSTALL=1` | You are offline and the packs are already installed. |
| `make dashboard DB=true` | You also want the telemetry history database. |
| `make dashboard CHANNEL=ue582` or `CHANNEL=v2` | You need an older pre-release line: `ue582` is the 5.8.2 review set, `v2` is UE 5.5.4. Each line has its own packs and data. |

### Ports used

| Port | Service | Checked at start |
|---|---|---|
| 3001 | Dashboard frontend | yes |
| 8001 | Dashboard API | yes |
| 8082 | Lichtblick (3D / plot viewer) | yes |
| 8764 | ROS 2 tools Foxglove websocket | yes |
| 8765+ | Foxglove bridge of the running stack (one port per vehicle) | no |
| 8767 | Bag replay bridge | no |
| 3000 | Grafana (optional monitoring) | no |

If a checked port is busy, `make dashboard` stops and names the process
holding it.

---

## 5. Walkthrough: from scenario to rosbag

The left sidebar has **Overview**, **Scenario Configuration**, **Monitor**,
**Replay**, **Calibration** and **VIO Stress**.

Open **Scenario Configuration**. Across the top is a stepper:

```
0 Content → 1a Author → 1b Generate → 1c ROS 2 → 1d Metrics → 2 Launch → 3 Runtime → 4 Analysis
```

Each step unlocks when the one before it is done. A completed step shows a
check mark.

### Step 0 — Content: *engine · level & object packs*

1. Under **Engine line**, every image role should show **matches**:
   - ScenarioLab (authoring)
   - Runtime host (simulation)
   - Stack generator
   - Product shell (pack install)

   If a role shows **not on this machine**, run `./product.sh setup`, then
   click **Refresh**.
2. Under **Level packs** and **Object packs**, each pack should be **ready**.
   If not, tick the packs you want and click **Download & stage N**, or
   **Stage N** when they only need staging. The panel shows the free disk
   space and how much the selection needs.
3. The step is done when the badge reads *N of M level packs ready*. Click
   **Continue to Author**.

### Step 1a — Author: *ScenarioLab export*

1. The header badge should read **environment ready**. If it shows *Authoring
   environment not ready*, it lists the failing checks; see
   [Troubleshooting](#7-troubleshooting). Fix them and click **Re-check
   environment**.
2. Click **Launch editor**. ScenarioLab opens in its own window after 30–90
   seconds.
3. In ScenarioLab:
   1. Pick a level.
   2. Place your vehicle(s) and attach sensors.
   3. Add zones and obstacles as needed.
   4. Click **Export**. The status line reads
      `Exported ScenarioSpec: …/scenarios/<name>/ScenarioSpec.yaml`.
4. Back in the dashboard, the scenario appears under **Authored scenarios**
   within about 5 seconds. **Click it** to select it and move to Generate.

Each row in **Authored scenarios** has three icons:
- ✏️ reopens the scenario in ScenarioLab.
- ⧉ copies it as the basis for a new scenario.
- 🗑 deletes the spec and generated stack. Recorded runs are kept.

> **No ScenarioLab?** Click **Skip authoring — build the spec in the wizard**
> to build the whole scenario in the next step instead.

### Step 1b — Generate: *spec → stack*

The left card is a five-step form: **Scenario → Vehicles → Sensors → Zones →
Obstacles**. If you exported from ScenarioLab, the form is already filled in.
The right card shows the resulting **ScenarioSpec** YAML.

1. On the **Scenario** step, check the **Name** and **Environment**.
   Environment is the level pack, shown as name and version. Optionally pick a
   **Runtime profile** and enable features such as `sim_real_eval`.
2. Review the remaining steps with **Continue →**.
3. Click **Generate stack**.

The badge above the YAML tells you where the spec came from:
- *matches authored* — the spec is exactly what you exported.
- *modified from authored* — you edited it in the form.
- *hand-edited* — you used **Edit YAML**.

**Restore authored** takes you back to the export. If you generate over a name
that already has a different authored spec, the dashboard asks before
overwriting it. **Save as new…** forks the spec under a new name instead.

The step is done when a green line reads *Generated <name>.* The dashboard then
moves to ROS 2 on its own. The stack is written to `generated/<name>/` in this
repository.

### Step 1c — ROS 2: *network · topics · bag*

This step decides **what goes into the rosbag**. It has four sub-steps.

| Sub-step | What you set |
|---|---|
| **Network** | `ROS_DOMAIN_ID`, the network preset, and optionally `cyclonedds.xml`. The defaults are fine for a single machine. |
| **Topic names** | An optional `TOPIC_PREFIX` and topic renames. |
| **Sensors** | Sensor settings for the stack. |
| **Bag capture** | What to record (see below). |

On **Bag capture**:

1. Keep **Start recording when the stack launches** ticked if you want
   recording to start automatically.
2. Tick the topics to record. The list comes from the generated stack, so you
   see exactly what it will publish.
   - If you select nothing, *every* topic is recorded, which is gigabytes per
     minute with cameras.
   - Pick the topics you need, typically pose/odometry, IMU, lidar and one
     camera.
3. Click **Save & continue to metrics**.

### Step 1d — Metrics: *what the sim logs*

1. Optionally tick **Measure this run** and choose a preset. Detectors write
   events to `outputs/metrics/<run>/events.jsonl`.
2. Click **Save & apply**.

### Step 2 — Launch: *run & record*

1. The chips at the top confirm your choices, for example *12 record topics ·
   domain 1*.
2. Under **Generated stack**, pick your stack (**Refresh stacks** if it is not
   listed) and click **Launch**.
3. Watch the service rows. Each one moves to **running · healthy**. The
   simulator (`unreal-airsim`) is the slowest; loading a level takes 1–3
   minutes.
4. When the status reads **Visualization ready — the viewer is answering**, the
   button turns into a green **Go to Monitor**.
5. If autostart recording is on, a line reads **Recording started — N topics
   armed from pre-run.**

A QGroundControl window and the Unreal simulator window also open on your
desktop.

### Step 3 — Runtime: *what is flying*

This step shows *X of Y healthy* for the stack's services and links to the
**Monitor** page. On **Monitor**:

- **Viewers → Lichtblick**: live 3D view, camera images and plots from the
  stack's ROS 2 topics. **Viewers → Sim** shows the simulator view.
- **Mission Control** (right-hand panel):
  - pick a vehicle
  - **Bag recording**: start and stop recording by hand. The badge shows
    **REC mm:ss** and the size written so far.
  - **Quick Mission Launch**, **Flight patterns**, and **Teleop (WASD)** for
    keyboard flight
  - compact telemetry
- **Run events** and **Grafana**: the metrics stream for this run.
- **Controls**: the evaluation runtime config and live sensor controls.

Fly your mission, from Quick Mission Launch, a flight pattern, teleop,
QGroundControl, or your own autonomy stack on the same ROS domain.

### Step 4 — Stop and finalize the recording

Go back to **Launch** and click **Stop**, or use **Stop & finalize** in
Mission Control's **Bag recording** panel first.

**Always stop through the dashboard.** Stopping finalizes the bag, which writes
its `metadata.yaml`, and only then tears the stack down. A bag without
`metadata.yaml` cannot be replayed or analysed. So do not `docker rm -f` a
recorder that shows **writing bag**; give it time to finish.

After Stop, a line reports *Run … archived*. The **Analysis** step unlocks.

### Step 5 — Get the rosbag

Every recorded run gets its own folder under the runs directory. That is
`~/tevv-runs` by default, or `TEVV_RUNS_DIR` if you set it.

```
~/tevv-runs/
  <scenario>_<YYYYmmdd_HHMMSS>/
    run.json            # run id and the stack it came from
    bag/
      metadata.yaml     # rosbag2 metadata (written on a clean stop)
      bag_0.db3         # rosbag2 sqlite3 storage (zstd per message); large runs split into bag_1.db3, …
```

There are three ways to get the bag:

1. **From disk.** Copy `~/tevv-runs/<run>/bag/`. It is a standard ROS 2
   (Humble) bag:
   ```bash
   ros2 bag info ~/tevv-runs/<run>/bag
   ros2 bag play ~/tevv-runs/<run>/bag
   ```
2. **Download in the browser.** Open **Calibration**, choose the run as *Sim
   run (candidate)*, then click **⬇ View bag files**. You get a download link
   per file. The downloaded `.db3` also opens in Lichtblick
   (<http://localhost:8082> → *Open local file*).
3. **Replay in the browser.** Go to **Analysis → Replay this run**, or
   **Replay → Bags**, to play the bag back in Lichtblick without the stack
   running.

**Analysis** also offers **Record integration bundle** / **Download bundle**,
which package the run for hand-off to an integration team.

---

## 6. Stopping and cleaning up

```bash
make dashboard-down        # stops the dashboard containers
```

Launch → **Stop** has already stopped the simulation stack, and Author →
**Stop** closes ScenarioLab.

`make dashboard-down` leaves a few helper containers that the dashboard creates
on demand. Remove them when you are completely done:

```bash
docker rm -f ros2-tools mns-replay-bridge mns-scenariolab-editor 2>/dev/null
docker ps -a --filter name=mns-recorder- -q | xargs -r docker rm -f
```

Check your disk use from time to time. Old runs are never deleted
automatically.

```bash
du -sh generated/ ~/tevv-runs
```

---

## 7. Troubleshooting

### Author preflight checks

When Author says *Authoring environment not ready*, it names the failing
checks:

| Check | Meaning | Fix |
|---|---|---|
| `docker_daemon` | The dashboard cannot reach Docker | Start Docker; make sure your user is in the `docker` group |
| `authoring_image` / `generator_image` | An image is not on this machine | `./product.sh setup` |
| `pack_store` | No packs are installed | Step 0 **Download & stage**, or re-run `make dashboard` |
| `packs_staged` | Packs are installed but ScenarioLab cannot see them | Step 0 **Stage N**, or `tools/stage-authoring-packs.sh` |
| `display` / `xauthority` | No usable X display | See [Display access](#display-access-gnome--wayland-hosts), then restart the dashboard from that terminal |
| `exports_writable` / `authoring_data_writable` | Folder permissions | Make sure `scenarios/` and `.mns/` belong to you, not root |

### Common problems

| Symptom | Cause and fix |
|---|---|
| `make dashboard` stops: *port 3001 (or 8001/8082/8764) in use* | Another program or an old dashboard is using the port. `make dashboard-down`, or stop the process it names. |
| `Conflict … "/airsim-dashboard-api" is already in use` | A container from an older install. Run `make dashboard-down`, then `make dashboard`. |
| Pack download fails with `404` | GitHub credentials are missing or expired. `gh auth login` or `export GH_TOKEN=…`. |
| No internet on the machine, or GitHub access blocked | Copy the `.mnslevelpack` / `.mnsassetpack` archives into `.mns/v1/packs/`, then run `tools/pull-packs.sh --import`. The archives are verified before install. Then start with `make dashboard MNS_SKIP_PACK_INSTALL=1`. |
| *Not enough free space for this selection* | Free some disk, or select fewer packs. `MNS_DEMO_PACK_DOWNLOAD_DIR=/bigdisk/tmp` moves the download staging area. |
| `pull access denied for dhdevspace/auto_mns` | Run `docker login` with an account that has access. |
| **Generate** fails with *generation failed* | Usually the generator image is missing, because Generate never pulls it. Run `./product.sh doctor`, then `./product.sh setup`. |
| Editor: *editor exited immediately (code N)* | Usually the GPU or the display. Check `docker run --rm --gpus all ubuntu nvidia-smi` and the X11 section above. The **Editor log** in Author shows Unreal's own error. |
| `could not select device driver "nvidia"` | The NVIDIA Container Toolkit is not installed or configured. `./setup.sh` prints the fix. |
| `…-unreal-airsim is unhealthy` / simulator restarts in a loop | Almost always X11. Set `XAUTHORITY` as above. If an empty directory exists at `/run/user/$(id -u)/gdm/Xauthority`, remove it with `rmdir`. |
| Launch hangs before *Visualization ready* | The level is still loading. Wait up to 3 minutes and check the `unreal-airsim` row and log. Also make sure nothing else is using port 8765, such as a legacy `./launch.sh` stack or another Foxglove bridge. |
| PX4 refuses to arm: `Preflight Fail: ekf2 missing data` | PX4's estimator needs 1–2 minutes after spawn. Wait, then arm again. |
| Lichtblick connects but shows no topics | A ROS domain mismatch. Relaunch from the dashboard so the viewer follows the stack's domain, and check the amber hint on Monitor. |
| A bag is missing from Replay / Analysis | It was never finalized, so it has no `metadata.yaml`. Always stop through the dashboard. |
| Very large bags | No topics were selected, so everything was recorded. Pick topics in ROS 2 → Bag capture. |
| ScenarioLab looks older than expected | An image variable in `./.env` overrides the release pins. Remove it; see [Configuration](#configuration-env). |

### Getting logs

- **Dashboard:**
  ```bash
  docker logs airsim-dashboard-api
  ```
- **Stack services:** use the collapsible log in **Launch**, or:
  ```bash
  docker compose --project-directory generated/<name> logs -f <service>
  ```
- **ScenarioLab:** Author → **Editor log**, or:
  ```bash
  docker logs mns-scenariolab-editor
  ```
- **API health:**
  ```bash
  curl -s localhost:8001/api/scenario/preflight
  ```
  This should report `"ready": true`.

---

## 8. Known limitations (MnS 1.0)

- **PX4 is not ready to arm immediately after spawn.** PX4's estimator needs
  1–2 minutes before it accepts arming. The dashboard teleop tries to arm as
  soon as a session starts, so the first attempt can be refused. Wait and try
  again.
- **ArduPilot:** its command channel carries heartbeats only. The dashboard
  cannot see the takeoff altitude, and the vehicle stays armed after a
  descent-style "land". Disarm it explicitly.
- **ROS `map` height:** the `map → odom` transform is the authored spawn point,
  so ROS map z is off by the small drop as the vehicle settles before arming.
  That is about 4.6 m on Electric Dreams.
- **Bag format:** bags are recorded as rosbag2 **sqlite3 (`.db3`)**. If you
  need MCAP, convert with `ros2 bag convert`. Replay and Calibration also
  accept uploaded `.mcap` bags.
- **Committed scenarios:** most scenarios under `scenarios/` are marked
  *LEGACY v1 SCENARIO SPEC* and cannot be generated. Re-author them in
  ScenarioLab. `scenarios/vio-reference` is current: it is the reference for
  `make campaign`, which flies a scored matrix of runs. See the README section
  *Characterise an algorithm (campaigns)*.
- **`tests/full-product-e2e`** covers the older Blocks-only release. It is not
  an acceptance test for MnS 1.0.

## 9. Quick reference

```bash
# once
./setup.sh && docker login && gh auth login
./product.sh setup && ./product.sh doctor

# every session
export XAUTHORITY=$(ls -t /run/user/$(id -u)/.mutter-Xwaylandauth.* 2>/dev/null | head -1)
make dashboard                 # → http://localhost:3001
#   Content → Author (Launch editor, Export) → Generate stack → ROS 2 (pick bag topics)
#   → Metrics → Launch → fly from Monitor → Launch: Stop
ls ~/tevv-runs/                # your runs; the bag is in <run>/bag/
make dashboard-down
```
