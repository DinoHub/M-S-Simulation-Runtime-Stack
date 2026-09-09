# Exact UE candidate dashboard selection

The candidate checker resolves one selection and forwards it to staging and the
dashboard. It writes/selects the `published` entry in its own image overlay,
regardless of the normal channel's `ue582` default. It also explicitly selects
the verified pack store, isolated authoring data root, candidate lock and both
consumer contracts. Stale shell/environment defaults cannot replace those values.

For dashboard launch/inspection, put all inputs inside the selected workspace.
The candidate lock must declare `host_contract` and
`authoring_host_contract` (paths relative to the workspace), or supply
`--runtime-host-contract` and `--authoring-host-contract` explicitly. Both
contracts must carry the candidate's full capability ID.

Example (run from the product workspace after assembling the candidate inputs):

```bash
python3 tools/check_ue_candidate.py \
  --lock .mns/candidate/lock.json \
  --pack-store .mns/ue582/pack-store \
  --workspace . \
  --runtime-host-contract .mns/candidate/runtime-host.json \
  --authoring-host-contract .mns/candidate/authoring-host.json \
  --authoring-data-root .mns/candidate/authoring-data \
  --start-dashboard
```

Use `--local-images` only with the existing exact-local-image-ID lock format.
The generated local rerun script preserves the contract and data-root arguments.
With `--engine-root`, the checker verifies engine/CL while preserving the
candidate's optional renderer fingerprint; image labels still have to match the
entire capability ID.

Candidate runs disable unrelated default-pack seeding and enforce no-pull staging.
Every pack in a browser E2E candidate must be authorable and appear in the staged
index under the selected capability. A missing/stale index fails before dashboard
startup. Use a distinct data root for a new candidate instead of deleting a user's
existing staged content. This checker still reports `e2e_verified: false`:
passing preflight is not a claim that runtime/GPU acceptance passed.

No release image digest, published pack, or channel lock is rewritten by this fix.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools -p 'test_*.py' -q
```

Tests exercise stale environment overrides, both legacy/channel store paths,
missing/mismatched/outside-workspace contracts, fingerprinted engine IDs, staged
index mismatches and live-dashboard selection inspection using mocked Docker.
