# tevv_ws Sensor Contract (Sub-project A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure and document the sensor contract between a generated UE 5.8.2 runtime stack and `/home/mnsuser/tevv_ws`, producing a go/no-go on monocular VIO before any image is published.

**Architecture:** Two dependency-free measurement modules (`contract_stats.py`, `mavlink_reach.py`) carrying all the logic and all the unit tests, a thin `contract_probe.py` ROS node that subscribes and calls them, and a Compose overlay that joins the probe to a running generated stack's `agent_internal-N` network. The probe is validated against `tevv_ws`'s own Gazebo stack — which has a stated 200 Hz IMU / 20 Hz camera baseline — before it is trusted against Unreal.

**Tech Stack:** Python 3, ROS 2 Humble (`rclpy`, `sensor_msgs`, `nav_msgs`, `rosgraph_msgs`), Docker Compose, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-09-tevv-ws-sensor-contract-design.md`

## Global Constraints

- **One drone's domain per probe run.** `compose/px4-xfs/docker-compose.yml:647`: each per-drone bridge is its own `/clock` publisher inside its own domain, and N bridges on one domain race `/clock` and reset tf2 buffers. Never subscribe across two drone domains.
- **`ROS_LOCALHOST_ONLY=0`** on every probe container. The stack sets this; `tevv_ws` defaults to `1`, which would make it deaf.
- **`ROS_DOMAIN_ID` must match the drone under test.** Stack default is `${DRONE_1_DOMAIN_ID:-1}` — so `1`, not `tevv_ws`'s default `42`.
- **`USE_SIM_TIME=true`.** `/clock` exists per drone; timestamps are sim time.
- **No motion.** No arming, no offboard, no setpoints. MAVLink is probed for reachability only. Anything that commands the vehicle is sub-project D.
- **Pure modules stay ROS-free.** `contract_stats.py` and `mavlink_reach.py` import no ROS. That is what makes them unit-testable without a ROS environment, matching `testing/trajectory.py`.
- **Style:** 4-space indent, compact one-line bodies where natural, module docstring first — match `testing/trajectory.py`. Tests are `unittest`, run with `python3 -m unittest discover -s testing/tests -v`.
- **No-go thresholds (from the spec, fixed in advance):** IMU below 100 Hz sustained or below 5x image rate; image publisher `BEST_EFFORT` with no reliable option; `/clock` absent, non-monotonic, or unrelated to published timestamps; ground truth not gravity-aligned metres on the IMU body origin.

## File Structure

| Path | Responsibility |
| --- | --- |
| `/home/mnsuser/tevv_ws/testing/contract_stats.py` | Pure measurement maths: rate/jitter, clock monotonicity, gravity alignment. No ROS, no I/O. |
| `/home/mnsuser/tevv_ws/testing/mavlink_reach.py` | Pure UDP reachability probe. No ROS. |
| `/home/mnsuser/tevv_ws/testing/contract_probe.py` | Thin ROS node: subscribe, collect, call the two modules, emit JSON. |
| `/home/mnsuser/tevv_ws/testing/tests/test_contract_stats.py` | Unit tests for the maths. |
| `/home/mnsuser/tevv_ws/testing/tests/test_mavlink_reach.py` | Unit tests for the UDP probe, against a local socket. |
| `/home/mnsuser/tevv_ws/compose.contract-probe.yaml` | Overlay joining the probe to an external stack network. |
| `/home/mnsuser/tevv_ws/testing/contract.env.example` | Documented variables for the overlay. |
| `M-S-Simulation-Runtime-Stack/docs/integration/unreal-sensor-contract.md` | The deliverable. Lives in the stack repo because sub-projects C and D cite it. |
| `M-S-Simulation-Runtime-Stack/docs/integration/unreal-sensor-contract.json` | Raw measurements, both runs. |

Two repositories are touched. Code lands in `tevv_ws`; the contract document lands in `M-S-Simulation-Runtime-Stack` alongside the spec that commissioned it.

---

### Task 1: Make tevv_ws a git repository

`tevv_ws` has a `.gitignore` and a `.dockerignore` that excludes `**/.git`, but no repository. Everything this plan writes would otherwise be unversioned. Sub-project B needs a repository anyway.

**Files:**
- Create: `/home/mnsuser/tevv_ws/.git/` (via `git init`)

**Interfaces:**
- Consumes: nothing
- Produces: a repository at `/home/mnsuser/tevv_ws` on branch `main`, with everything currently on disk committed except the existing `.gitignore` exclusions (`bags/`, `results/`, `testing/unreal.env`, `__pycache__`, `*.pyc`)

- [ ] **Step 1: Confirm there is no repository and nothing would be clobbered**

```bash
cd /home/mnsuser/tevv_ws
git rev-parse --git-dir 2>&1 | head -1   # expect: fatal: not a git repository
ls -a | head -20
```

Expected: the `fatal:` line, and a listing showing `.gitignore`, `.dockerignore`, `compose.yaml`, `metrics/`, `open_vins/`, `results/`, `simulation/`, `testing/`, `bags/`.

- [ ] **Step 2: Initialise and inspect what would be tracked**

```bash
cd /home/mnsuser/tevv_ws
git init -b main
git add -A
git status --short | wc -l
git status --short | grep -E '^A  (bags|results)/' | head
```

Expected: a nonzero file count, and the `grep` returns nothing — `bags/` and `results/` must be excluded by the existing `.gitignore`. If either appears, stop and fix `.gitignore` before committing.

`open_vins/` is a large vendored source tree. Confirm it is intended to be tracked before continuing:

```bash
du -sh open_vins; git status --short | grep -c '^A  open_vins/'
```

If that count is large and the tree is a vendored upstream checkout, add `open_vins/` to `.gitignore` and re-run `git add -A`. Record the decision in the commit message either way.

- [ ] **Step 3: Commit**

```bash
cd /home/mnsuser/tevv_ws
git commit -q -m "chore: initialise the tevv_ws repository

The workspace already carried a .gitignore and a .dockerignore excluding
**/.git, but was never initialised, so nothing here was versioned. The
sensor-contract work adds source files that must be reviewable, and
sub-project B needs a repository to build images from."
git log --oneline -1
```

Expected: one commit printed.

---

### Task 2: Rate, clock and gravity maths

The measurement logic, with no ROS and no I/O, so it can be tested anywhere.

**Files:**
- Create: `/home/mnsuser/tevv_ws/testing/contract_stats.py`
- Test: `/home/mnsuser/tevv_ws/testing/tests/test_contract_stats.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `rate_stats(stamps: list[float]) -> dict` with keys `count`, `hz`, `jitter_ms`, `min_dt`, `max_dt`. `hz`, `jitter_ms`, `min_dt`, `max_dt` are `None` when fewer than two samples.
  - `clock_report(pairs: list[tuple[float, float]]) -> dict` with keys `monotonic` (bool), `ratio` (sim seconds per wall second, or `None`), `samples` (int). `pairs` are `(sim_time, wall_time)`.
  - `rotate(quaternion_wxyz: tuple[float, float, float, float], vector_xyz: tuple[float, float, float]) -> tuple[float, float, float]`
  - `gravity_in_world(quaternion_wxyz, accel_body_xyz) -> dict` with keys `x`, `y`, `z`, `magnitude`, `tilt_deg` — the body-frame specific-force vector expressed in the truth world frame, and the angle between it and world +Z.

- [ ] **Step 1: Write the failing tests**

Create `/home/mnsuser/tevv_ws/testing/tests/test_contract_stats.py`:

```python
import math
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contract_stats import rate_stats, clock_report, rotate, gravity_in_world


class RateStatsTests(unittest.TestCase):
    def test_perfect_200hz(self):
        stamps = [i / 200.0 for i in range(201)]
        result = rate_stats(stamps)
        self.assertEqual(result['count'], 201)
        self.assertAlmostEqual(result['hz'], 200.0, places=6)
        self.assertAlmostEqual(result['jitter_ms'], 0.0, places=9)

    def test_jitter_is_interval_stddev_in_ms(self):
        # intervals alternate 0.01 and 0.03 s -> mean 0.02, stddev 0.01 s = 10 ms
        stamps = [0.0, 0.01, 0.04, 0.05, 0.08]
        result = rate_stats(stamps)
        self.assertAlmostEqual(result['hz'], 50.0, places=6)
        self.assertAlmostEqual(result['jitter_ms'], 10.0, places=6)
        self.assertAlmostEqual(result['min_dt'], 0.01, places=9)
        self.assertAlmostEqual(result['max_dt'], 0.03, places=9)

    def test_too_few_samples_reports_none_not_zero(self):
        for stamps in ([], [1.0]):
            result = rate_stats(stamps)
            self.assertIsNone(result['hz'])
            self.assertIsNone(result['jitter_ms'])
            self.assertEqual(result['count'], len(stamps))

    def test_unsorted_input_is_sorted_first(self):
        self.assertAlmostEqual(rate_stats([0.02, 0.0, 0.01])['hz'], 100.0, places=6)


class ClockReportTests(unittest.TestCase):
    def test_realtime_clock_is_monotonic_with_unit_ratio(self):
        pairs = [(t / 10.0, t / 10.0) for t in range(11)]
        result = clock_report(pairs)
        self.assertTrue(result['monotonic'])
        self.assertAlmostEqual(result['ratio'], 1.0, places=6)
        self.assertEqual(result['samples'], 11)

    def test_half_speed_sim_reports_half_ratio(self):
        pairs = [(t / 20.0, t / 10.0) for t in range(11)]
        self.assertAlmostEqual(clock_report(pairs)['ratio'], 0.5, places=6)

    def test_backwards_step_is_not_monotonic(self):
        pairs = [(0.0, 0.0), (1.0, 1.0), (0.5, 2.0), (2.0, 3.0)]
        self.assertFalse(clock_report(pairs)['monotonic'])

    def test_stalled_clock_reports_zero_ratio_and_stays_monotonic(self):
        pairs = [(5.0, t / 10.0) for t in range(11)]
        result = clock_report(pairs)
        self.assertTrue(result['monotonic'])
        self.assertAlmostEqual(result['ratio'], 0.0, places=6)


class RotateTests(unittest.TestCase):
    def test_identity_quaternion_returns_the_vector(self):
        self.assertEqual(rotate((1.0, 0.0, 0.0, 0.0), (0.0, 0.0, 9.81)), (0.0, 0.0, 9.81))

    def test_half_turn_about_x_flips_z(self):
        x, y, z = rotate((0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 9.81))
        self.assertAlmostEqual(x, 0.0, places=9)
        self.assertAlmostEqual(y, 0.0, places=9)
        self.assertAlmostEqual(z, -9.81, places=9)

    def test_yaw_does_not_change_the_z_component(self):
        q = (math.cos(math.pi / 4), 0.0, 0.0, math.sin(math.pi / 4))  # 90 deg about z
        self.assertAlmostEqual(rotate(q, (0.0, 0.0, 9.81))[2], 9.81, places=9)


class GravityInWorldTests(unittest.TestCase):
    def test_level_vehicle_is_aligned(self):
        result = gravity_in_world((1.0, 0.0, 0.0, 0.0), (0.0, 0.0, 9.81))
        self.assertAlmostEqual(result['z'], 9.81, places=6)
        self.assertAlmostEqual(result['magnitude'], 9.81, places=6)
        self.assertAlmostEqual(result['tilt_deg'], 0.0, places=6)

    def test_ninety_degree_roll_is_ninety_degrees_of_tilt(self):
        q = (math.cos(math.pi / 4), math.sin(math.pi / 4), 0.0, 0.0)  # 90 deg about x
        self.assertAlmostEqual(gravity_in_world(q, (0.0, 0.0, 9.81))['tilt_deg'], 90.0, places=4)

    def test_zero_acceleration_has_no_defined_tilt(self):
        self.assertIsNone(gravity_in_world((1.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0))['tilt_deg'])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /home/mnsuser/tevv_ws
python3 -m unittest discover -s testing/tests -p 'test_contract_stats.py' -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'contract_stats'`.

- [ ] **Step 3: Write the implementation**

Create `/home/mnsuser/tevv_ws/testing/contract_stats.py`:

```python
"""Sensor-contract measurement maths; no ROS dependency."""
import math
import statistics


def rate_stats(stamps):
    """Rate and interval jitter for a sequence of timestamps in seconds."""
    ordered = sorted(stamps)
    empty = {'count': len(ordered), 'hz': None, 'jitter_ms': None, 'min_dt': None, 'max_dt': None}
    if len(ordered) < 2: return empty
    gaps = [b - a for a, b in zip(ordered, ordered[1:])]
    mean_gap = statistics.fmean(gaps)
    return {
        'count': len(ordered),
        'hz': (1.0 / mean_gap) if mean_gap > 0 else None,
        'jitter_ms': (statistics.pstdev(gaps) * 1000.0) if len(gaps) > 1 else 0.0,
        'min_dt': min(gaps),
        'max_dt': max(gaps),
    }


def clock_report(pairs):
    """Monotonicity and sim-seconds-per-wall-second for (sim, wall) samples."""
    if len(pairs) < 2: return {'monotonic': True, 'ratio': None, 'samples': len(pairs)}
    sim = [p[0] for p in pairs]
    wall = [p[1] for p in pairs]
    monotonic = all(b >= a for a, b in zip(sim, sim[1:]))
    wall_span = wall[-1] - wall[0]
    ratio = ((sim[-1] - sim[0]) / wall_span) if wall_span > 0 else None
    return {'monotonic': monotonic, 'ratio': ratio, 'samples': len(pairs)}


def rotate(quaternion_wxyz, vector_xyz):
    """Rotate a vector by a unit quaternion given as (w, x, y, z)."""
    w, x, y, z = quaternion_wxyz
    vx, vy, vz = vector_xyz
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (vx + w * tx + (y * tz - z * ty),
            vy + w * ty + (z * tx - x * tz),
            vz + w * tz + (x * ty - y * tx))


def gravity_in_world(quaternion_wxyz, accel_body_xyz):
    """Body specific force expressed in the truth world frame, plus its tilt from +Z.

    Stationary and gravity-aligned means magnitude near 9.81 and tilt near zero.
    A large tilt means the truth orientation and the IMU disagree about which
    way is up, which invalidates every metric computed against that truth.
    """
    x, y, z = rotate(quaternion_wxyz, accel_body_xyz)
    magnitude = math.sqrt(x * x + y * y + z * z)
    tilt = math.degrees(math.acos(max(-1.0, min(1.0, z / magnitude)))) if magnitude > 0 else None
    return {'x': x, 'y': y, 'z': z, 'magnitude': magnitude, 'tilt_deg': tilt}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /home/mnsuser/tevv_ws
python3 -m unittest discover -s testing/tests -p 'test_contract_stats.py' -v
```

Expected: PASS, 14 tests. This implementation and these tests were run verbatim before this plan was written: 14 passed.

- [ ] **Step 5: Confirm the existing suite still passes**

```bash
cd /home/mnsuser/tevv_ws
python3 -m unittest discover -s testing/tests -v
```

Expected: PASS, including the pre-existing `TrajectoryTests`.

- [ ] **Step 6: Commit**

```bash
cd /home/mnsuser/tevv_ws
git add testing/contract_stats.py testing/tests/test_contract_stats.py
git commit -q -m "feat(testing): rate, clock and gravity maths for the sensor contract

Pure functions with no ROS import, so they can be tested without a ROS
environment, matching testing/trajectory.py. rate_stats reports None rather
than zero below two samples, so an absent publisher cannot be mistaken for a
0 Hz one. gravity_in_world expresses the IMU's specific force in the truth
world frame: a large tilt means truth and IMU disagree about which way is up,
which would invalidate every metric computed against that truth."
```

---

### Task 3: MAVLink reachability probe

The one measurement that decides whether sub-project D's MAVROS service is a one-line URL change or also needs a `px4-drone-1` change.

**Files:**
- Create: `/home/mnsuser/tevv_ws/testing/mavlink_reach.py`
- Test: `/home/mnsuser/tevv_ws/testing/tests/test_mavlink_reach.py`

**Interfaces:**
- Consumes: nothing
- Produces: `probe_udp(host: str, port: int, timeout: float = 5.0, payload: bytes = HEARTBEAT) -> dict` with keys `reachable` (bool), `bytes_received` (int), `error` (str or None). Also `crc_x25(data: bytes, extra: int) -> int` and module constant `HEARTBEAT: bytes`.

The checksum is computed, not hardcoded. PX4 discards a frame with a bad CRC
and would never answer, which the probe would report as "unreachable" — a
false no-go on the one measurement that decides D's cost.

- [ ] **Step 1: Write the failing tests**

Create `/home/mnsuser/tevv_ws/testing/tests/test_mavlink_reach.py`:

```python
from pathlib import Path
import socket
import sys
import threading
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mavlink_reach import probe_udp, crc_x25, HEARTBEAT


def _responder(sock, reply):
    try:
        data, peer = sock.recvfrom(2048)
        if reply: sock.sendto(reply, peer)
    except OSError:
        pass


class ProbeUdpTests(unittest.TestCase):
    def setUp(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', 0))
        self.port = self.sock.getsockname()[1]

    def tearDown(self):
        self.sock.close()

    def test_a_responding_endpoint_is_reachable(self):
        thread = threading.Thread(target=_responder, args=(self.sock, b'\xfe\x09'), daemon=True)
        thread.start()
        result = probe_udp('127.0.0.1', self.port, timeout=3.0)
        thread.join(timeout=3.0)
        self.assertTrue(result['reachable'])
        self.assertEqual(result['bytes_received'], 2)
        self.assertIsNone(result['error'])

    def test_a_silent_endpoint_is_not_reachable(self):
        thread = threading.Thread(target=_responder, args=(self.sock, None), daemon=True)
        thread.start()
        result = probe_udp('127.0.0.1', self.port, timeout=0.5)
        thread.join(timeout=3.0)
        self.assertFalse(result['reachable'])
        self.assertEqual(result['bytes_received'], 0)
        self.assertEqual(result['error'], 'timeout')

    def test_an_unresolvable_host_reports_the_error_rather_than_raising(self):
        result = probe_udp('no-such-host.invalid', 14555, timeout=0.5)
        self.assertFalse(result['reachable'])
        self.assertIsNotNone(result['error'])
        self.assertNotEqual(result['error'], 'timeout')

    def test_heartbeat_is_a_wellformed_mavlink_v1_frame(self):
        self.assertEqual(HEARTBEAT[0], 0xFE)
        self.assertEqual(HEARTBEAT[1], 9)                 # HEARTBEAT payload length
        self.assertEqual(len(HEARTBEAT), HEARTBEAT[1] + 8)  # 6 header + payload + 2 CRC

    def test_heartbeat_checksum_is_computed_over_the_frame(self):
        # The CRC covers everything after STX, then the message's CRC_EXTRA.
        expected = crc_x25(HEARTBEAT[1:-2], 50)
        self.assertEqual(HEARTBEAT[-2], expected & 0xFF)
        self.assertEqual(HEARTBEAT[-1], (expected >> 8) & 0xFF)

    def test_crc_x25_matches_a_known_vector(self):
        # MAVLink's X.25 is the BIT-REFLECTED CRC-16-CCITT, so the check value
        # over b"123456789" is 0x6F91, not the 0x906E of CCITT-FALSE. Verified
        # by running the reference crc_accumulate loop over that input.
        self.assertEqual(crc_x25(b'12345678', ord('9')), 0x6F91)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /home/mnsuser/tevv_ws
python3 -m unittest discover -s testing/tests -p 'test_mavlink_reach.py' -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'mavlink_reach'`.

- [ ] **Step 3: Write the implementation**

Create `/home/mnsuser/tevv_ws/testing/mavlink_reach.py`:

```python
"""UDP reachability probe for a MAVLink endpoint; no ROS dependency.

PX4's mavlink router in udp-client mode only starts sending once it has heard
from a peer, so an unsolicited datagram is the way to make it answer. This
sends one MAVLink v1 HEARTBEAT and waits for any datagram back. It proves the
port is open and answering on this network; it does not arm, command, or
negotiate anything.
"""
import socket

HEARTBEAT_CRC_EXTRA = 50   # message-specific seed for msgid 0
# len 9, seq 0, sysid 255, compid 190, msgid 0, then the 9-byte payload:
# custom_mode 0, type 6 (GCS), autopilot 8 (INVALID), base_mode 0,
# system_status 0, mavlink_version 3.
_HEARTBEAT_BODY = bytes([0x09, 0x00, 0xFF, 0xBE, 0x00,
                         0x00, 0x00, 0x00, 0x00, 0x06, 0x08, 0x00, 0x00, 0x03])


def crc_x25(data, extra):
    """MAVLink's X.25 checksum: bit-reflected CRC-16-CCITT, init 0xFFFF.

    Reflected, so the check value over b"123456789" is 0x6F91 and NOT the
    0x906E of CRC-16-CCITT-FALSE. Easy to get wrong, and getting it wrong
    means PX4 silently discards the frame and the probe reports a false
    "unreachable".
    """
    crc = 0xFFFF
    for byte in list(data) + [extra]:
        tmp = byte ^ (crc & 0xFF)
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


def _frame(body, extra):
    crc = crc_x25(body, extra)
    return bytes([0xFE]) + body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


HEARTBEAT = _frame(_HEARTBEAT_BODY, HEARTBEAT_CRC_EXTRA)   # 17 bytes, CRC 0x4228


def probe_udp(host, port, timeout=5.0, payload=HEARTBEAT):
    """Send one datagram and report whether anything answers within `timeout`."""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(payload, (host, port))
        data, _ = sock.recvfrom(4096)
        return {'reachable': True, 'bytes_received': len(data), 'error': None}
    except socket.timeout:
        return {'reachable': False, 'bytes_received': 0, 'error': 'timeout'}
    except OSError as exc:
        return {'reachable': False, 'bytes_received': 0, 'error': str(exc)}
    finally:
        if sock is not None: sock.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /home/mnsuser/tevv_ws
python3 -m unittest discover -s testing/tests -p 'test_mavlink_reach.py' -v
```

Expected: PASS, 6 tests.

This implementation and these tests were run verbatim before this plan was written: 6 passed. If `test_crc_x25_matches_a_known_vector` fails, the loop has been altered — MAVLink's X.25 is bit-reflected and its check value over `b"123456789"` is `0x6F91`. Fix the loop, not the expected value.

- [ ] **Step 5: Run the whole suite**

```bash
cd /home/mnsuser/tevv_ws
python3 -m unittest discover -s testing/tests -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/mnsuser/tevv_ws
git add testing/mavlink_reach.py testing/tests/test_mavlink_reach.py
git commit -q -m "feat(testing): UDP reachability probe for the PX4 MAVLink port

Whether MAVLINK_MODE=router exposes 14555 on the container network as well as
on loopback is the one thing that could still make sub-project D expensive.
The legacy mavros_d1 reaches PX4 at 127.0.0.1:14555 only because that service
is network_mode: host; a generated stack has to reach it by service name.

PX4's router in udp-client mode answers only once it has heard from a peer, so
this sends one HEARTBEAT and waits. Reachability only: no arming, no offboard,
no motion."
```

---

### Task 4: The probe node

Thin ROS wiring. All the logic is already tested; this task subscribes, collects, and serialises.

**Files:**
- Create: `/home/mnsuser/tevv_ws/testing/contract_probe.py`

**Interfaces:**
- Consumes: `contract_stats.rate_stats`, `contract_stats.clock_report`, `contract_stats.gravity_in_world`, `mavlink_reach.probe_udp`
- Produces: a CLI writing a JSON object to `--output` with top-level keys `label`, `duration_s`, `imu`, `image`, `truth`, `clock`, `camera_info`, `camera_qos`, `gravity`, `mavlink`, `verdict`. `verdict` is `{'go': bool, 'failures': list[str]}`.

- [ ] **Step 1: Write the implementation**

There is no unit test for this file. It is I/O and ROS wiring over already-tested functions, and its real verification is Task 5, which runs it against a source with a known answer. Keep it thin enough that this is true — if logic starts accumulating here, move it into `contract_stats.py` where it can be tested.

Create `/home/mnsuser/tevv_ws/testing/contract_probe.py`:

```python
"""Measure the sensor contract of a running stack and emit JSON.

Longer-lived sibling of probe.py: probe.py is a fast boolean gate run before
commanding motion, this reports numbers. Neither commands anything.
"""
import argparse
import json
import os
import sys
import time
import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import qos_profile_sensor_data, ReliabilityPolicy, DurabilityPolicy
from sensor_msgs.msg import CameraInfo, Image, Imu
from nav_msgs.msg import Odometry
from rosgraph_msgs.msg import Clock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from contract_stats import rate_stats, clock_report, gravity_in_world
from mavlink_reach import probe_udp

IMU_MIN_HZ = 100.0
IMU_TO_IMAGE_RATIO = 5.0
TILT_LIMIT_DEG = 5.0


def stamp_seconds(message): return message.header.stamp.sec + message.header.stamp.nanosec * 1e-9


def qos_of(node, topic):
    infos = node.get_publishers_info_by_topic(topic)
    return {
        'publishers': len(infos),
        'reliable': any(i.qos_profile.reliability == ReliabilityPolicy.RELIABLE for i in infos),
        'transient_local': any(i.qos_profile.durability == DurabilityPolicy.TRANSIENT_LOCAL for i in infos),
    }


def verdict_for(imu, image, camera_qos, clock, gravity):
    failures = []
    if imu['hz'] is None: failures.append('no IMU samples')
    elif imu['hz'] < IMU_MIN_HZ: failures.append(f"IMU {imu['hz']:.1f} Hz is below {IMU_MIN_HZ:.0f} Hz")
    if imu['hz'] and image['hz'] and imu['hz'] < IMU_TO_IMAGE_RATIO * image['hz']:
        failures.append(f"IMU {imu['hz']:.1f} Hz is below {IMU_TO_IMAGE_RATIO:.0f}x image {image['hz']:.1f} Hz")
    if image['hz'] is None: failures.append('no image samples')
    if not camera_qos['reliable']: failures.append('no RELIABLE camera publisher')
    if clock['samples'] < 2: failures.append('no /clock')
    elif not clock['monotonic']: failures.append('/clock is not monotonic')
    if gravity is None: failures.append('no simultaneous IMU and truth sample')
    elif gravity['tilt_deg'] is None or gravity['tilt_deg'] > TILT_LIMIT_DEG:
        failures.append(f"truth and IMU disagree on up by {gravity['tilt_deg']} deg")
    return {'go': not failures, 'failures': failures}


def main():
    parser = argparse.ArgumentParser(description='Measure a stack sensor contract.')
    parser.add_argument('--imu', required=True)
    parser.add_argument('--camera', required=True)
    parser.add_argument('--camera-info', required=True)
    parser.add_argument('--truth', required=True)
    parser.add_argument('--duration', type=float, default=30.0)
    parser.add_argument('--label', required=True, help='e.g. gazebo-control or unreal-ue582')
    parser.add_argument('--output', required=True)
    parser.add_argument('--mavlink-host', default='')
    parser.add_argument('--mavlink-port', type=int, default=14555)
    args = parser.parse_args()

    rclpy.init()
    use_sim_time = os.getenv('USE_SIM_TIME', 'true').lower() == 'true'
    node = Node('tevv_contract_probe',
                parameter_overrides=[Parameter('use_sim_time', value=use_sim_time)])
    imu_stamps, image_stamps, truth_stamps, clock_pairs = [], [], [], []
    latest = {'imu': None, 'truth': None, 'camera_info': None}

    def on_imu(m):
        imu_stamps.append(stamp_seconds(m))
        latest['imu'] = (m.linear_acceleration.x, m.linear_acceleration.y, m.linear_acceleration.z)

    def on_truth(m):
        truth_stamps.append(stamp_seconds(m))
        q = m.pose.pose.orientation
        latest['truth'] = ((q.w, q.x, q.y, q.z), m.header.frame_id, m.child_frame_id)

    def on_info(m):
        latest['camera_info'] = {'width': m.width, 'height': m.height,
                                 'k': list(m.k), 'd': list(m.d),
                                 'distortion_model': m.distortion_model}

    node.create_subscription(Imu, args.imu, on_imu, qos_profile_sensor_data)
    node.create_subscription(Image, args.camera, lambda m: image_stamps.append(stamp_seconds(m)), qos_profile_sensor_data)
    node.create_subscription(CameraInfo, args.camera_info, on_info, qos_profile_sensor_data)
    node.create_subscription(Odometry, args.truth, on_truth, qos_profile_sensor_data)
    node.create_subscription(Clock, '/clock',
                             lambda m: clock_pairs.append((m.clock.sec + m.clock.nanosec * 1e-9, time.monotonic())), 10)

    deadline = time.monotonic() + args.duration
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.05)

    camera_qos = qos_of(node, args.camera)
    imu = rate_stats(imu_stamps)
    image = rate_stats(image_stamps)
    truth = rate_stats(truth_stamps)
    clock = clock_report(clock_pairs)
    gravity = gravity_in_world(latest['truth'][0], latest['imu']) if latest['imu'] and latest['truth'] else None
    report = {
        'label': args.label,
        'duration_s': args.duration,
        'imu': imu, 'image': image, 'truth': truth, 'clock': clock,
        'camera_info': latest['camera_info'],
        'camera_qos': camera_qos,
        'truth_frames': {'frame_id': latest['truth'][1], 'child_frame_id': latest['truth'][2]} if latest['truth'] else None,
        'gravity': gravity,
        'mavlink': probe_udp(args.mavlink_host, args.mavlink_port) if args.mavlink_host else None,
        'verdict': verdict_for(imu, image, camera_qos, clock, gravity),
    }
    with open(args.output, 'w') as handle: json.dump(report, handle, indent=2, sort_keys=True)
    print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    node.destroy_node()
    rclpy.shutdown()
    raise SystemExit(0 if report['verdict']['go'] else 2)


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Check it parses and its arguments are wired**

```bash
cd /home/mnsuser/tevv_ws
python3 -c "import ast; ast.parse(open('testing/contract_probe.py').read())" && echo "syntax ok"
grep -c "add_argument" testing/contract_probe.py
```

Expected: `syntax ok`, and `10`.

- [ ] **Step 3: Verify the pure functions it calls still pass their tests**

```bash
cd /home/mnsuser/tevv_ws
python3 -m unittest discover -s testing/tests -v
```

Expected: PASS. (`contract_probe.py` itself is not importable outside a ROS environment; that is expected and is why the logic lives elsewhere.)

- [ ] **Step 4: Commit**

```bash
cd /home/mnsuser/tevv_ws
git add testing/contract_probe.py
git commit -q -m "feat(testing): contract probe node

Subscribes IMU, image, camera_info, truth and /clock, then reports rates,
jitter, camera QoS, intrinsics, clock behaviour and truth/IMU gravity
agreement as JSON, with a go/no-go verdict against thresholds fixed in the
design doc.

Deliberately thin. Every judgement it makes comes from contract_stats.py or
mavlink_reach.py, which are ROS-free and unit-tested; this file only
subscribes and serialises. Its own verification is the Gazebo control run,
where the expected numbers are already known."
```

---

### Task 5: Validate the probe against the Gazebo baseline

The control. `tevv_ws`'s README states its Gazebo stack passes headless sensor checks at 200 Hz IMU and 20 Hz camera. If the probe does not report approximately those numbers there, a bad Unreal number is uninterpretable.

**Files:**
- Create: `/home/mnsuser/tevv_ws/testing/contract.env.example`
- Modify: `/home/mnsuser/tevv_ws/compose.yaml` (add a `contract-probe` service under a `contract` profile)

**Interfaces:**
- Consumes: `contract_probe.py` from Task 4
- Produces: `results/contract-gazebo-control.json`, and a validated statement that the probe measures correctly

- [ ] **Step 1: Add the probe service to the existing compose file**

Append to the `services:` block of `/home/mnsuser/tevv_ws/compose.yaml`, matching the indentation of its siblings:

```yaml
  contract-probe:
    image: tevv-testing:humble
    profiles: [contract]
    build:
      context: .
      dockerfile: testing/Dockerfile
    network_mode: host
    ipc: host
    init: true
    environment:
      ROS_DOMAIN_ID: ${ROS_DOMAIN_ID:-42}
      ROS_LOCALHOST_ONLY: ${ROS_LOCALHOST_ONLY:-1}
      USE_SIM_TIME: ${USE_SIM_TIME:-true}
    volumes:
      - ./testing:/opt/tevv/testing:ro
      - ./results:/results
    entrypoint: ["/ros_entrypoint.sh", "python3", "/opt/tevv/testing/contract_probe.py"]
```

- [ ] **Step 2: Write the variable reference**

Create `/home/mnsuser/tevv_ws/testing/contract.env.example`:

```bash
# Copy to testing/contract.env and edit. Used by compose.contract-probe.yaml
# when measuring a generated M-S-Simulation-Runtime-Stack stack.
#
# The generated stack's network is named <compose-project>_agent_internal-<N>.
# Find it with:  docker network ls | grep agent_internal
MNS_STACK_AGENT_NETWORK=px4-xfs-xfs-scenario-single_agent_internal-1

# Must match the drone under test. The stack uses ${DRONE_1_DOMAIN_ID:-1}.
# One drone's domain per run: the per-drone bridges each publish /clock, and
# two of them in scope would race it.
ROS_DOMAIN_ID=1
ROS_LOCALHOST_ONLY=0
USE_SIM_TIME=true

# Topic names as the bridge publishes them for drone 1. Confirm against
# `make topics` in the runtime stack before each run.
CONTRACT_IMU_TOPIC=/Copter1/imu
CONTRACT_CAMERA_TOPIC=/Copter1/Camera1_Scene/image
CONTRACT_CAMERA_INFO_TOPIC=/Copter1/Camera1_Scene/camera_info
CONTRACT_TRUTH_TOPIC=/Copter1/ground_truth/odom

# PX4 MAVLink endpoint, reachable by service name on the stack network.
CONTRACT_MAVLINK_HOST=px4-drone-1
CONTRACT_MAVLINK_PORT=14555
```

- [ ] **Step 3: Build the probe image**

```bash
cd /home/mnsuser/tevv_ws
docker compose --profile contract build contract-probe
```

Expected: a successful build of `tevv-testing:humble`.

- [ ] **Step 4: Start the Gazebo stack and let it settle**

```bash
cd /home/mnsuser/tevv_ws
docker compose up -d gazebo openvins
docker compose exec gazebo /ros_entrypoint.sh python3 /opt/tevv/simulation/scripts/check_topics.py
```

Expected: the existing check reports the topics present. If it does not, stop — the baseline itself is broken and nothing measured here means anything.

- [ ] **Step 5: Run the probe against the baseline**

```bash
cd /home/mnsuser/tevv_ws
docker compose --profile contract run --rm contract-probe \
  --imu /imu0 --camera /cam0/image_raw \
  --camera-info /cam0/camera_info --truth /ground_truth/odometry \
  --duration 30 --label gazebo-control \
  --output /results/contract-gazebo-control.json
```

Expected: JSON on stdout and at `results/contract-gazebo-control.json`, with `imu.hz` near 200 and `image.hz` near 20 — the README's stated baseline.

- [ ] **Step 6: Judge the probe, not the stack**

```bash
cd /home/mnsuser/tevv_ws
python3 -c "
import json; r = json.load(open('results/contract-gazebo-control.json'))
print('imu   ', r['imu']['hz'])
print('image ', r['image']['hz'])
print('clock ', r['clock'])
print('verdict', r['verdict'])
"
```

The probe is validated when `imu.hz` is within roughly 10 percent of 200 and `image.hz` within roughly 10 percent of 20.

If they are far off, the probe is wrong and must be fixed before Task 6. The likely causes, in order: subscribing with `qos_profile_sensor_data` against a `RELIABLE`-only publisher and silently receiving nothing; counting header stamps while `use_sim_time` is false so stamps are wall time; and a `--duration` too short to establish a rate. Do not proceed to Unreal with an unvalidated probe — that is the entire purpose of this task.

- [ ] **Step 7: Tear down and commit**

```bash
cd /home/mnsuser/tevv_ws
docker compose down
git add compose.yaml testing/contract.env.example
git commit -q -m "feat(testing): contract-probe service and its variable reference

Adds a contract profile to compose.yaml so the probe can run against the
existing Gazebo stack, and documents the variables the Unreal overlay needs.

The Gazebo run is a control, not a result. The README states this stack
sustains 200 Hz IMU and 20 Hz camera, so if the probe reports those, it
measures correctly. Without that, a low Unreal number would be ambiguous
between a slow simulator and a miscounting probe, and the two live in
different repositories."
```

---

### Task 6: Measure the generated UE 5.8.2 stack

**Files:**
- Create: `/home/mnsuser/tevv_ws/compose.contract-probe.yaml`
- Create: `M-S-Simulation-Runtime-Stack/docs/integration/unreal-sensor-contract.json`
- Create: `M-S-Simulation-Runtime-Stack/docs/integration/unreal-sensor-contract.md`

**Interfaces:**
- Consumes: everything above
- Produces: the contract document that sub-projects B, C and D cite

- [ ] **Step 1: Write the overlay that joins the stack's network**

Create `/home/mnsuser/tevv_ws/compose.contract-probe.yaml`:

```yaml
# Joins a RUNNING generated M-S-Simulation-Runtime-Stack stack. Does not start one.
#
#   docker compose -f compose.yaml -f compose.contract-probe.yaml \
#     --env-file testing/contract.env --profile contract \
#     run --rm contract-probe <args>
#
# The external network name is the generated stack's, which is prefixed by its
# Compose project. That brittleness is deliberate and recorded in the design
# doc as an input to sub-project D: if this becomes a product feature, stackgen
# should emit a stable attach point instead of leaving consumers to guess.
name: tevv
services:
  contract-probe:
    network_mode: null
    networks: [stack]
    environment:
      ROS_DOMAIN_ID: ${ROS_DOMAIN_ID:?set to the drone under test, e.g. 1}
      ROS_LOCALHOST_ONLY: "0"
      USE_SIM_TIME: ${USE_SIM_TIME:-true}
networks:
  stack:
    external: true
    name: ${MNS_STACK_AGENT_NETWORK:?e.g. px4-xfs-xfs-scenario-single_agent_internal-1}
```

- [ ] **Step 2: Bring up a generated 5.8.2 stack and find its network**

```bash
cd /home/mnsuser/M-S-Simulation-Runtime-Stack
make dashboard MNS_DEMO_PACKS="--xfs --condo"
# generate and launch a scenario through the dashboard, then:
docker network ls | grep agent_internal
```

Expected: one or more `*_agent_internal-1` networks. Copy the exact name into `testing/contract.env` as `MNS_STACK_AGENT_NETWORK`.

Confirm the drone's domain and topic names rather than trusting the example file:

```bash
cd /home/mnsuser/M-S-Simulation-Runtime-Stack
python3 tools/preview_topics.py xfs-scenario | head -30
```

- [ ] **Step 3: Run the probe against the stack**

```bash
cd /home/mnsuser/tevv_ws
set -a; . testing/contract.env; set +a
docker compose -f compose.yaml -f compose.contract-probe.yaml \
  --env-file testing/contract.env --profile contract \
  run --rm contract-probe \
  --imu "$CONTRACT_IMU_TOPIC" --camera "$CONTRACT_CAMERA_TOPIC" \
  --camera-info "$CONTRACT_CAMERA_INFO_TOPIC" --truth "$CONTRACT_TRUTH_TOPIC" \
  --mavlink-host "$CONTRACT_MAVLINK_HOST" --mavlink-port "$CONTRACT_MAVLINK_PORT" \
  --duration 60 --label unreal-ue582 \
  --output /results/contract-unreal-ue582.json
```

Expected: JSON, and exit 0 (go) or 2 (no-go). Exit 2 is a valid, useful result — it is the cheap discovery this sub-project exists to make.

If nothing is received at all, check in this order: the network name matches a real network; `ROS_DOMAIN_ID` matches the drone; `ROS_LOCALHOST_ONLY` is `0` in the container (`docker compose ... run --rm contract-probe env | grep ROS_`); and the topic names match `preview_topics.py` for the scenario actually running.

- [ ] **Step 4: Record the stack's identity alongside the numbers**

The measurement is only meaningful against known images.

```bash
cd /home/mnsuser/M-S-Simulation-Runtime-Stack
mkdir -p docs/integration
cp /home/mnsuser/tevv_ws/results/contract-unreal-ue582.json /tmp/unreal.json
cp /home/mnsuser/tevv_ws/results/contract-gazebo-control.json /tmp/gazebo.json
python3 - <<'PY'
import json, subprocess
runs = {'gazebo_control': json.load(open('/tmp/gazebo.json')),
        'unreal_ue582': json.load(open('/tmp/unreal.json'))}
runs['stack'] = {
    'commit': subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip(),
    'ros2_bridge': subprocess.run(
        ['python3', '-c',
         "import yaml;d=yaml.safe_load(open('images/image-set.generated.yaml'));"
         "print(d['image_sets']['ue582']['images']['ros2_bridge'])"],
        capture_output=True, text=True).stdout.strip(),
}
json.dump(runs, open('docs/integration/unreal-sensor-contract.json', 'w'), indent=2, sort_keys=True)
print(json.dumps(runs['unreal_ue582']['verdict'], indent=2))
PY
```

- [ ] **Step 5: Write the contract document**

Create `M-S-Simulation-Runtime-Stack/docs/integration/unreal-sensor-contract.md`. Fill every value from `unreal-sensor-contract.json` — do not restate the example numbers from the design doc.

```markdown
# Unreal sensor contract, measured

Measured against: <scenario>, stack commit <sha>, ros2_bridge <pinned ref>.
Raw data: `unreal-sensor-contract.json`.

## Verdict

GO or NO-GO, and if no-go, which thresholds failed and therefore which
repository owns the fix.

## Measured

| | Gazebo control | Unreal UE 5.8.2 |
| --- | --- | --- |
| IMU rate | | |
| IMU jitter | | |
| Image rate | | |
| Image jitter | | |
| Camera RELIABLE | | |
| Truth rate | | |
| /clock monotonic | | |
| /clock sim:wall ratio | | |
| Truth/IMU tilt | | |

The Gazebo column is the control: the README claims 200 Hz IMU and 20 Hz
camera, and the probe reproducing that is what makes the Unreal column
trustworthy.

## Camera intrinsics

Resolution, K, D and distortion model as published on camera_info, plus whether
they agree with `config/unreal-airsim/xfs/settings-px4.json` (which declares
1280x720 with FOV_Degrees unset). Any disagreement is a finding: OpenVINS needs
the real intrinsics, and `testing/README.md` is explicit that Gazebo's
calibration must not be copied.

## Frames

Truth `frame_id` and `child_frame_id`, and whether truth and IMU agree on which
way is up.

## MAVLink reachability

Whether px4-drone-1:14555 answered on agent_internal-1. This decides whether
sub-project D's MAVROS service is a one-line fcu_url change or also needs a
px4-drone-1 change.

## What this obliges

- B builds images against these topic names, rates and QoS.
- C pins whatever B publishes.
- D emits services matching this domain, network and clock behaviour.

Re-measure and update this file whenever the ros2_bridge pin moves.
```

- [ ] **Step 6: Commit both repositories**

```bash
cd /home/mnsuser/tevv_ws
git add compose.contract-probe.yaml
git commit -q -m "feat(testing): overlay joining a generated runtime stack

Joins a running stack's agent_internal-N as an external network rather than
starting anything. The network name is project-prefixed and therefore brittle;
that is recorded in the design doc as an input to sub-project D."

cd /home/mnsuser/M-S-Simulation-Runtime-Stack
git add docs/integration/unreal-sensor-contract.md docs/integration/unreal-sensor-contract.json
git commit -q -m "docs(integration): measured Unreal sensor contract

The deliverable of sub-project A. Records what the generated UE 5.8.2 stack
actually publishes -- rates, jitter, camera QoS and intrinsics, clock
behaviour, truth frames and truth/IMU gravity agreement -- against a Gazebo
control run that reproduces this workspace's stated 200 Hz / 20 Hz baseline,
so the Unreal numbers can be trusted.

Sub-projects B, C and D cite this instead of re-deriving it. Re-measure when
the ros2_bridge pin moves."
```

---

## Self-Review

**Spec coverage.** Attach: Task 6 Step 1. Measure, all eight rows of the spec's table: IMU and image rate in Task 2, camera QoS in Task 4 (`qos_of`), `/clock` in Task 2 (`clock_report`), intrinsics in Task 4 (`on_info`), truth frames in Task 4 (`on_truth`), gravity in Task 2 (`gravity_in_world`), timestamp skew — **carried over from `probe.py`, which already checks it and is unchanged**; the contract document cites `probe.py` for that row rather than duplicating it. MAVLink reachability: Task 3. Record: Task 6. Testing against the Gazebo control: Task 5. No-go thresholds: Task 4 `verdict_for`, values from Global Constraints. The spec's "open question carried into B" needs no task, and Task 1 partly answers it by creating the repository B will need.

**Placeholder scan.** The contract document in Task 6 Step 5 is a template with empty table cells — that is a data-entry form for measurements that do not exist yet, not an unwritten plan step, and Step 4 produces the JSON that fills it.

**Type consistency.** `rate_stats` returns `hz`/`jitter_ms`/`min_dt`/`max_dt`/`count`, consumed as `imu['hz']`, `image['hz']` in `verdict_for` and Task 5 Step 6. `clock_report` returns `monotonic`/`ratio`/`samples`, consumed as `clock['samples']`, `clock['monotonic']`. `gravity_in_world` returns `tilt_deg`, consumed as `gravity['tilt_deg']`. `probe_udp` returns `reachable`/`bytes_received`/`error`, consumed in the `mavlink` key. `HEARTBEAT` is defined in `mavlink_reach.py` and asserted in its test. Names match across all tasks.
