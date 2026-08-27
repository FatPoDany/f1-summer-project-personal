"""racecoach CLI — the pipeline as commands.

    racecoach import <run.csv>     copy an exporter run into the store
    racecoach list                 show stored runs
    racecoach analyze <run_id>     rule-based metrics -> runs/<id>/metrics.json
    racecoach bob-analyze [files]  IBM Bob Shell code analysis -> docs/bob/exports/
    racecoach run                  drive through the TORCS Granite/SCR bridge
    racecoach capture-human        record a human TORCS session without controlling it
    racecoach recover-capture      register laps a crashed session left unregistered
    racecoach debrief <session>    coached debrief for a folder of canonical laps
    racecoach install-model <f>   adopt a Granite weights file you already have
    racecoach study-summary       per-participant, per-phase rows for statistics
    racecoach study-laps          one row per lap, for learning curves
    racecoach package <dir>       bundle one capture into a file to hand over
    racecoach collect <zips>      verify and pool handovers from participants
    racecoach capture-synthetic    run pinned unattended robot reference sessions
    racecoach report               render the post-race report

Exit codes: 0 ok, 2 readable user error (bad file, unknown run).
"""

import argparse
import json
import os
import sys
from pathlib import Path

from racecoach.analysis.metrics import analyze_run
from racecoach.feedback.contract import FeedbackSchemaError
from racecoach.feedback.engine import PROVIDER_NAMES, coach_run
from racecoach.ibm.bob import BobExportError, load_export, newest_export
from racecoach.ibm.bobshell import DEFAULT_QUESTION, analyze_with_bobshell
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

    bobshell_cmd = commands.add_parser(
        "bob-analyze",
        help="run IBM Bob Shell on the control code and archive the answer as an export",
    )
    bobshell_cmd.add_argument(
        "files", nargs="*", default=["src/racecoach/control/simple_driver.py"],
        help="files Bob is shown, relative to --cwd (default: the simple driver)",
    )
    bobshell_cmd.add_argument(
        "--prompt", default=DEFAULT_QUESTION,
        help="the question to ask (default: the retroanalysis question from docs/bob/README.md)",
    )
    bobshell_cmd.add_argument(
        "--topic", default="code-retro",
        help="filename slug for the export: YYYY-MM-DD-<topic>.md",
    )
    bobshell_cmd.add_argument(
        "--cwd", default=None, help="directory to run Bob Shell from (the analysed repo's root)"
    )
    bobshell_cmd.add_argument(
        "--key-file", default=None,
        help='JSON file with an "apikey" field; BOBSHELL_API_KEY in the environment wins',
    )
    bobshell_cmd.add_argument(
        "--timeout", type=float, default=300.0, help="seconds to wait for Bob's answer"
    )
    bobshell_cmd.add_argument(
        "--accept-license", action="store_true",
        help="accept the IBM license (needed once, on the first non-interactive run)",
    )

    run_cmd = commands.add_parser("run", help="drive the car and capture telemetry (Path B)")
    run_cmd.add_argument("--config", default="configs/race.toml")
    run_cmd.add_argument("--host", default=None, help="override [connection].host")
    run_cmd.add_argument("--port", type=int, default=None, help="override [connection].port")
    run_cmd.add_argument("--max-laps", type=int, default=None, help="override [race].max_laps")
    run_cmd.add_argument(
        "--live-coach",
        choices=("off", "mock", "granite"),
        default="off",
        help="asynchronous live advisor; Granite never controls the car (default: off)",
    )
    run_cmd.add_argument(
        "--coach-interval-s",
        type=float,
        default=10.0,
        help="seconds of simulation time between live advice snapshots (default: 10)",
    )
    run_cmd.add_argument(
        "--granite-base-url",
        default=None,
        help="OpenAI-compatible /v1 endpoint (or GRANITE_BASE_URL)",
    )
    run_cmd.add_argument(
        "--granite-model",
        default=None,
        help="served Granite model alias (or GRANITE_MODEL)",
    )
    from racecoach.telemetry.torcs_runtime import default_torcs_binary

    package_cmd = commands.add_parser(
        "package", help="bundle one capture folder into a single file to hand over"
    )
    package_cmd.add_argument("capture_dir", type=Path)
    package_cmd.add_argument(
        "--out", type=Path, default=None, help="destination .zip (default: alongside)"
    )

    collect_cmd = commands.add_parser(
        "collect", help="verify and unpack handovers from several participants"
    )
    collect_cmd.add_argument("archives", nargs="+", type=Path)
    collect_cmd.add_argument(
        "--into", type=Path, required=True, help="folder to pool the sessions into"
    )

    study_cmd = commands.add_parser(
        "study-summary",
        help="one row per participant per phase, for a paired statistical test",
    )
    study_cmd.add_argument(
        "roots",
        nargs="*",
        type=Path,
        default=None,
        help="session folders, or folders of them; defaults to the whole workspace",
    )
    study_cmd.add_argument(
        "--out", type=Path, default=None, help="write CSV here instead of stdout"
    )

    laps_cmd = commands.add_parser(
        "study-laps",
        help="one row per lap, for learning curves and mixed-effects models",
    )
    laps_cmd.add_argument(
        "roots",
        nargs="*",
        type=Path,
        default=None,
        help="session folders, or folders of them; defaults to the whole workspace",
    )
    laps_cmd.add_argument(
        "--out", type=Path, default=None, help="write CSV here instead of stdout"
    )

    install_model_cmd = commands.add_parser(
        "install-model",
        help="adopt a Granite weights file supplied by other means",
    )
    install_model_cmd.add_argument(
        "gguf", type=Path, help="the granite-4.1-3b-Q4_K_M.gguf file to verify and keep"
    )

    debrief_cmd = commands.add_parser(
        "debrief",
        help="write a coached debrief for a session folder of canonical laps",
    )
    debrief_cmd.add_argument(
        "session_dir", type=Path, help="a sessions/<name> folder of lap CSVs"
    )
    debrief_cmd.add_argument(
        "--out", type=Path, default=None, help="write markdown here instead of stdout"
    )
    debrief_cmd.add_argument(
        "--no-model",
        action="store_true",
        help="measure only; do not start or contact a model",
    )
    debrief_cmd.add_argument(
        "--granite-base-url", default=None, help="OpenAI-compatible /v1 endpoint"
    )
    debrief_cmd.add_argument("--granite-model", default=None, help="served model alias")

    recover_cmd = commands.add_parser(
        "recover-capture",
        help="register the laps in a capture folder that was never finalised",
    )
    recover_cmd.add_argument(
        "capture_dir",
        type=Path,
        help="a captures/human/<id>-<phase>-<timestamp> folder",
    )

    human_cmd = commands.add_parser(
        "capture-human",
        help="launch TORCS and record pseudonymous human-driver telemetry",
    )
    human_cmd.add_argument(
        "--participant-id",
        required=True,
        help="pseudonymous study id, for example P001 (never use a name or email)",
    )
    human_cmd.add_argument(
        "--phase",
        required=True,
        help="study phase/condition slug, for example baseline or coached",
    )
    human_cmd.add_argument(
        "--torcs",
        type=Path,
        default=default_torcs_binary(),
        help="patched TORCS executable produced by build.sh install",
    )
    human_cmd.add_argument(
        "torcs_args",
        nargs=argparse.REMAINDER,
        help="arguments passed directly to TORCS; put them after --",
    )
    synthetic_cmd = commands.add_parser(
        "capture-synthetic",
        help="run pinned unattended TORCS reference sessions",
    )
    synthetic_cmd.add_argument(
        "--count",
        type=int,
        default=3,
        help="number of sequential reference sessions, 1-20 (default: 3)",
    )
    synthetic_cmd.add_argument(
        "--torcs",
        type=Path,
        default=default_torcs_binary(),
        help="patched TORCS executable produced by build.sh install",
    )
    synthetic_cmd.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="temporary Apex workspace override for this batch",
    )
    synthetic_cmd.add_argument(
        "torcs_args",
        nargs=argparse.REMAINDER,
        help="arguments passed directly to TORCS; put them after --",
    )
    report_cmd = commands.add_parser("report", help="render the post-race report")
    report_cmd.add_argument("--run", dest="run_id", required=True)

    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except KeyboardInterrupt:
        print("racecoach: interrupted.", file=sys.stderr)
        return 130
    except (
        RunImportError,
        FeedbackSchemaError,
        BobExportError,
        RuntimeError,
        OSError,
        ValueError,
    ) as exc:
        print(f"racecoach: {exc}", file=sys.stderr)
        return 2


def _study_laps(roots: list[Path] | None) -> list:
    """Every readable lap under the folders given, or the whole workspace.

    Accepts a session folder, a folder of session folders, or nothing at all,
    because a researcher pooling what several participants sent in has the
    middle case and should not have to expand it into arguments by hand.
    """
    from f1coach_core.workspace import list_study_sessions
    from racecoach.granite.report import session_laps

    laps = []
    for root in roots or list_study_sessions():
        found = session_laps(root)
        if found:
            laps.extend(found)
            continue
        for child in sorted(Path(root).iterdir()) if Path(root).is_dir() else []:
            if child.is_dir():
                laps.extend(session_laps(child))
    if not laps:
        raise RunImportError(
            "No readable laps found. A folder of handovers holds raw simulator "
            "runs, not laps -- `racecoach collect` registers those; point this "
            "at the workspace, or at session folders."
        )
    return laps


def _backgrounds_for(rows: list) -> dict:
    """The questionnaire for every participant in these rows, by id."""
    from f1coach_core.participant import load_background

    return {row.driver: load_background(row.driver) for row in rows}


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
    if args.command == "bob-analyze":
        print(f"Asking Bob Shell (may take a few minutes; timeout {args.timeout:.0f}s) …",
              flush=True)
        destination = analyze_with_bobshell(
            args.prompt,
            args.files,
            topic=args.topic,
            cwd=args.cwd,
            key_file=Path(args.key_file) if args.key_file else None,
            timeout_s=args.timeout,
            accept_license=args.accept_license,
        )
        print(f"Bob Shell answer archived as {destination}")
        print("Next: racecoach coach <run_id> --bob")
        return 0
    if args.command == "run":
        from racecoach.granite import GraniteClient, LiveGraniteAdvisor, MockGraniteClient
        from racecoach.telemetry.live import capture_run

        config, driver = load_race_config(
            Path(args.config), host=args.host, port=args.port, max_laps=args.max_laps
        )
        advisor = None
        if args.live_coach != "off":
            backend = (
                MockGraniteClient()
                if args.live_coach == "mock"
                else GraniteClient(base_url=args.granite_base_url, model=args.granite_model)
            )

            def show_advice(snapshot, result) -> None:
                advice = result.advice
                print(
                    f"\n[Granite 4.1 · lap {snapshot.lap} · {snapshot.sim_time_s:.1f}s"
                    f" · {advice.urgency}] {advice.message}",
                    flush=True,
                )

            advisor = LiveGraniteAdvisor(
                backend,
                interval_s=args.coach_interval_s,
                on_advice=show_advice,
            )
        print(f"Connecting to the TORCS Granite/SCR bridge at {config.host}:{config.port} …")
        run_dir = capture_run(config, driver, advisor=advisor)
        print(f"Captured {run_dir.name} -> {run_dir}")
        if advisor is not None and advisor.audit_path is not None:
            print(f"Granite live audit -> {advisor.audit_path}")
            if advisor.audit_error is not None:
                print(
                    f"Warning: Granite audit write failed: {advisor.audit_error}",
                    file=sys.stderr,
                )
            if advisor.shutdown_timed_out:
                print("Warning: Granite worker did not stop before its timeout", file=sys.stderr)
        print(f"Next: racecoach analyze {run_dir.name} · racecoach coach {run_dir.name}"
              f" · racecoach report --run {run_dir.name}")
        return 0
    if args.command == "package":
        from racecoach.telemetry.handover import package

        out = args.out or args.capture_dir.with_suffix(".zip")
        result = package(args.capture_dir, out)
        print(f"{result.files} files ({result.laps} CSV) -> {result.path}")
        print("Send this one file to the research team.")
        return 0
    if args.command == "collect":
        from f1coach_core.workspace import sessions_root
        from racecoach.telemetry.handover import collect, register

        results = collect(args.archives, args.into)
        # Verifying and registering are two different promises. The first says
        # the data arrived intact; only the second makes it readable by anything
        # downstream, because what a participant hands over is a raw TORCS run
        # and every study reader takes canonical single laps.
        total = 0
        for handover in results:
            who = handover.participant_id or "(no id)"
            print(f"{who}: {handover.files} files -> {handover.path}")
            registered = register(handover)
            total += registered.laps
            kept = "background kept" if registered.background else "no background travelled"
            print(f"  {registered.laps} lap(s) in session {registered.session} ({kept})")
            for note in registered.skipped:
                print(f"  skipped {note}")
        print(
            f"{len(results)} handover(s) verified into {args.into}; "
            f"{total} lap(s) registered in {sessions_root()}"
        )
        print("Next: racecoach study-summary --out summary.csv (or study-laps for one row per lap)")
        return 0
    if args.command == "study-summary":
        from f1coach_core.study import summarise_all, summary_csv

        laps = _study_laps(args.roots)
        summaries = summarise_all(laps)
        if not summaries:
            raise RunImportError(
                f"{len(laps)} laps found, but none carry both a driver and a phase, "
                "so they cannot be assigned to a group."
            )
        text = summary_csv(summaries, backgrounds=_backgrounds_for(summaries))
        if args.out:
            args.out.write_text(text, encoding="utf-8")
            print(f"{len(summaries)} rows from {len(laps)} laps -> {args.out}")
        else:
            print(text, end="")
        return 0
    if args.command == "study-laps":
        from f1coach_core.study import lap_csv, lap_rows

        laps = _study_laps(args.roots)
        rows = lap_rows(laps)
        if not rows:
            raise RunImportError(
                f"{len(laps)} laps found, but none carry both a driver and a phase, "
                "so they cannot be assigned to a group."
            )
        text = lap_csv(laps, backgrounds=_backgrounds_for(rows))
        if args.out:
            args.out.write_text(text, encoding="utf-8")
            print(f"{len(rows)} rows from {len(laps)} laps -> {args.out}")
        else:
            print(text, end="")
        return 0
    if args.command == "install-model":
        from racecoach.granite import model as granite_model

        path = granite_model.import_model(args.gguf)
        print(f"Verified Granite model installed -> {path}")
        return 0
    if args.command == "debrief":
        from racecoach.granite import report as granite_report
        from racecoach.granite import server as granite_server

        laps = granite_report.session_laps(args.session_dir)
        if not laps:
            raise RunImportError(f"No readable laps in {args.session_dir}")

        base_url = model = None
        managed = None
        if not args.no_model:
            base_url = args.granite_base_url or os.environ.get("GRANITE_BASE_URL")
            model = args.granite_model or os.environ.get("GRANITE_MODEL")
            if base_url is None:
                # Nothing configured: bring up the bundled server ourselves, the
                # same way the app does for a participant. A researcher running
                # this over a folder someone emailed them should not have to.
                try:
                    managed = granite_server.GraniteServer()
                    base_url = managed.start()
                    model = model or granite_server.gm.MODEL_REPO.replace("-GGUF", "")
                except granite_server.ServerError as exc:
                    print(f"racecoach: coaching unavailable ({exc})", file=sys.stderr)
                    base_url = model = None
                    managed = None
        try:
            result = granite_report.build_report(laps, base_url=base_url, model=model)
        finally:
            if managed is not None:
                managed.stop()

        text = granite_report.render_markdown(result)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
            print(f"Debrief -> {args.out}")
        else:
            print(text)
        return 0
    if args.command == "recover-capture":
        from racecoach.telemetry.human_capture import finish_capture

        result = finish_capture(args.capture_dir)
        print(f"Recovered from {result.capture_dir}")
        for run_dir in result.run_dirs:
            print(f"Registered run {run_dir.name} -> {run_dir}")
        return 0
    if args.command == "capture-human":
        from racecoach.telemetry.human_capture import HumanCaptureConfig, capture_human_runs

        torcs_args = tuple(args.torcs_args)
        if torcs_args[:1] == ("--",):
            torcs_args = torcs_args[1:]
        print(
            f"Launching TORCS human capture for {args.participant_id} / {args.phase}. "
            "Quit TORCS after the driving session to finalize the data.",
            flush=True,
        )
        result = capture_human_runs(
            HumanCaptureConfig(
                participant_id=args.participant_id,
                phase=args.phase,
                torcs_binary=args.torcs,
                torcs_args=torcs_args,
            )
        )
        print(f"Raw capture and manifest -> {result.capture_dir}")
        for run_dir in result.run_dirs:
            print(f"Registered run {run_dir.name} -> {run_dir}")
        print("Next: racecoach analyze RUN_ID · racecoach coach RUN_ID")
        return 0
    if args.command == "capture-synthetic":
        from racecoach.telemetry.synthetic_capture import (
            SyntheticCaptureConfig,
            capture_synthetic_batch,
            default_robot_study_preset,
        )

        torcs_args = tuple(args.torcs_args)
        if torcs_args[:1] == ("--",):
            torcs_args = torcs_args[1:]
        previous_workspace = os.environ.get("APEX_WORKSPACE")
        if args.workspace is not None:
            os.environ["APEX_WORKSPACE"] = str(args.workspace)
        try:
            print(
                f"Launching {args.count} synthetic reference session(s): "
                "berniw 9 · g-track-1 · car7-trb1 · 3 laps.",
                flush=True,
            )
            result = capture_synthetic_batch(
                SyntheticCaptureConfig(
                    torcs_binary=args.torcs,
                    preset=default_robot_study_preset(args.torcs),
                    count=args.count,
                    torcs_args=torcs_args,
                )
            )
        finally:
            if args.workspace is not None:
                if previous_workspace is None:
                    os.environ.pop("APEX_WORKSPACE", None)
                else:
                    os.environ["APEX_WORKSPACE"] = previous_workspace
        print(f"Synthetic batch and audit -> {result.batch_dir}")
        for outcome in result.outcomes:
            if outcome.status == "complete":
                run_ids = ", ".join(path.name for path in outcome.run_dirs)
                print(f"  {outcome.session_id}: complete -> {run_ids}")
            else:
                detail = f" ({outcome.error})" if outcome.error else ""
                print(f"  {outcome.session_id}: {outcome.status}{detail}")
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
    bridge_token = os.environ.get("GRANITE_BRIDGE_TOKEN")
    if bridge_token is not None and not (
        16 <= len(bridge_token) <= 128
        and bridge_token.isascii()
        and all(character.isalnum() or character in "-_" for character in bridge_token)
    ):
        raise RunImportError(
            "GRANITE_BRIDGE_TOKEN must contain 16-128 letters, digits, '-' or '_'"
        )
    bot_id = (
        f"SCR:{bridge_token}"
        if bridge_token
        else str(connection.get("bot_id", "SCR"))
    )
    config = LiveConfig(
        host=host or connection.get("host", "localhost"),
        port=port or int(connection.get("port", 3001)),
        bot_id=bot_id,
        run_name=str(race.get("run_name", "live")),
        max_laps=max_laps if max_laps is not None else race.get("max_laps", 3),
        timeout_s=float(connection.get("timeout_s", 1.0)),
        identify_attempts=int(connection.get("identify_attempts", 5)),
        max_idle_s=float(connection.get("max_idle_s", 30.0)),
    )
    try:
        driver = SimpleDriver(**data.get("driver", {}))
    except TypeError as exc:
        raise RunImportError(f"[driver] in {path} has an unknown key: {exc}") from exc
    return config, driver


if __name__ == "__main__":
    raise SystemExit(main())
