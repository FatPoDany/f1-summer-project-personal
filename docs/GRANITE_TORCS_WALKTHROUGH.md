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

The script pins llama.cpp release `b10355` at commit
`dd1ea524333b1e697489067d7a4c39c60d32beee`. On the university Rocky 8 host it
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
  --live-coach granite --coach-interval-s 10 --max-laps 1
```

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

## Why the output is trustworthy

The response is grammar-constrained by JSON Schema, then independently
validated in Python. Every evidence key must exist in the same telemetry
snapshot, every number and unit must match exactly, duplicate evidence is
rejected, and prose containing unvalidated digits is rejected. Unknown or
extra fields are rejected. No response field is translated into a vehicle
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
