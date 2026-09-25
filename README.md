# MnS Product

MnS is a containerised simulation environment for testing drone autonomy. You
author a scenario in ScenarioLab (an Unreal editor), MnS generates a stack from
it (Unreal + Cosys-AirSim, PX4 or ArduPilot SITL, ROS 2 Humble), and you fly,
record and evaluate it from a browser dashboard. Everything runs in Docker.

This repository is the customer distribution. Its historical name is
`M-S-Simulation-Runtime-Stack`, but it holds the whole product, not one stack.

## Requirements

- Ubuntu 22.04 or later with a desktop session, and an NVIDIA GPU with a current driver
- Docker Engine with Compose v2, and the NVIDIA Container Toolkit
- About 35 GB of free disk for the first setup
- Google Chrome or Chromium for the dashboard
- Access to the private `dhdevspace/auto_mns` images (Docker Hub) and the
  `DinoHub/TEVV-Airsim` pack releases (GitHub). Ask your MnS contact.

`./setup.sh` checks each of these and prints the fix for anything missing.

## Quick start

```bash
./setup.sh           # checks the machine, logs in to Docker Hub, pulls the images
./download-packs.sh  # downloads the starter levels (logs in to GitHub)
make dashboard       # then open http://localhost:3001 in Chrome
```

From there, the [User Guide](docs/USER_GUIDE.md) walks you from an empty
scenario to a recorded rosbag. Stop with `make dashboard-down`.

## Find your way

**Use it**

| I want to... | Read |
| --- | --- |
| go from a fresh clone to my first rosbag | [User Guide](docs/USER_GUIDE.md) |
| connect my own autonomy stack to a running simulation | [User Guide: connecting your autonomy stack](docs/USER_GUIDE.md#connecting-your-autonomy-stack) |
| know which ROS 2 topics a stack will publish | [What will this stack publish?](docs/topics.md) |
| run stacks from a terminal or a script, without the browser | [The product shell from a terminal](docs/cli.md) |
| choose a fisheye rig (tiled or shared cubemap) and set it up for VIO | [Fisheye rig performance](docs/fisheye-rig-performance.md) |
| fly a scored run matrix to characterise an estimator | [Campaigns](docs/campaigns.md), then [the reference campaign](scenarios/vio-reference/README.md) |
| fly the same campaign on a GPU cluster (NVIDIA OSMO) | [Campaigns: OSMO](docs/campaigns.md#the-same-campaign-on-a-cluster-osmo), then the [OSMO runbook](docs/osmo-runbook.md) |
| install on a machine with no network | [Offline setup](docs/offline-setup.md) |
| fix something that went wrong | [User Guide: troubleshooting](docs/USER_GUIDE.md#9-troubleshooting) |

**Understand it**

| I want to... | Read |
| --- | --- |
| see which service does what, and which file each step leaves on disk | [How it fits together](docs/how-it-fits-together.md) |
| see how OSMO and Kubernetes map onto this repo | [OSMO and Kubernetes for this repo](docs/osmo-kubernetes-concepts.md), [One run, end to end](docs/osmo-run-flow.md) |
| read the TEVV platform architecture this stack maps onto | [docs/platform-architecture/](docs/platform-architecture/README.md) |
| know where runs, bags and packs are stored | [User Guide: where everything is stored](docs/USER_GUIDE.md#7-where-everything-is-stored) |

**Maintain it**

| I want to... | Read |
| --- | --- |
| pick an engine line, or install, update or remove content packs | [packs/README.md](packs/README.md) |
| change, pin or verify a container image | [Changing a container image](docs/images.md) |
| understand which images `make dashboard` pulls, or run my own dashboard build | [How the dashboard gets its images](docs/dashboard-images.md) |
| see what a release pins | [Release notes](docs/releases/v1.0.0.md) |
| know why there is one image catalog | [ADR 0002](docs/adr/0002-one-image-catalog.md) |
| use the sim-facing helper scripts | [tools/README.md](tools/README.md) |

Updating an existing checkout is covered in the
[User Guide](docs/USER_GUIDE.md#updating-an-existing-checkout).
