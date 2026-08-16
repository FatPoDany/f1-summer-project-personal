# Spec: TORCS Reference Robot Recorder

## Objective

Add an opt-in recorder to the pinned TORCS 1.3.9 built-in `berniw` module so
index 9 produces the same verified telemetry fields and units used by the
existing analysis pipeline. Recording observes the completed robot command at
the normal callback cadence and never changes vehicle control.

## Tech Stack and Commands

- TORCS 1.3.9, GNU++98-compatible C++, existing CSV writer conventions.

```bash
sha256sum -c integrations/torcs-1.3.9/SHA256SUMS
integrations/torcs-1.3.9/verify-reference-recorder.sh
integrations/torcs-1.3.9/build.sh prepare
```

## Project Structure

- `integrations/torcs-1.3.9/overlay/src/drivers/berniw/` — robot recorder sources.
- `integrations/torcs-1.3.9/patches/berniw-telemetry.patch` — minimal pinned-source hooks.
- `integrations/torcs-1.3.9/verify-reference-recorder.sh` — compile/source contract.
- `integrations/torcs-1.3.9/robot_telemetry_writer_test.cpp` — CSV contract test.

## Code Style

```cpp
if (directory == NULL || directory[0] == '\0') return;
```

Keep bounded arrays and filenames, explicit unit conversions, buffered output,
safe CSV escaping, no exceptions across the TORCS ABI, and no network/model work.

## Testing Strategy

- Compile the recorder and patched module contract against the exact archive headers.
- Run a standalone writer test covering header, schema, quoting, flush, and close.
- Source checks prove start/record/stop hooks exist once and control assignments are unchanged.
- Real runtime verification checks cadence, three complete laps, controls, and provenance.

## Boundaries

- Always: opt-in environment variable, index-9 allowlist, SI units, fail-safe
  recorder disablement, periodic flush, preserved robot control path.
- Ask first: patching another built-in robot or changing the telemetry row schema.
- Never: write when capture is disabled, mutate actuators, call Granite, block
  the drive callback, or label a row as human.

## Success Criteria

1. Ordinary TORCS and all other `berniw` indices produce no Apex robot CSV.
2. An enabled index-9 session produces parseable telemetry at the robot callback cadence.
3. Rows identify `driver_module=berniw`, `car_model=car7-trb1`, and a robot schema.
4. Recorder failures disable recording without escaping into or changing `rbDrive`.
5. The exact-archive verifier and prepared-source idempotency checks pass.

## Open Questions

- None for version one; broader controller qualification is a separate module/version.
