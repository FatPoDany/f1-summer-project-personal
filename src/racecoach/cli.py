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
from racecoach.ibm.bob import BobExportError, load_export, newest_export
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
    summary_source = coach_cmd.add_mutually_exclusive_group()
    summary_source.add_argument(
        "--code-summary", dest="code_summary", default=None,
        help="path to a code overview (e.g. an IBM Bob export) to ground the why",
    )
    summary_source.add_argument(
        "--bob", action="store_true",
        help="use the newest archived IBM Bob export (docs/bob/README.md)",
    )

    run_cmd = commands.add_parser("run", help="drive the car and capture telemetry (Path B)")
    run_cmd.add_argument("--config", default="configs/race.toml")
    run_cmd.add_argument("--host", default=None, help="override [connection].host")
    run_cmd.add_argument("--port", type=int, default=None, help="override [connection].port")
    run_cmd.add_argument("--max-laps", type=int, default=None, help="override [race].max_laps")
    report_cmd = commands.add_parser("report", help="render the post-race report")
    report_cmd.add_argument("--run", dest="run_id", required=True)

    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except (RunImportError, FeedbackSchemaError, BobExportError, RuntimeError, OSError) as exc:
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
        if args.bob:
            source = newest_export()
            summary_text = load_export(source)
            print(f"Grounding feedback with Bob export {source.name}")
        elif args.code_summary:
            summary_text = load_export(Path(args.code_summary))
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
        from racecoach.telemetry.live import capture_run

        config, driver = load_race_config(
            Path(args.config), host=args.host, port=args.port, max_laps=args.max_laps
        )
        print(f"Connecting to scr_server at {config.host}:{config.port} …")
        run_dir = capture_run(config, driver)
        print(f"Captured {run_dir.name} -> {run_dir}")
        print(f"Next: racecoach analyze {run_dir.name} · racecoach coach {run_dir.name}"
              f" · racecoach report --run {run_dir.name}")
        return 0
    if args.command == "report":
        from racecoach.report.run_report import report_run

        destination = report_run(args.run_id)
        print(f"Report written to {destination}")
        return 0
    raise AssertionError(f"unhandled command {args.command}")


def load_race_config(path: Path, *, host=None, port=None, max_laps=None):
    """configs/race.toml (+ CLI overrides) -> (LiveConfig, SimpleDriver)."""
    import tomllib

    from racecoach.control.simple_driver import SimpleDriver
    from racecoach.telemetry.live import LiveConfig

    data: dict = {}
    if path.is_file():
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    elif str(path) != "configs/race.toml":  # an explicitly named config must exist
        raise RunImportError(f"No such config file: {path}")
    connection = data.get("connection", {})
    race = data.get("race", {})
    config = LiveConfig(
        host=host or connection.get("host", "localhost"),
        port=port or int(connection.get("port", 3001)),
        bot_id=str(connection.get("bot_id", "SCR")),
        run_name=str(race.get("run_name", "live")),
        max_laps=max_laps if max_laps is not None else race.get("max_laps", 3),
        timeout_s=float(connection.get("timeout_s", 1.0)),
        identify_attempts=int(connection.get("identify_attempts", 5)),
    )
    try:
        driver = SimpleDriver(**data.get("driver", {}))
    except TypeError as exc:
        raise RunImportError(f"[driver] in {path} has an unknown key: {exc}") from exc
    return config, driver


if __name__ == "__main__":
    raise SystemExit(main())
