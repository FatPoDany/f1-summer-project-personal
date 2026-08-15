# Implementation Plan: Human TORCS Telemetry Capture

## Overview

Build one evidence-preserving path from a human TORCS drive to a validated raw
run. The native human driver records only when explicitly enabled; Python owns
study metadata, process launch, discovery, validation, and run registration.

## Architecture Decisions

- Instrument the existing human driver callback because it sees the final
  keyboard/gamepad/wheel commands and does not require a second robot car.
- Enable capture through a child-process environment variable so ordinary TORCS
  use remains unchanged and Apex can choose a unique local output directory.
- Keep study metadata in a sidecar manifest instead of repeating participant
  information in every telemetry row.
- Reuse the current exporter signature and run store so analysis and coaching do
  not gain a parallel ingestion path.

## Task List

### Phase 1: Orchestration contract

- [x] Task 1: Add failing Python tests for validated metadata, safe launch,
  output discovery, and run-store registration.
- [x] Task 2: Implement the human-capture orchestrator and CLI command.

### Checkpoint: Python slice

- [x] Focused tests pass and CLI help exposes the complete workflow.

### Phase 2: Native recorder

- [x] Task 3: Add a GNU++98 CSV recorder with deterministic schema and a
  standalone compile/runtime contract test.
- [x] Task 4: Integrate the recorder into the pinned TORCS human driver through
  an idempotent build patch without changing vehicle controls.

### Checkpoint: Native slice

- [x] Exact-archive native verification passes.
- [x] Prepared source contains the recorder calls and still builds the human
  module contract.

### Phase 3: Handoff

- [x] Task 5: Document build, collection, data location, fields, recovery, and
  the real-lap acceptance procedure.
- [x] Task 6: Run focused tests, full pytest, Ruff, and inspect the final diff.

### Checkpoint: Complete

- [x] All automated criteria pass; any unavailable real TORCS GUI verification
  is explicitly reported as the remaining environment gate.

### Phase 4: Participant desktop workflow

- [x] Task 7: Add a readiness-gated, no-terminal Collect Data guide.
- [x] Task 8: Run TORCS capture off the GUI thread with safe cancellation.
- [x] Task 9: Open registered raw runs as named Garage sessions.
- [x] Task 10: Define packaged-runtime discovery and facilitator failure states.

### Checkpoint: Desktop slice

- [x] Focused core/UI/window tests pass and navigation alone never starts TORCS.
- [x] The guide renders at Apex's 960×540 minimum size with visible controls.
- [ ] One real participant-style lap is completed from the Apex guide on the study PC.

### Phase 5: Fixed graphical study preset

- [x] Task 11: Add a pinned TORCS graphical race-config entry distinct from headless `-r`.
- [x] Task 12: Ship one versioned human-only development preset with the TORCS runtime.
- [x] Task 13: Add a validated Python preset contract and record it in capture manifests.
- [x] Task 14: Show the locked preset in Collect Data and launch it without menu selection.

### Checkpoint: Controlled launch slice

- [x] Source/native checks prove `-R` enters the graphical race path and preserves `-r`.
- [x] Focused orchestration and Qt tests pass with missing-preset failure coverage.
- [x] A VNC runtime check reaches the human-drivable `g-track-1` race with the preset.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Recorder slows the human callback | High | Buffered stream, one-second flush, no model/network work |
| Partial/crashed session | Medium | Periodic flush; reject empty/malformed files; preserve raw capture directory |
| CSV text corrupts columns | Medium | RFC-style quote escaping with native contract test |
| Existing dirty work is overwritten | High | Touch only new files plus clean CLI/build/ignore integration points |
| Study identity leaks | High | Pseudonymous id validation; no names/emails in row data |
| `-r` accidentally used for participants | High | Separate `-R` contract plus source/runtime verification |
| Development preset mistaken for final protocol | Medium | Version id in manifest; protocol change creates a new id |

## Open Questions

- The study-PC real lap remains the hardware-dependent acceptance gate before
  recruitment; the desktop workflow exposes this explicitly rather than
  treating automated GUI tests as participant evidence.
