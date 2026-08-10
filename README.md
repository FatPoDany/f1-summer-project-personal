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

- `apex/` — Apex desktop application, telemetry core, `racecoach` CLI, tests,
  and technical documentation recovered from the local development snapshot.
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
cd apex
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
QT_QPA_PLATFORM=offscreen pytest -q
```

Never commit API keys. Use environment variables or a local ignored `.env`
file, and provide only `.env.example` templates when configuration needs to be
documented.
