"""The screen the evaluation is argued from."""

import numpy as np
import pytest

from apex.study_view import StudyView
from f1coach_core.participant import Background, save_background
from f1coach_core.study import summary_columns


def write_lap(directory, lap_number, *, driver, phase, seconds, off_track=0):
    directory.mkdir(parents=True, exist_ok=True)
    n = 120
    dist = np.linspace(0.0, 900.0, n)
    t = np.linspace(0.0, seconds, n)
    pos = np.zeros(n)
    for index in range(off_track):
        start = 10 + index * 25
        pos[start : start + 10] = 1.5
    rows = [
        f"{ti:.4f},{d:.1f},40.0,1.0,0.0,0.0,4,1,{p:.3f}"
        for ti, d, p in zip(t, dist, pos, strict=True)
    ]
    (directory / f"telemetry-lap{lap_number:02d}.csv").write_text(
        "# schema_version: 1\n"
        f"# lap: {lap_number}\n"
        f"# driver: {driver}\n"
        f"# phase: {phase}\n"
        "t,dist,speed,throttle,brake,steer,gear,sector,track_pos\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def paired(tmp_path):
    """One participant who drove both phases and got quicker."""
    write_lap(tmp_path / "A001-baseline", 1, driver="A001", phase="baseline",
              seconds=20.0, off_track=3)
    write_lap(tmp_path / "A001-baseline", 2, driver="A001", phase="baseline",
              seconds=19.0, off_track=2)
    write_lap(tmp_path / "A001-coached", 1, driver="A001", phase="coached",
              seconds=18.0, off_track=1)
    write_lap(tmp_path / "A001-coached", 2, driver="A001", phase="coached",
              seconds=17.0, off_track=0)
    return [tmp_path / "A001-baseline", tmp_path / "A001-coached"]


@pytest.fixture
def arms(tmp_path):
    """Two participants in two arms: one coached, one left to practise alone.

    Nobody drove all three phases, which is the point -- an arm is an assignment,
    not a missing session.
    """
    write_lap(tmp_path / "A001-baseline", 1, driver="A001", phase="baseline", seconds=20.0)
    write_lap(tmp_path / "A001-coached", 1, driver="A001", phase="coached", seconds=17.0)
    write_lap(tmp_path / "B002-baseline", 1, driver="B002", phase="baseline", seconds=21.0)
    write_lap(tmp_path / "B002-control", 1, driver="B002", phase="control", seconds=20.5)
    return sorted(tmp_path.glob("*-*"))


def test_a_participant_who_drove_both_phases_is_paired(qtbot, paired):
    view = StudyView()
    qtbot.addWidget(view)
    view.reload(paired)

    assert view._table.rowCount() == 2  # one row per participant per phase
    assert [view._table.item(r, 1).text() for r in range(2)] == ["baseline", "coached"]
    assert "1 of 1 participant(s) drove both 'baseline' and 'coached'" in view._headline.text()
    assert "-2.00 s" in view._headline.text()  # 19.0 best -> 17.0 best


def test_only_one_phase_says_what_is_missing_rather_than_comparing_anyway(qtbot, paired):
    view = StudyView()
    qtbot.addWidget(view)
    view.reload([paired[0]])

    assert view._table.rowCount() == 1
    assert "needs the same participant in both" in view._headline.text()


def test_an_empty_workspace_explains_where_the_rows_come_from(qtbot, tmp_path):
    view = StudyView()
    qtbot.addWidget(view)
    view.reload([tmp_path])

    assert view._table.rowCount() == 0
    assert "Collect Data records both" in view._headline.text()
    assert not view._export.isEnabled()


def test_a_channel_the_recording_lacks_shows_as_missing_not_as_zero(qtbot, tmp_path):
    """A measured zero and an unrecorded channel are different claims."""
    directory = tmp_path / "B002-baseline"
    directory.mkdir()
    (directory / "telemetry-lap01.csv").write_text(
        "# schema_version: 1\n# lap: 1\n# driver: B002\n# phase: baseline\n"
        "t,dist,speed,throttle,brake,steer,gear,sector\n"
        + "\n".join(f"{i * 0.1:.1f},{i * 5.0:.1f},40,1,0,0,4,1" for i in range(120))
        + "\n",
        encoding="utf-8",
    )
    view = StudyView()
    qtbot.addWidget(view)
    view.reload([directory])

    incidents = view._table.item(0, 8)
    assert incidents.text() == "—"
    assert "Not recorded" in incidents.toolTip()


def test_the_export_is_the_file_a_statistical_test_consumes(qtbot, paired, tmp_path, monkeypatch):
    """Every column, including the ones that say whether the groups were comparable.

    Asserting only the first few fields let the background columns export empty
    for as long as they did: the screen showed a background the file did not
    carry, and the export exists precisely so that an outcome test, a
    comparability check and a dose-response check read one file rather than a
    join of three.
    """
    from f1coach_core.exposure import CORNER_VIEW, ReviewView, append_view

    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    save_background(Background(participant_id="A001", racing_games="weekly", age_band="25-34"))
    append_view(
        ReviewView(driver="A001", phase="baseline", kind=CORNER_VIEW, seconds=52.0,
                   corner="T3", advice=True, id="view-1")
    )
    view = StudyView()
    qtbot.addWidget(view)
    view.reload(paired)
    assert view._export.isEnabled()

    target = tmp_path / "summary.csv"
    monkeypatch.setattr(
        "apex.study_view.QFileDialog.getSaveFileName", lambda *a, **k: (str(target), "")
    )
    view._export_csv()

    lines = target.read_text("utf-8").strip().splitlines()
    assert lines[0] == ",".join(summary_columns())
    assert len(lines) == 3
    assert lines[1].startswith("A001,baseline")
    assert lines[2].startswith("A001,coached")
    for line in lines[1:]:
        assert line.endswith("weekly,3,,,,,25-34,1")
    cells = [dict(zip(lines[0].split(","), line.split(","), strict=True))
             for line in lines[1:]]
    assert (cells[0]["review_seconds"], cells[0]["advice_seconds"]) == ("52.0", "52.0")
    # The phase they went on to drive is the response, not a second dose.
    assert cells[1]["review_seconds"] == "0"


def test_the_view_reports_measurements_and_leaves_significance_to_the_analyst(
    qtbot, paired
):
    """A p-value computed quietly here would be worth less than one they can defend."""
    view = StudyView()
    qtbot.addWidget(view)
    view.reload(paired)

    text = view._headline.text().lower()
    assert "significant" not in text
    assert "p =" not in text and "p<" not in text
    assert "export the rows to test" in text


def test_reload_survives_being_wired_to_a_button(qtbot, paired, monkeypatch):
    """clicked emits `checked`, which arrived as `roots` and was then iterated."""
    monkeypatch.setattr(
        "apex.study_view.list_study_sessions", lambda: paired
    )
    view = StudyView()
    qtbot.addWidget(view)

    # Exactly what the Reload button does, argument and all.
    view._reload_button_clicked(False)

    assert view._table.rowCount() == 2


def test_the_control_arm_is_compared_against_its_own_baseline(qtbot, arms):
    """Hard-coding baseline against coached told the control arm its data was short.

    It was not short: those participants drove the second run with no advice,
    which is the comparison that separates coaching from having driven the track
    three more times. The screen could show the table but could not name it.
    """
    view = StudyView()
    qtbot.addWidget(view)
    view.reload(arms)

    assert view._table.rowCount() == 4
    assert view._selected_phases() == ("baseline", "coached")
    assert [driver for driver, _b, _a in view._paired()] == ["A001"]
    assert "1 of 2 participant(s) drove both 'baseline' and 'coached'" in view._headline.text()

    view._right_phase.setCurrentText("control")

    assert [driver for driver, _b, _a in view._paired()] == ["B002"]
    text = view._headline.text()
    assert "1 of 2 participant(s) drove both 'baseline' and 'control'" in text
    assert "-0.50 s" in text  # 21.0 -> 20.5, the practice-only arm's own change


def test_the_pickers_offer_every_condition_the_data_carries_in_protocol_order(qtbot, arms):
    view = StudyView()
    qtbot.addWidget(view)
    view.reload(arms)

    offered = [view._left_phase.itemText(i) for i in range(view._left_phase.count())]
    assert offered == ["baseline", "coached", "control"]
    assert view._left_phase.isEnabled() and view._right_phase.isEnabled()


def test_a_phase_is_never_left_compared_with_itself(qtbot, arms):
    """Every participant would pair with themselves and the difference be zero."""
    view = StudyView()
    qtbot.addWidget(view)
    view.reload(arms)

    view._left_phase.setCurrentText("coached")

    left, right = view._selected_phases()
    assert left == "coached"
    assert right != "coached"
    assert f"'{left}' and '{right}'" in view._headline.text()


def test_reloading_keeps_the_comparison_being_read(qtbot, arms):
    """A reload after one more session must not move the comparison under somebody."""
    view = StudyView()
    qtbot.addWidget(view)
    view.reload(arms)
    view._right_phase.setCurrentText("control")

    view.reload(arms)

    assert view._selected_phases() == ("baseline", "control")


def test_one_phase_on_its_own_leaves_the_pickers_alone(qtbot, paired):
    view = StudyView()
    qtbot.addWidget(view)
    view.reload([paired[0]])

    assert view._selected_phases() == ("baseline", "")
    assert not view._left_phase.isEnabled()
    assert view._paired() == []


def test_the_sample_that_ships_with_the_app_is_not_a_participant(qtbot, tmp_path, monkeypatch):
    """Every install would otherwise show the same phantom in the coached arm.

    The bundled sample is five real laps that still carry `driver: 0822` and
    `phase: coached`, so a screen that reads the workspace read it as a sixth
    person -- one nobody recruited, whose numbers no assignment explains.
    """
    from f1coach_core.workspace import ensure_sample_session, sessions_root

    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))
    ensure_sample_session()
    write_lap(sessions_root() / "A001-baseline", 1, driver="A001", phase="baseline",
              seconds=20.0)

    view = StudyView()
    qtbot.addWidget(view)
    view.reload()

    assert [summary.driver for summary in view._summaries] == ["A001"]
