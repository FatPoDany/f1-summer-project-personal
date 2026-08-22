# Spec: Garage Coaching Automation and Footage Truth

## Objective

Make post-session coaching complete and inspectable without requiring a driver to
open every lap and press a button. The Garage automatically checks each managed
lap for an exact, currently valid Granite report, queues missing work one lap at
a time, and shows its live state in the Status column. Lap Analysis and Review a
corner reuse the same validated and audited report.

Footage remains a synchronized human-review aid. The installed text-only Granite
path receives frozen telemetry evidence, not pixels, so Apex must never imply
that the model inspected a recording.

## Tech Stack

- Python 3.11+, PySide6/Qt 6 background workers
- Existing `Lap`, `Session`, evidence summary, coaching validator, and audit contracts
- Existing pinned local IBM Granite 4.1 3B GGUF and `llama-server`
- Existing ffmpeg race-window recorder and Qt Multimedia replay pane

## Commands

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q tests/test_garage_view.py
QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q tests/test_coach_panel.py
QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q tests/test_analysis_view.py
QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q tests/test_capture_view.py tests/test_human_capture.py tests/test_screen_capture.py
.venv/bin/ruff check .
QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q
```

## Project Structure

- `src/apex/garage_view.py` presents per-lap coaching states and requests session work.
- `src/apex/coaching_queue.py` owns automatic exact-context jobs on the shared serial
  Granite pool; `coach_panel.py` handles the interactive view on that same resource.
- `src/apex/analysis_view.py` maps validated report findings onto reviewable stretches.
- `src/apex/capture_view.py` and `src/racecoach/telemetry/human_capture.py` keep TORCS
  capture independent and bounded.
- `src/f1coach_core/` remains the source of telemetry truth, evidence, validation,
  and audit persistence.
- `src/racecoach/telemetry/screen_capture.py` records and cuts footage but never
  supplies evidence to the model.

## Code Style

Use existing frozen domain contracts and Qt signals. Long model/capture work runs
on dedicated workers; GUI slots only update state and render validated outcomes.
Status values are explicit text, not colour-only indicators.

## Testing Strategy

- Prove a TORCS process and an ffmpeg process cannot leave Stop waiting forever.
- Prove Granite work and TORCS capture do not share a worker pool.
- Prove a loaded session requests coaching for every lap, with the best lap using
  single-lap checks and every other lap using that best lap as reference.
- Prove exact saved reports are restored rather than regenerated.
- Prove queued, generating, ready, setup-needed, unavailable, and failed states
  appear in Garage without blocking Qt.
- Prove Review a corner receives advice only from a validated report finding whose
  cited corner/span matches the deterministic debrief stretch.
- Keep the real Windows TORCS-window and recording check as an explicit runtime gate.

## Boundaries

- Always: one Granite inference at a time; exact-context restore; current evidence
  revalidation; success/failure audit; visible state; deterministic controller ownership.
- Ask first: downloading 2.1 GB merely by opening Garage, adding another model,
  transmitting footage, or expanding the study/privacy protocol.
- Never: block TORCS behind model work, infer facts from video without a validated
  vision contract, publish uncited model prose, or let AI control the car.

## Success Criteria

1. Starting Collect Data is not delayed by outstanding Granite requests.
2. Stop terminates stubborn TORCS/ffmpeg processes within bounded grace periods
   and returns the guide to a terminal state while preserving available raw evidence.
3. Loading a Garage session automatically checks every lap and queues only missing
   exact-context reports, serially.
4. Status visibly distinguishes queued, generating, ready, failed, setup-needed,
   and unavailable coaching while retaining the session-best marker.
5. A verified installed model or configured endpoint starts missing work
   automatically; an absent model is never downloaded without an explicit user action.
6. Review a corner shows matched personalized advice from the same validated audit
   record restored by AI Race Engineer; unmatched stretches remain explicitly measured
   but unadvised.
7. The UI and documentation state that current AI advice is telemetry-grounded and
   that footage is for synchronized playback, not a model input.

## Open Questions

- A future vision-coaching capability needs a separately selected multimodal model,
  frame/clip evidence contract, privacy review, evaluation, and provenance scheme.
  It is deliberately not inferred from the presence of an MP4 recorder.
- A real Windows run is required to prove the packaged title-based recorder captures
  the patched TORCS window and produces a playable MP4.
