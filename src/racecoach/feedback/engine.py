"""The feedback engine: run metrics (+ optional code summary) -> validated
five-section feedback, with the same audit discipline as the desktop app —
every run, success or failure, mock included, leaves a verbatim record
(prompt, raw response, verdict) in the run directory.

Providers are the project's usual three. mock is deterministic and grounded
in the metrics (worst events become issues); ollama/watsonx send the real
prompt through the same `stream_completion` path the lap coach uses.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from f1coach_core.llm import extract_json_object
from racecoach.analysis.metrics import METRICS_NAME, analyze_run
from racecoach.feedback.contract import (
    MAX_ISSUES,
    FeedbackSchemaError,
    RunFeedback,
    feedback_from_dict,
)
from racecoach.telemetry.run_store import load_run

FEEDBACK_NAME = "feedback.json"
AUDIT_DIR = "coaching"
PROMPT_VERSION = "feedback-v1"
PROVIDER_NAMES = ("mock", "ollama", "watsonx")

_INSTRUCTIONS = """\
You are a racing driver coach reviewing one whole session ("run") after the
race. Use ONLY the run metrics JSON below — every number you mention must
come from it; never invent values.
{code_block}
Run metrics:

{metrics}

Reply with ONLY a JSON object, no prose, no markdown fences, in exactly this
shape:

{{"overall": "<one-paragraph assessment with numbers>",
  "highlights": ["<what went well, backed by the metrics>"],
  "issues": [
    {{"issue": "<one line naming the problem>",
      "cause": "<what the driving/control did, with numbers>",
      "action": "<one concrete instruction>",
      "evidence": [
        {{"ref": "<an event_id like 'off_track-1', a section label like 'T1', or 'lap N'>",
          "detail": "<the numbers behind the claim, copied from the metrics>",
          "numbers": {{}}}}
      ]}}
  ],
  "code_recommendations": ["<concrete controller change: parameter, condition, function>"],
  "next_experiment": "<one thing to try in the next run>"}}

Rules:
- At most {max_issues} issues, worst first (use event severity and lap time impact).
- Every issue needs at least one evidence item whose "ref" exists in the metrics.
- highlights: at most 3, only claims the metrics support.
- If the run is clean, say so in overall and return "issues": [].
"""

_CAUSES = {
    "off_track": "the car ran beyond the track edge",
    "collision": "contact damaged the car",
    "pedal_overlap": "throttle and brake were applied together",
    "steering_jerk": "the steering input stepped abruptly",
    "wheel_lockup": "the brakes locked the wheels",
}
_ACTIONS = {
    "off_track": "tighten the line through that section and rejoin earlier",
    "collision": "leave more margin where the contact happened",
    "pedal_overlap": "release the brake fully before opening the throttle",
    "steering_jerk": "feed the steering in progressively instead of stepping it",
    "wheel_lockup": "release brake pressure sooner and brake slightly earlier",
}
_CODE_HINTS = {
    "off_track": "clamp the target racing line so |track_pos| stays below 0.9",
    "collision": "add a damage-delta check to cut speed after contact",
    "pedal_overlap": "gate accel_cmd to 0 whenever brake_cmd exceeds 0.1",
    "steering_jerk": "rate-limit steer_cmd changes per tick in the controller",
    "wheel_lockup": "cap brake_cmd below the lock-up threshold or modulate on wheel spin",
}


def build_feedback_prompt(metrics: dict, code_summary: str | None = None) -> str:
    code_block = ""
    if code_summary:
        code_block = (
            "\nController code summary (from the IBM Bob analysis; use it to "
            "explain WHY the car behaved this way):\n\n" + code_summary + "\n"
        )
    return _INSTRUCTIONS.format(
        code_block=code_block,
        metrics=json.dumps(metrics, indent=1),
        max_issues=MAX_ISSUES,
    )


def coach_run(
    run_id: str,
    provider: str = "mock",
    code_summary: str | None = None,
    on_progress=None,
    streamer=None,
) -> Path:
    """Generate validated feedback for a stored run; returns the feedback path.

    `streamer` (tests, future providers): callable(prompt, on_progress) -> (text, model),
    overriding the named provider's transport.
    """
    if provider not in PROVIDER_NAMES and streamer is None:
        raise FeedbackSchemaError(
            f"Unknown feedback provider '{provider}'; available: {', '.join(PROVIDER_NAMES)}"
        )
    run = load_run(run_id)
    metrics_path = run.path / METRICS_NAME
    if not metrics_path.is_file():
        analyze_run(run_id)  # one-command UX: analysis is a prerequisite, so do it
    metrics = json.loads(metrics_path.read_text("utf-8"))

    prompt = build_feedback_prompt(metrics, code_summary)
    raw_text, feedback, error, model = "", None, None, provider
    try:
        if provider == "mock" and streamer is None:
            feedback = mock_feedback(metrics)
            raw_text = json.dumps(feedback.to_dict(), indent=2)
            model = feedback.model
            if on_progress is not None:
                on_progress(raw_text)
        else:
            stream = streamer or _streamer_for(provider)
            raw_text, model = stream(prompt, on_progress)
            feedback = feedback_from_dict(
                extract_json_object(raw_text), metrics,
                model=model, prompt_version=PROMPT_VERSION,
            )
    except Exception as exc:
        error = str(exc)
    _write_audit(run.path, provider=model, prompt=prompt, raw=raw_text,
                 feedback=feedback, error=error)
    if error is not None or feedback is None:
        raise FeedbackSchemaError(f"feedback generation failed: {error}")
    destination = run.path / FEEDBACK_NAME
    destination.write_text(json.dumps(feedback.to_dict(), indent=2), encoding="utf-8")
    return destination


def mock_feedback(metrics: dict) -> RunFeedback:
    """Deterministic, offline, contract-valid — grounded in the actual metrics."""
    events = sorted(metrics.get("events", []), key=lambda e: e["severity"], reverse=True)
    laps = metrics.get("laps", [])
    timed = [lap for lap in laps if lap.get("lap_time_s")]
    best = min(timed, key=lambda lap: lap["lap_time_s"]) if timed else None

    issues = []
    for event in events[:MAX_ISSUES]:
        kind = event["kind"]
        issues.append(
            {
                "issue": f"Lap {event['lap']}: {event['summary']}",
                "cause": (
                    f"{_CAUSES.get(kind, 'the car misbehaved')} at "
                    f"{event['dist_start_m']:.0f}-{event['dist_end_m']:.0f} m."
                ),
                "action": _ACTIONS.get(kind, "smooth the inputs through that zone"),
                "evidence": [
                    {"ref": event["event_id"], "detail": event["summary"],
                     "numbers": event["evidence"]}
                ],
            }
        )

    highlights = []
    if best is not None:
        highlights.append(
            f"Best lap {best['lap_time_s']:.3f} s on lap {best['lap']}"
            + (f" (top speed {best['top_speed_kmh']:.0f} km/h)."
               if "top_speed_kmh" in best else ".")
        )
    complete = sum(1 for lap in laps if lap.get("complete"))
    if complete:
        highlights.append(f"{complete} of {len(laps)} laps completed at full distance.")

    overall = (
        f"{len(laps)} laps analysed on a {metrics['track']['length_m']:.0f} m track; "
        + (f"best lap {best['lap_time_s']:.3f} s. " if best else "")
        + (
            f"{len(events)} incident(s) detected — the worst is addressed below."
            if events else "No incidents detected — a clean run."
        )
    )
    payload = {
        "overall": overall,
        "highlights": highlights,
        "issues": issues,
        "code_recommendations": sorted(
            {_CODE_HINTS[event["kind"]] for event in events[:MAX_ISSUES]
             if event["kind"] in _CODE_HINTS}
        ),
        "next_experiment": (
            f"Repeat the run focusing on {events[0]['kind'].replace('_', ' ')} "
            f"around {events[0]['dist_start_m']:.0f} m."
            if events else "Push one step harder on corner entry and compare lap times."
        ),
    }
    return feedback_from_dict(payload, metrics, model="mock", prompt_version="mock-1")


def _streamer_for(provider: str):
    if provider == "ollama":
        from f1coach_core.ollama_coach import OllamaCoach

        coach = OllamaCoach()
        return lambda prompt, on_progress: (
            coach.stream_completion(prompt, on_progress), f"ollama/{coach.model}"
        )
    from f1coach_core.watsonx_coach import WatsonxCoach

    coach = WatsonxCoach()
    return lambda prompt, on_progress: (
        coach.stream_completion(prompt, on_progress), coach.model_id
    )


def _write_audit(run_dir: Path, *, provider, prompt, raw, feedback, error) -> None:
    """Same audit discipline as the desktop app; a failed write must not break
    the coaching it audits."""
    record = {
        "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "provider": provider,
        "ok": error is None,
        "error": error,
        "prompt_version": feedback.prompt_version if feedback else PROMPT_VERSION,
        "prompt": prompt,
        "raw_response": raw,
        "feedback": feedback.to_dict() if feedback else None,
    }
    try:
        audit_dir = run_dir / AUDIT_DIR
        audit_dir.mkdir(parents=True, exist_ok=True)
        stamp = f"{datetime.now(UTC):%Y%m%d-%H%M%S-%f}"  # µs: same-second runs must not overwrite
        (audit_dir / f"{stamp}-{provider}.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )
    except OSError:
        pass
