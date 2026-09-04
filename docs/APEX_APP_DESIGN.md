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

1. **Two clicks from file to insight** — import or collect a lap into Garage → full analysis.
2. **Every AI claim is clickable** — each Granite recommendation cites evidence; clicking highlights that zone on the charts (makes M7's grounding audit a feature).
3. **Demos never depend on the network** — bundled sample session, Granite swappable for local model / canned responses.

## Screens

- **Garage** — session library, lap table with deltas, import, confirmed deletion of Apex-managed copies, and a visible automatic-coaching state for every lap. Import accepts loose telemetry CSVs and whole participant handover archives; a handover is digest-verified on the way in, opens a session named after its capture, and carries the driver, phase, preset and background questionnaire that a loose CSV cannot. The participant line above the table states who drove the session and what they answered, or says plainly that the files carry neither. Missing exact-context reports are queued serially; Status distinguishes queued, generating, failed, setup-needed, and unavailable, and reports `ANALYSED` once answers exist — without a count, because a findings count belongs to a (lap, reference) pair and one lap has as many answers as it has comparisons. The hover lists every stored pair with its own count, the one that row opens into first. The Garage also states whether synchronized race-window footage is attached.
- **Lap Analysis** (the core) — the session best opens as a single-lap technique review; every other lap opens against the session best, with other references still selectable. It combines a sector ribbon, synced speed/throttle/brake strips, a track map beside them, corner table, and the Granite-only **AI Race Engineer panel**. The ribbon's S1–S3 are equal thirds of the lap distance derived by Apex and say so on hover: TORCS exports no timing sectors, and an unlabelled ribbon invites comparison with broadcast splits that mean something else. The corner table keeps the same deterministic technique-review column in both modes; a comparison adds the per-corner delta rather than trading the review away for it. Findings follow the fixed issue/evidence/cause/action/confidence schema with "◈ show" evidence zoom and trusted model/prompt provenance. "◈ show" picks the cited stretch out on the track map as well as the strips, so a citation reads as a place on the circuit and not only as a region of a chart; laps recorded without world position say so rather than drawing an empty box. Exact saved reports are revalidated before display. Reviewing a corner is reached by double-clicking its row in the corner table rather than by a separate control: the row is the thing being reviewed, and every corner opens — including the ones that cost no time, and including single-lap mode, where the review shows the footage and claims no delta because none was measured. It reuses advice from that same report only when the report's validated corner and distance span match the corner being reviewed. Footage is located through the `wall_clock_s` stamped on every telemetry sample, so a clip lines up with the corner even when the simulator did not keep real time. The replay and the footage share one transport: the track marker owns Play, and the picture is anchored to the marker's own wall clock whenever playback starts, the slider is dragged, or the stretch comes round again. One press replays the corner until it is paused, because a corner is watched several times over before anything about it sinks in. Two players each starting on their own showed different parts of the same corner. Play is withheld while a clip is still being cut: a marker started against footage that has not loaded drives the corner alone and appears to outlast it. A comparison shows both laps side by side — the compared lap's clip cut from its own driver's wall clock and run at the rate that holds it at the same point on track as the marker, the way the second dot on the map already moves by distance rather than by elapsed time. A rate far enough outside what a player usefully holds is clamped, and the caption over that picture says so for that corner: a picture that has quietly stopped keeping step otherwise reads as something the other car did. One rate also treats the two laps' times through the corner as rising evenly, which they do not, so playback asks a few times a second whether either picture has wandered off the marker and re-seats only the one that has — seeking on every frame is the stutter the shared transport exists to avoid, and the dots never need it because they are matched every frame. The marker itself runs off a clock rather than a count of frames, because a frame is however long Qt took to get round to one: a nominal step per tick ran the replay at 0.84 of the speed it was replaying while the picture beside it ran at real time, so most of what that check found to correct was the marker's own. A correction that does not take is the last one that picture gets for that pass, because a seek costs a visible hitch, and paying it twice a second to move a picture that does not stay moved is the stutter the check exists to prevent arriving by way of the cure; the verdict lasts the pass and not the corner, since going round again puts every picture back on the marker and a picture written off for good is one that stays black once its clip has run out. Holding both markers at the same point on track is what makes the corner comparable and equally what takes the gap in seconds off the screen, so the readout says it in words: how far behind or ahead of the named lap the driver is by the metre the marker is on, read off the same cumulative delta the Compare screen draws, called level rather than 0.00 s when it rounds away, and absent altogether when the two laps share too little distance to go on one grid. Clips are named after the seconds of recording they hold and moved into place whole, so no lap's corner can be shown in place of another's and an interrupted cut leaves nothing behind that looks finished. Both laps in a comparison are named wherever they appear — the map legend, the readout line under it, the caption over each picture and the review window's own title, all in the same words as the reference picker they were chosen in. The picker marks which of the laps it offers is the session best, so the lap most drivers are measuring themselves against is findable without reading every lap time in the list; a lap is absent from its own picker, so nothing carries the mark when the session best is the lap on screen. The reference is whichever lap the driver picked, so a legend reading "your best lap" and a readout reading "best lap here" described a lap that was usually not on the screen; the debrief sentence names it for the same reason, and reads a lap held against a slower one as quicker than it rather than as a negative loss.
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
one validated provider interface; the desktop AI Race Engineer exposes only the
pinned local IBM Granite 4.1 endpoint. The mock provider remains test-only.
```

Rule: **the app never computes telemetry truth** — it renders what `f1coach-core` returns. CLI, notebooks, and app are three faces of one package.

Long Granite work is serialized on a dedicated Qt pool; TORCS capture owns a
different worker so model latency cannot delay driving. Optional ffmpeg footage
is synchronized to deterministic telemetry for human replay only. The current
text-only Granite path receives no pixels, frames, or clips.

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

**Non-goals:** model control of the car, visual-AI claims from unvalidated video,
multi-user services, or simulator-physics changes.

## Next steps (team meeting)

1. Sign off the stack (or veto with evidence at A0).
2. Confirm `dist` in M2's export schema — or agree the loader derives it.
3. Pick a name.

Then: A0 spike this week; mockups go into this week's blog post ("from notebook to pit wall") for John.
