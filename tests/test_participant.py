"""Prior experience: recorded once, coarse on purpose, never invented."""

import pytest

from f1coach_core.participant import (
    DECLINED,
    Background,
    background_columns,
    load_background,
    save_background,
)


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


def test_a_participant_never_asked_has_no_answers_rather_than_default_ones():
    assert load_background("A001") is None


def test_answers_survive_a_round_trip():
    save_background(Background(participant_id="A001", racing_games="weekly", driving="no licence"))
    loaded = load_background("A001")

    assert loaded.racing_games == "weekly"
    assert loaded.driving == "no licence"
    assert loaded.sim_racing == DECLINED  # not asked is not the same as answered


def test_a_declined_answer_is_distinct_from_every_answer_that_exists():
    """A skipped question must not become a middle value nobody gave."""
    background = Background(participant_id="A001")

    assert not background.is_answered
    assert background.rank("racing_games") is None
    assert background_columns(background)["racing_games_rank"] == ""


def test_answers_carry_an_order_a_comparison_can_use():
    """Groups are compared by ranking, so the order is part of the contract."""
    low = Background(participant_id="A", racing_games="never")
    high = Background(participant_id="B", racing_games="most days")

    assert low.rank("racing_games") < high.rank("racing_games")


def test_the_export_carries_both_the_label_and_the_rank():
    """The label is what a reader understands; the rank is what a test operates on."""
    columns = background_columns(Background(participant_id="A001", sim_racing="casual"))

    assert columns["sim_racing"] == "casual"
    assert columns["sim_racing_rank"] == 1


def test_a_participant_with_no_record_still_yields_every_column():
    """Otherwise the CSV would gain ragged rows the moment somebody skipped it."""
    columns = background_columns(None)

    assert set(columns) == set(background_columns(Background(participant_id="A001")))
    assert all(value == "" for value in columns.values())


def test_an_unreadable_file_reads_as_never_asked(tmp_path):
    from f1coach_core.participant import background_path

    path = background_path("A001")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this is not json", encoding="utf-8")

    assert load_background("A001") is None
