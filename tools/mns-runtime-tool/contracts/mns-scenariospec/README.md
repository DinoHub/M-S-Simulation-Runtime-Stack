# MnS ScenarioSpec

Source-of-truth contract for MnS ScenarioSpec documents.

This repository owns:

- `schema/ScenarioSpecSchema.yaml`
- ScenarioSpec shape validation
- canonical examples
- conformance tests
- schema versioning and extension policy docs

It does not own stack generation, authoring UI, packaging workflows, simulator
plugins, or Docker Compose templates. Those implementation repositories consume
this contract.

## Validate

```bash
python3 -m unittest discover -s tests
python3 -m mns_scenariospec.cli validate-schema
python3 -m mns_scenariospec.cli validate examples/split-folder
```

Installed as a package:

```bash
mns-scenariospec validate examples/split-folder
```
