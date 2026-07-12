"""racecoach CLI — the pipeline as commands.

    racecoach import <run.csv>     copy an exporter run into the store
    racecoach list                 show stored runs
    racecoach analyze <run_id>     rule-based metrics -> runs/<id>/metrics.json
    racecoach run / report         arrive with Path B and the report stage

Exit codes: 0 ok, 2 readable user error (bad file, unknown run).
"""

import argparse
import json
import sys
from pathlib import Path

from racecoach.analysis.metrics import analyze_run
from racecoach.feedback.contract import FeedbackSchemaError
from racecoach.feedback.engine import PROVIDER_NAMES, coach_run
from racecoach.telemetry.run_store import RunImportError, import_run, list_runs, runs_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="racecoach", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    import_cmd = commands.add_parser("import", help="import a TORCS exporter CSV")
    import_cmd.add_argument("csv", help="path to the exporter run CSV")

    commands.add_parser("list", help="list stored runs")

    analyze_cmd = commands.add_parser("analyze", help="compute metrics for a stored run")
    analyze_cmd.add_argument("run_id")

    coach_cmd = commands.add_parser("coach", help="LLM feedback for a stored run")
    coach_cmd.add_argument("run_id")
    coach_cmd.add_argument("--provider", default="mock", choices=PROVIDER_NAMES)
    coach_cmd.add_argument(
        "--code-summary", dest="code_summary", default=None,
        help="path to a code overview (e.g. an IBM Bob export) to ground the why",
    )

    run_cmd = commands.add_parser("run", help="drive the car and capture telemetry (Path B)")
    run_cmd.add_argument("--config", default="configs/race.toml")
    report_cmd = commands.add_parser("report", help="render the post-race report")
    report_cmd.add_argument("--run", dest="run_id", required=True)

    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except (RunImportError, FeedbackSchemaError, RuntimeError, OSError) as exc:
        print(f"racecoach: {exc}", file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "import":
        run_dir = import_run(args.csv)
        print(f"Imported as {run_dir.name} -> {run_dir}")
        return 0
    if args.command == "list":
        metas = list_runs()
        if not metas:
            print(f"No runs yet. Import one with: racecoach import <run.csv>  ({runs_root()})")
            return 0
        for meta in metas:
            laps = f"{len(meta.laps_seen)} laps" if meta.laps_seen else "laps unknown"
            cadence = f"{meta.cadence_hz} Hz" if meta.cadence_hz else "cadence unknown"
            print(f"{meta.run_id}  {meta.n_samples} samples · {laps} · {cadence}"
                  f" · cars: {', '.join(meta.car_names) or '?'}")
        return 0
    if args.command == "analyze":
        destination = analyze_run(args.run_id)
        metrics = json.loads(destination.read_text("utf-8"))
        events = metrics["events"]
        print(f"Metrics written to {destination}")
        print(f"  laps: {len(metrics['laps'])} · sections: {len(metrics['sections'])}"
              f" · events: {len(events)}")
        for event in events[:8]:
            print(f"  [{event['event_id']}] lap {event['lap']} "
                  f"@ {event['dist_start_m']:.0f} m: {event['summary']}")
        if len(events) > 8:
            print(f"  … and {len(events) - 8} more")
        for note in metrics["analysis_notes"]:
            print(f"  note: {note}")
        return 0
    if args.command == "coach":
        summary_text = None
        if args.code_summary:
            summary_text = Path(args.code_summary).read_text(encoding="utf-8")
        destination = coach_run(args.run_id, provider=args.provider, code_summary=summary_text)
        feedback = json.loads(destination.read_text("utf-8"))
        print(f"Feedback written to {destination}  ({feedback['model']}"
              f" · {feedback['prompt_version']})")
        print(f"\nOverall: {feedback['overall']}")
        for line in feedback["highlights"]:
            print(f"  + {line}")
        for issue in feedback["issues"]:
            refs = ", ".join(item["ref"] for item in issue["evidence"])
            print(f"  ! {issue['issue']}  [{refs}]")
            print(f"    -> {issue['action']}")
        for line in feedback["code_recommendations"]:
            print(f"  # {line}")
        print(f"Next experiment: {feedback['next_experiment']}")
        return 0
    if args.command == "run":
        print(
            "racecoach run needs the live SCR/TORCS environment, which isn't set up "
            "yet (see docs/DATA_AVAILABILITY.md §6). Import an exporter CSV instead: "
            "racecoach import <run.csv>",
            file=sys.stderr,
        )
        return 2
    if args.command == "report":
        from racecoach.report.run_report import report_run

        destination = report_run(args.run_id)
        print(f"Report written to {destination}")
        return 0
    raise AssertionError(f"unhandled command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
