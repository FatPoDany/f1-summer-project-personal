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

# Synthetic Robot Pilot Capture

## Task 15: Fail-closed synthetic contract

**Description:** Define immutable robot, preset, session, progress, result, and
manifest values plus safe synthetic storage and atomic provenance persistence.

**Acceptance criteria:**

- [x] Only `berniw` index 9, `car7-trb1`, `g-track-1`, three laps,
  `reference-pilot`, `SIM-BERNIW9-NNN`, and batch counts 1–20 are accepted.
- [x] Synthetic paths cannot resolve into `captures/human`; manifests and
  registered metadata cannot use `human-driver` or human phase labels.
- [x] Manifest writes are atomic and retain timestamps, arguments, preset id,
  robot identity, CSV hash, sample count, and run id.

**Verification:**

- [x] `QT_QPA_PLATFORM=offscreen pytest -q tests/test_synthetic_capture.py`
- [x] `ruff check src/racecoach/telemetry/synthetic_capture.py tests/test_synthetic_capture.py`

**Dependencies:** None

**Files likely touched:**

- `src/racecoach/telemetry/synthetic_capture.py`
- `tests/test_synthetic_capture.py`

**Estimated scope:** Small (2 files)

## Checkpoint: Synthetic contract

- [x] Valid inputs round-trip through typed values and atomic JSON.
- [x] Every invalid/provenance-conflicting input fails before a subprocess or run-store write.

## Task 16: Native robot CSV writer

**Description:** Add a GNU++98-compatible, buffered robot telemetry writer and
standalone contract test without yet changing the `berniw` callbacks.

**Acceptance criteria:**

- [x] The writer emits the approved robot schema, SI units, required telemetry
  fields, safe CSV quoting, periodic flush, and independent close behavior.
- [x] Missing/invalid output configuration disables recording without throwing
  across the TORCS ABI.
- [x] The native test checks header, representative row, quoting, flush, and close.

**Verification:**

- [x] `integrations/torcs-1.3.9/verify-reference-recorder.sh`

**Dependencies:** Task 15

**Files likely touched:**

- `integrations/torcs-1.3.9/overlay/src/drivers/berniw/apex_robot_telemetry_writer.h`
- `integrations/torcs-1.3.9/overlay/src/drivers/berniw/apex_robot_telemetry_writer.cpp`
- `integrations/torcs-1.3.9/robot_telemetry_writer_test.cpp`
- `integrations/torcs-1.3.9/verify-reference-recorder.sh`

**Estimated scope:** Medium (4 files)

## Task 17: Pinned `berniw` integration

**Description:** Adapt TORCS state into writer rows and add idempotent
start/record/stop hooks for index 9 only, observing rather than changing control.

**Acceptance criteria:**

- [x] Only enabled `berniw` index 9 starts a recorder; all other indices and
  ordinary launches create no Apex robot CSV.
- [x] Hooks are present exactly once and do not add or modify actuator assignments.
- [x] Re-running source preparation neither duplicates hooks nor changes output.

**Verification:**

- [x] `sha256sum -c integrations/torcs-1.3.9/SHA256SUMS`
- [x] `integrations/torcs-1.3.9/verify-reference-recorder.sh`
- [x] Run `integrations/torcs-1.3.9/build.sh prepare` twice and inspect both passes.

**Dependencies:** Task 16

**Files likely touched:**

- `integrations/torcs-1.3.9/overlay/src/drivers/berniw/apex_robot_telemetry.h`
- `integrations/torcs-1.3.9/overlay/src/drivers/berniw/apex_robot_telemetry.cpp`
- `integrations/torcs-1.3.9/patches/berniw-telemetry.patch`
- `integrations/torcs-1.3.9/build.sh`
- `integrations/torcs-1.3.9/verify-reference-recorder.sh`

**Estimated scope:** Medium (5 files)

## Task 18: Unattended three-lap preset

**Description:** Add a distinct robot racemanager preset and ensure normal
installation ships exactly one `berniw` index-9 entry for three laps.

**Acceptance criteria:**

- [x] The preset fixes `g-track-1`, `berniw` index 9, and three laps and resolves
  the expected `car7-trb1` assignment.
- [x] It is launched only via an argument-list `-r <preset>` path and remains
  distinct from the graphical human `apexstudy.xml` preset.
- [x] Source and installed-runtime verification fail on any assignment drift.

**Verification:**

- [x] `integrations/torcs-1.3.9/verify-robot-study-preset.sh`
- [x] `integrations/torcs-1.3.9/build.sh install` when the native environment is available.
- [x] `integrations/torcs-1.3.9/verify-study-preset.sh`

**Dependencies:** Task 17

**Files likely touched:**

- `integrations/torcs-1.3.9/overlay/src/raceman/apexrobotstudy.xml`
- `integrations/torcs-1.3.9/verify-robot-study-preset.sh`
- `integrations/torcs-1.3.9/build.sh`

**Estimated scope:** Medium (3 files)

## Checkpoint: Native reference

- [x] Archive checksum and reference recorder/preset verifiers pass.
- [x] Prepared source is idempotent and human capture/preset verifiers still pass.
- [x] Recorder code has no model, network, or actuator-control path.

## Task 19: Python preset preflight

**Description:** Represent the exact installed robot preset as trusted Python
configuration and reject missing or drifted XML before launching TORCS.

**Acceptance criteria:**

- [x] Preflight validates preset id/file, track, module/index, car source, and three laps.
- [x] Launch arguments are a sequence containing `-r` and an exact resolved path,
  never a shell command.
- [x] Preset identity is frozen into session evidence.

**Verification:**

- [x] `QT_QPA_PLATFORM=offscreen pytest -q tests/test_synthetic_capture.py`

**Dependencies:** Tasks 15 and 18

**Files likely touched:**

- `src/racecoach/telemetry/synthetic_capture.py`
- `tests/test_synthetic_capture.py`

**Estimated scope:** Small (2 files)

## Task 20: Validated single-session collection

**Description:** Launch one isolated TORCS reference race, discover and validate
its CSV, preserve provenance, and register only complete evidence in the run store.

**Acceptance criteria:**

- [x] The child receives only the unique synthetic output directory and validated arguments.
- [x] CSV robot/module/index/track/car/lap evidence, non-empty samples, and hash
  are validated before `capture="synthetic-robot"` registration.
- [x] Failure or malformed/mismatched output retains raw evidence and creates no run.

**Verification:**

- [x] `QT_QPA_PLATFORM=offscreen pytest -q tests/test_synthetic_capture.py`
- [x] Regression: `QT_QPA_PLATFORM=offscreen pytest -q tests/test_human_capture.py tests/test_racecoach_store.py`

**Dependencies:** Task 19

**Files likely touched:**

- `src/racecoach/telemetry/synthetic_capture.py`
- `tests/test_synthetic_capture.py`

**Estimated scope:** Small (2 files)

## Task 21: Sequential batch and CLI recovery

**Description:** Run 1–20 sessions sequentially with immutable progress,
cancellation, failure isolation, atomic batch audit, and a facilitator CLI command.

**Acceptance criteria:**

- [x] Default count 3 yields distinct `SIM-BERNIW9-NNN` session directories and
  summarizes every completed/failed/cancelled outcome without silent retry.
- [x] Cancellation terminates the active child and prevents subsequent launches;
  completed siblings remain registered and failed raw evidence remains intact.
- [x] `capture-synthetic` exposes validated count/runtime/workspace options and
  prints batch/session outcomes without model invocation.

**Verification:**

- [x] `QT_QPA_PLATFORM=offscreen pytest -q tests/test_synthetic_capture.py`
- [x] `PYTHONPATH=src python -m racecoach.cli capture-synthetic --help`

**Dependencies:** Task 20

**Files likely touched:**

- `src/racecoach/telemetry/synthetic_capture.py`
- `src/racecoach/cli.py`
- `tests/test_synthetic_capture.py`

**Estimated scope:** Medium (3 files)

## Checkpoint: Collection slice

- [x] Tests simulate three successes, middle-session failure, cancellation,
  mismatched provenance, malformed CSV, and zero output.
- [x] Each terminal result is explicit; only valid completed sessions appear in the run store.

## Task 22: Research batch worker and page

**Description:** Add the background adapter and facilitator UI for count,
confirmation, progress, cancellation, results, and explicit synthetic labelling.

**Acceptance criteria:**

- [x] The page defaults to three, bounds count to 1–20, and never starts on navigation.
- [x] TORCS work runs off the Qt thread; Stop is responsive and progress identifies
  current/total, robot, and terminal status.
- [x] Late signals from an older task cannot mutate the active batch.

**Verification:**

- [x] `QT_QPA_PLATFORM=offscreen pytest -q tests/test_synthetic_capture_view.py`
- [x] Offscreen interaction check confirms controls remain usable at 960×540.

**Dependencies:** Task 21

**Files likely touched:**

- `src/apex/synthetic_capture_task.py`
- `src/apex/synthetic_capture_view.py`
- `tests/test_synthetic_capture_view.py`

**Estimated scope:** Medium (3 files)

## Task 23: Gated navigation and Garage handoff

**Description:** Expose the page only in explicit research mode and open valid
completed runs through the existing named-session/Garage flow.

**Acceptance criteria:**

- [x] Research mode disabled leaves participant navigation and human collection unchanged.
- [x] Research mode enabled exposes one clearly labelled pilot action; opening it
  or switching pages cannot launch TORCS.
- [x] Completed synthetic runs open without CSV selection and remain visibly
  distinguishable from participant sessions.

**Verification:**

- [x] `QT_QPA_PLATFORM=offscreen pytest -q tests/test_synthetic_capture_view.py tests/test_window.py tests/test_garage_view.py`
- [x] Offscreen runtime screenshot/inspection of setup, progress, and results states.

**Dependencies:** Task 22

**Files likely touched:**

- `src/apex/main_window.py`
- `src/apex/synthetic_capture_view.py`
- `tests/test_synthetic_capture_view.py`
- `tests/test_window.py`

**Estimated scope:** Medium (4 files)

## Checkpoint: Desktop slice

- [x] Qt automation completes the default three-session visible-control flow.
- [x] The UI stays responsive, cancellation works, stale callbacks are ignored,
  and participant-mode behavior is unchanged.

## Task 24: Current-state documentation

**Description:** Document setup, launch, output layout, schema/provenance,
recovery, analysis handoff, claims limits, and real qualification procedure.

**Acceptance criteria:**

- [x] Commands and paths match the implemented CLI, Apex gate, TORCS preset, and manifests.
- [x] Documentation distinguishes synthetic pilots, fixed robot references, and
  human comparative-study evidence and does not claim measured coaching benefit.
- [x] Data fields/units and missing-channel behavior remain traceable to verified sources.

**Verification:**

- [x] Follow the documented mock/test path from a clean temporary workspace.
- [x] `ruff check .`

**Dependencies:** Tasks 21 and 23

**Files likely touched:**

- `docs/GRANITE_TORCS_WALKTHROUGH.md`
- `docs/DATA_AVAILABILITY.md`
- `integrations/torcs-1.3.9/README.md`

**Estimated scope:** Medium (3 files)

## Task 25: Final automated and runtime qualification

**Description:** Run the complete regression matrix, inspect the final diff,
and execute a real three-session unattended batch through the research UI when
the local TORCS runtime is available.

**Acceptance criteria:**

- [x] Full pytest, Ruff, checksum, bridge, human, graphical-preset, robot-recorder,
  and robot-preset checks pass without generated artifacts entering Git.
- [x] A real batch yields three distinct validated three-lap synthetic runs and
  opens them through Garage, with manifests matching raw evidence.
- [x] Any unavailable runtime gate is reported precisely and no effectiveness or
  qualification claim is made from automated tests alone.

**Verification:**

- [x] `QT_QPA_PLATFORM=offscreen pytest -q`
- [x] `ruff check .`
- [x] `sha256sum -c integrations/torcs-1.3.9/SHA256SUMS`
- [x] Run all `integrations/torcs-1.3.9/verify-*.sh` scripts.
- [x] Perform the documented real three-session Apex/TORCS smoke procedure.

**Dependencies:** Task 24

**Files likely touched:** None, except narrowly scoped fixes exposed by verification

**Estimated scope:** Small (verification)

## Checkpoint: Synthetic pilot complete

- [x] All specification success criteria and project Definition of Done are met.
- [x] Final diff contains no unrelated user changes, secrets, or generated captures.
- [x] The fixed robot data is labelled as pipeline/reference evidence, never as a human outcome.

# Recorder Defect Fixes

## Task 26: Real per-wheel vertical load

**Description:** Both recorders read `tWheelState::Fx/Fy/Fz`, which TORCS 1.3.9
declares but no simulation module ever writes, so every capture carried three
constant-zero force columns per wheel. Source the vertical load from the channel
TORCS actually publishes and drop the two columns the driver ABI cannot supply.

**Acceptance criteria:**

- [x] `*_force_z_n` comes from `priv.reaction[i]` (`simuv2/wheel.cpp:368`) in
  both the `human` and `berniw` recorders.
- [x] `*_force_x_n` and `*_force_y_n` are removed rather than kept as zeros;
  schemas become `apex-human-v2` and `apex-robot-v2`, and Python validates the
  new robot version.
- [x] The recorded load is physically correct: 11 270 N at rest against the
  1 150 kg `car7-trb1` mass, rising to about 25 kN under aero load at speed.
- [x] Documentation states the limitation and that v1 captures keep their zero
  columns and are not upgraded in place.

**Verification:**

- [x] `integrations/torcs-1.3.9/verify-reference-recorder.sh`
- [x] `integrations/torcs-1.3.9/verify-human-capture.sh`
- [x] `QT_QPA_PLATFORM=offscreen pytest -q` and `ruff check .`
- [x] Rebuilt runtime plus a real three-session batch registering `apex-robot-v2`
  runs with non-zero four-wheel loads, opened through the Garage lap split.

**Dependencies:** Task 25

**Files likely touched:**

- `integrations/torcs-1.3.9/overlay/src/drivers/human/apex_human_telemetry*.{h,cpp}`
- `integrations/torcs-1.3.9/overlay/src/drivers/berniw/apex_robot_telemetry*.{h,cpp}`
- `integrations/torcs-1.3.9/{human,robot}_telemetry_writer_test.cpp`
- `src/racecoach/telemetry/synthetic_capture.py`, `tests/test_synthetic_capture.py`,
  `tests/test_human_capture.py`
- `docs/HUMAN_TELEMETRY_CAPTURE.md`, `docs/DATA_AVAILABILITY.md`,
  `integrations/torcs-1.3.9/README.md`

**Estimated scope:** Medium (schema change across native, Python, and docs)

# Windows Study Installer

## Task 27: Windows build and per-user installer

**Description:** Let a participant drive locally on Windows instead of over a
remote desktop, whose input latency invalidates a driving measurement. Build the
overlay with the Visual Studio solution and prebuilt Win64 dependencies already
in the pinned archive, and package Apex plus the patched simulator as one
per-user installer produced by CI.

**Acceptance criteria:**

- [x] The recorders compile without POSIX-only calls: `getpid` goes through a
  `_getpid` shim and no other `unistd.h`/socket use remains outside
  `granite_bridge`, which is excluded from the Windows build.
- [x] `patches/windows-graphical-race.patch` gives `src/windows/main.cpp` the
  same `-R` contract as the Linux build plus `APEX_TORCS_LOCAL_DIR`, so each
  session gets its own TORCS profile directory.
- [x] `patches/windows-vcxproj-telemetry.patch` registers the recorder sources
  in `human.vcxproj` and `berniw.vcxproj`; both patches dry-run apply cleanly
  against the pinned archive.
- [x] `racecoach.telemetry.torcs_runtime` owns both runtime layouts
  (`bin/torcs` + `share/games/torcs` versus `wtorcs.exe` + `config/raceman`),
  with unit tests for each platform branch.
- [x] The Linux path is unchanged: full pytest, Ruff, all five native verifiers,
  and a real three-lap synthetic session still pass.
- [x] `msbuild` compiles the patched solution, including all four recorder
  sources, and produces `wtorcs.exe`, `human.dll`, and `berniw.dll`.
- [x] The runtime carries the assignment's own assets, not just binaries:
  both presets, `g-track-1.xml`/`.acc`, and `car7-trb1.xml`/`.acc`, above a
  200 MB floor. Bulk data needs `setup_win32-data-from-CVS_generic.bat`, which
  `setup_win32_generic.bat` does not cover — it never mentions `g-track-1`.
- [x] PyInstaller produces `Apex.exe` and `makensis` compiles
  `installer/apexstudy.nsi` into a 380.2 MB `ApexStudySetup.exe`, against 369 MB
  for the stock upstream installer of the same archive.
- [x] The installed build runs: `-R` opens the pinned preset, a fresh
  `APEX_TORCS_LOCAL_DIR` profile is seeded with all eight race managers including
  both Apex presets, and the recorder writes a CSV on Windows.
- [ ] A participant drives the full five-lap assignment from Apex's Collect Data
  and the run registers as SAVED. Only a header-plus-few-rows CSV has been
  written so far, so the writer is proven but a lap's worth of data is not.

**Verification:**

- [x] `patch --dry-run` for both Windows patches against the verified archive.
- [x] `QT_QPA_PLATFORM=offscreen pytest -q tests/test_torcs_runtime.py`
- [x] `QT_QPA_PLATFORM=offscreen pytest -q` and `ruff check .`
- [x] All `integrations/torcs-1.3.9/verify-*.sh` scripts.
- [x] Workflow YAML parses and the Release configuration links the static CRT
  (`RuntimeLibrary=MultiThreaded`), so no Visual C++ redistributable is needed.
- [x] Run the `windows-installer` workflow: green in 16m, one `ApexStudySetup`
  artifact.
- [ ] Install that artifact on a real Windows machine and drive a lap.

**Dependencies:** Task 26

**Blocked on:** installing the artifact and driving a lap. Everything up to
producing the installer is done and green; only the on-machine behaviour is
unproven. The archive is not a gate: upstream's published `torcs-1.3.9.tar.bz2`
is byte-identical to the pinned archive (SHA-256 `f9c69e86…` verified against the
SourceForge download), so the workflow needs no configuration.

**Windows-only defects found and fixed while getting the build green,** each of
which the Linux path never exercises:

1. `Invoke-WebRequest` silently wrote SourceForge's 130 KB HTML download page
   over the archive: SourceForge answers PowerShell's user agent with HTTP 200
   and no redirect. Fetch with `curl.exe`; verify size before hash.
2. `Get-Command patch` resolved to Strawberry Perl's GNU patch 2.5.9, which
   aborts on an assertion. Locate Git for Windows' patch explicitly; never trust
   PATH.
3. `setup_win32_generic.bat` had to run *before* msbuild — it is the header
   export step (82 copies into `export/include`), not just a data install.
4. `graphical-race.patch` was malformed: a body line began with TAB instead of
   the mandatory context space, so GNU patch had been guessing its position.
   Regenerated from the pinned source against the known-good tree; now applies
   with zero fuzz and zero offsets.
5. `ReRunRaceOnGUI` was missing from `client.def`, whose exports are listed by
   hand, so `wtorcs.exe` failed to link (LNK2019). Added in place, not by patch:
   `client.def` is CRLF and a diff over it cannot survive `eol=lf`.
6. Track and car data were absent entirely — a separate 3218-line script carries
   them. The build was green and the installer 64.7 MB instead of 380 MB.
7. TORCS was launched with no working directory, so it died with
   `0xC0000005`/`3221225477` before the race began. Stock TORCS resolves several
   paths against the current directory, fatally `data/fonts/%s` in
   `tgfclient/guifont.cpp:77`: the font fails to load, `create()`'s result is
   never checked, and the first text draw dereferences it. The Linux launcher
   script exports absolute directories, so nothing here is reachable from Linux,
   and this is upstream behaviour rather than something the Apex patches added —
   the `-R` entry merely made TORCS start from somewhere it never had before.
   Confirmed by A/B with the same fresh profile and only the working directory
   differing: correct directory runs, wrong directory reproduces the exit code.
   `torcs_launch_cwd()` now supplies it on both capture paths, asserted by tests,
   because the fakes took `**kwargs` and a missing `cwd` had raised nothing.

**Files likely touched:**

- `integrations/torcs-1.3.9/build-windows.ps1`
- `integrations/torcs-1.3.9/installer/apexstudy.nsi`
- `integrations/torcs-1.3.9/patches/windows-graphical-race.patch`
- `integrations/torcs-1.3.9/patches/windows-vcxproj-telemetry.patch`
- `.github/workflows/windows-installer.yml`
- `src/racecoach/telemetry/torcs_runtime.py`, `tests/test_torcs_runtime.py`
- `integrations/torcs-1.3.9/README.md`, `docs/HUMAN_TELEMETRY_CAPTURE.md`

**Estimated scope:** Large (new build/packaging path across native, Python, CI)

# Participant Experience (from the first real Windows session)

Raised after a participant completed the assignment on Windows. The capture
itself worked; everything below is what the run exposed.

## Task 28: Data correctness from a real session

- [x] Five driven laps imported as five. The splitter closed a final lap only on
  `race_finished` or `capture_lap_closed`, and the human recorder emits neither:
  it runs in the driver callback, which the race engine stops calling at the
  finish line, so the last lap had no closing distance reset either. Every human
  capture silently lost its last lap. Distance coverage now closes it, which is
  the same 98% rule the SCR path already used, and a part-driven lap still
  imports as an incomplete fragment.
- [x] The Collect Data failure line stated the capture directory twice: the
  exception message already ends with it and the view appended it again.
- [x] Lap column shows the bare lap number, not the imported file stem.

## Task 29: Study identity through to the Garage

- [x] Carry participant id, study phase, and the assigned setup from the capture
  into the run store and into each lap file's own header, so a lap keeps saying
  who drove it wherever the file ends up.
- [x] Garage columns: Lap, Driver, Time, delta to best, status; status reads
  `SESSION BEST` or `ANALYSE · N findings`.

## Task 30: What the participant sees after driving

- [x] Register the run in the Garage automatically when a session finishes. The
  completion screen stays put rather than navigating away, so the hand-over
  instruction is still on the screen the participant ends on.
- [x] Tell them where their data is and what to hand to the researchers.
- [ ] Idea, not scheduled: upload finished runs to a study server automatically.
  Recorded here so it is not lost; needs its own decision on hosting, consent,
  and what leaves the participant's machine.

## Task 33: A window a participant can actually drive in

- [x] The study session fixes the TORCS render size (1280x720 by default)
  in its own profile's `config/screen.xml`, written immediately before
  launch. TORCS ships 640x480, which is too small to place a car, and
  maximising the window does not help: `Reshape` in `tgfclient/screen.cpp`
  keeps the viewport at the configured view size and only re-centres it,
  while the race renderer caches `scrx/scry/scrw/scrh` from race start and
  reuses them every frame. A maximised window therefore drew the race in a
  small box offset toward the bottom-left, black elsewhere.
- [x] The size is part of the frozen assignment and appears in the capture
  manifest: a different render size is a different condition to drive under.
- [ ] In-place resizing still leaves the viewport behind. Fixing that means
  patching the renderer's cached viewport, which cannot be verified from a
  Linux host; TORCS itself answers a resolution change by restarting the
  process (`GfScrReinit`). Revisit only if a participant needs to resize
  mid-session.

## Task 31: Coaching a driver can act on

- [x] Deterministic half: `f1coach_core.debrief` ranks the stretches where a lap
  lost time to a reference and names the largest measured difference over the
  same stretch. Metres and km/h are ranked against their own reporting
  thresholds so different units stay comparable.
- [x] It stops at measurement. A stretch where nothing moved far enough is shown
  with its time loss and no explanation, rather than an invented one, and no
  point claims the difference caused the loss.
- [x] Surfaced above the corner table in Lap Analysis: a one-line summary plus up
  to three clickable stretches that zoom the strips onto themselves. Needs no
  model, so it works with Task 32 deferred.
- [x] Replay proper: `x`/`y` now travel through to canonical laps as optional
  columns, carried only when every kept row has a finite pair, so the track can
  be drawn. Double-clicking a stretch opens a replay window that draws the lap's
  own shape, picks out that stretch, and runs a marker along it at the elapsed
  time the driver actually took, with speed, pedals and gear alongside. Laps
  without a position channel say so and keep the numbers.

## Task 34: berniw commands more throttle than the actuator has

- [ ] `accel_cmd` reaches 1.253 in the reference captures, above the 0..1 the
  actuator accepts (`car.h:345-349`), in over half the rows. The simulator
  clamps it, so the car never did what the number says. The stored channel is
  left exactly as recorded, and the participant-facing replay clamps only its
  display. The researcher-facing corner table still reports `exit_throttle_pct`
  straight from the channel and can therefore print above 100; decide whether
  that column should say "commanded" or clamp too.
- [ ] Let a model narrate these points once Task 32 lands. The points are already
  the evidence it would be given.

## Task 32: Granite without a terminal

- [ ] The app must start and manage the model itself. Participants cannot run a
  server from a command line, so today the coaching path is unreachable for them.
  Delivery method is an open decision: bundling the weights would take the
  installer from 380 MB to several GB.

## Task 35: TORCS exits 0xC0000005 after a normal quit on Windows

- [ ] NOT ruled out as ours. An earlier version of this entry claimed the A/B
  cleared our code; that was wrong and is corrected here. The Windows event log
  names the faulting module as `client.dll`, same offset `0x49335` on all three
  crashes, across two builds and two install locations. On Windows `client.dll`
  compiles `src/libs/client/*` **and** `src/libs/raceengineclient/*`, and
  `graphical-race.patch` adds `ReRunRaceOnGUI` to `raceinit.cpp` -- so our code
  is inside the faulting module. Worse, `ReInit()` has exactly one caller in the
  whole 1.3.9 tree (`raceinit.cpp:161`, inside our function): upstream never
  calls it, so our `-R` path is the only thing that has ever executed it.
- [x] Reproduced deterministically in 80 seconds (2026-08-19). The trigger is
  **finishing the race**, not session length and not lap count. With a one-lap
  copy of `apexstudy.xml`, alternating runs gave:

      finish the lap, let the race end   -1073740771, -1073741819, -1073741819
      abort part way through              0, 0, 0

  Fifteen earlier short runs that all aborted mid-race were clean, which is why
  the first A/B saw nothing: shortening the runs removed the trigger. The two
  distinct codes (0xC000041D fatal app exit, 0xC0000005 access violation) are
  two faces of the same failure. Recipe: copy the raceman with `laps` set to 1,
  pass it with `-R`, cross the line, quit from the results screen.
- [x] Root cause found and fixed (2026-08-19). It is ours. `ReInit()` callocs
  `ReInfo`, leaving `_reMenuScreen` NULL; upstream's menu route sets it on the
  next line (`singleplayer.cpp:41-44`) and `ReRunRaceOnGUI` never did. Finishing
  a race goes `RE_STATE_SHUTDOWN` -> `RE_STATE_CONFIG` (`racestate.cpp:153`) ->
  `ReRacemanMenu`, which wires "Back to Main" and escape to that pointer with
  `GfuiScreenActivate` as the callback (`racemanmenu.cpp:291,350,352`). Quitting
  then called `GfuiScreenActivate(NULL)`, which assigns the argument to
  `GfuiScreen` (`gui.cpp:471`) and dereferences it unconditionally at
  `gui.cpp:482`. On Windows `client.vcproj` compiles `..\tgfclient\gui.cpp` into
  `client.dll`, which is why the event log named that module. Aborting never
  reaches the raceman menu, so only completed races died.

  Fix: `ReInfo->_reMenuScreen = ReSinglePlayerInit(NULL);` in `ReRunRaceOnGUI`
  -- the same screen the menu route uses, already built by `TorcsEntry()`, which
  both main.cpp paths run first. `graphical-race.patch` regenerated against the
  pristine archive: applies with zero fuzz and zero offsets, and the file
  compiles and links on Linux with `ReSinglePlayerInit` resolving from
  `singleplayer.o` in the same library.
- [ ] Unverified end to end. The fix is a one-line assignment plus a same-folder
  include, but nobody has yet driven a completed race on a build that contains
  it. Acceptance is the 80-second recipe above: three finished one-lap races
  through `-R`, all exiting 0.
- [x] Attribution settled by the missing arm being run: run the same one-lap
  race **through the menus** with no `-R`. Both paths reach the same
  race-completion code, so a menu race that also crashes puts this upstream and
  clears `ReRunRaceOnGUI`.
- [ ] Hypothesis, not a conclusion: `ReInit()` loads the track and graphic
  modules into the static `reEventModList` (`raceinit.cpp:82-89`) and
  `ReShutdown()` unloads that whole list (`raceinit.cpp:113`). If the GUI still
  holds pointers into those modules, unloading them faults -- in `client.dll`,
  during shutdown, intermittently, which is what is observed. Confirming it
  needs a debugger with symbols.
- [ ] The A/B has a missing arm, which is why it proved less than it looked.
  Every crash so far happened on a run that passed `-R` (our path); the only
  clean arm, `0-menu-only`, also never started a race, so race-vs-no-race and
  ours-vs-stock are confounded. The decisive arm is a race started **through the
  menus with no `-R`**, repeated -- clean menu races against crashing `-R` races
  would implicate `ReRunRaceOnGUI`.
- [x] What the A/B did establish (2026-08-19). The recorder is
  gated entirely on `APEX_HUMAN_TELEMETRY_DIR` (`apex_human_telemetry.cpp:89`
  returns before opening anything when it is unset), so the same `wtorcs.exe`
  runs both code paths. Results, quitting the same way each time:

      0-menu-only    exit 0        (no race started)
      1-race-norec   exit -1073741819   <- recorder inert, crashed anyway
      1-race-norec   exit 0             (repeat)
      2-race-rec     exit 0        x2, CSVs written

  The crash reproduces with the recorder writing nothing, so the recording path
  is not the cause. It is also intermittent: the same test passed on repeat, and
  the two runs with the recorder on happened not to trigger it. "Recorder on
  exited cleanly" is therefore not evidence of innocence; the crash with the
  recorder inert is the evidence that matters.
- [x] Handled so it costs no data. `capture_human_runs` validates the telemetry
  before consulting the exit code, registers a session whose laps are complete,
  and records `status: complete_after_abnormal_exit` in the manifest. Both study
  captures so far (Y001 08:52, A001 10:42) exited 3221225477 and both hold five
  complete laps, verified against TORCS's own `last_lap_time_s`.
- [x] `racecoach recover-capture <dir>` registers a folder an earlier build
  abandoned, taking the identity from its manifest. Used to rescue Y001, whose
  manifest now reads `complete_after_recovery`.
- [ ] Root cause still unknown. Cheapest next step needs no reproduction: the
  Application event log already holds an Application Error entry per crash naming
  the faulting module.

      Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='Application Error'} -MaxEvents 40 |
        Where-Object { $_.Message -match 'wtorcs' } |
        ForEach-Object { "--- $($_.TimeCreated)"; ($_.Message -split "`r?`n")[0..7] }

  A faulting module of `human.dll` would put our code back in scope; an OpenGL
  or GLUT module would close it.
- [ ] One residual doubt the A/B cannot settle: `gWriters[]` is a static array
  linked into `human.dll` whichever way the environment is set, so its teardown
  runs in both arms. Excluding that needs a build without the overlay, which is
  why the faulting module is worth reading first.
- [ ] Watch whether crash frequency tracks session length. Both crashing study
  captures were full five-lap sessions of roughly six and a half minutes; the
  clean manual runs were shorter. Five samples support no conclusion, only the
  question.

## Task 36: TORCS sometimes dies within a second of launching

- [x] Distinct from Task 35, on the evidence: same module, different fault
  address. `client.dll` offset `0x1875d1` here against `0x49335` for the
  end-of-race crash, and this one happens about a second after launch, before
  any race. The `client.dll` timestamp in the report (`0x6a863061`) is the build
  that carries the Task 35 fix, which also confirms the three clean acceptance
  runs were made against a fixed build.
- [x] Costs no data and is not a study blocker. The failed attempt registered
  `status: no_data` with an empty `runs` list; retrying immediately produced a
  normal five-lap capture. Seen once so far.
- [x] The message now tells the participant what to do. No CSV at all means the
  recorder never opened its file, so the simulator never reached a race -- a
  retry, not a redo. That is now said in those words, while a file with a header
  and no samples still says to select a human driver and drive a session. The
  exit code travels in both.
- [ ] Root cause unknown. `client.dll` also compiles `libs/musicplayer`
  (including `OpenALMusicPlayer.cpp`), and `startMenuMusic()` is the last thing
  `TorcsEntry()` does, which fits the timing -- but `isEnabled()` defaults to
  disabled, so read `torcs-profiles/apex-study-v1/config/sound.xml` before
  believing it. Watch the frequency; one occurrence justifies no more than that.

## Task 37: the in-race minimap draws the car but not the track

- [x] Mechanism identified. `cGrTrackMap`'s constructor draws the track into the
  back buffer and bakes it into a texture with
  `glReadPixels(0, 0, texturesize, texturesize, GL_RGBA, GL_BYTE, ...)`
  (`grtrackmap.cpp:336`), where `texturesize` is the largest power of two not
  above `MIN(grWinw, grWinh)`. The car dots are drawn live every frame. So an
  outline that never appears while the dot does means the one-time bake came
  back blank -- it is not a view-mode problem, since `TRACK_MAP_NONE` would draw
  nothing at all.
- [x] Not the driver. A menu-started race on the same machine draws the outline
  in full; only `-R` loses it. That also clears the `GL_BYTE` readback, which was
  the earlier suspect.
- [x] Root cause and fix. `main.cpp` calls `ReRunRaceOnGUI` *before*
  `glutMainLoop()`, and `ReStateManage` runs the chain synchronously, so
  `ReRaceStart` -> the graphic module's `initView` (`grmain.cpp:255`) ->
  `initBoard` -> `new cGrTrackMap` all executed against a window that had never
  drawn a frame, and the bake read an undrawn back buffer. Started from the menus
  the same chain runs inside a GLUT callback with a realised window. Fixed by
  handing the start to `glutTimerFunc`, so it happens inside the event loop where
  the menu route has always run it. Patch applies to the pristine archive with no
  fuzz, and compiles and links on Linux.
- [x] Confirmed on Windows: the outline is drawn.
- [x] Regression the deferral caused, and its fix. Moving the start into the
  event loop meant the splash screen now got to draw a frame, and
  `splashDisplay` is what sets `SplashDisplaying` (`splash.cpp:112`) -- not the
  splash still being on screen. Its seven-second `glutTimerFunc` then fired
  mid-lap and called `TorcsMainMenuRun()`, so the participant was dropped into
  the main menu with the engine still audible behind it. Before the deferral the
  splash never drew, the flag stayed 0, and the timer was inert; the fix had
  quietly armed it. `TorcsSuppressSplashTimer()` now guards that timer, declared
  in `client.h` and called from both `main.cpp` files on the `-R` branch --
  main.cpp because it links both libraries, where `raceinit.cpp` calling into
  `libclient` would invert the existing dependency. Exported from `client.def`
  alongside `ReRunRaceOnGUI` by the same in-place edit in `build-windows.ps1`.
- [ ] Reverify on Windows after the rebuild: outline present, no jump to the main
  menu during a full five-lap session, and the Task 35 exit code still 0. The
  splash-timer regression is exactly the kind a single short test would miss --
  it needs more than seven seconds of driving to show up.
- [ ] Not a study blocker. Apex's own replay draws the track from the recorded
  `x`/`y`, so participants still get a map; this is the simulator's HUD only.

## Task 38: -R only strips forward slashes from the race config path

- [ ] `ReRunRaceOnGUI` derives `_reFilename` with `strstr(s, "/")`, copied from
  upstream's `ReRunRaceOnConsole`. On Windows the configuration is passed as
  `D:\Apex\...\apexstudy.xml`, so nothing is stripped and `_reFilename` keeps
  the whole path. `racemanmenu.cpp:214` builds `results/<_reFilename>/<file>`
  from it, which cannot be a sane path. No observed symptom yet -- results saving
  may simply never be exercised in the study flow -- and it was deliberately left
  out of the Task 35/37 fix so that acceptance measures one change at a time.
