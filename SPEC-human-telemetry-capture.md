# Spec: Human TORCS Telemetry Capture

## Objective

Add an opt-in, non-controlling telemetry recorder to the pinned TORCS 1.3.9
human driver. A researcher launches the simulator through `racecoach`, drives
with the existing keyboard, gamepad, or wheel controls, and receives a raw
multi-lap CSV that is validated and registered in the existing Apex run store.

The native recorder and command-line path remain the testable foundation. The
participant-facing release exposes the same workflow as an Apex desktop guide:
prepare the session, open TORCS with one button, drive, then validate and open
the captured laps. TORCS remains a separate managed simulation window;
embedding its legacy OpenGL surface inside Qt is outside this feature boundary.

The study launch path also owns one versioned graphical race preset. Apex passes
that preset to the pinned TORCS runtime, which opens the normal human-drivable
OpenGL race while skipping the track, driver, and race-configuration menus. The
standard TORCS `-r` switch is not used because it deliberately selects a headless
console mode in which human drivers are rejected.

## Tech Stack

- Python 3.11+, `argparse`, `subprocess`, existing pandas run-store validation
- TORCS 1.3.9 human driver, GNU++98-compatible C++
- pytest and a standalone native compile/contract check

## Commands

```bash
# Focused Python tests
QT_QPA_PLATFORM=offscreen pytest -q tests/test_human_capture.py

# Native source/ABI contract check
integrations/torcs-1.3.9/verify-human-capture.sh

# Full quality gate
ruff check .
QT_QPA_PLATFORM=offscreen pytest -q

# User workflow
racecoach capture-human --participant-id P001 --phase baseline

# Participant workflow
apex  # choose Collect Data; no terminal or CSV handling after launch
```

## Project Structure

- `src/racecoach/telemetry/human_capture.py` — validated launch/import workflow
- `src/racecoach/cli.py` — user command and readable status output
- `src/apex/capture_view.py` — interactive prepare/drive/saved desktop guide
- `src/apex/capture_task.py` — cancellable background simulator lifecycle
- `integrations/torcs-1.3.9/overlay/src/drivers/human/` — native CSV recorder
- `integrations/torcs-1.3.9/overlay/src/raceman/apexstudy.xml` — versioned study preset
- `integrations/torcs-1.3.9/patches/graphical-race.patch` — pinned GUI auto-start entry
- `integrations/torcs-1.3.9/patches/` — minimal pinned-source integration patch
- `tests/test_human_capture.py` — Python behavior tests
- `docs/HUMAN_TELEMETRY_CAPTURE.md` — build and collection protocol

## Code Style

```python
@dataclass(frozen=True)
class HumanCaptureConfig:
    participant_id: str
    phase: str
    torcs_binary: Path
```

Use frozen validated contracts, explicit domain errors, pathlib paths, readable
messages, and no shell command construction. Native code remains GNU++98
compatible with bounded filenames and buffered output.

## Testing Strategy

- Unit tests validate pseudonymous identifiers, safe subprocess environment,
  no-output failures, multiple generated files, and run-store registration.
- The native verifier compiles the recorder against the exact pinned TORCS
  headers and runs a standalone CSV-writer contract test.
- Existing run-store tests prove the output remains consumable by analysis.
- Qt tests cover readiness gating, non-blocking launch, cancellation, result
  presentation, and automatic opening of captured laps in the Garage.
- Source-contract tests prove the distinct graphical option is patched into the
  pinned Linux launcher and cannot silently fall back to headless `-r` mode.
- Python tests prove the selected preset is validated, passed without a shell,
  and frozen into the capture manifest before TORCS starts.
- The final environment-dependent check is a real human-controlled TORCS lap.

## Boundaries

- Always: opt-in capture, SI units, explicit schema version, local-only files,
  atomic research metadata, safe CSV escaping, one recorder per human driver,
  and a visible versioned preset for measured sessions.
- Ask first: new dependencies, network services, participant identity fields,
  or changing the existing canonical telemetry schema.
- Never: names/emails, credentials, invented channels, vehicle control, model
  calls in the capture loop, committing generated participant telemetry.

## Success Criteria

1. With capture enabled, a human TORCS session produces a parseable multi-lap
   CSV at roughly 50 Hz without changing any actuator command.
2. Rows contain the canonical analysis signature plus timing, track, vehicle,
   collision, and four-wheel channels with documented units.
3. `racecoach capture-human` requires a pseudonymous participant id, launches
   without a shell, and registers every non-empty completed CSV in `runs/`.
4. Missing TORCS, invalid metadata, simulator failure, and zero captured files
   produce readable errors and no falsely finalized runs.
5. Generated raw data and research manifests remain outside version control.
6. Opening Collect Data never starts TORCS; a valid pseudonym, all readiness
   checks, and an available simulator are required.
7. Capture runs off the GUI thread, can be stopped, and opens registered raw
   data as a named Garage session without terminal or CSV interaction.
8. Apex study capture launches TORCS through a graphical option distinct from
   headless `-r`; a human driver can control the resulting race window.
9. The development preset fixes track, category, car, and lap count in one
   checked-in XML file and contains only the normal Human driver.
10. Apex shows the preset before launch, does not ask participants to select a
    track or car, and disables Start when the matching preset file is absent.
11. The capture manifest records the preset id, track, car, lap count, and race
    configuration path so every measured run retains its experimental context.

## Open Questions

- The study distribution must include the verified `torcs-runtime` directory
  beside the Apex executable (or inside PyInstaller's bundle root). Development
  builds continue to use the verified user-local runtime.
- The input-device questionnaire remains study metadata outside the raw row
  schema for this slice.
- `g-track-1`, `car7-trb1`, and five laps are development defaults for proving
  the controlled launch. The final study preset remains subject to the internal
  pilot and supervisor-approved protocol; changing it requires a new preset id.
