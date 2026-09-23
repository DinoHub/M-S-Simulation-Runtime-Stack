# Pixel Streaming for OSMO runs — design, not built

Status: scoped. Nothing here exists yet. The live view that does exist is
Foxglove (`docs/osmo-runbook.md`, "Watching a run"): the vehicle's cameras,
TF and trajectories. Pixel Streaming would add the simulator's own viewport —
a spectator you can move — which Foxglove cannot give.

## Why it cannot simply be switched on

- The v1 runtime host image (`tevv-runtime-host-v1.0.0`, UE 5.8.2) contains
  no Pixel Streaming plugin: no `*PixelStreaming*` anywhere in the image.
- `TEVVRuntimeHost.uproject` (TEVV-Airsim, `projects/TEVVRuntimeHost/`)
  does not enable one. Its plugin list is AirSim, the Scenario* and Metrics*
  plugins, PCG and a few engine plugins.
- The only Pixel Streaming in the catalog is the legacy UE 5.5 stack:
  `tevv-pixel-streaming-signalling-5.5`, dialled by the legacy images via
  `--with-pixel-streaming`. It does not match a 5.8 streamer.

So this is a runtime-host build, in TEVV-Airsim, plus new deployment pieces
here.

## The work, in order

### 1. Host build (TEVV-Airsim)

Enable UE 5.8's `PixelStreaming2` engine plugin in `TEVVRuntimeHost.uproject`,
rebuild and package through the project's package hook, publish a new host
image.

Compatibility with existing level packs: the host id
(`ue-5.8.2-cl56702186-linux-development-vulkan-sm6-iostore-v2-render-35e7a9a52ee2ef15`)
is engine, changelist, target and container format plus a hash of the
declared renderer settings (`docs/ue582-renderer-compatibility.md` in
TEVV-Airsim) — not the plugin list. Adding a plugin should therefore leave the
id, and every cooked level pack, valid. But the frozen contract
(`packs/runtime-host-compatibility.v1.json`) also carries a `plugins`
inventory, and **whether the pack verifier compares plugin inventories must be
confirmed before publishing** — if it does, every pack needs re-verifying
against the new host.

Keep it off unless asked: the streamer should start only when the host is
launched with `-PixelStreamingURL=...`, so scored runs render exactly as today.

### 2. Signalling server

A 5.8-era signalling server from Epic's PixelStreamingInfrastructure (the
"Wilbur" SignallingWebServer on the branch matching the engine), built as a
new catalog image. It serves the player page and brokers the WebRTC session.
In the workflow: one more task in the `run` group, gated like `viz`.

### 3. The streamer in the pod

- NVENC: `NVIDIA_DRIVER_CAPABILITIES=all` is already set on the sim task.
- The sim task adds `-PixelStreamingURL=ws://<signalling task IP>:8888` when
  the flag is on; the address is resolved the way the other tasks resolve
  theirs (a DNS name from `{{host:...}}` turned into an IP first).
- Signalling HTTP/WebSocket reaches the host through the same
  `kubectl port-forward` that `osmo/campaign.py watch` uses.

### 4. The hard part: the media path

Signalling is one TCP port; the video is WebRTC, which is UDP to ICE
candidates. Pod IPs (10.244.x) are not routable from the host, and a port
forward tunnels named TCP ports only. Options, to evaluate in this order:

1. **TURN over TCP inside the gang** — a coturn task; the streamer and the
   browser both relay through it; forward its TCP port. Most likely to work
   through a port forward, at some latency.
2. **A host route into the kind pod network** (`ip route` to the pod CIDR via
   the kind node) so ICE host candidates are reachable. Simple locally, does
   not carry to a real cluster.
3. On a real cluster, a NodePort or LoadBalancer with the node's address
   advertised as a candidate.

This is what makes Pixel Streaming a project rather than a flag.

### 5. Cost to the measurement

Encoding a full-resolution viewport shares the GPU with the two stereo
captures. Like `--chase-cam`, it is a watching mode: its runs go to a
`<campaign>-viz` manifest, never the scored one.

## Done when

A `--pixel-stream` run of `vio-osmo-condo` shows the spectator viewport in a
browser on the host while the drone flies the bedroom route, and a run without
the flag renders, scores and packs exactly as before.
