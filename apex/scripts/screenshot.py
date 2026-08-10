"""Render the Garage, Lap Analysis, Compare, and audit screens to PNGs —
headless-safe.

Feeds the weekly blog post and IBM status forms with real pixels; CI uploads
them per OS as build artifacts.

Usage: [QT_QPA_PLATFORM=offscreen] python scripts/screenshot.py [out_dir]
"""

import sys
from pathlib import Path

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


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    out_dir.mkdir(parents=True, exist_ok=True)
    app = create_app([sys.argv[0]])
    ensure_sample_session()

    window = MainWindow()
    window.resize(1280, 720)
    window.show()

    def settle() -> None:
        for _ in range(6):
            app.processEvents()

    ok = True
    settle()
    ok &= window.grab().save(str(out_dir / "apex-garage.png"))

    session = load_sample_session()
    best = session.best_lap
    ragged = session.laps[2]
    assert best is not None
    window.show_analysis(ragged, session)  # reference defaults to the session best
    summary = build_evidence_summary(ragged, best)
    raw: list[str] = []
    report = get_provider("mock").generate(summary, on_progress=lambda text: raw.append(text))
    view = window._analysis
    view._panel.show_report(report)
    if report.findings:  # shade the worst finding's evidence zone, keep full-lap zoom
        view._stack.highlight_span(*report.findings[0].evidence[0].span)
    settle()
    ok &= window.grab().save(str(out_dir / "apex-analysis.png"))

    window._stacked.setCurrentWidget(window._compare)
    settle()
    ok &= window.grab().save(str(out_dir / "apex-compare.png"))

    # the audit affordance: the same record a real run writes, opened in its dialog
    audit_path = write_coaching_audit(
        lap_source=ragged.source,
        provider="mock",
        lap_name=ragged.source.stem,
        reference_name=best.source.stem,
        evidence_summary=summary,
        prompt=build_coach_prompt(summary),
        raw_response=raw[-1] if raw else "",
        report=report,
    )
    dialog = AuditDialog(audit_path, window)
    dialog.resize(760, 560)
    dialog.show()
    settle()
    ok &= dialog.grab().save(str(out_dir / "apex-audit.png"))
    dialog.close()

    print(
        f"{'wrote' if ok else 'FAILED to write'} apex-garage.png, apex-analysis.png, "
        f"apex-compare.png, apex-audit.png in {out_dir}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
