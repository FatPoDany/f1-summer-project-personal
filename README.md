# Apex — Evidence-Bounded AI Coaching for Simulated Racing

**Yiyang Dong · MSc Computer Science Summer Project · University of Bristol × IBM**

This repository is my individual contribution to the group project. It holds one
program, **Apex**, and everything needed to build, run, test and reproduce it.

The group split the work in two. My teammates built a browser-based collection kit
and a web platform. I built a single desktop application that covers the whole
loop on one machine — recruit a participant, record them driving, measure what they
did, explain it, and export the result — and then made its output flow into the
team's platform as well.

---

## What this is, in plain words

A racing simulator can report, many times a second, everything the car is doing:
how fast it is going, where it is on the circuit, how hard the brake is pressed,
which gear is selected. That stream of numbers is called **telemetry**. One trip
around the circuit is a **lap**; the places where the driver has to slow down and
turn are **corners**. Almost all the time a driver loses is lost at corners.

Apex records that stream while somebody drives, works out corner by corner where
they lost time and why, and then writes them a short piece of advice in ordinary
English — "let go of the brake sooner so the car keeps rolling through the middle."

The advice is written by a language model. Language models invent plausible
sentences, and an invented number here is worse than silence: a driver told to
brake later where they already braked too late gets slower and has no way to
know the advice was made up. **So in Apex the model is not allowed to write a
number at all.** Every quantity a participant reads was measured by ordinary
deterministic code and checked before it reached the screen. That constraint is
the central idea of this repository, and Section 2 below is where to look at it.

Everything runs on the participant's own laptop. The model is a 2.1 GB file on
disk, not a web service, so a session needs no network and no participant data
leaves the machine.

---

## Where to look first

If you have fifteen minutes, read these four files in this order. Each is
self-contained and each carries the reasoning in its own docstring, not just the
code.

| # | File | Why |
|---|---|---|
| 1 | [`src/f1coach_core/reference.py`](src/f1coach_core/reference.py) | The design decision I am most pleased with: compare a driver against **themselves**, corner by corner, instead of against the fastest lap in the session. |
| 2 | [`src/f1coach_core/coach.py`](src/f1coach_core/coach.py) | The contract that confines the language model. Read `_require_measurement_free_prose` (line ~206) for the defect that produced the rule. |
| 3 | [`src/f1coach_core/footage.py`](src/f1coach_core/footage.py) | How a claim about metre 1,412 of the lap becomes a video clip of that moment — the second clock written into every telemetry row. |
| 4 | [`docs/GRANITE_TORCS_WALKTHROUGH.md`](docs/GRANITE_TORCS_WALKTHROUGH.md) | Build it and run it end to end from nothing. |

If you have five minutes, read this file and then `src/f1coach_core/coach.py`.

---

## Three contributions I would like read closely

### 1. Compare a driver with themselves, one corner at a time

`src/f1coach_core/reference.py`

Apex originally compared every lap against the quickest lap of the session, which
is the obvious thing to do and wrong in three ways. Three laps is a small sample,
so the quickest of them is partly luck. The quickest lap has its own bad corners,
and where it does, a driver is told they matched the reference — true and useless.
Worst of all, the quickest lap has nothing quicker to be read against, so it falls
back to generic single-lap checks: **the better somebody drove, the less the
program had to say to them.**

The fix keeps one real lap as the grid and lets each corner's target be whichever
lap went through *that corner* quickest. Nothing is synthesised — every reference
number is a number some lap actually recorded, and each row of the corner table
says which lap it came from.

Three details in that file took the most care, and the docstring explains each:
corners are detected once on an anchor lap so that "T4" keeps meaning the same
corner; the choice is made on time through the corner rather than on lap time;
and a composite reference never claims a lap time, reporting instead something
like "11.70 s across 2 of 3 corners".

### 2. Confine the model so it cannot state a measurement

`src/f1coach_core/coach.py`, `src/f1coach_core/guidance.py`, `src/f1coach_core/llm.py`

Apex measures a lap first, deterministically, with no model involved: about twenty
quantities per corner, and the same set again for the driver's own best at that
corner. That table is the **evidence packet**. The model receives the packet and
must answer with structured data, not prose, in a shape that is checked before
anything is displayed:

```json
{
  "focus":  "cornering",
  "issue":  "You slow down more at the slowest part of this corner than on your quicker run through here.",
  "cause":  "The Down Arrow is held past the point where the car has slowed enough.",
  "action": "Let go of the Down Arrow sooner so the car keeps rolling through the middle.",
  "evidence": [ { "metric": "min_speed", "corner": "T1", "value": 18.9,
                  "ref": 172.1, "unit": "km/h", "span_m": [435.4, 845.4] } ]
}
```

Every part is checked. `focus` must be one of three fixed words. Each finding
carries one to four citations; each citation must name a metric actually present
for that corner in the packet, and its unit, value, reference value and stretch of
road must all match what Apex measured. A response failing any check is rejected
whole, and the participant is told plainly that no validated advice was produced
for that corner — a worse experience than a confident sentence, and a much better
one than a wrong one.

This is an **allowlist rather than a filter**. The model is not scored for
plausibility; it is restricted to a vocabulary of corner names and metric names,
with every value supplied from an independent source.

**The rule I did not expect to need.** The first working version was looser: a
finding could state any number in its prose provided it also cited that number. It
passed its tests. Then five laps against the real model produced a finding that
cited a braking point of 1775 m against the driver's own reference of 1795 m —
twenty metres *earlier* — described it in prose as "later than the reference", and
advised braking earlier still. Every number was correct and every number was
cited. The sentence around them was backwards. Verifying that a number is real is
not verifying that the sentence containing it is true, so the rule is now
absolute: **the prose fields may not contain a digit.** The model writes the shape
of the advice; Apex writes every quantity, from templates generated out of the
comparison itself, which cannot get the direction wrong.

### 3. Make the claim watchable

`src/f1coach_core/footage.py`, `src/apex/widgets/replay_window.py`,
`src/racecoach/telemetry/screen_capture.py`

A citation makes a claim checkable against the measurements. Whether the
measurements match what actually happened is a further question, so Apex records
the screen as well as the telemetry, and any corner can be opened as a clip of
that moment with the driven line beside it.

Two recordings with no shared clock cannot be aligned afterwards. The published
methods infer the offset from features common to both signals — they exist
because their authors did not control the recorders. I controlled both, so I
wrote a second clock into the data instead: every telemetry row carries the
simulator's own time **and** the operating system's wall clock. Finding a corner
in the video file is then a subtraction rather than an inference, and the clip is
cut with three seconds of lead-in so the driver sees the approach and not only the
mistake.

`docs/REPLAY_SYNC_POSITION_VS_TIME.md` records the follow-up question — whether
two laps in a side-by-side replay should be aligned by position on track or by
elapsed time — and the four changes that came out of answering it.

---

## The study instrument

The point of the software is to support a comparative user study, so parts of it
exist to make the evaluation defensible rather than to make the app nicer.

- **`src/f1coach_core/participant.py`** — what is known about a participant before
  they drive. The background questionnaire is asked *before* driving, so knowing
  how they did cannot colour how they answer.
- **`src/f1coach_core/exposure.py`** — how much coaching a participant actually
  took in, measured rather than assumed: seconds spent in the review, how many
  corners they opened, how long the debrief was on screen.
- **`src/f1coach_core/adherence.py`** — whether the advice they were given then
  showed up in their driving. A study that hands somebody advice and measures only
  their lap time cannot tell "the advice was wrong" from "they never read it".
- **`src/f1coach_core/study.py`** and **`src/apex/study_view.py`** — the screen the
  evaluation is argued from, and the 30-column CSV export behind it.
- **`src/f1coach_core/build.py`** — which build of Apex produced each session. A
  study whose instrument changes mid-collection has to be able to say which
  participants got which version.

`docs/STUDY_CAN_WE_SHOW_IMPROVEMENT.md` is the honest write-up of what this design
can and cannot demonstrate at the sample size actually achieved.

---

## Working with the team's half

The team's repository is [`UOBGraduate/IBMF1`](https://github.com/UOBGraduate/IBMF1)
(live at <https://demo.lzqqq.org/>). Apex is a separate program, but it was built
to meet that system rather than to sit beside it:

- it drives **the same practice-race configuration** the team's kit issues
  (`integrations/torcs-1.3.9/reference/ibmf1-practice.xml`), so sessions collected
  on either side are comparable;
- it **translates a finished session into the team's upload format and sends it**
  (`src/f1coach_core/ibmf1.py`, `src/racecoach/telemetry/ibmf1_upload.py`), so a
  supervised session appears on their platform alongside a remote one. This was
  verified end to end on 2026-08-31: *Custom session stored — 40 checkpoints*.

`docs/TEAM_INTEGRATION_IBMF1.md` is the full analysis of how the two halves fit,
including which side should own each capability.

I also offered five changes to the team's own repository. One reached their main
branch:

| Change | Status |
|---|---|
| `79fb4e2` Keep the control arm out of the coach | **merged to `main`** |
| `b26daa4` Add the standalone guided IBMF1 Collector (~1,325 lines) | open |
| `61c8690` Keep the questionnaire that came with a race | open |
| `f91570e` Suggest the arm the package declared | open |
| `fc7bdb3` Compare only races on the same track | open |

The merged one closed a hole in the study rather than a bug in the code. The
control group is defined by never receiving AI coaching, and that was enforced in
exactly one place: the launcher on the player's machine declined to open the
review after a control-group race. But the race was still uploaded and stored —
both groups must produce comparable data — and any stored race could be opened
from the website. A control-group participant reaching their own race through the
website could therefore ask the server for coaching and get it, with nothing in
the record showing it had happened. The fix moves the decision to the server:
coaching is permitted only when the race declares the coached group or declares no
group at all, and anything else is refused *including* a group name the server
does not recognise, because an unfamiliar label is not evidence that its members
may see coaching. Two further details matter — the refusal does not name the
group, and the group is kept out of the public view of a session entirely.
Publishing every race's group to every visitor would unblind the study through the
same page the check exists to protect.

---

## Repository map

```
src/f1coach_core/     telemetry loading, schemas, corner detection, evidence
                      contracts, coaching validation, audit records, study
                      measures.  No Qt — this is the part that decides what is
                      true.                                    8,090 lines
src/apex/             the PySide6 desktop application: Garage, Lap Analysis,
                      Compare, Session Debrief, Collect Data, Study, and the
                      corner review window.                     8,053 lines
src/racecoach/        the command-line side: TORCS runtime control, live and
                      synthetic capture, screen recording, the local Granite
                      server and client, HTML run reports, handover and
                      upload to the team platform.             9,466 lines
tests/                54 test modules, 1,010 tests.            19,160 lines
integrations/torcs-1.3.9/   native overlay for the simulator: telemetry writers
                      for the human and robot drivers, the Granite bridge, race
                      presets, build scripts and verification scripts.
                                                    2,741 lines of C/C++
integrations/granite-4.1/   pinned llama.cpp bootstrap and model download.
docs/                 walkthroughs, decision records, study documents, slides.
SPEC-*.md             the seven specifications the work was built to (below).
configs/              race configuration.
scripts/              packaging entry points and screenshot rendering.
.github/workflows/    CI on Ubuntu and macOS; the Windows installer build.
```

`AGENTS.md` records the working conventions for this repository — the
source-of-truth map, the stack, and the rules the code is held to.

### How the work was specified

Each specification ends in a list of tasks, and beside every task the command that
decides whether it is finished — not "implement corner detection" but "implement
corner detection; `pytest tests/test_features.py -k corners` passes". Writing the
check first is what stops a task being declared done because it looks done.

| Spec | Subject |
|---|---|
| `SPEC-human-telemetry-capture.md` | recording a real person driving, without a terminal |
| `SPEC-coaching-automation-and-footage.md` | automatic coaching in the Garage, and footage truth |
| `SPEC-torcs-reference-recorder.md` | the native telemetry writer inside the simulator |
| `SPEC-synthetic-capture-contract.md` | the contract a robot capture must satisfy |
| `SPEC-synthetic-pilot-runner.md` | running reference robot sessions |
| `SPEC-robot-study-preset.md` | the pinned robot/track/car preset |
| `SPEC-research-pilot-ui.md` | the participant-facing screens |

---

## Running it

```bash
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"

apex                               # opens the Garage on the bundled sample session
```

The sample session is five recorded laps of the Aalborg circuit driven by study
participant `0822`. They are real laps deliberately: the sample is the first thing
anybody opens, and a synthetic stand-in would teach every reader a track, a spread
of lap times and a set of mistakes that never happened.

Double-click a lap to open **Lap Analysis**; double-click a corner in the table to
open the review window on that corner. Every corner opens, not only the ones that
cost time — a corner somebody was quick through still has footage of them being
quick through it.

**Tests** (no simulator, no model, no network required):

```bash
QT_QPA_PLATFORM=offscreen pytest -q
ruff check .
```

**A contract-only demonstration of the coaching pipeline** that needs no model
download:

```bash
PYTHONPATH=src python -m racecoach.granite.smoke --mock
```

**The full pipeline**, including the simulator and the real model, is in
[`docs/GRANITE_TORCS_WALKTHROUGH.md`](docs/GRANITE_TORCS_WALKTHROUGH.md) and
[`integrations/torcs-1.3.9/README.md`](integrations/torcs-1.3.9/README.md).
`docs/STUDY_PORTABLE_BUILD.md` describes the portable build that was actually
handed to participants.

---

## How it was verified

- **1,010 tests** across 54 modules, run on every pull request on Ubuntu and
  macOS (`.github/workflows/ci.yml`). The suite covers the deterministic
  analysis, the coaching contract, every screen of the UI under Qt's offscreen
  platform, the capture orchestration and the upload path. On Windows 15 of them
  skip — they need POSIX shell scripts, `chmod` bits or symbolic links — so a
  Windows run reads `995 passed, 15 skipped`; on the two CI platforms all 1,010
  execute.
- **Ruff** lint and import ordering, same job.
- **Native verification scripts** for the parts CI cannot reach. The simulator
  overlay is compiled and exercised against the pinned TORCS archive by five
  scripts under `integrations/torcs-1.3.9/` — `verify-bridge.sh`,
  `verify-human-capture.sh`, `verify-reference-recorder.sh`,
  `verify-robot-study-preset.sh` and `verify-study-preset.sh`.
- **A coaching audit record** is written for every model request, successful or
  not (`src/f1coach_core/audit.py`), holding the evidence packet, the prompt, the
  raw response and the validation outcome. Any advice a participant saw can be
  reconstructed and re-checked afterwards.
- **Coverage of the review was itself measured.** A comment at
  `src/f1coach_core/coach.py:42` records the finding: measured across the four
  collected sessions plus the sample, 72 corners had lost time, only 60 reached
  the model and only 36 could ever be answered, because one constant was serving
  as both the per-request limit and the whole-review limit. Half the corners a
  participant opened said "no validated AI advice cited this exact stretch" while
  their evidence sat in the packet the model had already been given. Splitting the
  two constants (PR #16) fixed it.

The project ran to **131 commits** between 6 July and 2 September 2026 — 12 in
July, 83 in August, 36 in September. Every change reached `main` through a pull
request.

---

## Provenance and attribution

**Third-party components**, not my work and not modified except as noted:

- **TORCS 1.3.9** (GPL-2.0) — the racing simulator. This repository contains no
  TORCS source. `integrations/torcs-1.3.9/` holds overlay files and patches that
  apply *to* the upstream archive, with `SHA256SUMS` pinning the version they
  apply to, plus `COPYING` and `NOTICE.md`.
- **SCR sensor helpers.** `sensors.{cpp,h}` and `ObstacleSensors.{cpp,h}` under
  `overlay/src/drivers/granite_bridge/` are derived from Daniele Loiacono's
  Simulated Car Racing Championship server and remain GPL-2.0-or-later. Their
  provenance, down to the upstream commit they were taken from, is recorded in
  `integrations/torcs-1.3.9/NOTICE.md`.
- **llama.cpp** (MIT) — runs the model. Pinned and fetched by
  `integrations/granite-4.1/bootstrap-llama-server.sh`; not vendored here.
- **IBM Granite 4.1 3B** — the language model, downloaded as a quantised GGUF
  weights file (2.1 GB). Not in this repository; see
  `src/racecoach/granite/model.py` for the pins and the integrity check.
- **PySide6 / Qt 6** (LGPL-3.0), **pyqtgraph**, **pandas**, **numpy**, **keyring** —
  declared in `pyproject.toml`.

**Everything else is my own work**, written for this project: all of `src/`,
`tests/`, `scripts/`, `configs/`, `docs/`, the `SPEC-*.md` files, and — apart
from the four SCR files named above — the overlay, patches and verification
scripts under `integrations/`.

**Development tooling.** AI coding assistants were used during development, under
the conventions recorded in `AGENTS.md`. Every line was reviewed, and the test
suite and verification scripts above are how that review was made checkable.

---

## What is deliberately not in this repository

- **Participant data.** Raw telemetry, screen recordings and handover archives
  hold a participant's session and travel by memory stick, not by git
  (`/win_collect_data/` is ignored). The bundled sample session is the one
  exception: five laps split out of participant `0822`'s own handover, shipped as
  the application's demonstration data so that a first launch does not depend on
  a network or on a synthetic lap nobody drove. Participants are identified by
  pseudonym throughout; no name or email address exists anywhere in the record.
- **Model weights** (2.1 GB) and the **llama.cpp binaries** — fetched by the
  bootstrap scripts, pinned by digest.
- **Credentials.** Never commit API keys. Use environment variables or a local
  ignored `.env`, and commit only `.env.example`. The watsonx provider resolves
  credentials from the environment first and then the OS keychain
  (`src/f1coach_core/watsonx_coach.py`).
- **Shared team deliverables.** The team repository remains the source of truth
  for the group's work; this one holds mine.
- **Build products and virtual environments.**
