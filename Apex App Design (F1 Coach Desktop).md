# Apex — Desktop App Design (v0.1, 6 Jul 2026)

**Full design doc with UI mockups:** https://claude.ai/code/artifact/909f239e-6c63-4fe4-bf1a-dff5576011da

A native **macOS + Linux desktop app** for the IBM F1 Telemetry & Driver Coaching project — the interactive front end the plan currently leaves open ("lightweight web app, notebook, or static report"). "Apex" is a working name (alternatives: Pit Wall, Boxbox, Delta).

## Decision

**Stack: PySide6 (Qt 6) + PyQtGraph, packaged with PyInstaller** → `Apex.app` (macOS 12+, unsigned) and `Apex-x86_64.AppImage` (Ubuntu 22.04+).

Why:
- **One language, one process** — the app `import`s the team's `f1coach-core` package directly. No IPC, no sidecar, no second language. M3/M4/M5's work appears in the UI the day it merges.
- **PyQtGraph is built for telemetry** — fast engineering plots, crosshairs, linked axes, regions (MoTeC-style strip stacks).
- **Native shell** — menus, file dialogs, drag-and-drop, system dark mode. LGPL, `pip install`.
- **IBM Bob generates Qt well** — scaffolding sessions feed the Bob usage log.

Considered: **Tauri 2 + React** (only if M6 is far stronger in TS *and* someone owns the Python sidecar — decide at gate A0). **Plotly Dash in pywebview** = pre-agreed parachute if gate A2 slips. Ruled out: Electron (heavier Tauri), SwiftUI (no Linux), Kivy (weak charting), Flutter (new language).

## Experience requirements

1. **Two clicks from file to insight** — import or auto-detect a lap from a watched folder → full analysis.
2. **Every AI claim is clickable** — each Granite recommendation cites evidence; clicking highlights that zone on the charts (makes M7's grounding audit a feature).
3. **Demos never depend on the network** — bundled sample session, Granite swappable for local model / canned responses.

## Screens

- **Garage** — session library, lap table with deltas/status, import + watched folder; first run opens the bundled sample session (zero credentials).
- **Lap Analysis** (the core) — reference picker (session best / previous / other driver / ideal), sector ribbon with timing-screen colors, synced speed/throttle/brake strips on a shared distance axis with one crosshair, corner table, and the **AI Race Engineer panel**: findings rendered from a fixed JSON schema (issue / evidence / cause / action / confidence) with "◈ show" evidence-zoom, provider + prompt-version chip.
- **Compare** — cumulative time-delta trace for two laps/drivers; doubles as the **before/after-coaching** view for evaluation.
- **Race Engineer Q&A** — stretch; a docked chat panel reusing the same evidence summary. Ships only if Phase 5 has room.
- **Report export** — self-contained HTML/PDF per analysis; feeds blog, IBM status forms, final report.

App design language: dark-first (pit-wall convention), IBM Plex type, Carbon-palette accents; purple = session best, green = gained, red = lost.

## Architecture

```
Desktop shell (PySide6, M6)  — views, Qt signals, QThreadPool workers, streaming coach card
        │  imports (same process, no IPC)
f1coach-core (pure Python, M2–M5) — loaders → features → analysis + evidence summary → coach client
        │                                   Workspace store: ~/Apex/sessions/… (parquet/csv + analysis.json + coaching.json)
one provider interface, three backends:
  watsonx.ai (ibm-watsonx-ai, keys in OS keychain via keyring)
  local Ollama granite3.3:8b (offline demo insurance)
  mock (canned schema-valid responses — UI dev, CI, M7 test laps)
```

Rule: **the app never computes telemetry truth** — it renders what `f1coach-core` returns. CLI, notebooks, and app are three faces of one package.

### Contracts to agree in week one of Phase 3
- **Telemetry file** (M2): csv/parquet, one row per sample — `t, dist, speed, throttle, brake, steer, gear, sector` + `schema_version`. `dist` is the shared x-axis; if the sim can't export it, the loader derives `cumsum(speed·Δt)`.
- **Coaching response** (M5): fixed JSON — `findings[{issue, evidence[{metric, corner, value, ref, unit, span_m}], cause, action, confidence}]` + `model`, `prompt_version`. `span_m` anchors the evidence-zoom.

## Milestones (mapped to the team plan)

| Milestone | Due | Demoable outcome |
|---|---|---|
| **A0 · Spike — gate** | Sun 12 Jul | Scaffold + window on Mac/Linux CI; CSV → speed trace. Proves the stack in 5 days. |
| **A1 · Chart core** | Tue 21 Jul | Synced strips, sector ribbon, Garage with import/watch. (= M6's Phase 3 task) |
| **A2 · Coach skeleton — gate** | Tue 4 Aug | Evidence panel + coach card (mock provider), evidence-zoom. Slipping → Dash parachute. (= Phase 4 "dashboard skeleton") |
| **A3 · Full flow** | Tue 18 Aug | watsonx streaming + Ollama fallback, Compare + before/after, report export, packaged builds. Shoots the demo video. (= Phase 5 "complete dashboard") |
| **A4 · Hardening** | Tue 1 Sep | M7 usability fixes, graceful failure states, audit affordances. |
| **A5 · Ship** | Wed 9 Sep | Final builds, quickstart, walkthrough chapter, video assets. |

Lane fit: M6 owns the shell; M5's provider adapter is the assigned watsonx work; M2's sample laps become the bundled session; M7 usability-tests and audits via "◈ show". Nothing new for anyone else.

## Risks

| Risk | Mitigation |
|---|---|
| Qt new to M6 | A0 gate (5-day proof), Bob scaffolding, buddy M7; parachute decision owned by gate A2 |
| Slow rendering on high-rate telemetry | Downsample to ~2k pts/strip for display; parse in workers |
| Live API fails mid-demo | Demo order: cached response → local Ollama Granite → watsonx; rehearse offline |
| Unsigned macOS build | README two-click bypass; demo from dev machine; ad-hoc signing if IBM asks |
| Schema drift | `schema_version` + validating loader with readable errors → M7's reliability log |

**Non-goals:** real-time in-sim overlay, Windows testing, multi-user, physics work.

## Next steps (team meeting)

1. Sign off the stack (or veto with evidence at A0).
2. Confirm `dist` in M2's export schema — or agree the loader derives it.
3. Pick a name.

Then: A0 spike this week; mockups go into this week's blog post ("from notebook to pit wall") for John.
