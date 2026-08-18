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

- [ ] The current coach output is built for researchers reading traces. A
  participant needs the specific stretches of track they lost time on, replayed
  with a plain-language conclusion per stretch.

## Task 32: Granite without a terminal

- [ ] The app must start and manage the model itself. Participants cannot run a
  server from a command line, so today the coaching path is unreachable for them.
  Delivery method is an open decision: bundling the weights would take the
  installer from 380 MB to several GB.
