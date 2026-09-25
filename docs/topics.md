# What will this stack publish?

The bridges' topic names are the product of four inputs that only meet at
runtime — `settings.json` sensors/cameras, `topic_names.yaml` renames,
`topic_prefix`/`TOPIC_PREFIX`, and the bridge's fixed topic list. Resolve them
before starting anything:

```bash
make topics STACK=generated/<name>
./tools/preview_topics.py generated/<name> --json
```

The names come from the bridge image's own launch code (`_final_topic`,
`_canonical_vehicle_topics`, `load_topic_renames`) and each entry launch file's
declared argument defaults — not a second copy of the rules here — so a new
bridge image changes this output with it. The `tevv-airsim-ros2-bridge-humble`
bridge defaults `topic_prefix` to `/` (flat names, one ROS domain per vehicle)
and adds a canonical `lidar/points` alias.

The output separates published topics from services and command inputs, and
flags `topic_names.yaml` keys matching no topic on a vehicle — harmless for a
sensor the scenario does not run, and identical to what a typo'd key looks like.

Scope: the vehicle node's own graph; `ros2 topic list` also shows ROS's own
`/clock`, `/rosout`, `/parameter_events`, `/tf`, `/tf_static`. Cameras on the
iceoryx shared-memory path are listed as not on ROS, but the v1 bridge
republishes them as `/<camera>/image_raw` once the stack runs. Nodes outside
the bridge launch are not visible here.

The default topic list for one vehicle, frames and command services are in the
[User Guide](USER_GUIDE.md#topics).
