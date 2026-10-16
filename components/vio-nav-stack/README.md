# vio-nav-stack: a whole stack, OpenVINS feeding SUPER

A worked example of a stack under test in TEVV: OpenVINS estimates the
vehicle's pose from the stereo pair and the IMU, and SUPER plans and flies on
that estimate. The stack starts itself (`start.sh`, `compose.yml`); its adaptor
(`adaptor/tevv_adaptor.py`) fits both to TEVV's fixed interface.

| | |
| --- | --- |
| Consumes | `stereo_primary`, `imu` (OpenVINS), `point_cloud` (SUPER's map), `ground_truth` (read once: the start pose) |
| Produces | `estimate` (OpenVINS, scored), `command` (SUPER's velocity, flown) |
| Ready | `/planner_state` after take-off |

**The start pose.** OpenVINS's world frame is fixed where it initialised;
goals and commands are in TEVV's `odom`. The adaptor takes one ground-truth
sample at the stamp of OpenVINS's first estimate, as a real mission gets its
origin from the operator, then closes the ground-truth subscription. From
then on SUPER flies on OpenVINS's estimate alone, so VIO drift shows up as
missed goals or lost clearance.

**The map.** SUPER maps obstacles from TEVV's registered cloud
(`/tevv/sensors/point_cloud`, in `odom`). A stack that registers the raw lidar
itself would also need the lidar's mount, which `interface.json` does not
carry yet.

Build the adaptor, check the package, run its suite:

```bash
docker build -t dhdevspace/auto_mns:vio-nav-stack-adaptor-0.1.0 components/vio-nav-stack/adaptor
./tevv component check components/vio-nav-stack
./tevv suite run suites/vio-nav-stack --component components/vio-nav-stack
```
