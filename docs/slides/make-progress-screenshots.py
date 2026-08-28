"""Screens for the progress deck, rendered headless from the real study data.

Nothing here is mocked. The workspace is a copy of the researcher's own -- five
phases from three participants -- and the coaching shown is the report Granite
actually produced for that lap, restored from its audit record rather than
regenerated, so the deck shows what a participant was really told.

    python docs/slides/make-progress-screenshots.py <outdir> [workspace]

Deliberately NOT run under QT_QPA_PLATFORM=offscreen: that plugin ships no font
engine on Windows, so every label renders as an empty box and the deck would
show a working layout full of tofu. The real platform plugin is used instead and
each window is given WA_DontShowOnScreen, which lays it out and paints it into a
pixmap without ever mapping it to the desktop -- so it neither steals focus from
whatever the researcher is doing nor flashes on screen.
"""

import os
import shutil
import sys
import time
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/slides/figures/progress")
WS = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.home() / "apex-deck-ws"
REAL = Path.home() / "Apex"
W, H = 1500, 845


def seed_workspace() -> None:
    """A throwaway copy, so nothing the deck does can touch the study data."""
    shutil.rmtree(WS, ignore_errors=True)
    (WS / "sessions").mkdir(parents=True)
    for session in sorted((REAL / "sessions").iterdir()):
        if session.is_dir() and session.name != "sample-session":
            shutil.copytree(session, WS / "sessions" / session.name)
    if (REAL / "participants").is_dir():
        shutil.copytree(REAL / "participants", WS / "participants")
    print(f"  seeded {len(list((WS / 'sessions').iterdir()))} sessions into {WS}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    # Set here rather than left to the caller: forget it on the command line and
    # the app quietly points at the researcher's own workspace, which is both a
    # thing to write into by accident and the reason a restored audit record
    # stops matching -- coaching_audit_dir only looks beside a lap that is under
    # this workspace's sessions root.
    os.environ["APEX_WORKSPACE"] = str(WS)
    # The review window cuts its clip with ffmpeg, which ships inside the
    # packaged build and not in a source checkout. Without this the pane
    # honestly reports that it cannot cut a clip -- true of this checkout, and
    # false of the thing being presented.
    package = Path.home() / "repos" / "Apex"
    if (package / "ffmpeg" / "ffmpeg.exe").is_file():
        os.environ.setdefault("APEX_FFMPEG", str(package / "ffmpeg" / "ffmpeg.exe"))
        print(f"  using packaged ffmpeg: {package / 'ffmpeg'}")
    # Same reason: the Collect Data page checks for the simulator beside the
    # executable, which in a source checkout is the venv's python. Left alone it
    # reports "simulator component is not available" -- true here, false of the
    # build being presented, and a red line in a deck is read as a fault.
    if (package / "torcs-runtime").is_dir():
        os.environ.setdefault("TORCS_PREFIX", str(package / "torcs-runtime"))
        print(f"  using packaged TORCS: {package / 'torcs-runtime'}")
    weights = Path.home() / "Apex" / "models" / "granite-4.1-3b-Q4_K_M.gguf"
    if weights.is_file():
        os.environ.setdefault("GRANITE_MODEL_PATH", str(weights))
        print(f"  using the real weights: {weights.name}")
    llama = package / "granite-runtime" / "llama-server.exe"
    if llama.is_file():
        os.environ.setdefault("LLAMA_SERVER_BIN", str(llama))
        print(f"  using packaged llama-server: {llama}")
    seed_workspace()

    from PySide6.QtWidgets import QSplitter

    from apex.app import create_app
    from apex.main_window import MainWindow
    from f1coach_core import latest_coaching_report, load_session

    from PySide6.QtCore import Qt

    app = create_app([sys.argv[0]])
    window = MainWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    window.resize(W, H)
    window.show()
    saved = []

    def settle(n: int = 10) -> None:
        for _ in range(n):
            app.processEvents()

    def shot(widget, name: str, crop_h: int | None = None, crop_w: int | None = None) -> None:
        settle()
        pixmap = widget.grab()
        if crop_h is not None or crop_w is not None:
            pixmap = pixmap.copy(0, 0, crop_w or pixmap.width(), crop_h or pixmap.height())
        pixmap.save(str(OUT / f"{name}.png"))
        saved.append(name)
        print(f"  wrote {name}  {pixmap.width()}x{pixmap.height()}")

    # --- 1. Collect Data: the participant-facing capture page ------------
    capture = window._capture
    window._stacked.setCurrentWidget(capture)
    capture._participant_id.setText("C0826")
    control = next(
        i for i in range(capture._phase.count())
        if capture._phase.itemData(i) == "control"
    )
    capture._phase.setCurrentIndex(control)
    for check in capture._readiness_checks:
        check.setChecked(True)
    capture._update_start_state()
    window.resize(W, 660)
    shot(window, "01-collect-control")

    # --- 2. Garage: whose laps these are travels with the files ----------
    window.resize(W, 600)
    window._stacked.setCurrentWidget(window._garage)
    window._garage.refresh_sessions(select="B0826-baseline-20260826-091209")
    settle(60)
    shot(window, "02-garage", crop_h=250)

    # --- 3. Lap Analysis with the report Granite really gave -------------
    window.resize(W, H)
    session = load_session(WS / "sessions" / "B0826-baseline-20260826-091209")
    lap = session.laps[1]
    window.show_analysis(lap, session)
    view = window._analysis
    splitter = view.findChild(QSplitter)
    if splitter is not None:
        splitter.setSizes([W - 500, 480])
    # A single-lap technique review, so the report on screen and the audit
    # record on the last slide are the same analysis of the same lap -- and one
    # written by the build that names the participant in it.
    view._ref_combo.setCurrentIndex(0)
    settle()
    reference = view._ref_combo.currentData()
    stored = latest_coaching_report(lap, reference, provider="granite")
    if stored is None and reference is not None:
        stored = latest_coaching_report(lap, None, provider="granite")
    if stored is not None:
        print(f"  restored real report: {stored.report.model} "
              f"{len(stored.report.findings)} findings from {stored.path.name}")
        view._panel.show_report(stored.report)
        view._panel.audit_path = stored.path
        if stored.report.findings:
            view._show_evidence(*stored.report.findings[0].evidence[0].span)
    else:
        print("  NO stored granite report matched this lap")
    shot(window, "03-analysis")
    return finish(window, view, stored, session, lap, shot, settle, saved)


def finish(window, view, stored, session, lap, shot, settle, saved) -> int:
    """The review window, the Study screen, and the audit record."""
    from PySide6.QtCore import Qt

    from apex.coach_panel import AuditDialog

    # --- 4. Review window: one corner, with the advice about it ----------
    if view._review_points:
        index = min(view._advice) if view._advice else 0
        view._review_corner(index)
        settle()
        replay = view._replay_window
        replay.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        replay.resize(1180, 620)
        # ffmpeg cuts the clip on a worker thread and the player then has to
        # decode a frame, so this needs real time passing, not just a flush of
        # the event queue.
        def pump(seconds: float) -> None:
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                settle(4)
                time.sleep(0.05)

        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline and replay._footage.busy:
            pump(0.25)
        pump(4.0)  # let the player paint its first frame
        print(f"  footage pane busy={replay._footage.busy}")
        # Cropped past the video pane. The clip is cut and loaded -- ffmpeg runs,
        # the player accepts the file -- but a video surface is composited by the
        # graphics stack rather than painted into the widget, so QWidget.grab()
        # returns black for it on a window that was never mapped. A black
        # rectangle in a deck reads as a broken feature, which is the opposite of
        # true; the deck says in words what the pane does.
        shot(replay, "04-review", crop_w=592)
        replay.close()

    # --- 5/6. Study Results: any two phases, on the real five ------------
    window.resize(1500, 820)
    study = window._study
    window._stacked.setCurrentWidget(study)
    study.reload()
    settle()
    for left, right, name in (
        ("baseline", "coached", "05-study-coached"),
        ("baseline", "control", "06-study-control"),
    ):
        if study._left_phase.findText(left) < 0 or study._right_phase.findText(right) < 0:
            print(f"  skipped {name}: phases not present")
            continue
        study._left_phase.setCurrentText(left)
        study._right_phase.setCurrentText(right)
        settle()
        # Select the participant who drove both, so the chart underneath shows
        # the pair being compared rather than whichever row came first.
        for row in range(study._table.rowCount()):
            if study._table.item(row, 1).text() == right:
                study._table.setCurrentCell(row, 0)
                break
        settle()
        shot(window, name)

    # --- 7. Audit record: what the model was asked and answered ----------
    if stored is not None:
        dialog = AuditDialog(stored.path, window)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        dialog.resize(940, 640)
        dialog.show()
        shot(dialog, "07-audit")
        dialog.close()

    print(f"\n{len(saved)} screenshots in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
