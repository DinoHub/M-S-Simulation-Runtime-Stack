#!/usr/bin/env bash
# Download the MnS content packs (Unreal levels and object packs) and make
# them visible to ScenarioLab. Run ./setup.sh first.
#
#   ./download-packs.sh --list                 what is available and installed
#   ./download-packs.sh                        the starter set: Warehouse + vehicle models (~0.8 GB)
#   ./download-packs.sh --all                  every pack (~7.7 GB)
#   ./download-packs.sh --warehouse --office   only these (vehicle models always added)
#   ./download-packs.sh --objects              every object pack
#
# Safe to re-run: packs already installed are skipped, and an interrupted
# download resumes. CHANNEL=<name> picks another release line, as it does
# for `make dashboard`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; }

LIST_ONLY=false
SELECTION=()
for arg in "$@"; do
  case "$arg" in
    --list) LIST_ONLY=true ;;
    -h|--help) usage; exit 0 ;;
    --*) SELECTION+=("$arg") ;;
    *) echo "Unknown argument: $arg" >&2; usage >&2; exit 2 ;;
  esac
done
# The starter set: the smallest level ScenarioLab can open (Warehouse, the
# one the user guide walks through); the vehicle models are added below.
# Runtime-only levels (Condo, XFS, ...) are left out: ScenarioLab cannot open
# them, so they never show in its level list. Big packs are opt-in; GitHub
# releases download slowly.
STARTER=(--warehouse)
STARTER_USED=false
if [[ ${#SELECTION[@]} -eq 0 ]]; then SELECTION=("${STARTER[@]}"); STARTER_USED=true; fi

# The channel's lock, contracts and pack roots, exactly as make dashboard uses them.
eval "$(make -s --no-print-directory print-channel-env)"

# One pass over the lock: validate the selection, add the vehicle models,
# size it, and render the --list table (installed state from the store).
installed=$(./tools/install-demo-packs.sh --check --all 2>/dev/null | sed -n 's/^ *installed: .*(\(.*\))$/\1/p' || true)
plan=$(SELECTION="${SELECTION[*]}" INSTALLED="$installed" LIST_ONLY="$LIST_ONLY" python3 - <<'PY'
import json, os, sys
lock = json.load(open(os.environ["MNS_DEMO_PACK_LOCK"]))
contract = os.environ.get("MNS_AUTHORING_HOST_CONTRACT")
editor = {p["id"] for p in json.load(open(contract))["plugins"]} if contract else None
installed = set(os.environ["INSTALLED"].split())
packs = lock["packs"]
known = {p["selection"] for p in packs}
names = [a[2:] for a in os.environ["SELECTION"].split()]

if os.environ["LIST_ONLY"] == "true":
    print(f"Packs for channel {os.environ['MNS_CHANNEL']} ({os.path.basename(os.environ['MNS_DEMO_PACK_LOCK'])}):", file=sys.stderr)
    print(f"  {'option':<26} {'kind':<7} {'size':>8}  {'status':<10} ScenarioLab", file=sys.stderr)
    for p in packs:
        need = set(p.get("required_plugins", []))
        where = "opens it" if editor is None or need <= editor else "runtime only"
        state = "installed" if p["selection"] in installed else "-"
        print(f"  --{p['selection']:<24} {p['kind']:<7} {p['size_bytes'] / 1e9:>6.2f} GB  {state:<10} {where}", file=sys.stderr)
    print(f"  {'--all':<26} {'':<7} {sum(p['size_bytes'] for p in packs) / 1e9:>6.2f} GB", file=sys.stderr)
    sys.exit(0)

bad = [n for n in names if n not in known | {"all", "objects"}]
if bad:
    sys.exit("unknown pack(s): " + " ".join("--" + b for b in bad)
             + "\nChoose from: " + " ".join("--" + k for k in sorted(known)) + "  (or --all, --objects)")
# ScenarioLab cannot place a drone without the vehicle models.
if "all" not in names and "mns_vehicle_models" in known and "mns_vehicle_models" not in names:
    names.append("mns_vehicle_models")
chosen = [p for p in packs if "all" in names or p["selection"] in names
          or ("objects" in names and p["kind"] == "asset")]
todo = [p for p in chosen if p["selection"] not in installed]
print(" ".join("--" + n for n in names))
print(round(sum(p["size_bytes"] for p in todo) / 1e9, 2))
print(len(chosen) - len(todo))
PY
) || { [[ -n "$plan" ]] && echo "$plan" >&2; exit 2; }
if [[ "$LIST_ONLY" == true ]]; then
  echo
  echo "Download: ./download-packs.sh (starter: Warehouse)  ·  --all  ·  or name packs, e.g. --office --xfs"
  exit 0
fi
{ read -r PACKS; read -r NEED_GB; read -r ALREADY; } <<<"$plan"

echo "========================================"
echo " MnS content packs (channel $MNS_CHANNEL)"
echo "========================================"
echo "  selection: $PACKS"
[[ "$STARTER_USED" == true ]] && echo "  (the starter set; ./download-packs.sh --all for everything, --list to choose)"
echo "  to download: ${NEED_GB} GB ($ALREADY already installed)"

# GitHub credentials: the pack releases are private (DinoHub/TEVV-Airsim).
if [[ "$NEED_GB" != "0.0" && "$NEED_GB" != "0" ]]; then
  if [[ -z "${GH_TOKEN:-}${GITHUB_TOKEN:-}" ]] && ! { command -v gh >/dev/null 2>&1 && gh auth token >/dev/null 2>&1; }; then
    if command -v gh >/dev/null 2>&1 && [[ -t 0 && -t 1 ]]; then
      echo "  GitHub login needed for the MnS content packs:"
      gh auth login --hostname github.com --git-protocol https --web
    else
      echo "  ✗ no GitHub credentials  →  gh auth login   (or: export GH_TOKEN=<token>)" >&2
      command -v gh >/dev/null 2>&1 || echo "    the GitHub CLI is not installed: sudo apt-get install -y gh" >&2
      exit 1
    fi
  fi
  # Archive + store copy exist side by side while a pack installs.
  need=$(python3 -c "import math; print(math.ceil(2 * $NEED_GB + 1))")
  free=$(df -Pk "$SCRIPT_DIR" | awk 'NR==2 {printf "%d", $4/1024/1024}')
  if [[ "$free" -lt "$need" ]]; then
    echo "  ✗ only ${free} GB free; installing needs about ${need} GB. Free space, or choose fewer packs (--list)." >&2
    exit 1
  fi
fi

echo
if ! make --no-print-directory stage-authoring-packs MNS_DEMO_PACKS="$PACKS"; then
  echo
  echo "Pack download failed; the install-demo-packs line above says why. A network"
  echo "drop only needs ./download-packs.sh again (installed packs are kept, and a"
  echo "partial download resumes); a 404 means your GitHub account cannot read the"
  echo "pack releases (ask your MnS contact)."
  exit 1
fi

echo
echo "========================================"
echo " Packs ready"
echo "========================================"
echo "Next: make dashboard   (from a terminal on the desktop), then http://localhost:3001"
