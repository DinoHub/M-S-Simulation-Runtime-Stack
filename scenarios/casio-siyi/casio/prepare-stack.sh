#!/usr/bin/env bash
# Adapt a generated casio-siyi stack for the casio-edge containers, then write
# the .env that compose.sim.yml reads.
#
#   scenarios/casio-siyi/casio/prepare-stack.sh [generated/casio-siyi]
#
# Run it after `product.sh cli runtime ... --no-run` and before `run-stack`.
# Regenerating the stack undoes steps 1-3, so run it again after every
# regeneration. It is idempotent.
#
#   1. The stack switches to CycloneDDS (casio's RMW) with this folder's
#      cyclonedds.xml, via the stack .env and config/dds/.
#   2. The bridge publishes the SIYI camera under casio's names,
#      /camera/image_raw and /camera/camera_info, instead of
#      /camera/siyi/image_raw and /camera/siyi/camera_info.
#   3. The vehicle's ROS domain is checked to be casio's (42).
#   4. casio/.env gets the stack's agent_internal-1 network name, and the
#      config and model paths from CASIO_DEPLOYMENT_DIR / CASIO_MODELS_DIR.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
stack=$(cd "${1:-$repo/generated/casio-siyi}" && pwd)
compose="$stack/docker-compose.yml"
[[ -f "$compose" ]] || { echo "no docker-compose.yml in $stack: generate the stack first" >&2; exit 1; }

set_env() {  # set_env FILE KEY VALUE: replace KEY=... or append it
  local file=$1 key=$2 value=$3
  touch "$file"
  if grep -q "^${key}=" "$file"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

# 1. CycloneDDS on the stack side. The bridge and foxglove services read both
#    variables from the stack .env and mount config/dds at /cfg/dds.
mkdir -p "$stack/config/dds"
cp "$here/cyclonedds.xml" "$stack/config/dds/cyclonedds.xml"
set_env "$stack/.env" RMW_IMPLEMENTATION rmw_cyclonedds_cpp
set_env "$stack/.env" CYCLONEDDS_URI file:///cfg/dds/cyclonedds.xml

# 2. casio's camera topic names. The bridge's native names are
#    <camera>_Scene/image and <camera>_Scene/camera_info.
names="$stack/config/topic_names.yaml"
python3 - "$names" <<'EOF'
import sys, yaml
path = sys.argv[1]
doc = yaml.safe_load(open(path)) or {}
renames = doc.get("topic_renames") or {}
renames["siyi_Scene/image"] = "camera/image_raw"
renames["siyi_Scene/camera_info"] = "camera/camera_info"
doc["topic_renames"] = dict(sorted(renames.items()))
doc.setdefault("topic_prefix", "/")
with open(path, "w") as f:
    f.write("# Bridge topic naming, with casio's camera names applied by\n"
            "# scenarios/casio-siyi/casio/prepare-stack.sh.\n")
    yaml.safe_dump(doc, f, sort_keys=False)
EOF

# 2b. casio owns map -> base_link, as on the Jetson: casio_pointcloud's
#     pose_to_tf publishes it from /mavros/global_position/local. The bridge
#     must then publish neither odom -> base_link nor map -> odom, or base_link
#     gets two parents. The bridge's switch for the first edge is a launch
#     argument the generator does not wire, so it is added to the command.
set_env "$stack/.env" LOCALIZATION_SOURCE external
if ! grep -q 'suppress_bridge_odom_tf:=' "$compose"; then
  sed -i 's|^\(\s*\)- localization_source:=\(.*\)$|&\n\1- suppress_bridge_odom_tf:=true|' "$compose"
fi
grep -q 'suppress_bridge_odom_tf:=true' "$compose" \
  || { echo "could not add suppress_bridge_odom_tf to the bridge command in $compose" >&2; exit 1; }

# 2c. Bridge image with the camera-tilt fix (TEVV-Airsim-ROS2-Bridge #82):
#     v1.0.0 publishes a settings camera's pitch as radians, so the 25 deg
#     down A8 mini shows 7.6 deg up in TF. Regeneration writes v1.0.0 back.
bridge_image=${CASIO_BRIDGE_IMAGE:-dhdevspace/auto_mns:tevv-airsim-ros2-bridge-humble-v1.0.0-camtilt.1}
set_env "$stack/.env" ROS2_IMAGE "$bridge_image"
if ! docker image inspect "$bridge_image" >/dev/null 2>&1; then
  echo "note: $bridge_image is not local yet; run-stack will pull it" >&2
fi

# 3. Domain check: casio's compose is fixed to 42.
domains=$(grep -oE 'ROS_DOMAIN_ID: *[0-9]+' "$compose" | grep -oE '[0-9]+$' | sort -u | tr '\n' ' ')
if [[ "$domains" != "42 " ]]; then
  echo "stack ROS_DOMAIN_ID is '$domains', casio uses 42: set ros_domain_id: 42 in the ScenarioSpec" >&2
  exit 1
fi

# 4. casio .env
network=$(python3 - "$compose" <<'EOF'
import sys, yaml
nets = yaml.safe_load(open(sys.argv[1])).get("networks", {})
print(nets["agent_internal-1"]["name"])
EOF
)
casio_env="$here/.env"
set_env "$casio_env" CASIO_STACK_NETWORK "$network"
# A path given in the environment wins; otherwise keep what the file has, and
# only a missing key gets the default.
for pair in CASIO_DEPLOYMENT_DIR:"$HOME/casio-config/deployment" CASIO_MODELS_DIR:"$HOME/casio-config/models"; do
  key=${pair%%:*} default=${pair#*:}
  if [[ -n "${!key:-}" ]]; then
    set_env "$casio_env" "$key" "${!key}"
  elif ! grep -q "^${key}=" "$casio_env"; then
    set_env "$casio_env" "$key" "$default"
  fi
done

echo "stack:   $stack (CycloneDDS, domain 42, /camera/image_raw)"
echo "network: $network"
echo "casio:   $casio_env"
