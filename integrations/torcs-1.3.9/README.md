# TORCS 1.3.9 Granite Bridge

This integration adds a native TORCS robot named **Granite Bridge** to the
verified `torcs-1.3.9.tar.bz2` in the repository root. Stock TORCS 1.3.9 has
no SCR network robot, so the existing Python driver cannot connect until this
overlay is built.

The bridge is deliberately small and deterministic:

- TORCS calls `rbDrive` about every 20 ms of simulator time.
- A non-blocking UDP socket exchanges SCR-compatible telemetry and actions on
  `127.0.0.1:3001`.
- Missing or stale actions cause full braking after 12 robot ticks.
- TORCS' built-in Driver Board shows the sidecar link state and the latest
  validated Granite advice; press the number key **1** during a race to cycle
  the board if those two lines are hidden.
- IBM Granite runs only in a Python background worker. It never supplies an
  actuator value and can never delay `rbDrive`.

## 1. Verify the supplied source and bridge ABI

From the repository root:

```bash
sha256sum -c integrations/torcs-1.3.9/SHA256SUMS
integrations/torcs-1.3.9/verify-bridge.sh
```

The second command compiles `granite_bridge.so` against the exact 1.3.9
headers and checks its exported TORCS entry point. It does not install the
simulator.

## 2. Install native build dependencies

TORCS is a 2000s-era OpenGL application. On Rocky Linux 8 it needs a C/C++
toolchain plus development headers for X11/XF86VidMode, OpenGL/GLU/GLUT,
SDL, OpenAL/ALUT, Vorbis, PNG/JPEG/zlib, and PLIB. Package names vary by
distribution; on a managed university host ask the administrator for the
corresponding `-devel` packages.

PLIB 1.8.5 can instead be built without root access:

```bash
integrations/torcs-1.3.9/bootstrap-plib.sh
```

On this Rocky Linux 8 university machine, the other missing development
headers and FreeALUT can also be prepared without root access:

```bash
integrations/torcs-1.3.9/bootstrap-rocky-devel.sh
```

That helper downloads signed RPMs from the machine's configured repositories,
extracts them under `/tmp`, and builds the pinned FreeALUT 1.1.0 tag. It does
not install system packages. Other distributions should use their native
development packages instead.

Both dependencies and generated TORCS sources default to
`/tmp/apex-torcs-$UID`, avoiding the repository storage quota.

## 3. Prepare, build, and install TORCS

```bash
integrations/torcs-1.3.9/build.sh prepare
integrations/torcs-1.3.9/build.sh install
```

The script verifies the 563 MiB archive before extraction, applies the
overlay, and installs a user-local runtime at:

```text
/tmp/apex-torcs-$UID/torcs-runtime/bin/torcs
```

Override `TORCS_BUILD_ROOT`, `TORCS_PREFIX`, or `TORCS_DEPS_PREFIX` when a
different temporary or persistent location is required.
The build defaults to one job because TORCS 1.3.9's generated-header phase is
not parallel-safe.

## 4. Run a race

For a multi-user server, set the same private token in the terminals that
launch TORCS and `racecoach`:

```bash
export GRANITE_BRIDGE_TOKEN="$(openssl rand -hex 24)"
```

Start TORCS, open **Practice** or **Quick Race**, and add the driver
**Granite Bridge**. Its XML uses the `pro` skill level so TORCS 1.3.9 updates
the tyre model. The car remains safely braked until the Python client
completes its handshake. Its Driver Board progresses through
`Apex: WAITING FOR SIDECAR`, `Apex: SIDECAR CONNECTED`, and—only after a
schema-validated model result—`Granite 4.1: ADVICE`. A stale sidecar is shown
as `Apex: SIDECAR LOST` while the full-brake failsafe is active.
With several cars in the race, use **Page Up/Page Down** until the camera is
following Granite Bridge; the board always belongs to the currently viewed
car. The simplest demo removes the other drivers.

In another terminal, from this repository:

```bash
PYTHONPATH=src python3.11 -m racecoach.cli run \
  --config configs/race.toml --live-coach mock --max-laps 3
```

Use `--live-coach granite` after starting the Granite server described in
[`docs/GRANITE_TORCS_WALKTHROUGH.md`](../../docs/GRANITE_TORCS_WALKTHROUGH.md).
For the visible workflow, start `apex`, open **Live Pit Wall**, and press
**Start live session** instead of running the CLI command. Do not run both
clients simultaneously; this bridge intentionally locks one active peer.

## Human-driver telemetry capture

Granite Bridge controls its own robot car, so its CSV is not human-study data.
The build overlay also adds a separate opt-in recorder to TORCS' normal `human`
driver. It observes the human callback without changing any command and writes
the canonical analysis signature plus vehicle, track, collision and four-wheel
channels.

```bash
integrations/torcs-1.3.9/verify-human-capture.sh
integrations/torcs-1.3.9/verify-study-preset.sh
integrations/torcs-1.3.9/build.sh install
racecoach capture-human --participant-id P001 --phase baseline
```

Participants should normally open Apex and use **Collect Data** instead of this
command. The desktop guide performs the same opt-in launch, validation, and run
registration in a background task, then opens complete laps in the Garage. A
packaged study build can place `torcs-runtime/` beside the Apex executable; the
application discovers it automatically without participant configuration.

For the desktop path, Apex uses graphical `-R` to open the versioned
`apexstudy.xml` directly: one normal Human driver, `g-track-1`, and five laps.
The assignment is shown before Start and frozen into the capture manifest, so a
participant does not select a driver, track, car, or lap count in TORCS.
Standard `-r` keeps its original console/headless meaning for automation.

Drive the assigned laps and exit TORCS to finalize and register the run. The
recorder is disabled unless the launcher supplies its private output directory.
See
[`docs/HUMAN_TELEMETRY_CAPTURE.md`](../../docs/HUMAN_TELEMETRY_CAPTURE.md) for
fields, units, integrity metadata, failure recovery and the pre-study real-lap
acceptance check.

## Protocol and limitations

Standard SCR state/action tags remain compatible with the Python client. The
bridge appends `tireWear`, `tireTempC`, `tirePressureKPa`, `tireGraining`,
`raceLap`, `remainingLaps`, `totalLaps`, `raceFinished`, and `simTime`.
The race fields come directly from TORCS so the sidecar does not mistake the
initial grid-to-start-line crossing for a completed lap. Wheel order is
front-right, front-left, rear-right, rear-left; wear is `0` for new and `1`
for fully worn.

The Python capture preserves raw `raceFinished` separately as
`race_finished`. When this TORCS mode ends with `***shutdown***` at the final
line before emitting that state, it records a distinct deterministic
`capture_lap_closed` marker only after the configured last lap covers at least
98% of an already observed full-lap distance. A mid-lap shutdown is therefore
still imported as an incomplete fragment.

The standard action string is unchanged unless validated advice is available.
The sidecar then appends `(coach BASE64URL)`. The unpadded token must decode
canonically to 1–31 printable ASCII bytes, matching one NUL-terminated TORCS
driver-message line. Missing or malformed values clear the old HUD advice and
never alter acceptance, clamping, or application of actuator fields. Parsing
remains bounded and non-blocking inside `rbDrive`. The Python sidecar also
expires a displayed summary after two coaching intervals, so a slow or failed
next inference cannot leave an old instruction on screen indefinitely.

TORCS 1.3.9 has no SCR focus or restart API, so `focus` is reported as five
`-1` values and incoming `focus`/`meta` fields are ignored. This first version
supports one Granite Bridge car (driver index zero) in real-time GUI mode.
Accelerated headless simulation is not yet supported because its simulated
ticks can outrun the external Python process.

The socket is loopback-only, locks an active peer, and optionally authenticates
the handshake with `GRANITE_BRIDGE_TOKEN`. Never commit that token.
