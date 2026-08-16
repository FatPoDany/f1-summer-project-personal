# Spec: Synthetic Pilot Runner

## Objective

Run 1–20 isolated reference sessions sequentially, validate every raw output,
register only complete evidence, and retain a batch-level audit. One failed
session must not be mistaken for success or corrupt another session.

## Tech Stack and Commands

- Python 3.11+, subprocess without shell, existing managed runner and run store.

```bash
QT_QPA_PLATFORM=offscreen pytest -q tests/test_synthetic_capture.py
PYTHONPATH=src python -m racecoach.cli capture-synthetic --count 3
```

## Project Structure

- `src/racecoach/telemetry/synthetic_capture.py` — single/batch lifecycle.
- `src/racecoach/cli.py` — facilitator recovery command.
- `tests/test_synthetic_capture.py` — orchestration and failure paths.

## Code Style

```python
for ordinal in range(1, config.count + 1):
    run_one_synthetic_session(config, ordinal, stop_requested=stop_requested)
```

Use explicit sequential control flow, validated argument lists, atomic status
updates, and callbacks carrying immutable progress snapshots.

## Testing Strategy

- Fake subprocesses create real test CSVs for success, one-session failure,
  cancellation, mismatched robot evidence, and zero-output cases.
- Assert commands are lists, `shell` is absent, ids are deterministic within a
  batch, and a failed session creates no registered run.
- A real three-session TORCS smoke run is the final environment gate.

## Boundaries

- Always: sequential launch, 1–20 bound, stop between sessions, per-session
  manifest plus atomic batch manifest, preserve failed raw evidence.
- Ask first: parallel TORCS processes, remote execution, automatic coaching,
  or a batch larger than 20.
- Never: overwrite captures, retry a failed session silently, launch through a
  shell, mix human directories, or run the live SCR client concurrently.

## Success Criteria

1. Count 3 yields three distinct synthetic ids, capture directories, manifests,
   and registered run ids when all sessions succeed.
2. Progress identifies current/total, robot identity, and terminal status.
3. Cancellation stops the active process and prevents later sessions starting.
4. A failed session is recorded as failed; completed siblings remain valid.
5. The batch manifest summarizes every outcome without rewriting raw evidence.

## Open Questions

- Version one does not automatically run Granite; generated runs enter the
  existing deterministic analysis/coaching workflow after collection.

