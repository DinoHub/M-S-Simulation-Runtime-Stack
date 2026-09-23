#!/bin/bash
# Stage the generated stack's config where the binary expects it. The
# checkout is mounted read-only, and these are the paths the compose
# stack binds one by one.
set -e
S="/workspace/generated/${STACK}/config"
test -d "$S" || { echo "no generated stack at $S; generate it first" >&2; exit 1; }
# The image runs as uid 1000 (ue4), so / is not writable and the
# compose stack's /simrunner paths cannot be recreated here. Every
# path below is passed to the binary explicitly, so anywhere writable
# will do.
R=/tmp/simrunner
mkdir -p "$R/content-packs" "$R/mounted-runtime-asset-paks"
cp "$S/unreal-airsim/settings.json"          "$R/settings.json"
if [ -n "${AUTOPILOT_HOST:-}" ]; then
  # ArduPilot is the one connector the simulator dials out to: PX4
  # listens on TCP 4560 and the autopilot connects in, so the sim needs
  # to know nothing about it, but AirSim SENDS sensor data to
  # ArduPilot over UDP and the generated settings.json names the
  # compose hostname (ardupilot-drone-0) to send it to. Under OSMO
  # that name does not exist, so the pod's own address goes in here.
  python3 - "$R/settings.json" "$AUTOPILOT_HOST" <<'PATCH'
import json, socket, sys
path, host = sys.argv[1], sys.argv[2]
addr = socket.gethostbyname(host)
doc = json.load(open(path))
for name, vehicle in (doc.get("Vehicles") or {}).items():
    if vehicle.get("UdpIp"):
        print("%s: UdpIp %s -> %s (%s)" % (name, vehicle["UdpIp"], addr, host))
        vehicle["UdpIp"] = addr
json.dump(doc, open(path, "w"), indent=2)
PATCH
fi

cp "$S/scenario/scenario_runtime.json"       "$R/"
cp "$S/scenario/object_clutter.yaml"         "$R/"
cp "$S/scenario/environment_parameters.json" "$R/"
cp "$S/scenario-plugin/scenario_plugin.json" "$R/"
cp -r "$S/content-packs/." "$R/content-packs/"
# Same flags the generated stack uses, with the window swapped for
# off-screen rendering: a pod has no X server. Never -NullRHI, which
# skips rendering and takes the cameras with it.
exec /app/TEVVRuntimeHost/TEVVRuntimeHost.sh \
  -RenderOffScreen -NoSound -Unattended -NoSplash \
  -NoRayTracing \
  -ExecCmds='r.RayTracing 0;r.RayTracing.ForceAllRayTracingEffects 0;r.Lumen.HardwareRayTracing 0' \
  -ini:Engine:[SystemSettings]:r.Vulkan.RHIThread=0 \
  -ini:Engine:[SystemSettings]:r.PSOPrecaching=0 \
  -ini:Engine:[SystemSettings]:r.Vulkan.AllowPSOPrecaching=0 \
  -settings="$R/settings.json" \
  -ScenarioPath="$R/scenario_runtime.json" \
  -startSeed=42 \
  -MnSScenarioPluginConfig="$R/scenario_plugin.json" \
  -SimObjectClutterConfig="$R/object_clutter.yaml" \
  -SimObjectClutterSeed=42 \
  -SimObjectClutterDensity=none \
  -MnSResolvedPackSet="$R/content-packs/resolved-pack-set.json" \
  -MnSEnvironmentParameters="$R/environment_parameters.json"
