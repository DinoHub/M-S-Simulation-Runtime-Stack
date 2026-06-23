# Monitoring & observability plane

Three cross-cutting overlays sit alongside any sim scenario. All share the
`monitoring-internal` network and the baked Grafana/Prometheus images (config is
built into the images — `docker compose up` builds them, no separate step).

| Plane | Compose file | What it adds |
|-------|--------------|--------------|
| **Metrics** | `docker-compose-metrics.yml` | `metrics-collector` benchmark runs → ES `run-summaries` (see `config/metrics-collector/README.md`) |
| **Monitoring** | `docker-compose-monitoring.yml` | Prometheus + Grafana + exporters + Elasticsearch + foxglove/lichtblick |
| **Logs** | `docker-compose-logs.yml` | Loki + Grafana Alloy (container/ROS log aggregation) |

## Run it

Via the launcher (idempotent; brings up sim + the chosen overlays):

```bash
./launch.sh px4-safticity --with-monitoring        # sim + monitoring plane
./launch.sh px4-safticity --all                    # + metrics plane (--with-monitoring + --with-metrics)
# .env equivalents: START_MONITORING=true / START_METRICS=true
```

Standalone (monitoring only, against an already-running sim or with mock data):

```bash
# real sim already up:
CONFIG_ROOT=$PWD/config docker compose -f docker-compose-monitoring.yml --profile monitoring up -d
# dashboard testing with synthetic data, no sim:
docker compose -f docker-compose-monitoring.yml --profile mock-testing up -d
# minimal (prometheus + grafana + airsim-exporter only):
docker compose -f docker-compose-monitoring.yml --profile monitoring-minimal up -d
```

Logs plane overlay:

```bash
CONFIG_ROOT=$PWD/config docker compose -f docker-compose-logs.yml up -d
```

## Endpoints

| Service | URL | Notes |
|---------|-----|-------|
| **Grafana** | http://localhost:3000 | dashboards (TEVV exploration), datasources for Prometheus/ES/Loki |
| Prometheus | http://localhost:9090 | metric store + targets |
| Elasticsearch | http://localhost:9210 | (container `:9200`) — `run-summaries` index = benchmark results |
| Pushgateway | http://localhost:9091 | batch/job metrics sink |
| node / dcgm / cadvisor | :9100 / :9400 / :8085 | host CPU / GPU / container metrics |
| ros2 / airsim / px4 exporters | :9200 / :9201 / :9202 | sim-side telemetry → Prometheus |
| Lichtblick | http://localhost:8082 | web visualizer (foxglove-bridge feeds it) |

## Data flow

```
exporters (node/dcgm/cadvisor/ros2/airsim/px4) ─┐
pushgateway (batch jobs) ───────────────────────┼─▶ Prometheus ─┐
                                                                 ├─▶ Grafana (dashboards)
metrics-collector ─▶ Elasticsearch (run-summaries) ──────────────┤
container/ROS logs ─▶ Alloy ─▶ Loki ─────────────────────────────┘
```

Benchmark runs (the metrics plane) land in ES keyed by `run_id`; the Grafana TEVV
dashboard reads them for per-run verdict/path/time comparison. Live host/sim telemetry
goes through Prometheus.

## Gotchas

- **ES disk flood-stage (the common ingest failure).** If the host disk is >95%,
  Elasticsearch sets a `read-only-allow-delete` block on every index and writes fail with
  `429 ... TOO_MANY_REQUESTS/12/... disk usage exceeded flood-stage watermark`. Symptom:
  metrics runs compute + PASS but never appear in Grafana.
  Fix (preferred) — free disk, block auto-releases:
  ```bash
  docker system df && docker system prune -f      # then re-run ingest_to_es.py
  ```
  Override (dev box, when free space is fine in absolute terms but % is over threshold) —
  raise the watermarks + clear the existing block:
  ```bash
  curl -XPUT localhost:9210/_cluster/settings -H 'Content-Type: application/json' -d '{
    "transient":{"cluster.routing.allocation.disk.watermark.low":"20gb",
    "cluster.routing.allocation.disk.watermark.high":"15gb",
    "cluster.routing.allocation.disk.watermark.flood_stage":"10gb"}}'
  curl -XPUT localhost:9210/run-summaries/_settings -H 'Content-Type: application/json' \
    -d '{"index.blocks.read_only_allow_delete": null}'
  ```
- Grafana/Prometheus config is **baked into the images** — to change dashboards/rules,
  rebuild the image (`docker compose ... build grafana`), not just restart.
- Networks (`airsim-ecosystem`, `ros2-multi-node-network`) are `external` — created by the
  sim launch. Bring a scenario up first, or the monitoring compose errors on missing nets.
