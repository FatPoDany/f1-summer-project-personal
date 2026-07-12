# IBM Bob → racecoach: the code-analysis evidence flow

The IBM contact asked for two things at once: use **IBM Bob** during
development, and make the post-race feedback explain *why* the car drove the
way it did by combining telemetry with an analysis of the control code. Bob
is an IDE assistant (installed as the Bob IDE; there is no scriptable CLI in
the current install), so the integration is a documented manual-export flow —
deliberately, and with an evidence trail at every step.

## The flow

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
3. **Feed the coach.** `racecoach coach <run_id> --bob` grounds the feedback
   prompt with the newest archived export (or pass a specific file with
   `--code-summary path`). The engine's prompt tells the model to use the
   code summary to explain *why* the telemetry looks the way it does.
4. **Evidence chain.** Every coach run writes an audit record
   (`runs/<id>/coaching/*.json`) embedding the full prompt — including the
   Bob text it used — plus the raw response and the validated feedback. So
   the chain **Bob export → prompt → feedback** is reproducible end to end,
   which is exactly the "retroanalysis + logged Bob usage" the client asked
   to see.

`docs/bob/exports/2026-07-12-template.md` shows the format; it is a template,
not evidence, and the tooling ignores it.
