# Apex — A0 spike

Desktop shell for **AI-Enhanced Telemetry Analysis & Driver Coaching**
(University of Bristol MSc Group 1 × IBM). PySide6 + PyQtGraph on top of the
team's pure-Python `f1coach-core`; IBM Granite becomes the coaching brain from
milestone A2 onward.

* Design doc: `Apex App Design (F1 Coach Desktop).md` in the project vault ·
  [full design with mockups](https://claude.ai/code/artifact/909f239e-6c63-4fe4-bf1a-dff5576011da)
* This repo is **milestone A0** (gate: **Sun 12 Jul**) — prove the stack:
  scaffold + native window on Mac & Linux CI, CSV → speed trace.

## A0 gate checklist

- [x] Repo scaffold with the core/shell boundary from the design doc
- [x] Window shell: menu, ⌘O file dialog, drag-and-drop, status line, dark-first
- [x] CSV → validated telemetry → speed-vs-distance trace (sample lap opens on launch)
- [x] Loader contract tests + offscreen window smoke tests green on macOS
- [ ] CI green on ubuntu + macos (lights up when this is pushed to GitHub)
- [ ] M6 runs it on Linux
- [ ] Team stack sign-off (or evidence-based veto → Tauri/Dash discussion)

## Quickstart

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
apex                          # opens the bundled sample lap — no network, no credentials
apex path/to/lap.csv          # or drag any telemetry CSV onto the window
pytest                        # loader contract + window smoke (headless-safe)
python scripts/screenshot.py  # PNG of the window for blog posts / status forms
```

Python ≥ 3.11. On headless boxes run Qt things with `QT_QPA_PLATFORM=offscreen`
(CI does; desktop Linux may need `sudo apt install libegl1 libxkbcommon0`).

## Layout — and who owns what

```
src/f1coach_core/        pure Python, no Qt — lanes M2–M5
  schema.py                telemetry contract v1 (DRAFT until the week-one meeting)
  loader.py                CSV → validated Lap; readable errors by design
  lap.py                   the Lap object every front end renders
  sample.py + data/        bundled synthetic lap (demos never need the network)
src/apex/                desktop shell, Qt lives here only — lane M6
  app.py                   entry point (`apex`), Fusion + dark scheme
  main_window.py           menu / drag-and-drop / status line
  widgets/speed_trace.py   PyQtGraph strip (A1 turns this into the synced stack)
scripts/                 sample-lap generator · window screenshot renderer
tests/                   pytest; runs offscreen on Mac/Linux CI
```

House rule: **the app never computes telemetry truth** — it renders what
`f1coach_core` returns. CLI, notebooks, and app are three faces of one package.

## Telemetry contract v1 (draft — confirm with M2 in week one)

One lap per CSV, one row per sample, optional leading `# key: value` metadata
lines (`# schema_version: 1`; absent means 1). Units are SI in files; the app
converts for display. Unknown extra columns are kept, not rejected.

| column | unit | notes |
|---|---|---|
| `t` | s | non-decreasing |
| `dist` | m | **optional** — loader derives `cumsum(speed·Δt)` when the sim can't export it |
| `speed` | m/s | displayed as km/h |
| `throttle`, `brake` | 0–1 | |
| `steer` | −1…1 | negative = left |
| `gear` | int | −1 reverse, 0 neutral |
| `sector` | 1–3 | optional until M2's exporter lands |

Open decisions for the meeting: `dist` in M2's export schema (else the loader
derives it), and the app's name.

## Next: A1 — chart core (due Tue 21 Jul, = M6's Phase 3 lane)

Synced speed/throttle/brake strips with one crosshair · sector ribbon · Garage
screen (session library, import + watched folder). Start in
`src/apex/widgets/speed_trace.py`; `Lap` already carries every column you need.
