# Spec: Synthetic Capture Contract

## Objective

Define a typed, fail-closed boundary for robot-generated research pilot data.
The contract makes synthetic provenance impossible to confuse with a student,
keeps raw evidence separate, and supplies the runner, UI, native recorder, and
analysis store with one vocabulary.

## Tech Stack and Commands

- Python 3.11+, frozen dataclasses, pathlib, JSON, pandas-based validation.

```bash
QT_QPA_PLATFORM=offscreen pytest -q tests/test_synthetic_capture.py
ruff check src tests
```

## Project Structure

- `src/racecoach/telemetry/synthetic_capture.py` — contracts and orchestration owner.
- `src/racecoach/telemetry/run_store.py` — existing raw-run registration.
- `tests/test_synthetic_capture.py` — boundary and provenance tests.
- `<workspace>/captures/synthetic/` — ignored generated evidence.

## Code Style

```python
@dataclass(frozen=True)
class RobotIdentity:
    module: str
    index: int
    car_id: str
```

Use frozen validated values, explicit domain errors, atomic JSON replacement,
safe slugs, and user-readable messages. No dictionary-shaped configuration may
cross into the launcher without validation.

## Testing Strategy

- Reject human phase names, unsafe ids, unsupported robots, impossible counts,
  mismatched track/car/module/index evidence, empty output, and malformed CSV.
- Prove synthetic manifests and run metadata cannot say `human-driver`.
- Prove hashes, row counts, run ids, preset identity, command, and timestamps are retained.

## Boundaries

- Always: local files, exact robot allowlist, explicit schema/provenance, atomic
  manifests, bounded batch count, validate before import.
- Ask first: new identity fields, new controller families, schema migration, or
  storage outside the configured workspace.
- Never: names/emails, human phase labels, secrets, generated data in Git, or
  claims that synthetic runs measure coaching effectiveness.

## Success Criteria

1. Synthetic ids and the `reference-pilot` phase are validated before launch.
2. Raw data and manifests use the synthetic root and never the human root.
3. Registered runs use `capture="synthetic-robot"`.
4. A manifest identifies the exact robot, TORCS preset, track, car, laps,
   command, CSV hash, samples, and run id.
5. Any provenance mismatch fails before run registration and preserves raw evidence.

## Open Questions

- A future contract version may add qualified alternative robots; version one
  deliberately allows only the approved `berniw` index 9 reference.

