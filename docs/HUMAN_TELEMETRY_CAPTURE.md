# Human TORCS Telemetry Capture

This is the reproducible data-collection path for human driving. Apex is the
single participant-facing launcher: its **Collect Data** guide opens TORCS,
records the existing `human` driver at the robot callback cadence (normally
about 50 Hz), validates the result, and opens captured laps for analysis. It
does not replace, delay, or change keyboard, gamepad, or wheel commands.

## Participant workflow — no terminal required

1. Open the **Apex** desktop application.
2. Choose **Collect Data** in the top navigation.
3. Enter the assigned pseudonymous participant id and select the study phase.
4. Complete the fixed-track/car, input-device, and quiet-space checks.
5. Select **Open TORCS and start recording**.
6. Apex opens the assigned five-lap Human session directly. Drive the session;
   do not change the track, vehicle, driver, or lap count. Exit TORCS when the
   assigned laps are complete.
7. Back in Apex, choose **Open captured laps** to split complete laps and enter
   the Garage. No CSV selection or terminal command is required.

TORCS deliberately opens as a second, Apex-managed simulation window. Treating
the two processes as one product avoids a fragile fork of TORCS' legacy OpenGL
renderer while still giving participants one launcher and one guided workflow.

## Build once

```bash
integrations/torcs-1.3.9/verify-human-capture.sh
integrations/torcs-1.3.9/verify-study-preset.sh
integrations/torcs-1.3.9/build.sh install
```

The verifier compiles and runs the CSV contract test, compiles the recorder
adapter against the exact pinned TORCS headers, and proves that the minimal
human-driver patch applies to the verified archive. The preset verifier also
proves that graphical `-R` remains distinct from headless `-r`, installs one
Human driver, and locks the development track and lap count. `build.sh install`
produces the default executable at:

```text
/tmp/apex-torcs-$UID/torcs-runtime/bin/torcs
```

## Facilitator/automation fallback

The CLI remains available for automated checks and recovery. Use a pseudonymous
study id, never a participant's name or email:

```bash
racecoach capture-human --participant-id P001 --phase baseline
```

To use another installed binary or pass TORCS arguments in an operator-led
recovery session:

```bash
racecoach capture-human \
  --participant-id P001 \
  --phase coached \
  --torcs /path/to/torcs \
  -- -r /path/to/practice.xml
```

The fallback command intentionally leaves race selection to the facilitator;
the Apex desktop workflow does not. For a fallback session:

1. Choose a fixed Practice or Quick Race configuration.
2. Select the normal **Human** driver, not Granite Bridge.
3. Complete the familiarisation or measured laps defined by the protocol.
4. Exit TORCS after the session. The command then validates and registers the
   captured CSVs and prints their run ids.

## Controlled graphical preset

The desktop workflow uses `Apex Study v1` (`apex-study-v1`): road track
`g-track-1`, the normal Human driver backed by `car7-trb1`, and five Practice
laps. Apex shows these assignments before Start, launches TORCS with the
graphical `-R <race.xml>` entry, and stores the complete preset identity in
`manifest.json`. Standard `-r` remains the console/headless path and is never
used for participant driving.

This is a development preset for engineering and pilot runs. Freeze a new
versioned preset after pilot evidence and supervisor approval rather than
silently editing `apex-study-v1` during a study.

The raw evidence and manifest are stored under:

```text
~/Apex/captures/human/P001-baseline-YYYYMMDD-HHMMSS/
```

Validated copies used by analysis are stored under `~/Apex/runs/`. Set
`APEX_WORKSPACE` to put both trees on an approved study-data volume.

## What is recorded

Each CSV declares `schema_version=apex-human-v2` and includes more than 100 raw
or directly exposed TORCS channels:

- simulation time/delta, sample number, car/track identity and race type;
- lap counters and times, race position, distance, damage and collision state;
- throttle, brake, steering, clutch and requested/actual gear;
- body/world speed in m/s, body acceleration in m/s², position and attitude;
- engine angular speed in rad/s plus the explicit rpm conversion and fuel in L;
- track offset/width, normalized position, heading angle, segment geometry and
  surface properties;
- per-wheel spin, brake-temperature ratio, slip velocities, vertical
  ground-contact load in N, tyre wear, temperature in °C, pressure in kPa and
  graining.

Wheel order is front-right, front-left, rear-right, rear-left. `track_to_start_m`
converts TORCS' curve-segment arc to distance; no radian value is mislabeled as
metres.

`*_force_z_n` is the vertical load TORCS publishes as `priv.reaction[i]`
(`simuv2/wheel.cpp:368`). It is the only per-wheel force a driver module can
observe: 1.3.9 declares `tWheelState::Fx/Fy/Fz` but no simulation module writes
them, so no longitudinal or lateral tyre force exists to record. Schema
`apex-human-v1` read those dead members and therefore emitted constant-zero
`*_force_x_n`, `*_force_y_n` and `*_force_z_n` columns for all four wheels;
`apex-human-v2` drops the two unobtainable columns and sources `*_force_z_n`
from the published channel. The synthetic `berniw` recorder changed identically,
from `apex-robot-v1` to `apex-robot-v2`. Files captured under the v1 schemas
keep their zero columns and are not upgraded in place. Text values use CSV quoting, and the writer flushes about once per second
so an abnormal simulator exit loses at most a short tail under normal cadence.

The callback pairs the current observable car state with the human control
command selected for the next physics update. This is the normal control-loop
meaning of one row; it is not a post-physics reconstruction.

## Integrity and failure handling

`manifest.json` records the pseudonymous id, phase, exact TORCS command, locked
study preset, start and finish times, process result, output filenames, row
counts, SHA-256 hashes, and registered run ids. It is written atomically.

- A missing/non-executable TORCS binary fails before a capture directory is made.
- A missing preset file disables Start before a capture directory is made and
  gives the facilitator a repair message.
- A non-zero simulator exit preserves raw files and marks the manifest failed;
  it does not silently register them as valid observations.
- Every CSV is checked before any file from the session is registered.
- For a locked study launch, every non-empty CSV must report the assigned
  internal track id, car model, Human driver module, and initial lap count;
  otherwise the whole capture is rejected as `invalid_telemetry`.
- A header-only or absent CSV is a readable `no_data` failure.
- Native writer errors disable recording and return immediately; they never
  change the driver's actuator values.

Generated participant telemetry is local research data and must not be committed
to Git. Back it up according to the approved data-management plan.

## First real-lap acceptance check

Before recruiting participants, collect one internal run and verify:

- the manifest status is `complete` and its SHA-256 matches the raw CSV;
- cadence is close to 50 Hz without long gaps;
- throttle, brake and steer visibly follow the chosen input device;
- at least one complete lap imports and can be analyzed by `racecoach analyze`;
- lap time matches the TORCS result within the documented start-line sampling
  tolerance;
- off-track and damage events agree with an observer's notes or video.

Passing this check establishes engineering readiness only. It does not establish
that coaching improves human driving; that requires the planned controlled study.

## Packaging contract

For a participant installation, ship the verified `torcs-runtime/` directory
beside the Apex executable or inside the PyInstaller bundle root. Apex discovers
that runtime automatically. Repository development builds fall back to the
user-local runtime produced by `build.sh install`; `TORCS_PREFIX` remains an
operator-only override. If the runtime is absent, the guide disables Start and
shows a facilitator-facing repair message rather than exposing a filesystem path
or terminal instructions to the participant.
