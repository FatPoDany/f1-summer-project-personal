"""The screen the evaluation is argued from."""

import numpy as np
import pytest

from apex.study_view import StudyView


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


def test_a_participant_who_drove_both_phases_is_paired(qtbot, paired):
    view = StudyView()
    qtbot.addWidget(view)
    view.reload(paired)

    assert view._table.rowCount() == 2  # one row per participant per phase
    assert [view._table.item(r, 1).text() for r in range(2)] == ["baseline", "coached"]
    assert "1 of 1 participant(s) have both phases" in view._headline.text()
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
    view = StudyView()
    qtbot.addWidget(view)
    view.reload(paired)
    assert view._export.isEnabled()

    target = tmp_path / "summary.csv"
    monkeypatch.setattr(
        "apex.study_view.QFileDialog.getSaveFileName", lambda *a, **k: (str(target), "")
    )
    view._export_csv()

    lines = target.read_text("utf-8").strip().split("\n")
    assert lines[0].startswith("driver,phase,laps,best_lap_s")
    assert len(lines) == 3
    assert lines[1].startswith("A001,baseline")
    assert lines[2].startswith("A001,coached")


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
