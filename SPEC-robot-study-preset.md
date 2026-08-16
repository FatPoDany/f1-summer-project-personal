# Spec: Robot Study Preset

## Objective

Ship a versioned unattended race configuration for one isolated reference
robot. It fixes the human development study's track and car family, uses a
shorter three-lap synthetic assignment, and invokes TORCS' existing console
race entry so a batch finishes without menu or result-screen interaction.

## Tech Stack and Commands

- TORCS racemanager XML and shell source-contract verification.

```bash
integrations/torcs-1.3.9/verify-robot-study-preset.sh
integrations/torcs-1.3.9/build.sh install
```

## Project Structure

- `integrations/torcs-1.3.9/overlay/src/raceman/apexrobotstudy.xml` — preset.
- `integrations/torcs-1.3.9/verify-robot-study-preset.sh` — exact assignment checks.
- `src/racecoach/telemetry/synthetic_capture.py` — matching typed preset evidence.

## Code Style

```xml
<attstr name="module" val="berniw"/>
<attnum name="idx" val="9"/>
```

All experimental assignments are explicit XML values and are duplicated in a
validated Python contract only for preflight and manifest evidence.

## Testing Strategy

- Source checks assert one driver, exact module/index, `g-track-1`, three laps,
  and unattended-compatible display/results settings.
- Python tests prove the command uses `-r <preset>` without a shell.
- Installed-runtime inspection proves the exact file ships.

## Boundaries

- Always: one robot per session, fixed versioned id, same track/car/laps,
  penalties and physical factors retained.
- Ask first: changing track, car, lap count, robot, weather, fuel, or adding opponents.
- Never: reuse the human preset id, silently edit a frozen preset, or present
  this race as a measured participant session.

## Success Criteria

1. The preset contains only `berniw` index 9 and resolves to `car7-trb1`.
2. It assigns `g-track-1`, three laps, and exits unattended through `-r`.
3. The verifier fails on any assignment drift.
4. The preset id and file identity are recorded in every synthetic manifest.

## Open Questions

- Runtime qualification will determine whether additional deterministic race
  settings need to be frozen in a new preset version.
