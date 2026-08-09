"""Garage: the mockup status column — SESSION BEST / COACHED · n / NEW."""

import json

import pytest

from apex.garage_view import GarageView
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
    table = view._table
    return {
        table.item(row, 0).text(): table.item(row, 3).text() for row in range(table.rowCount())
    }


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
    assert got[coach_me.source.stem] == f"COACHED · {len(report.findings)} findings"
    assert got[fresh.source.stem] == "NEW — just captured"

    # opening the fresh lap consumes its NEW tag
    row = next(
        r for r in range(view._table.rowCount())
        if view._table.item(r, 0).text() == fresh.source.stem
    )
    opened = []
    view.lapOpened.connect(lambda lap, session: opened.append(lap))
    view._open_row(row)
    assert opened and statuses(view)[fresh.source.stem] == ""


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

    assert latest_coaching_outcomes(tmp_path) == {"lap_01": 0}
    assert latest_coaching_outcomes(tmp_path / "nowhere") == {}
