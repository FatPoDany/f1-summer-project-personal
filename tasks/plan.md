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

# Implementation Plan: Synthetic Robot Pilot Capture

## Overview

Add a facilitator-only path that runs 1–20 sequential, unattended TORCS
reference sessions using pinned `berniw` index 9. Each session uses
`g-track-1`, `car7-trb1`, and three laps; records independently validated robot
telemetry; registers complete runs; and preserves per-session and batch audit
evidence. Synthetic data remains physically and semantically separate from
participant data and does not automatically invoke Granite.

## Architecture Decisions

- Put synthetic configuration, provenance, validation, launch lifecycle, and
  manifests in `racecoach.telemetry.synthetic_capture`; reuse the existing run
  store only after validation rather than adding another ingestion system.
- Allow only `berniw` index 9 in version one. Repeated sessions exercise the
  collection workflow and establish a fixed reference, but are not represented
  as different users or evidence of coaching effectiveness.
- Add a separate opt-in environment variable and robot CSV schema. The native
  recorder observes commands already produced by `rbDrive` and cannot write to
  actuator fields or call a model/network service.
- Use a new `apexrobotstudy.xml` with TORCS' unattended `-r` entry. The existing
  graphical human preset, `-R` entry, and five-lap participant assignment remain
  unchanged.
- Launch one TORCS process at a time with an argument list and per-session
  directory. Cancellation terminates the active child, prevents later launches,
  and records an explicit outcome without deleting partial raw evidence.
- Gate the Apex page behind explicit research mode. Use `QThreadPool` and task
  identity checks so collection never blocks Qt and stale signals cannot mutate
  a later batch.

## Dependency Graph

```text
Task 15 synthetic contract
  -> Tasks 16-17 native writer and berniw hook
  -> Task 18 unattended three-lap preset
  -> Tasks 19-21 Python preflight, single-session, and batch/CLI
  -> Tasks 22-23 facilitator UI and Garage handoff
  -> Tasks 24-25 documentation and final qualification
```

## Task List

### Phase 1: Evidence contract

- [x] Task 15: Implement the fail-closed synthetic capture contract.

### Checkpoint: Contract

- [x] Invalid identity, phase, count, paths, and provenance fail before launch;
  valid evidence is atomic and cannot be labelled human.

### Phase 2: Pinned TORCS reference

- [x] Task 16: Add and contract-test the GNU++98 robot CSV writer.
- [x] Task 17: Hook the opt-in recorder into `berniw` index 9 without changing control.
- [x] Task 18: Ship and verify the unattended three-lap robot preset.

### Checkpoint: Native reference

- [x] Exact-archive preparation is idempotent, source/native verifiers pass,
  other robots remain unrecorded, and the human preset is unchanged.

### Phase 3: Validated collection

- [x] Task 19: Validate the installed robot preset from Python before launch.
- [x] Task 20: Complete one synthetic session through validation and run registration.
- [x] Task 21: Add sequential batches, cancellation, audit summaries, and CLI recovery.

### Checkpoint: Collection slice

- [x] Fake-process tests cover three successes, failure isolation, cancellation,
  malformed/mismatched CSV, and zero output without registering invalid runs.

### Phase 4: Facilitator workflow

- [x] Task 22: Add the cancellable research batch worker and visible pilot page.
- [x] Task 23: Gate navigation and open completed runs through existing Garage plumbing.

### Checkpoint: Desktop slice

- [x] Offscreen Qt automation uses the visible controls for a default batch of
  three, remains responsive, and rejects stale results.

### Phase 5: Evidence and qualification

- [x] Task 24: Document the synthetic protocol, provenance boundary, commands, and recovery.
- [x] Task 25: Run all automated checks and the real three-session TORCS/UI smoke gate.

### Checkpoint: Complete

- [x] Focused tests, full pytest, Ruff, archive checksum, native verifiers,
  offscreen Qt runtime inspection, and final diff review pass.
- [x] Three real unattended sessions each produce exactly one distinct,
  validated three-lap synthetic run, or the unavailable environment gate is
  reported explicitly without claiming qualification.

### Phase 6: Recorder defect fixes

- [x] Task 26: Record the vertical wheel load TORCS actually publishes and drop
  the two force columns the driver ABI cannot supply (`apex-human-v2`,
  `apex-robot-v2`).

### Phase 7: Windows participant build

- [x] Task 27 (portability): make the overlay MSVC-clean, add the Windows `-R`
  and profile-directory patch, register the recorder sources in the Visual
  Studio projects, and centralise both runtime layouts.
- [ ] Task 27 (qualification): run the `windows-installer` workflow and install
  the artifact on a real Windows machine. The workflow needs no configuration —
  upstream ships the byte-identical pinned archive — so this is gated only on a
  Windows run, which a Linux host cannot perform.

## Verification Checkpoints

1. After Task 15: run `QT_QPA_PLATFORM=offscreen pytest -q tests/test_synthetic_capture.py`.
2. After Tasks 16–18: run the archive checksum, reference-recorder verifier,
   robot-preset verifier, and `integrations/torcs-1.3.9/build.sh prepare` twice.
3. After Tasks 19–21: rerun synthetic tests and inspect
   `PYTHONPATH=src python -m racecoach.cli capture-synthetic --help`.
4. After Tasks 22–23: run synthetic Qt tests plus `tests/test_window.py` and
   perform an offscreen interaction/screenshot check.
5. After Tasks 24–25: run full pytest, Ruff, all relevant native verifiers, and
   the real three-session smoke procedure when the TORCS runtime is available.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Pinned `berniw` callbacks or build flags differ from the human module | High | Compile against the exact verified archive early; keep hooks minimal and GNU++98-compatible |
| Recorder changes robot control timing or values | High | Observe only after normal control calculation; source checks prohibit actuator assignments; use buffered fail-safe output |
| Headless race does not exit or leaves an orphan process | High | Bounded managed subprocess lifecycle, explicit cancellation, terminal outcome audit, and real smoke gate |
| Synthetic data is confused with participants | High | Separate root, phase, schema, capture tag, id prefix, UI warning, and fail-closed provenance validation |
| Partial or mismatched output is imported | High | Unique directories, exact module/index/track/car/lap checks, hash before registration, preserve rejected raw evidence |
| Robot repeatability is overstated | Medium | Freeze TORCS/preset/controller identity and call sessions reference pilots, not distinct users or bit-identical trials |
| Late Qt signals overwrite a newer batch | Medium | Immutable progress snapshots and current-task identity guards |
| Existing human workflow regresses | High | Do not alter human contracts; rerun human capture, capture-view, window, and preset verification tests |

## Parallelization and Sequencing

- Tasks 16 and 18 are conceptually separate but both touch the TORCS build
  overlay, so execute them sequentially in this workspace.
- Tasks 19–21 must follow the native/preset contract so Python validates real
  identifiers rather than guessed ones.
- UI work starts only after the runner callback and cancellation contract are stable.
- Documentation may be drafted after the collection checkpoint, then corrected
  against the qualified runtime behavior before completion.

## Open Questions

- No design decision is currently open. The TORCS environment on this machine is
  built and qualified, so it is no longer an execution gate for the synthetic
  path; the human participant lap in Phase 4 remains the one open gate.
- Longitudinal and lateral per-wheel tyre force stay unavailable. Recording them
  would mean patching simuv2 to publish its private `tWheel` forces, which turns
  the observe-only driver-module recorder into a physics-engine change. Raise it
  as a separate decision if coaching evidence ever needs those channels.
