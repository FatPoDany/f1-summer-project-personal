# Apex

Desktop app for **AI-Enhanced Telemetry Analysis & Driver Coaching**
(University of Bristol MSc × IBM). PySide6 + PyQtGraph shell over the
pure-Python `f1coach-core`; IBM Granite becomes the coaching brain at
milestone A2. Currently developed solo — the M1–M7 lane names from the team
plan survive as module boundaries.

* Design doc: `Apex App Design (F1 Coach Desktop).md` in the project vault ·
  [full design with mockups](https://claude.ai/code/artifact/909f239e-6c63-4fe4-bf1a-dff5576011da)
* TORCS telemetry research: `M_Lin/` in the vault (field cheatsheet + 50 Hz
  high-frequency exporter report) — drives the `f1coach_core.torcs` adapter.

## Milestones

- [x] **A0 · Spike — gate** (12 Jul) — scaffold, window, CSV → speed trace.
      Passed 6 Jul: quickstart ran end-to-end on macOS (Python 3.14,
      PySide6 6.11); stack signed off, name stays **Apex**, `dist` stays
      optional-with-derivation.
- [x] **A1 · Chart core** (21 Jul) — synced speed/throttle/brake strips with
      one crosshair, sector ribbon with timing colours, reference-lap overlay,
      Garage (session library, lap deltas, import, watched folder), TORCS
      run import. Done 7 Jul.
- [x] **A2 · Coach skeleton — gate** (4 Aug) — coaching JSON contract +
      validator, features stage (corner detection, evidence summary), mock
      provider grounded in real evidence, AI Race Engineer panel with
      "◈ show" evidence-zoom. Done 7 Jul — gate cleared 4 weeks early, so
      the Dash parachute stays packed.
- [x] **A3 · Full flow** (18 Aug) — watsonx.ai + local Ollama Granite
      providers with live streaming and a provider picker, Compare view
      (cumulative time delta), self-contained HTML report export,
      PyInstaller `Apex.app` (152 MB, smoke-tested). Done 7 Jul — the
      watsonx path is validated against an injected fake; first real
      credentialled call still pending.
- [x] **A4 · Hardening** (1 Sep) — graceful failure states across providers,
      import, export, and startup (crash-dialog excepthook; every failure is a
      readable, actionable sentence); per-run coaching audit trail (evidence +
      exact prompt + raw response + verdict, as JSON beside the session, with
      an **Audit…** viewer in the panel); M7 usability pass. Done 11 Jul —
      live watsonx/Ollama validation still pending (needs credentials / a
      local Ollama install).
- [ ] **A5 · Ship** (9 Sep)
- [ ] CI green on ubuntu + macos (lights up once pushed to GitHub)

## Quickstart

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,watsonx]"
apex                          # Garage opens on the bundled sample session
apex path/to/lap.csv          # canonical lap -> straight into Lap Analysis
apex path/to/torcs_run.csv    # TORCS run -> split into laps, lands in Garage
pytest                        # 76 tests, headless-safe
python scripts/screenshot.py  # Garage/Analysis/Compare/audit PNGs for blog posts
```

Python ≥ 3.11. On headless boxes run Qt things with `QT_QPA_PLATFORM=offscreen`.
The workspace lives at `~/Apex/sessions/` (override with `APEX_WORKSPACE`).

## racecoach — post-race pipeline CLI

The CLI face of the same package family, for whole TORCS exporter *runs*
(multi-lap raw CSVs; see `docs/DATA_AVAILABILITY.md` for exactly what they
contain and how they were verified):

```sh
racecoach import path/to/run.csv   # -> <workspace>/runs/<run_id>/{telemetry.csv,meta.json}
racecoach list                     # stored runs with laps/cadence/cars
racecoach analyze <run_id>         # rule-based metrics -> runs/<run_id>/metrics.json
```

`analyze` detects, per lap and with distance spans: off-track excursions
(|2·toMiddle/width| > 1), collisions (damage deltas), throttle+brake overlap,
steering jerks, and wheel lock-ups — each detector skips with a readable note
when its columns are missing. Sections (T1/S1…) come from the exporter's
`track_seg_type` ground truth, with per-lap speed envelopes and braking
points. The metrics JSON is the only evidence the LLM feedback stage is
allowed to cite. `racecoach run` (live Python-controlled capture over SCR)
lands once the simulator environment exists.

## Layout

```
src/f1coach_core/        pure Python, no Qt — loaders/analysis/coaching
  schema.py                telemetry contract v1
  loader.py                CSV -> validated Lap; readable errors by design
  lap.py · session.py      the objects every front end renders
  analysis.py              sector spans/times
  features.py              corner detection, evidence summary, time-delta trace
  coach.py                 coaching contract v1, validator, provider registry, mock
  llm.py                   versioned coach prompt + JSON extraction (shared)
  audit.py                 per-run audit records: prompt, raw response, verdict
  watsonx_coach.py         watsonx.ai Granite, streaming, keychain credentials
  ollama_coach.py          local Ollama Granite (offline demo insurance)
  report.py                self-contained HTML report (inline SVG charts)
  torcs.py                 Lin's high-freq TORCS exports -> canonical laps
  workspace.py             ~/Apex/sessions store, import, watched-folder logic
  sample.py + data/        bundled 3-lap synthetic session (no network needed)
src/apex/                desktop shell, Qt lives here only
  app.py                   entry point, dark Fusion + Carbon palette
  main_window.py           toolbar nav, menu, drag-and-drop routing
  garage_view.py           session library · lap table · import · watch folder
  analysis_view.py         reference picker · readout · timing colours · export
  coach_panel.py           AI Race Engineer: provider picker, streaming, ◈ show
  compare_view.py          two-lap cumulative time-delta (before/after coaching)
  widgets/strip_stack.py   ribbon + synced strips + crosshair + evidence-zoom
src/racecoach/           post-race pipeline CLI (run store, events, metrics)
  telemetry/run_store.py   <workspace>/runs/<id>/ — raw run + meta, offline-reproducible
  analysis/events.py       off-track, collision, pedal overlap, steer jerk, lock-up
  analysis/sections.py     straight/corner sections from track_seg_type ground truth
  analysis/metrics.py      the run metrics JSON (sole LLM evidence source)
scripts/                 sample-session generator · screenshot renderer
tests/                   pytest (loader, analysis, session, workspace, torcs, shell, racecoach)
```

House rule: **the app never computes telemetry truth** — it renders what
`f1coach_core` returns. CLI, notebooks, and app are three faces of one package.

## Telemetry contract v1

One lap per CSV, one row per sample, optional leading `# key: value` metadata
(`# schema_version: 1`; absent means 1). Units are SI in files; the app
converts for display. Unknown extra columns are kept, not rejected.

| column | unit | notes |
|---|---|---|
| `t` | s | non-decreasing |
| `dist` | m | optional — loader derives `cumsum(speed·Δt)` when absent |
| `speed` | m/s | displayed as km/h |
| `throttle`, `brake` | 0–1 | |
| `steer` | −1…1 | negative = left |
| `gear` | int | −1 reverse, 0 neutral |
| `sector` | 1–3 | optional; TORCS imports derive thirds of track length |

**TORCS runs** (Lin's simuv2 exporter, ~50 Hz, 249 columns): detected by
column signature, split at start-line crossings; each complete lap is written
as a canonical CSV with provenance comments. Incomplete fragments (grid start,
cut-off final lap) are skipped and reported.

## Coaching contract v1

Every provider (mock now; watsonx + Ollama at A3) receives the **evidence
summary** from `features.build_evidence_summary` — lap/reference metadata,
total delta, and per-corner zones (`span_m`, apex/min speeds, brake points,
throttle points, `time_lost_s`) — and must return:

```json
{"findings": [{"issue": "...", "cause": "...", "action": "...",
               "confidence": 0.9,
               "evidence": [{"metric": "min_speed", "corner": "T1",
                             "value": 73.9, "ref": 93.6, "unit": "km/h",
                             "span_m": [510.0, 960.0]}]}],
 "model": "mock", "prompt_version": "mock-1"}
```

`coaching_report_from_dict` validates with readable errors and **requires at
least one evidence item per finding** ("every AI claim is clickable" is an
experience requirement, not a nice-to-have). `span_m` anchors the ◈ show
evidence-zoom. Corner labels are detection-ordered (T1…Tn along the lap),
not official track names.

## Coach providers

Pick in the AI Race Engineer panel (persisted). All three stream their raw
output live, and every response goes through the same validator. Every run —
success or failure, mock included — writes an audit record beside the session
(`…/<session>/coaching/*.json`: evidence summary, the exact prompt, the raw
response, and the validated report or the error Apex refused with); the
panel's **Audit…** button opens the latest one.

* **mock** — offline, deterministic, grounded in the real evidence summary.
  What CI, tests, and no-network demos use.
* **ollama** — local Granite: `ollama serve` + `ollama pull granite3.3:8b`.
  Override with `APEX_OLLAMA_MODEL` / `APEX_OLLAMA_URL`.
* **watsonx** — IBM watsonx.ai Granite (`ibm/granite-3-3-8b-instruct`;
  override `APEX_WATSONX_MODEL`). Credentials, in resolution order:
  1. env: `WATSONX_APIKEY`, `WATSONX_PROJECT_ID`, `WATSONX_URL` (optional,
     defaults to eu-gb)
  2. OS keychain: `python -m keyring set apex-watsonx api_key` and
     `python -m keyring set apex-watsonx project_id`

Demo insurance ladder (rehearse in this order): mock → ollama → watsonx.

## Packaged builds

```sh
pip install -e ".[package]"
pyinstaller apex.spec        # macOS: dist/Apex.app · Linux: dist/Apex/
APEX_SMOKE_TEST=1 ./dist/Apex.app/Contents/MacOS/Apex   # paints, exits 0
```

Unsigned: first launch on another Mac needs right-click → Open. The bundle
excludes the watsonx SDK (offline demo device — mock + Ollama); remove the
exclude in `apex.spec` to ship it. AppImage wrapping happens on a Linux box.

## Next: A5 — ship (9 Sep)

Final builds · quickstart polish · walkthrough chapter · video assets. Carried
over from A3/A4: the first real credentialled watsonx call and a real local
Ollama run (everything is validated against injected fakes so far).
