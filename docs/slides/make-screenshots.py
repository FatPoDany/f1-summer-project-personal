"""Render every Apex feature screen to PNG, headless.

Real IBM Granite 4.1 is used for the coaching screens when the local server
answers; the deterministic mock is never substituted silently.
"""
import sys
from pathlib import Path

from PySide6.QtWidgets import QSplitter

from apex.app import create_app
from apex.coach_panel import AuditDialog
from apex.main_window import MainWindow
from f1coach_core import (
    build_coach_prompt,
    build_evidence_summary,
    ensure_sample_session,
    get_provider,
    load_sample_session,
    write_coaching_audit,
)

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
W, H = 1440, 810
saved: list[str] = []


def main() -> int:
    app = create_app([sys.argv[0]])
    ensure_sample_session()
    window = MainWindow()
    window.resize(W, H)
    window.show()

    def settle(n: int = 8) -> None:
        for _ in range(n):
            app.processEvents()

    def resize_window(height: int) -> None:
        window.resize(W, max(height, 545))
        settle()

    def shot(widget, name: str, crop_h: int | None = None) -> None:
        settle()
        pixmap = widget.grab()
        if crop_h is not None:  # trim the empty remainder of a short page
            pixmap = pixmap.copy(0, 0, pixmap.width(), crop_h)
        pixmap.save(str(OUT / f"{name}.png"))
        saved.append(name)
        print("  wrote", name, pixmap.width(), "x", pixmap.height())

    # --- 1. Garage ------------------------------------------------------
    resize_window(560)
    shot(window, "01-garage")

    # --- 2..4 Collect Data (participant workflow) -----------------------
    capture = window._capture
    window._stacked.setCurrentWidget(capture)
    capture._participant_id.setText("P001")
    capture._phase.setCurrentIndex(0)
    for check in capture._readiness_checks:
        check.setChecked(True)
    capture._update_start_state()
    resize_window(620)
    shot(window, "02-collect-prepare")

    capture._pages.setCurrentWidget(capture._drive_page)
    capture._drive_status.setText(
        "Recording — TORCS is running; 5 assigned laps on g-track-1."
    )
    resize_window(560)
    shot(window, "03-collect-drive", crop_h=362)

    capture._pages.setCurrentWidget(capture._complete_page)
    capture._result_summary.setText(
        "1 run validated and saved — your driving is recorded as individual "
        "laps you can review."
    )
    capture._result_path.setText(
        "Send this folder to the researchers: "
        "~/Apex/captures/human/P001-baseline-20260817-105515"
    )
    resize_window(560)
    shot(window, "04-collect-saved", crop_h=292)
    capture._pages.setCurrentWidget(capture._setup_page)

    # --- 5. Lap Analysis, single lap, real Granite ----------------------
    session = load_sample_session()
    best = session.best_lap
    ragged = session.laps[2]
    resize_window(H)
    window.show_analysis(ragged, session)
    view = window._analysis
    splitter = view.findChild(QSplitter)
    if splitter is not None:
        splitter.setSizes([W - 470, 450])
    settle()

    summary = build_evidence_summary(ragged, best)
    raw: list[str] = []
    provider = get_provider(PROVIDER)
    report = provider.generate(summary, on_progress=lambda t: raw.append(t))
    print(f"  granite: model={report.model} findings={len(report.findings)}")
    view._panel.show_report(report)
    if report.findings:
        view._stack.highlight_span(*report.findings[0].evidence[0].span)
    shot(window, "06-analysis-granite")

    # --- 6. Debrief + evidence zoom (reference lap selected) ------------
    idx = view._ref_combo.findData(best)
    if idx >= 0:
        view._ref_combo.setCurrentIndex(idx)
    settle()
    ref_report = provider.generate(build_evidence_summary(ragged, best))
    print(f"  granite (vs reference): findings={len(ref_report.findings)}")
    view._panel.show_report(ref_report)
    if view._debrief.count():
        view._debrief.setCurrentRow(0)
        view._zoom_debrief_item(view._debrief.item(0))
    shot(window, "07-analysis-debrief")

    # --- 7. Replay window ------------------------------------------------
    # The bundled demo fixture carries no world position; a real capture does,
    # so the track map is drawn from genuinely recorded x/y.
    from apex.widgets.replay_window import ReplayWindow
    from f1coach_core.features import detect_corners

    real_lap = None
    if REAL_RUN.exists():
        window._open_captured_runs([str(REAL_RUN)], navigate=False)
        settle()
        real_session = window._garage.session
        if real_session is not None and real_session.laps:
            candidate = real_session.laps[0]
            if candidate.has_track_map:
                real_lap = candidate
                print("  replay source:", real_session.name, candidate.source.stem)
    replay_lap = real_lap if real_lap is not None else ragged
    replay = ReplayWindow(window)
    replay.resize(640, 520)
    zones = detect_corners(replay_lap)
    if zones:
        zone = zones[min(2, len(zones) - 1)]
        d0, d1 = zone["span_m"]
        title = f"{zone['corner']} · {replay_lap.source.stem}"
    else:
        # No detected corner zone: centre the stretch on the slowest point, the
        # tightest part of the recorded drive.
        df = replay_lap.df
        apex = float(df["dist"].iloc[int(df["speed"].idxmin())])
        end = float(df["dist"].iloc[-1])
        d0, d1 = max(0.0, apex - 150.0), min(end, apex + 150.0)
        title = f"{replay_lap.source.stem} · slowest point"
    replay._replay.set_stretch(replay_lap, d0, d1, title)
    replay.show()
    shot(replay, "08-replay")
    replay.close()

    # --- 8. Compare -----------------------------------------------------
    window._compare.set_session(session, lap_a=ragged)
    window._stacked.setCurrentWidget(window._compare)
    shot(window, "09-compare")

    # --- 9. Audit record -------------------------------------------------
    audit_path = write_coaching_audit(
        lap_source=ragged.source,
        provider=PROVIDER,
        lap_name=ragged.source.stem,
        reference_name=best.source.stem,
        evidence_summary=summary,
        prompt=build_coach_prompt(summary),
        raw_response=raw[-1] if raw else "",
        report=report,
    )
    dialog = AuditDialog(audit_path, window)
    dialog.resize(900, 620)
    dialog.show()
    shot(dialog, "11-audit")
    dialog.close()

    print(f"\n{len(saved)} screenshots in {OUT}")
    return 0


PROVIDER = "granite"
REAL_RUN = Path.home() / "Apex/runs/human-1-1786964116-1566559-1-20260817-115752"
if __name__ == "__main__":
    raise SystemExit(main())
