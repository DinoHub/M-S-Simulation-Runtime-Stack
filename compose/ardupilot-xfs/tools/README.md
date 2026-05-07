# tools/

## generate_compose.py

Vendored copy of `generate_compose.py` from Cosys-AirSim's
`feat/multi-agent-ros2-zenoh-architecture` branch:
`integrations/ros2/docker/Linux/multi-agent/generate_compose.py`.

It produces `docker-compose.bridges.yml` (one directory up) for the
per-drone AirSim ROS2 bridge stack that pairs with our
`agent_internal-N` networks.

### Refresh

When the upstream generator changes, refresh:

```bash
git -C /path/to/Cosys-AirSim show \
  feat/multi-agent-ros2-zenoh-architecture:integrations/ros2/docker/Linux/multi-agent/generate_compose.py \
  > tools/generate_compose.py
```

Then update the `Vendored at:` SHA in the header comment of
`tools/generate_compose.py` to match the new upstream tip.

### Regenerate the compose

```bash
python3 tools/generate_compose.py --drones 4 --mode integration \
  --vehicle-prefix Copter --output docker-compose.bridges.yml
```

This is what `../test-per-drone-bridges.sh` does automatically. It
prefers this vendored copy and falls back to extracting from the
Cosys-AirSim repo only if the vendored copy is missing.

### Self-test

The generator's `_self_test()` covers both standalone and integration
modes plus the `--vehicle-prefix` flag:

```bash
python3 -c "import sys; sys.path.insert(0, 'tools'); import generate_compose; generate_compose._self_test()"
```

Expected output: `self_test: OK`.
