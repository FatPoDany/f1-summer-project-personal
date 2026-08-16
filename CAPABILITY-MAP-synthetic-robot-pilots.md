# Capability Map: Synthetic Robot Pilot Capture

## Objective

Exercise the Apex research-data collection workflow with unattended, pinned
TORCS robot sessions before recruiting students. Synthetic runs provide
reproducible pipeline/reference data; they are never human-study observations
and cannot establish that coaching changes human behaviour.

## Modules

| Module id | Responsibility | Depends on |
|---|---|---|
| `synthetic-capture-contract` | Synthetic provenance, configuration, manifests, storage, and validation | — |
| `torcs-reference-recorder` | Opt-in telemetry recording for pinned built-in `berniw` index 9 | `synthetic-capture-contract` |
| `robot-study-preset` | Fixed unattended TORCS race using `g-track-1`, `car7-trb1`, three laps, and `berniw` index 9 | `torcs-reference-recorder` |
| `synthetic-pilot-runner` | Sequential multi-session launch, cancellation, isolation, validation, import, and batch manifest | preceding three modules |
| `research-pilot-ui` | Facilitator-only Apex controls, progress/results, and Qt interaction automation | `synthetic-pilot-runner` |

Build order:

```text
synthetic-capture-contract
  -> torcs-reference-recorder
  -> robot-study-preset
  -> synthetic-pilot-runner
  -> research-pilot-ui
```

## Approved assumptions

- Version one repeats one pinned built-in controller (`berniw`, index 9) rather
  than treating different controller families as interchangeable users.
- The controller uses the same `car7-trb1` and track as the development
  human-study preset, with a shorter fixed three-lap synthetic assignment.
- A researcher starts the batch from Apex; TORCS uses its unattended console
  race path so each session exits without a person dismissing a results screen.
- The UI defaults to three sessions and accepts 1–20 per batch.
- Synthetic ids use `SIM-BERNIW9-NNN`; data lives under
  `<workspace>/captures/synthetic/` and run metadata says `synthetic-robot`.
- Synthetic phases use `reference-pilot`; `baseline`, `coached`, and
  `familiarisation` remain reserved for real participant collection.
- The existing human recorder, human-only graphical preset, validation rules,
  and `captures/human/` layout remain unchanged.

## Specifications

- `SPEC-synthetic-capture-contract.md`
- `SPEC-torcs-reference-recorder.md`
- `SPEC-robot-study-preset.md`
- `SPEC-synthetic-pilot-runner.md`
- `SPEC-research-pilot-ui.md`
