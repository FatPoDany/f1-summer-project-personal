"""Garage: the status column — SESSION BEST / ANALYSED · n / NEW."""

import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from apex.coaching_queue import CoachingProgress, CoachingStage
from apex.garage_view import GarageView, _findings
from f1coach_core import (
    build_coach_prompt,
    build_evidence_summary,
    ensure_sample_session,
    get_provider,
    latest_coaching_outcomes,
    write_coaching_audit,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


def statuses(view: GarageView) -> dict[str, str]:
    """Lap file stem -> status text.

    Keyed by the file rather than by the Lap column, which shows the race lap
    number a real capture recorded for itself: several files in a session can
    legitimately read "1", and the column was never a unique key.
    """
    table = view._table
    laps = view.session.laps if view.session is not None else ()
    return {
        (laps[row].source.stem if row < len(laps) else table.item(row, 0).text()): (
            table.item(row, 4).text()
        )
        for row in range(table.rowCount())
    }


def row_of(view: GarageView, lap) -> int:
    """The table row showing this lap, found by file rather than by cell text.

    Matched on the source path: a refresh rebuilds the Lap objects, so identity
    does not survive one, and the Lap column shows the race lap number rather
    than anything unique.
    """
    return next(
        row for row, one in enumerate(view.session.laps) if one.source == lap.source
    )


def test_status_column_speaks_the_mockup_vocabulary(qtbot):
    ensure_sample_session()
    view = GarageView()
    qtbot.addWidget(view)
    view.refresh_sessions()
    session = view.session
    assert session is not None
    best = session.best_lap
    coach_me = next(lap for lap in session.laps if lap is not best)

    summary = build_evidence_summary(coach_me, best)
    report = get_provider("mock").generate(summary, on_progress=lambda _t: None)
    write_coaching_audit(
        lap_source=coach_me.source,
        provider="mock",
        lap_name=coach_me.source.stem,
        reference_name=best.source.stem,
        evidence_summary=summary,
        prompt=build_coach_prompt(summary),
        raw_response="…",
        report=report,
    )
    fresh = next(lap for lap in session.laps if lap is not best and lap is not coach_me)
    view._fresh.add(fresh.source.stem)
    view.refresh_sessions()

    got = statuses(view)
    assert got[best.source.stem] == "SESSION BEST"
    assert got[coach_me.source.stem] == "ANALYSED"
    # The count lives in the tooltip, where it can name the comparison it is for.
    tip = view._table.item(row_of(view, coach_me), 4).toolTip()
    assert f"{best.source.stem} (session best) — {_findings(len(report.findings))}" in tip
    assert got[fresh.source.stem] == "NEW — just captured"

    # opening the fresh lap consumes its NEW tag
    row = row_of(view, fresh)
    opened = []
    view.lapOpened.connect(lambda lap, session: opened.append(lap))
    view._open_row(row)
    assert opened and statuses(view)[fresh.source.stem] == ""


def test_status_lists_every_comparison_rather_than_counting_one(qtbot):
    """A findings count belongs to a pair, so the column cannot carry one.

    This lap has been read against the session best and against another lap, and
    the two gave different answers. Publishing either number beside the lap made
    the Garage promise something Lap Analysis did not then show; the column now
    says only that answers exist, and names them on hover.
    """
    ensure_sample_session()
    view = GarageView()
    qtbot.addWidget(view)
    view.refresh_sessions()
    session = view.session
    best = session.best_lap
    lap = next(row for row in session.laps if row is not best)
    other = next(row for row in session.laps if row is not best and row is not lap)

    coaching = session.path / "coaching"
    coaching.mkdir(exist_ok=True)
    (coaching / "20260101-000000-mock.json").write_text(
        json.dumps(
            {"ok": True, "lap": lap.source.stem, "reference": best.source.stem,
             "report": {"findings": [1, 2]}}
        ),
        encoding="utf-8",
    )
    (coaching / "20260102-000000-mock.json").write_text(
        json.dumps(
            {"ok": True, "lap": lap.source.stem, "reference": other.source.stem,
             "report": {"findings": [1, 2, 3, 4]}}
        ),
        encoding="utf-8",
    )
    # …and the lap on its own, which is a third answer again.
    (coaching / "20260103-000000-mock.json").write_text(
        json.dumps({"ok": True, "lap": lap.source.stem, "report": {"findings": [1]}}),
        encoding="utf-8",
    )

    view.refresh_sessions()

    assert statuses(view)[lap.source.stem] == "ANALYSED"
    tip = view._table.item(row_of(view, lap), 4).toolTip()
    assert tip.splitlines() == [
        "Analysed against",
        f"  {best.source.stem} (session best) — 2 findings",  # what it opens into
        f"  {other.source.stem} — 4 findings",
        "  single lap — 1 finding",
    ]


def test_loading_a_session_requests_automatic_coaching(qtbot):
    ensure_sample_session()
    view = GarageView()
    qtbot.addWidget(view)

    with qtbot.waitSignal(view.coachingRequested) as requested:
        view.refresh_sessions()

    assert requested.args[0] is view.session


def test_selected_lap_has_a_visible_analysis_action(qtbot):
    """AI setup must not depend on discovering a hidden double-click gesture."""
    ensure_sample_session()
    view = GarageView()
    qtbot.addWidget(view)
    view.refresh_sessions()

    assert not view._open_button.isEnabled()
    view._table.selectRow(1)
    assert view._open_button.isEnabled()

    with qtbot.waitSignal(view.lapOpened) as opened:
        view._open_button.click()
    assert opened.args[0] is view.session.laps[1]


def test_garage_states_when_a_session_has_no_race_window_footage(qtbot):
    ensure_sample_session()
    view = GarageView()
    qtbot.addWidget(view)
    view.refresh_sessions()

    text = view._footage_status.text().lower()
    assert "no race-window footage" in text
    assert "ai advice" in text and "telemetry" in text


def test_garage_confirms_linked_footage_is_for_review_not_ai_input(qtbot):
    session_dir = ensure_sample_session()
    video = session_dir / "session.mp4"
    video.write_bytes(b"finished mp4")
    (session_dir / "recording.json").write_text(
        json.dumps({"path": str(video), "started_at": 1.0, "duration_s": 5.0}),
        encoding="utf-8",
    )
    view = GarageView()
    qtbot.addWidget(view)
    view.refresh_sessions()

    text = view._footage_status.text().lower()
    assert "footage available" in text
    assert "ai advice uses telemetry, not video" in text


def test_status_column_tracks_live_ai_work_and_keeps_session_best(qtbot):
    ensure_sample_session()
    view = GarageView()
    qtbot.addWidget(view)
    view.refresh_sessions()
    session = view.session
    assert session is not None
    best = session.best_lap
    other = next(lap for lap in session.laps if lap is not best)

    view.apply_coaching_progress(
        CoachingProgress(other.source, CoachingStage.QUEUED)
    )
    assert statuses(view)[other.source.stem] == "AI QUEUED"

    view.apply_coaching_progress(
        CoachingProgress(other.source, CoachingStage.GENERATING)
    )
    assert statuses(view)[other.source.stem] == "AI GENERATING…"

    view.apply_coaching_progress(
        CoachingProgress(best.source, CoachingStage.READY, findings=2)
    )
    assert statuses(view)[best.source.stem] == "SESSION BEST · ANALYSED"

    view.apply_coaching_progress(
        CoachingProgress(other.source, CoachingStage.FAILED, message="model timed out")
    )
    assert statuses(view)[other.source.stem] == "AI FAILED"
    row = row_of(view, other)
    assert "model timed out" in view._table.item(row, 4).toolTip()


@pytest.mark.parametrize(
    ("stage", "label"),
    [
        (CoachingStage.SETUP_NEEDED, "AI SETUP NEEDED"),
        (CoachingStage.UNAVAILABLE, "AI UNAVAILABLE"),
    ],
)
def test_status_explains_when_automatic_coaching_cannot_start(qtbot, stage, label):
    ensure_sample_session()
    view = GarageView()
    qtbot.addWidget(view)
    view.refresh_sessions()
    lap = view.session.laps[0]

    view.apply_coaching_progress(
        CoachingProgress(lap.source, stage, message="Install the local coach first.")
    )

    assert label in statuses(view)[lap.source.stem]


def test_latest_outcomes_newest_wins_and_failures_are_skipped(tmp_path):
    coaching = tmp_path / "coaching"
    coaching.mkdir()
    records = {
        "20260101-000000-mock.json": {"ok": True, "lap": "lap_01", "report": {"findings": [1, 2]}},
        "20260102-000000-mock.json": {"ok": True, "lap": "lap_01", "report": {"findings": []}},
        "20260103-000000-mock.json": {"ok": False, "lap": "lap_01", "report": None},
    }
    for name, record in records.items():
        (coaching / name).write_text(json.dumps(record))
    (coaching / "20260104-000000-mock.json").write_text("{not json")

    assert latest_coaching_outcomes(tmp_path) == {("lap_01", None): 0}
    assert latest_coaching_outcomes(tmp_path / "nowhere") == {}


def test_two_references_for_one_lap_are_two_separate_outcomes(tmp_path):
    """A newer answer about a different comparison must not replace this one."""
    coaching = tmp_path / "coaching"
    coaching.mkdir()
    (coaching / "20260101-000000-mock.json").write_text(
        json.dumps(
            {"ok": True, "lap": "lap_01", "reference": "lap_03",
             "report": {"findings": [1, 2]}}
        )
    )
    (coaching / "20260102-000000-mock.json").write_text(
        json.dumps(
            {"ok": True, "lap": "lap_01", "reference": "lap_04",
             "report": {"findings": [1, 2, 3]}}
        )
    )

    assert latest_coaching_outcomes(tmp_path) == {
        ("lap_01", "lap_03"): 2,
        ("lap_01", "lap_04"): 3,
    }


def test_importing_a_handover_keeps_who_drove_it(qtbot, tmp_path, monkeypatch):
    """A loose CSV carries the driving and nothing else.

    The package exists so the driver, the phase, the preset and the background
    travel with the laps. Importing only the CSV out of it would put a dash in
    the Driver column for every participant who ever sent one in, and lose the
    questionnaire that no later check can go back and ask for.
    """
    from f1coach_core.participant import (
        Background,
        background_path,
        load_background,
        save_background,
    )
    from racecoach.telemetry.handover import package
    from test_torcs import make_human_run

    capture = tmp_path / "P007-baseline-20260819-104237"
    capture.mkdir()
    make_human_run(capture / "human-1.csv", laps=2)
    (capture / "manifest.json").write_text(
        json.dumps(
            {
                "participant_id": "P007",
                "phase": "baseline",
                "study_preset": {"preset_id": "apex-study-v1"},
            }
        ),
        encoding="utf-8",
    )
    save_background(Background(participant_id="P007", racing_games="weekly"))
    archive = package(capture, tmp_path / "P007.zip").path
    background_path("P007").unlink()  # the analyst has never heard of them

    view = GarageView()
    qtbot.addWidget(view)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a, **k: ([str(archive)], ""))

    view._import_files()

    assert view.session is not None
    assert view.session.name == capture.name  # not the doubled unpack folder name
    drivers = {
        view._table.item(row, 0).text(): view._table.item(row, 1).text()
        for row in range(view._table.rowCount())
    }
    assert drivers
    assert set(drivers.values()) == {"P007"}

    restored = load_background("P007")
    assert restored is not None and restored.racing_games == "weekly"

    # …and all of it is on screen, not merely on disk: storing the questionnaire
    # where nobody can see it is the same as losing it.
    shown = view._participant.text()
    assert "P007" in shown and "baseline" in shown and "apex-study-v1" in shown
    assert "racing games weekly" in shown


def test_the_participant_line_names_who_drove_these_laps(qtbot):
    """The bundled sample is a real recorded session, so it names its driver."""
    ensure_sample_session()
    view = GarageView()
    qtbot.addWidget(view)
    view.refresh_sessions()

    shown = view._participant.text()
    assert "0822" in shown and "coached" in shown and "apex-study-v1" in shown
    # Nobody has answered a questionnaire for them on this machine.
    assert "background not recorded" in shown


def test_a_session_of_loose_csvs_says_it_carries_no_participant(qtbot, tmp_path):
    """The dash in the Driver column is a fact about the file, not a fault.

    Saying so where the reader is looking is the difference between "this import
    lost something" and "a bare CSV never had it".
    """
    from f1coach_core import import_telemetry
    from test_torcs import make_human_run

    run = make_human_run(tmp_path / "human-1.csv", laps=2)
    import_telemetry(run, "anonymous-run")  # no identity: a loose file has none
    view = GarageView()
    qtbot.addWidget(view)
    view.refresh_sessions(select="anonymous-run")

    assert "No participant recorded" in view._participant.text()
    drivers = {
        view._table.item(row, 1).text() for row in range(view._table.rowCount())
    }
    assert drivers == {"—"}


def test_a_damaged_handover_is_refused_rather_than_half_imported(qtbot, tmp_path, monkeypatch):
    """Half a participant's session is worse than none: it looks complete."""
    from racecoach.telemetry.handover import package
    from test_torcs import make_human_run

    capture = tmp_path / "P008-baseline-20260819-104237"
    capture.mkdir()
    make_human_run(capture / "human-1.csv", laps=2)
    (capture / "manifest.json").write_text(
        json.dumps({"participant_id": "P008"}), encoding="utf-8"
    )
    archive = package(capture, tmp_path / "P008.zip").path
    archive.write_bytes(archive.read_bytes()[:-200])  # truncated in transit

    view = GarageView()
    qtbot.addWidget(view)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a, **k: ([str(archive)], ""))
    shown = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kwargs: shown.append(args))

    view._import_files()

    assert shown, "a damaged handover must say so"
    assert view.session is None


def test_delete_session_requires_confirmation(qtbot, monkeypatch):
    target = ensure_sample_session()
    view = GarageView()
    qtbot.addWidget(view)
    view.refresh_sessions()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No),
    )

    view._delete_session()

    assert target.is_dir()
    assert view.session is not None


def test_confirmed_delete_refreshes_the_garage(qtbot, monkeypatch):
    target = ensure_sample_session()
    view = GarageView()
    qtbot.addWidget(view)
    view.refresh_sessions()
    statuses: list[str] = []
    view.status.connect(statuses.append)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )

    view._delete_session()

    assert not target.exists()
    assert view.session is None
    assert view._session_list.count() == 0
    assert statuses == ["Deleted session 'sample-session' from the Apex workspace"]


def test_study_laps_show_their_lap_number_and_driver(qtbot, tmp_path):
    """A researcher must be able to tell whose laps these are from the table."""
    import numpy as np
    import pandas as pd

    from f1coach_core import StudyIdentity, import_telemetry

    track, n_per_lap = 2050.0, 60
    dist = np.concatenate(
        [np.linspace(track - 40, track - 1, 25)]
        + [np.linspace(0, track, n_per_lap, endpoint=False)] * 2
    )
    n = dist.size
    run = tmp_path / "human-1.csv"
    pd.DataFrame(
        {
            "sim_time_s": 100.0 + np.arange(n) * 0.02,
            "dist_from_start_m": dist,
            "total_speed_mps": np.full(n, 45.0),
            "accel_cmd": np.full(n, 0.6),
            "brake_cmd": np.zeros(n),
            "steer_cmd": np.zeros(n),
            "gear": np.full(n, 4),
            "race_lap": np.concatenate(
                [np.full(25, 1)] + [np.full(n_per_lap, i + 1) for i in range(2)]
            ),
            "car_name": "Human, Driver",
        }
    ).to_csv(run, index=False)
    import_telemetry(run, "P001-baseline", StudyIdentity(driver="P001", phase="baseline"))

    view = GarageView()
    qtbot.addWidget(view)
    view.refresh_sessions(select="P001-baseline")

    table = view._table
    assert [table.item(r, 0).text() for r in range(table.rowCount())] == ["1", "2"]
    assert {table.item(r, 1).text() for r in range(table.rowCount())} == {"P001"}


def test_session_actions_live_on_the_session_they_act_on(qtbot, tmp_path, monkeypatch):
    """New and delete are things you do to a session, not permanent buttons."""
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    view = GarageView()
    qtbot.addWidget(view)

    assert not hasattr(view, "_delete_button")
    policy = view._session_list.contextMenuPolicy()
    assert policy == Qt.ContextMenuPolicy.CustomContextMenu
    assert view._table.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu


def test_watching_a_folder_is_gone_now_that_capture_registers_its_own_laps(
    qtbot, tmp_path, monkeypatch
):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    view = GarageView()
    qtbot.addWidget(view)

    assert not hasattr(view, "_watch_button")
    assert not hasattr(view, "_watcher")


def test_a_lap_exports_as_the_recorded_file_not_a_re_rendering(
    qtbot, tmp_path, monkeypatch
):
    """What reaches the research team has to be the recording, header and all."""
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    ensure_sample_session()
    view = GarageView()
    qtbot.addWidget(view)
    view.refresh_sessions()
    assert view._session is not None and view._session.laps

    target = tmp_path / "exported.csv"
    monkeypatch.setattr(
        "apex.garage_view.QFileDialog.getSaveFileName",
        lambda *a, **k: (str(target), ""),
    )
    view._export_row(0)

    source = view._session.laps[0].source
    assert target.read_bytes() == source.read_bytes()


def test_importing_a_handover_in_the_app_keeps_what_they_read(qtbot, tmp_path, monkeypatch):
    """The GUI and `racecoach collect` are two doors into the same study.

    A researcher who imports a package through the Garage rather than the
    command line must not end up with a dose column that exports blank -- the
    two paths were already capable of losing the questionnaire that way, and a
    viewing log left where it unpacked is lost the same way.
    """
    from f1coach_core.exposure import (
        CORNER_VIEW,
        ReviewView,
        append_view,
        exposure_path,
        load_exposure,
    )
    from racecoach.telemetry.handover import package
    from test_torcs import make_human_run

    capture = tmp_path / "P007-baseline-20260819-104237"
    capture.mkdir()
    make_human_run(capture / "human-1.csv", laps=2)
    (capture / "manifest.json").write_text(
        json.dumps({"participant_id": "P007", "phase": "baseline"}), encoding="utf-8"
    )
    append_view(
        ReviewView(driver="P007", phase="baseline", kind=CORNER_VIEW, seconds=44.0,
                   corner="T3", advice=True, id="view-1")
    )
    archive = package(capture, tmp_path / "P007.zip").path
    exposure_path("P007").unlink()  # the analyst has never heard of them

    view = GarageView()
    qtbot.addWidget(view)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a, **k: ([str(archive)], ""))

    view._import_files()

    assert [(v.corner, v.seconds) for v in load_exposure()["P007"]] == [("T3", 44.0)]
