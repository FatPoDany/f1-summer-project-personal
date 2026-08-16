# Spec: Apex Research Pilot UI

## Objective

Give a facilitator a safe Apex interface for unattended synthetic batches and
prove the collection interaction path with Qt automation. Student-facing
Collect Data remains focused on real participants and cannot accidentally
start or register a robot as human.

## Tech Stack and Commands

- PySide6/Qt 6, QThreadPool worker, pytest-qt, existing Apex design system.

```bash
QT_QPA_PLATFORM=offscreen pytest -q tests/test_synthetic_capture_view.py
QT_QPA_PLATFORM=offscreen pytest -q tests/test_window.py
apex
```

## Project Structure

- `src/apex/synthetic_capture_view.py` — facilitator setup/progress/results.
- `src/apex/synthetic_capture_task.py` — cancellable background batch adapter.
- `src/apex/main_window.py` — research-mode navigation only.
- `tests/test_synthetic_capture_view.py` — interaction and stale-result tests.

## Code Style

```python
task.signals.progress.connect(
    lambda snapshot, current=task: self._on_progress(current, snapshot)
)
```

Follow existing widgets and spacing, accessible labels, readable empty/error
states, no blocking work on the GUI thread, and identity-guard all worker signals.

## Testing Strategy

- Qt tests click navigation, choose count, start, observe progress, cancel, and
  open results using a fake runner that writes validated evidence.
- Verify the page is absent unless facilitator research mode is explicitly enabled.
- Verify opening/navigating never starts TORCS and late worker results cannot
  mutate a newer batch.
- Perform an offscreen runtime screenshot and a real three-session UI smoke run.

## Boundaries

- Always: explicit “Synthetic — not participant data” labelling, count 1–20,
  confirmation summary, background work, cancel, accessible controls.
- Ask first: exposing the page in participant builds or collecting new metadata.
- Never: participant names, human phase choices, hidden auto-start, model calls
  during capture, or synthetic results displayed as human study outcomes.

## Success Criteria

1. With research mode disabled, the participant navigation and human guide are unchanged.
2. With research mode enabled, a facilitator can launch a default batch of three.
3. Progress and results clearly identify synthetic data and per-session status.
4. Results open through existing registered-run/Garage plumbing without CSV selection.
5. Qt automation exercises the same visible controls used by the facilitator.
6. Stop is responsive and stale callbacks cannot alter a subsequent batch.

## Open Questions

- Version one uses an environment-gated research page rather than authentication;
  packaged study deployment must decide whether to include that page at all.

