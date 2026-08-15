# Human TORCS Telemetry Capture

## Task 1: Orchestration tests

- [x] Invalid participant/phase values are rejected before process launch.
- [x] TORCS receives only validated capture environment and argument values.
- [x] Completed exporter CSVs are registered; zero files and failed TORCS are errors.
- Verify: `QT_QPA_PLATFORM=offscreen pytest -q tests/test_human_capture.py`

## Task 2: Orchestrator and CLI

- [x] Add `racecoach capture-human` with participant, phase, TORCS path, and passthrough args.
- [x] Write an atomic local manifest and print imported run ids.
- Verify: focused pytest and `racecoach capture-human --help`.

## Task 3: Native recorder

- [x] Add the documented CSV header and buffered row writer.
- [x] Escape text and close independent recorder instances safely.
- Verify: `integrations/torcs-1.3.9/verify-human-capture.sh`.

## Task 4: Pinned human-driver integration

- [x] Apply an idempotent patch during TORCS preparation.
- [x] Start/sample/stop hooks never mutate controls.
- Verify: prepared-source inspection and native compile check.

## Checkpoint: Core capture

- [x] Focused Python and native checks pass.

## Task 5: Protocol documentation

- [x] Explain build, launch, data fields/units, local paths, and recovery.
- [x] Document the real human-lap acceptance checklist and claims boundary.

## Task 6: Final verification

- [x] Ruff passes.
- [x] Full pytest passes.
- [x] Final diff contains no unrelated or generated files.

## Task 7: Graphical participant guide

- [x] Add Collect Data navigation and prepare/drive/saved pages.
- [x] Require a pseudonym and the study readiness checklist before launch.
- [x] Keep TORCS and import work off the Qt GUI thread.
- [x] Support safe Stop and ignore late background-task signals.
- [x] Open captured runs as named Garage sessions without CSV selection.
- [x] Detect a TORCS runtime shipped with a packaged Apex application.
- [ ] Complete the documented real-lap check on the study PC.

## Task 11: Graphical preset entry

- [x] Add and verify a pinned `-R <race.xml>` GUI path without changing headless `-r`.
- [x] Keep the normal OpenGL event loop and human driver available.
- Verify: `integrations/torcs-1.3.9/verify-study-preset.sh`.

## Task 12: Versioned development preset

- [x] Ship one human-only `apexstudy.xml` fixing track, car source, and five laps.
- [x] Install it with the normal TORCS racemanager data.
- Verify: source contract plus installed-runtime inspection.

## Task 13: Python preset evidence

- [x] Validate the packaged preset before process launch.
- [x] Pass graphical launch arguments without a shell and freeze preset metadata in manifest.
- Verify: `QT_QPA_PLATFORM=offscreen pytest -q tests/test_human_capture.py`.

## Task 14: Locked participant presentation

- [x] Display the assigned preset and remove manual track/car-selection instructions.
- [x] Disable Start with a facilitator-facing message when the preset is missing.
- Verify: `QT_QPA_PLATFORM=offscreen pytest -q tests/test_capture_view.py`.

## Checkpoint: Controlled graphical launch

- [x] A VNC smoke run produced 846 Human-driver rows at about 50 Hz on
  `g-track-1` with `car7-trb1` from the locked preset.
- [ ] Complete one participant-style full lap before recruitment.
