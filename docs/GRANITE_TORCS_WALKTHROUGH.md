# IBM Granite 4.1 + TORCS Student Walkthrough

This walkthrough reproduces the project's first vertical slice: a TORCS
1.3.9 car is driven by deterministic code while IBM Granite 4.1 reads live
telemetry and produces evidence-grounded race-engineering advice.

## What was integrated

```text
TORCS physics (500 Hz)
  -> Granite Bridge robot callback (~50 Hz simulator time)
  -> loopback UDP telemetry
  -> Python SimpleDriver -> bounded actions back to TORCS
  -> one-slot asynchronous snapshot queue
  -> llama-server -> IBM Granite 4.1 3B Q4_K_M
  -> strict JSON Schema + exact telemetry evidence validation
  -> Apex Live Pit Wall + TORCS Driver Board + append-only JSONL audit
```

The language model is outside the control loop. At the observed CPU speed a
fresh prompt takes several seconds, far longer than a 20 ms driving deadline.
If Granite is slow, unavailable, or returns invalid data, driving continues
unchanged and the failure is written to the audit log.

## Prerequisites

- Linux x86-64 with Python 3.11 or newer.
- About 8 GiB total RAM minimum; 16 GiB is more comfortable beside TORCS.
- About 2.1 GB free in `/tmp` for the official Q4 model.
- Native TORCS dependencies listed in
  [`integrations/torcs-1.3.9/README.md`](../integrations/torcs-1.3.9/README.md).

This server has no usable NVIDIA device, so the reproducible default is the
official IBM 3B instruct GGUF. The model weights are downloaded from Hugging
Face; they are never copied into Git.

## Step 1: install the Python project

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

The Granite live client itself uses only Python's standard library. The GUI
and analysis dependencies still come from the main project install.

## Step 2: build the pinned model server

```bash
integrations/granite-4.1/bootstrap-llama-server.sh
```

The script pins llama.cpp release `b10549` at commit
`b2e5e9b28b2484fbf94b543432ece638996a8b97`. On the university Rocky 8 host it
automatically selects the available GCC 13 toolchain because system GCC 8 is
too old for this llama.cpp revision.

## Step 3: download and verify Granite

```bash
integrations/granite-4.1/download-model.sh
```

The download is pinned to IBM repository revision
`ab4701481089b58a082ef63cc1cee738887293ff`:

- file: `granite-4.1-3b-Q4_K_M.gguf`
- size: `2,099,501,664` bytes
- SHA-256: `662b0626cd58f443baea23559b469df6576a81d349649c59413b36a9fb32eb29`

The script refuses a file that does not match both size and digest.

## Step 4: start Granite's loopback API

On a shared host, choose an API key first and export it in both the server and
client terminals:

```bash
export GRANITE_API_KEY="$(openssl rand -hex 24)"
integrations/granite-4.1/start-server.sh
```

The endpoint is `http://127.0.0.1:8080/v1`. It uses a 4,096-token context,
one slot, eight CPU threads, JSON-capable Jinja chat templates, and no GPU
offload. The API is not exposed to the school network.

## Step 5: prove the real model contract

In another terminal:

```bash
source .venv/bin/activate
export GRANITE_API_KEY="the same value used by the server"
PYTHONPATH=src python -m racecoach.granite.smoke
```

Success prints `"ok": true`, the server-declared model identity, and one
validated advice object. The startup script independently verifies the local
GGUF SHA-256 before serving it; the HTTP client deliberately labels its own
provenance fields as declared/unverified because an alias cannot prove loaded
weights. A no-model contract demonstration is also available:

```bash
PYTHONPATH=src python -m racecoach.granite.smoke --mock
```

## Step 6: build and select the TORCS bridge

Follow the TORCS integration README, then launch the installed simulator. On
a multi-user host, generate one private bridge token and export the same value
before starting both TORCS and the Python client:

```bash
export GRANITE_BRIDGE_TOKEN="$(openssl rand -hex 24)"
```

In TORCS choose **Practice** or **Quick Race**, add **Granite Bridge**, and
start the session. During the race, press the number key **1** if the Driver
Board is hidden. Before the Python process connects it reads
`Apex: WAITING FOR SIDECAR`; a connection alone reads
`Apex: SIDECAR CONNECTED` and is not presented as a model connection.
If the race contains other cars, use **Page Up/Page Down** to follow Granite
Bridge—the board shows messages for the currently viewed car only.

## Step 7: open the Live Pit Wall (recommended)

Export the same credentials in the terminal that launches Apex, then open the
fourth toolbar page:

```bash
source .venv/bin/activate
export GRANITE_API_KEY="the same model API key"
export GRANITE_BRIDGE_TOKEN="the same TORCS bridge token"
apex
```

Select **Live Pit Wall**, keep **Granite 4.1 (local)** selected, and press
**Start live session** after the Granite Bridge car has entered the race. The
page displays:

- the independently reported bridge and model states (the model is not marked
  Online until its first response passes schema and evidence validation);
- lap, simulator time and speed at a bounded 10 Hz UI refresh rate;
- FR/FL/RR/RL tyre wear, temperature, pressure and graining;
- advice urgency/focus, exact evidence, source lap/time and inference latency.

Advice older than two configured coaching intervals is visibly marked
`STALE` in Apex and is removed from the TORCS HUD. **Stop safely** requests a
clean capture cancellation and sends a full-brake action. The explicit
**Mock contract — TEST** choice is only a deterministic UI/protocol check and
is always labelled TEST; it is not the Granite model.

Do not run the CLI capture below at the same time as Live Pit Wall: one process
must own the single Granite Bridge car and its UDP peer.

## Step 8: CLI capture alternative

```bash
source .venv/bin/activate
export GRANITE_API_KEY="the same model API key"
export GRANITE_BRIDGE_TOKEN="the same TORCS bridge token"
racecoach run --config configs/race.toml \
  --live-coach granite --coach-interval-s 10 --max-laps 3
```

Three laps are the default collection target. Raw Granite Bridge
`raceFinished` state is persisted as `race_finished`. Some TORCS race modes
instead send `***shutdown***` at the line without a final state or distance
reset; in that case the client sets the separate deterministic
`capture_lap_closed` marker only when the configured final lap is active and
its distance covers at least 98% of a previously observed lap. Mid-lap exits
remain incomplete. If TORCS advances its lap counter, the closing sample stays
assigned to the completed lap and cannot appear as a one-sample extra lap.

Granite advice appears in the terminal and, after strict validation, as a
printable ASCII summary of at most 31 characters on TORCS' Driver Board under
`Granite 4.1: ADVICE`. The deterministic driver continues to send every
throttle, brake, steering, clutch, and gear action. The optional HUD tuple is
display-only; it is parsed separately and never becomes an actuator value.
It expires after two coaching intervals if fresh validated advice does not
arrive.

The run directory contains:

- `telemetry.csv`: normalized 50 Hz telemetry, including the 1.3.9 tyre model;
- `coaching/granite-4.1-live.jsonl`: snapshot, exact prompt, raw response,
  validation result, latency, model provenance, display-publication decision,
  suppression reason, and any failure;
- after `racecoach analyze RUN_ID`: deterministic metrics and events;
- after `racecoach report --run RUN_ID`: the HTML report.

## Synthetic collection pilot (no Granite required)

Before recruiting participants, a facilitator can exercise the collection,
validation, run-store, Garage, and analysis path with the pinned TORCS built-in
reference robot. First verify and install the native components:

```bash
integrations/torcs-1.3.9/verify-reference-recorder.sh
integrations/torcs-1.3.9/verify-robot-study-preset.sh
integrations/torcs-1.3.9/build.sh install
```

For the visible workflow, launch Apex in explicit research mode:

```bash
APEX_RESEARCH_MODE=1 apex
```

Open **Robot Pilot** and start the default batch. Apex runs three sequential
sessions of pinned `berniw` index 9; each session uses `g-track-1`,
`car7-trb1`, and three laps. Progress, cancellation, per-session outcomes, and
completed Garage runs are shown without asking for participant identity. The
page is absent in the normal participant-facing application.

The equivalent recovery command is:

```bash
racecoach capture-synthetic --count 3
```

Raw evidence is kept under `<workspace>/captures/synthetic/`. Every session
manifest records the exact robot, preset, command, CSV hash, sample count,
complete lap labels, and registered run id; the batch manifest records
complete, failed, cancelled, and not-started outcomes. A failed session is not
silently retried and does not invalidate completed siblings. Collection does
not call Granite—the runs become inputs to the existing deterministic analysis
and optional post-lap coaching after capture.

Synthetic runs prove that the software pipeline operates against a frozen
controller and can support known-difference experiments. They are not users,
do not represent baseline/coached conditions, and cannot establish a human
coaching benefit.

## Step 9: Granite post-lap coaching

After importing a run into an Apex session, open any complete lap in
**Analysis** and press **Analyze lap**. The AI Race Engineer is fixed to local
**Granite 4.1**; there is no provider selector. The default **Single-lap
analysis** does not require a reference and sends a compact packet of
deterministic technique checks. To make a lap-to-lap comparison, select another
lap under **Compare with** before running Granite. The same loopback server from
step 4 is used in both modes.

The post-lap request uses server-sent events. Apex shows the accumulated model
text as it arrives, while the pinned `llama-server` sends heartbeat comments
during long prompt processing so a slow CPU is not mistaken for a failed
request. Only the completed response can become a coaching report; if streaming
or validation fails, any partial text remains evidence in the run's audit.

The deterministic analysis measures, per detected corner:

- brake onset, peak pressure and release point;
- entry, minimum and exit speed, including the distance of minimum speed;
- throttle reapplication, half-throttle and full-throttle points, exit
  throttle and coasting distance.

Single-lap mode additionally records repeated brake/throttle applications and
pedal-overlap percentage. Conservative code-stamped review thresholds select
which zones Granite may discuss; they are labelled as review guides, not as a
reference lap, an optimal target, or a lap-time improvement claim.

In comparison mode only the highest-loss corner zones are sent to Granite; in
single-lap mode only zones crossing a deterministic review threshold are sent.
The response is grammar-constrained and then checked again in Python: every
cited metric, corner, value, reference/guide value, unit and distance span must
exactly match that packet. Repeated citations are removed, and findings that
resolve to the same grounded technique are merged into one card with up to four
unique evidence links. Each candidate is validated independently, so one
malformed finding cannot suppress valid siblings; if none survive, the run
still fails closed. Advice cards are labelled **Braking**,
**Cornering** or **Throttle**; use **◈ show** to zoom all telemetry strips to
the cited zone. Full prompts, raw output, validation failures and accepted
reports are kept in the coaching audit directory. When the same lap is opened
again with the same single-lap or selected-reference context, Apex restores the
newest successful Granite report from that directory only after its frozen
evidence packet and every citation pass validation again. Results are never
reused across different reference selections.

## Why the output is trustworthy

The response is grammar-constrained by JSON Schema, then independently
validated in Python. Every evidence key must exist in the same telemetry
snapshot, every number and unit must match exactly, duplicate evidence is
suppressed before each surviving finding is revalidated, and prose containing
unvalidated digits is rejected. Unknown or extra fields are rejected. No
response field is translated into a vehicle
control.

If the bridge stops supplying valid telemetry for 30 seconds, the client
terminates the capture and sends a best-effort full-brake command. Adjust
`[connection].max_idle_s` in `configs/race.toml` for unusually long pauses.

## Extending this work

The same sidecar can support procedural commentary or post-lap coaching. For
an AI race bot, keep Granite at the strategy layer: let it propose a bounded
pace mode, pit request, or validated controller-parameter update between laps.
Do not call an LLM from `rbDrive`, and never execute model-generated code or
apply unvalidated actuator values.

## Official references

- [IBM Granite 4.1 repository](https://github.com/ibm-granite/granite-4.1-language-models)
- [IBM Granite 4.1 3B model card](https://huggingface.co/ibm-granite/granite-4.1-3b)
- [IBM official Granite 4.1 3B GGUF](https://huggingface.co/ibm-granite/granite-4.1-3b-GGUF)
- [IBM Granite 4.1 documentation](https://www.ibm.com/granite/docs/models/granite4-1)
- [llama.cpp function-calling documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md)
