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
- [ ] **A2 · Coach skeleton — gate** (4 Aug) — evidence panel + coach card on
      the mock Granite provider, evidence-zoom ("◈ show").
- [ ] **A3 · Full flow** (18 Aug) — watsonx streaming + Ollama fallback,
      Compare view, report export, packaged builds.
- [ ] **A4 · Hardening** (1 Sep) · **A5 · Ship** (9 Sep)
- [ ] CI green on ubuntu + macos (lights up once pushed to GitHub)

## Quickstart

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
apex                          # Garage opens on the bundled sample session
apex path/to/lap.csv          # canonical lap -> straight into Lap Analysis
apex path/to/torcs_run.csv    # TORCS run -> split into laps, lands in Garage
pytest                        # 27 tests, headless-safe
python scripts/screenshot.py  # Garage + Analysis PNGs for blog/status forms
```

Python ≥ 3.11. On headless boxes run Qt things with `QT_QPA_PLATFORM=offscreen`.
The workspace lives at `~/Apex/sessions/` (override with `APEX_WORKSPACE`).

## Layout

```
src/f1coach_core/        pure Python, no Qt — loaders/analysis/coaching
  schema.py                telemetry contract v1
  loader.py                CSV -> validated Lap; readable errors by design
  lap.py · session.py      the objects every front end renders
  analysis.py              sector spans/times (seed of the features lane)
  torcs.py                 Lin's high-freq TORCS exports -> canonical laps
  workspace.py             ~/Apex/sessions store, import, watched-folder logic
  sample.py + data/        bundled 3-lap synthetic session (no network needed)
src/apex/                desktop shell, Qt lives here only
  app.py                   entry point, dark Fusion + Carbon palette
  main_window.py           toolbar nav, menu, drag-and-drop routing
  garage_view.py           session library · lap table · import · watch folder
  analysis_view.py         reference picker · readout · timing colours
  widgets/strip_stack.py   ribbon + synced strips + shared crosshair
scripts/                 sample-session generator · screenshot renderer
tests/                   pytest (loader, analysis, session, workspace, torcs, shell)
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

## Next: A2 — coach skeleton (gate, 4 Aug)

Coaching JSON contract (`findings[{issue, evidence[...], cause, action,
confidence}]`) · mock Granite provider with canned schema-valid responses ·
AI Race Engineer panel beside the strips · "◈ show" evidence-zoom onto the
charts. Slipping this gate triggers the Plotly-Dash-in-pywebview parachute.
