# IBM Bob → racecoach: the code-analysis evidence flow

The IBM contact asked for two things at once: use **IBM Bob** during
development, and make the post-race feedback explain *why* the car drove the
way it did by combining telemetry with an analysis of the control code. Bob
ships two clients — the **Bob IDE** (interactive) and **Bob Shell** (`bob`,
scriptable non-interactively with an API key) — and both feed the same
archive: every analysis lands under `docs/bob/exports/` as a dated markdown
file with an auditable header, and everything downstream reads only that
archive.

## Producing an export

### Automated: Bob Shell (`racecoach bob-analyze`)

1. Install Bob Shell (needs Node.js ≥ 22.15):
   `curl -fsSL https://bob.ibm.com/download/bobshell.sh | bash`
2. Provide an API key with scope **Inference** (created in the Bob web
   portal): either `export BOBSHELL_API_KEY=…` or `--key-file <path>` naming
   a JSON file with an `"apikey"` field. racecoach hands the key to Bob
   Shell via the environment only — it never appears on a command line.
3. First run only: pass `--accept-license` to accept the IBM license.
4. Run the analysis. The defaults ask the retroanalysis question about
   `src/racecoach/control/simple_driver.py`:

   ```
   racecoach bob-analyze --key-file ../apex.json --accept-license
   racecoach bob-analyze src/robot.cpp --cwd ~/torcs-robot --topic braking-retro \
       --prompt "Which parameters and conditions limit cornering speed?"
   ```

   This runs `bob --auth-method api-key --hide-intermediary-output -p …`,
   extracts the marker-fenced answer from Bob's output, and archives it as
   `docs/bob/exports/YYYY-MM-DD-<topic>.md` with the header below —
   `tool: IBM Bob (Bob Shell)`, so provenance stays visible. The prompt asked
   is recorded verbatim in the header.

### Manual: Bob IDE

1. **Analyse in Bob.** Open the controller/robot code in the Bob IDE and ask
   for (a) a code overview and (b) a retroanalysis of the control strategy —
   e.g. *"Summarise how this driver decides braking points, steering, and
   throttle. List parameters and conditions that limit cornering speed."*
2. **Archive the export.** Save Bob's answer under `docs/bob/exports/` as
   `YYYY-MM-DD-<topic>.md`, starting with this header:

   ```markdown
   ---
   tool: IBM Bob (Bob IDE)
   date: 2026-07-12
   scope: <files/modules Bob was shown>
   prompt: <the question you asked Bob>
   ---
   ```

   Commit it. The dated filename keeps exports chronological; the header
   makes each one auditable on its own.

## Consuming exports

3. **Feed the coach.** `racecoach coach <run_id> --bob` grounds the feedback
   prompt with the newest archived export (or pass a specific file with
   `--code-summary path`). The engine's prompt tells the model to use the
   code summary to explain *why* the telemetry looks the way it does.
4. **Evidence chain.** Every coach run writes an audit record
   (`runs/<id>/coaching/*.json`) embedding the full prompt — including the
   Bob text it used — plus the raw response and the validated feedback. So
   the chain **Bob analysis → export → prompt → feedback** is reproducible
   end to end, which is exactly the "retroanalysis + logged Bob usage" the
   client asked to see. The automated path strengthens the log: the prompt
   in the export header is what was actually sent, not a retyping.

`docs/bob/exports/2026-07-12-template.md` shows the format; it is a template,
not evidence, and the tooling ignores it (as it does any export with
"template" in the name — `bob-analyze` refuses such `--topic` values so real
evidence can't be accidentally invisible).

Bob Shell cannot run in CI or on machines without the key, so its wrapper is
verified against a stub `bob` executable (see `tests/test_racecoach_bobshell.py`
and the `RACECOACH_BOBSHELL_BIN` override) — the same pattern as the SCR stub
for `racecoach run`. Live validation against a real Bob Shell install is a
one-command check: `racecoach bob-analyze --key-file ../apex.json --accept-license`.
