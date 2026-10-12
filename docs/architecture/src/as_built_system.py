"""The MnS system as built today, as a draw.io (mxGraph) file.

    python3 docs/architecture/src/as_built_system.py
    docker run --rm -v "$PWD/docs/architecture:/data" rlespinasse/drawio-export \
        -f png -e -b 20 --remove-page-suffix -o .

writes docs/architecture/as-built-system.drawio, then the PNG beside it (with
the diagram embedded, so the PNG opens in draw.io too). Plain mxGraph XML, no
third-party dependencies.

Every box is checked against code or configuration: this repository (the
Makefile, tools/, images/catalog.yaml, packs/, components/, osmo/,
docker-compose-dashboard.yml, generated stacks), MnS-Integration-Platform
platform/stacks (stackgen, mns_stacks), TEVV-Web-Dashboard and TEVV-Metrics.
Each box carries a status: built, prototype, or target only. The target
design is the vendored snapshot under docs/platform-architecture/; this
diagram is what runs.

Box heights follow the number of lines, so keep each line short (about 44
characters) and break lines with <br>.
"""
from pathlib import Path
from xml.sax.saxutils import escape

COLW, GAP, TOP, X0 = 340, 40, 100, 20
LINE, PAD, SPACE = 15, 12, 14
LANES = [
    ("Dashboard: make dashboard", "#d5e8d4", "#82b366"),
    ("You and the product shell (this repository)", "#dae8fc", "#6c8ebf"),
    ("Platform: the mns-stacks image", "#fff2cc", "#d6b656"),
    ("A generated stack: generated/&lt;name&gt;/", "#e1d5e7", "#9673a6"),
    ("Judge and report", "#f8cecc", "#b85450"),
    ("Where a campaign runs", "#f5f5f5", "#666666"),
]
STATUS = {
    "built": "",
    "prototype": "fillColor=#FBEBD2;strokeColor=#B4501E;dashed=1;",
    "target": "fillColor=#F1F1EE;strokeColor=#8A8F99;dashed=1;fontColor=#4A5160;",
}
cells, nid = [], [2]
geo = {}
cursor = [TOP + 10] * len(LANES)


def new_id():
    nid[0] += 1
    return f"c{nid[0]}"


def lane_x(i):
    return X0 + i * (COLW + GAP)


def gap_x(i):
    """x of the channel between lane i-1 and lane i."""
    return lane_x(i) - GAP // 2


def attr(value):
    return escape(value, {'"': "&quot;"})


def box(lane, value, kind="box", status="built", at=None):
    """A box at the bottom of its lane (or lower, at `at`), as tall as its lines."""
    fill, stroke = LANES[lane][1], LANES[lane][2]
    style = {
        "box": f"rounded=1;arcSize=8;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor={stroke};fontSize=11;align=left;spacingLeft=8;spacingRight=6;verticalAlign=top;spacingTop=2;",
        "file": f"shape=note;size=12;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor={stroke};fontSize=11;align=left;spacingLeft=8;verticalAlign=top;spacingTop=2;",
        "step": f"rounded=1;arcSize=20;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};fontSize=11;fontStyle=1;",
        "proc": f"shape=process;size=0.035;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor={stroke};fontSize=11;align=left;spacingLeft=16;spacingRight=10;verticalAlign=top;spacingTop=2;",
    }[kind] + STATUS[status]
    lines = value.count("<br>") + 1
    h = lines * LINE + PAD
    y = max(cursor[lane], at or 0)
    cursor[lane] = y + h + SPACE
    i = new_id()
    geo[i] = (lane_x(lane) + 12, y, COLW - 24, h)
    cells.append(f'<mxCell id="{i}" value="{attr(value)}" style="{style}" vertex="1" parent="1">'
                 f'<mxGeometry x="{lane_x(lane) + 12}" y="{y}" width="{COLW - 24}" height="{h}" as="geometry"/></mxCell>')
    return i


def edge(a, b, label="", dashed=False, exit_=None, entry=None, points=()):
    i = new_id()
    style = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;fontSize=10;endArrow=block;endFill=1;strokeColor=#444444;labelBackgroundColor=#ffffff;"
    if dashed:
        style += "dashed=1;"
    if exit_:
        style += f"exitX={exit_[0]};exitY={exit_[1]};exitDx=0;exitDy=0;"
    if entry:
        style += f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
    pts = ""
    if points:
        pts = '<Array as="points">' + "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in points) + "</Array>"
    cells.append(f'<mxCell id="{i}" value="{attr(label)}" style="{style}" edge="1" parent="1" source="{a}" target="{b}">'
                 f'<mxGeometry relative="1" as="geometry">{pts}</mxGeometry></mxCell>')


def loop(a, b, x):
    """One waypoint at x, halfway down between a and b: a side loop."""
    ya, yb = geo[a][1] + geo[a][3] / 2, geo[b][1] + geo[b][3] / 2
    return ((x, (ya + yb) / 2),)


def text(x, y, w, h, value, size=11, bold=False, color="#222222"):
    i = new_id()
    style = (f"text;html=1;fontSize={size};align=left;verticalAlign=top;whiteSpace=wrap;fontColor={color};"
             + ("fontStyle=1;" if bold else ""))
    cells.append(f'<mxCell id="{i}" value="{attr(value)}" style="{style}" vertex="1" parent="1">'
                 f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
    return i


def note(lane, value, lines):
    y = cursor[lane]
    cursor[lane] = y + lines * 14 + SPACE
    return text(lane_x(lane) + 12, y, COLW - 24, lines * 14, value, size=10, color="#555555")


DASH, SHELL, PLAT, STACK, JUDGE, WHERE = range(6)

# ---- Dashboard ---------------------------------------------------------------
fe = box(DASH, "<b>dashboard-frontend</b> :3001<br>the scenario wizard:<br>"
               "0 Content · 1a Author · 1b Generate<br>1c ROS 2 · 1d Metrics · <b>1e Campaign</b><br>"
               "2 Run · 3 Analysis<br>Monitor: Run events, Run metrics")
camp = box(DASH, "<b>Campaign step</b> (1e)<br>components under test, the mission,<br>"
                 "start (steady streams, then a delay) and end,<br>sweeps × seeds × repeats,<br>"
                 "what each run is judged on<br>→ a CampaignSpec; Run campaign<br>"
                 "→ one progress row per run, with its verdict")
be = box(DASH, "<b>dashboard-backend</b> :8001 (FastAPI)<br>docker.sock: runs mns-stacks, mns-packs<br>"
               "and ScenarioLab as sibling containers;<br>imports no platform code<br>"
               "/api/campaign/* · /api/metrics (→ :8770)", "proc")
rt = box(DASH, "<b>ros2-tools</b> (created by the backend)<br>the bridge image, on the stack's ROS domain:<br>"
               "Foxglove websocket :8764, bag replay", "proc")
lb = box(DASH, "<b>dashboard-lichtblick</b> :8082<br>the topic viewer")
db = box(DASH, "<b>postgres-telemetry</b> (TimescaleDB)<br>only with make dashboard DB=true")
note(DASH, "The Campaign step, and the Run step that replaces Launch and Runtime, are on the dashboard "
           "branch feat/live-stack-viewer and run from local images (tevv-web-dashboard-*-v1.0.0-live.8 "
           "and later). The catalog still pins the release/v1.0.0-next build: 2 Launch, 3 Runtime, "
           "4 Analysis, no Campaign step.", 6)

# ---- You and the product shell -------------------------------------------------
you = box(SHELL, "<b>You</b>: a browser on http://localhost:3001,<br>or a terminal in this checkout")
make = box(SHELL, "<b>make</b> (Makefile)<br>dashboard · dashboard-down<br>fly (RECORD=1, KEEP=1) · evaluate · stop<br>"
                  "campaign · campaign-status<br>author · stacks · topics<br>doctor · ensure-images · verify-images", "proc")
tools = box(SHELL, "<b>tools/</b><br>fly.sh · evaluate.sh: generate, wait for the<br>"
                   "components, record, fly, stop, the verdict<br>mns-stacks.sh: mns-stacks as a sibling<br>"
                   "container, host paths mounted<br>images.sh sync · verify · bump", "proc")
scen = box(SHELL, "<b>scenarios/&lt;name&gt;/</b><br>ScenarioSpec.yaml (+ includes)<br>CampaignSpec.yaml · routes/", "file")
comps = box(SHELL, "<b>components/&lt;id&gt;/component.yaml</b><br>consumes · produces · ready · services<br>"
                   "scoring · latency<br>estimators: mac-vo, openvins<br>planners: mighty, super<br>"
                   "perception: casio-edge<br>camera realism: siyi-a8-realism", "file")
lab = box(SHELL, "<b>ScenarioLab</b> (the authoring image)<br>the Unreal editor on your X display;<br>"
                 "exports a ScenarioSpec", "proc")
cat = box(SHELL, "<b>images/catalog.yaml</b><br>one image catalog: Docker Hub<br>dhdevspace/auto_mns, tag + digest<br>"
                 "→ images/*.generated.env, image sets<br>→ a generated stack's .env pins", "file")
lock = box(SHELL, "<b>packs/v1.0.0.lock.json</b><br>level and object pack versions", "file")
store = box(SHELL, "<b>.mns/v1/pack-store/</b><br>installed by mns-packs (download-packs.sh,<br>"
                   "the Content step); mounted into the<br>runtime host and ScenarioLab", "file")

# ---- Platform: mns-stacks --------------------------------------------------------
cli = box(PLAT, "<b>mns-stacks</b> (cli.py): one command per container<br>generate · run · stop · record · score<br>"
                "report · campaign · component · status", "step")
camp2 = box(PLAT, "<b>campaign</b> (campaign.py)<br>init · validate · preflight · plan · run<br>"
                  "status · watch · cancel<br>per run: materialize a ScenarioSpec,<br>generate it with its components,<br>"
                  "fly it (run --until-done), stop<br>then the campaign's evaluator<br>"
                  "→ campaign_manifest.json, progress.jsonl", "proc")
sg = box(PLAT, "<b>generate</b> (stackgen)<br>the ScenarioSpec + image set + pack store<br>"
               "<b>components.py</b>: attach --component packages,<br>outside the ScenarioSpec, with adaptors<br>"
               "<b>evaluation_catalog.yaml</b>: what runs are<br>judged on<br>"
               "<b>metrics_catalog.yaml</b>: simulator detectors<br><b>zones.py</b>: keep-out, keep-in, coverage", "proc")
dirc = box(PLAT, "<b>run --until-done</b>: the run director", "step")
states = box(PLAT, "<b>director.py</b> states<br>VALIDATE → STACK_UP<br>→ WAIT_HEALTHY: the simulator healthcheck<br>"
                   "→ <b>WAIT_READY</b>: each component's ready topics<br>→ RECORD_START: warm-up until streams<br>"
                   "are steady, then record (--topics @scoring)<br>→ MISSION_START: start delay, then a QGC<br>"
                   "plan, an external start-cmd, or none<br>→ WAIT_DONE: landed · topic · event · timeout<br>"
                   "→ RECORD_STOP → HANDOFF → TEARDOWN", "proc")
rec = box(PLAT, "<b>record start | stop</b> (recorder.py)<br>ros2 bag record inside the bridge container;<br>"
                "SIGINT, so rosbag2 writes metadata.yaml", "proc")
stop = box(PLAT, "<b>stop</b> (lifecycle.py)<br>graceful simulator stop, finalize_metrics,<br>"
                 "compose down, the run's metrics copied", "proc")
score = box(PLAT, "<b>score</b> (scoring.py), each attached component<br>built in: estimate (ATE, RPE, drift),<br>"
                  "planner (goals, clearance, jerk), latency<br>(input to output, from the bag),<br>"
                  "or the component's own scorer image", "proc")
rep = box(PLAT, "<b>report</b>: the verdict table<br><b>campaign status</b>: the scorecard")
note(PLAT, "Component attach, WAIT_READY, scoring and the campaign's start and end conditions are on the "
           "platform branch feat/components and run from local mns-stacks-v1.0.0-rc.services.N images; "
           "the catalog pins mns-stacks-v1.0.0-rc.", 5)

# ---- Generated stack ---------------------------------------------------------------
files = box(STACK, "<b>docker-compose.yml</b>, <b>.env</b> (image pins,<br>MNS_METRICS_ARGS)<br>"
                   "config/stack/contract.json (topic roles)<br>config/metrics/evaluation.yaml, zones.json<br>"
                   "config/topic_names.yaml<br>generated-manifest.json", "file")
host = box(STACK, "<b>unreal-airsim</b>: the runtime host<br>Unreal Engine 5.8 + Cosys-AirSim<br>"
                  "MetricsEmitter plugin: pose, collisions,<br>clearance, zones, run.ended", "proc", at=geo[states][1])
sitl = box(STACK, "<b>px4-drone-N</b> or <b>ardupilot-drone-N</b><br>autopilot SITL, one per vehicle", "proc")
bridge = box(STACK, "<b>airsim_bridge_&lt;vehicle&gt;</b><br>the ROS 2 bridge, with MAVROS;<br>"
                    "the recorder runs here", "proc")
fox = box(STACK, "<b>foxglove_bridge_&lt;vehicle&gt;</b> :8765 and up<br><b>iceoryx-init</b>: camera shared memory")
csv = box(STACK, "<b>component services</b> &lt;id&gt;_&lt;service&gt;<br>from component.yaml, with adaptors:<br>"
                 "mac_vo_{config, macvo, adaptor}<br>openvins_ov_msckf · mighty_planner<br>"
                 "super_planner · casio_edge_*<br>siyi_a8_realism_realism", "proc")
opt = box(STACK, "optional: qgroundcontrol-x11,<br>sim-real-eval-worker")
bag = box(STACK, "<b>&lt;runs&gt;/&lt;run id&gt;/bag/</b>, run.json", "file")
outm = box(STACK, "<b>outputs/metrics/&lt;run&gt;/</b><br>events.jsonl, manifest.json<br>components/&lt;id&gt;.json<br>"
                  "<b>outputs/components/&lt;id&gt;/</b> (/mns/out)", "file")

# ---- Judge and report ----------------------------------------------------------------
ms = box(JUDGE, "<b>mns-metrics-service</b> :8770<br>TEVV-Metrics jsonl ingestor, watch<br>"
                "(in docker-compose-dashboard.yml)<br>reads generated/**/outputs/metrics/<br>"
                "&lt;run&gt;/events.jsonl, campaign stacks too<br>thresholds: the stack's evaluation.yaml<br>"
                "zones: the stack's zones.json<br>component results: components/&lt;id&gt;.json<br>"
                "→ mission checks + component checks", "proc")
res = box(JUDGE, "<b>runs/&lt;stack&gt;/&lt;run&gt;/metrics/</b><br>evaluated.json: PASS or FAIL, and why<br>"
                 "metrics.json · run_summary.json", "file")
dv = box(JUDGE, "<b>Dashboard</b>: Monitor → Run metrics,<br>Analysis, the Campaign step's progress rows")
cv = box(JUDGE, "<b>Terminal</b>: make evaluate's verdict,<br>mns-stacks report, campaign status")
live = box(JUDGE, "<b>Live telemetry, outside the product</b><br>the metrics_kafka_bridge compose<br>"
                  "(Cosys_Airsim_Exploration/tooling):<br>Kafka · ClickHouse · Grafana :3000 · MinIO<br>"
                  "the dashboard embeds Grafana (GRAFANA_URL)", status="prototype")

# ---- Where a campaign runs ------------------------------------------------------------
loc = box(WHERE, "<b>Local Docker Compose</b><br>make fly · evaluate · campaign, the dashboard;<br>"
                 "one campaign at a time (campaign.lock)")
osmo = box(WHERE, "<b>OSMO on a local kind cluster</b> (osmo/)<br>setup-local-osmo.sh: KAI scheduler,<br>"
                  "GPU operator, OSMO 6.3.1<br>campaign.py run: mns-stacks campaign plan<br>"
                  "and generate, then one workflow per run<br>(sim-bridge-vio.workflow.yaml):<br>"
                  "groups run → evaluate → aggregate", "proc")
obs = box(WHERE, "<b>OSMO observability</b> (osmo/observability)<br>Loki + Alloy: every task's logs, :31100<br>"
                 "run registry: Postgres (CNPG)<br>Grafana: campaign progress, run logs")
gap = box(WHERE, "Under OSMO: attached component packages<br>and the metrics service", status="target")
argo = box(WHERE, "<b>Argo Workflows on OpenShift</b><br>design and run prototype, PR #113<br>"
                  "(argo/workflows/tevv-campaign-run.yaml)", status="prototype")

# ---- edges ---------------------------------------------------------------------------
edge(you, fe, exit_=(0, 0.5), entry=(1, 0.15))
edge(you, make)
edge(make, tools)
edge(fe, camp)
edge(camp, be)
edge(rt, lb, "foxglove ws")
edge(camp, scen, "CampaignSpec.yaml", dashed=True, exit_=(1, 0.85), entry=(0, 0.3))
edge(lab, scen, "export", exit_=(0, 0.5), entry=(0, 0.8), points=loop(lab, scen, lane_x(SHELL) + 5))
edge(lock, store, "mns-packs install", dashed=True)
# the backend's sibling containers: over the top of the shell lane
edge(be, cli, "docker run", exit_=(1, 0.3), entry=(0, 0.3),
     points=((gap_x(SHELL), TOP + 3), (gap_x(PLAT), TOP + 3)))
edge(tools, cli, "docker run", exit_=(1, 0.5), entry=(0, 0.75))
edge(scen, sg, exit_=(1, 0.5), entry=(0, 0.3))
edge(comps, sg, "--component", exit_=(1, 0.3), entry=(0, 0.7))
edge(cli, camp2)
edge(camp2, sg, "each run")
edge(camp2, dirc, "each run", exit_=(1, 0.5), entry=(1, 0.5), points=loop(camp2, dirc, gap_x(STACK) - 8))
edge(sg, files, exit_=(1, 0.2), entry=(0, 0.3))
edge(dirc, states)
edge(states, host, "compose up", exit_=(1, 0.12), entry=(0, 0.3))
edge(states, csv, "ready topics", dashed=True, exit_=(1, 0.4), entry=(0, 0.3))
edge(states, rec)
edge(rec, bridge, "ros2 bag record", exit_=(1, 0.5), entry=(0, 0.7))
edge(rec, stop)
edge(stop, score)
edge(bridge, bag, exit_=(1, 0.5), entry=(1, 0.5))
edge(bag, score, "bag", exit_=(0, 0.5), entry=(1, 0.3))
edge(score, outm, "components/&lt;id&gt;.json", exit_=(1, 0.8), entry=(0, 0.5))
edge(host, outm, "events", exit_=(1, 0.7), entry=(1, 0.3), points=loop(host, outm, gap_x(JUDGE) - 10))
edge(csv, outm, "/mns/out", exit_=(1, 0.8), entry=(1, 0.75), points=loop(csv, outm, gap_x(JUDGE) - 10))
edge(outm, ms, "events, results", exit_=(1, 0.5), entry=(0, 0.75), points=loop(outm, ms, gap_x(JUDGE) + 6))
edge(files, ms, "evaluation.yaml, zones.json", dashed=True, exit_=(1, 0.5), entry=(0, 0.2))
edge(ms, res)
edge(res, dv)
edge(dv, cv)
edge(loc, osmo, "the same CampaignSpec", dashed=True)
edge(osmo, obs)
edge(obs, gap, dashed=True)

# ---- title, lanes, legend ------------------------------------------------------------------
WIDTH = 6 * COLW + 5 * GAP
H = max(cursor) - TOP + 20
for n, (name, fill, stroke) in enumerate(LANES):
    i = new_id()
    cells.insert(0, f'<mxCell id="{i}" value="{attr(name)}" style="swimlane;startSize=30;html=1;fillColor={fill};strokeColor={stroke};fontStyle=1;fontSize=13;swimlaneFillColor=#fbfbfb;rounded=1;arcSize=2;" vertex="1" parent="1">'
                    f'<mxGeometry x="{lane_x(n)}" y="{TOP - 30}" width="{COLW}" height="{H + 30}" as="geometry"/></mxCell>')
text(X0, 0, 1400, 28, "MnS as built, 4 Oct 2026: a scenario and its components to a judged run, from a browser or a terminal",
     size=17, bold=True)
text(X0, 30, WIDTH, 34,
     "Each box is an image pinned in images/catalog.yaml, or code or a file in the repository it names. "
     "The target design is the vendored snapshot in docs/platform-architecture/; this page is what runs today.")
LY = TOP + H + 24
text(X0, LY + 4, 80, 24, "Legend", size=12, bold=True)
lx = X0 + 80
for label, status in (("built", "built"), ("prototype", "prototype"), ("target only", "target")):
    i = new_id()
    style = "rounded=1;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#444444;fontSize=11;" + STATUS[status]
    cells.append(f'<mxCell id="{i}" value="{label}" style="{style}" vertex="1" parent="1">'
                 f'<mxGeometry x="{lx}" y="{LY}" width="110" height="28" as="geometry"/></mxCell>')
    lx += 124
text(lx + 10, LY, WIDTH - lx, 40,
     "Solid arrow: data or a command · dashed arrow: configuration or a reference · folded corner: a file · "
     "double edge: a process or a container. Lane colours only group the boxes; the status is the box style.")

PAGE_W = X0 * 2 + WIDTH
PAGE_H = LY + 50
xml = ('<mxfile host="Electron" type="device"><diagram id="mns-as-built" name="As built">'
       f'<mxGraphModel dx="1600" dy="1100" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="{PAGE_W}" pageHeight="{PAGE_H}" background="#ffffff" math="0" shadow="0">'
       '<root><mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(cells) + '</root></mxGraphModel></diagram></mxfile>')
out = Path(__file__).resolve().parent.parent / "as-built-system.drawio"
out.write_text(xml, encoding="utf-8")
print(f"{out}: {len(cells)} cells")
