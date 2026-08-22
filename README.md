# F1 Summer Project — Personal Workspace

Personal working repository for the University of Bristol × IBM F1 telemetry
and AI driver-coaching summer project.

## Purpose

- Synchronise source code, research notes, tests, and decisions between the
  personal computer and the university Linux workstation.
- Keep an attributable record of individual technical contributions and
  experiments for the individual report.
- Maintain small, reproducible fixtures and generated-result instructions
  without committing credentials or large raw datasets.

## Contents

- `src/`, `tests/`, `configs/` — canonical Apex application, telemetry core,
  `racecoach` CLI, configuration, and tests.
- `integrations/torcs-1.3.9/` — verified native Granite/SCR robot overlay for
  the supplied TORCS source archive.
- `integrations/granite-4.1/` — pinned llama.cpp build, IBM GGUF download, and
  loopback model-server scripts.
- `docs/GRANITE_TORCS_WALKTHROUGH.md` — student-reproducible end-to-end guide.
- `apex/` — legacy recovery snapshot retained for provenance; do not develop
  both copies in parallel.
- `prompt/` — consolidated project and supervisor guidance.
- `apex-screenshots/` — small reference screenshots for the current prototype.
- `Apex App Design (F1 Coach Desktop).md` — application design and milestone
  record.
- `keypoint.md` — current implementation notes; keep authorship and provenance
  explicit when updating it.

Shared team deliverables, damaged placeholder files, raw telemetry/video,
virtual environments, build products, and credentials are intentionally not
tracked here. The team repository remains the source of truth for shared work.

## Linux quick start

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
QT_QPA_PLATFORM=offscreen pytest -q
```

## Granite 4.1 + TORCS

The current vertical slice connects TORCS 1.3.9 to a deterministic Python
driver over a native non-blocking robot bridge. IBM Granite 4.1 runs as an
asynchronous race engineer: it reads compact telemetry and returns strict,
evidence-grounded JSON advice, but never controls the car.

Open `apex` and select **Live Pit Wall** for the visible live experience. It
shows bridge/model health, lap/time/speed, four-wheel tyre wear, temperature,
pressure and graining, plus the latest validated Granite advice, evidence and
latency. TORCS' own Driver Board also shows the sidecar state and a bounded
summary of fresh advice; press the number key **1** during the race if that
board is hidden.

For post-lap coaching, import a CSV into a session or open a captured session in
**Garage**. Apex checks every lap automatically: the session best receives
single-lap technique checks and each other lap is compared with that best lap.
The Status column shows queued, generating, ready, failed, or setup-required
state while one local **Granite 4.1** request runs at a time. Opening a lap in
**Analysis** (select it and choose **Analyze selected lap**, or double-click it)
restores that exact result and exposes the one-time coach download when needed;
**Review a corner** reuses its validated
advice only when the cited corner and distance span exactly match the measured
stretch. Each card can zoom the plotted telemetry to its evidence. A successful
result is retained in the session's coaching audit directory and is revalidated
before reuse. Sessions can be deleted from Garage after explicit confirmation;
only Apex-managed copies and their audits are removed.

The deterministic Granite Bridge capture defaults to three laps through
`configs/race.toml`, providing enough data for consistency and optional
lap-to-lap comparison while retaining single-lap coaching when only one lap is
complete.

For human telemetry, open **Collect Data** in Apex. The guided workflow checks
study readiness, shows the locked assignment, opens TORCS directly on the
three-lap `aalborg` Human session, records and validates the drive, and registers
complete laps in the Garage. It also attempts to record only the TORCS race
window and reports the footage outcome explicitly. That video is for synchronized
human review; current AI coaching receives validated telemetry, not video frames.
Participants do not use a terminal or select the TORCS driver, track, vehicle,
or lap count. See the
[human capture guide](docs/HUMAN_TELEMETRY_CAPTURE.md) for the build and pilot
acceptance procedure.

Facilitators can validate the same software pipeline before recruitment with a
fixed TORCS built-in robot. Launch `APEX_RESEARCH_MODE=1 apex` and open
**Robot Pilot**, or run `racecoach capture-synthetic --count 3`. The default is
three sequential sessions of three laps using pinned `berniw` index 9,
`g-track-1`, and `car7-trb1`. Synthetic evidence is stored separately and is
never presented as participant data or proof that coaching improves lap time.

Start with the
[Granite/TORCS walkthrough](docs/GRANITE_TORCS_WALKTHROUGH.md) and the
[TORCS build notes](integrations/torcs-1.3.9/README.md). A contract-only demo
that needs no model download is:

```bash
PYTHONPATH=src python -m racecoach.granite.smoke --mock
```

Never commit API keys. Use environment variables or a local ignored `.env`
file, and provide only `.env.example` templates when configuration needs to be
documented.
