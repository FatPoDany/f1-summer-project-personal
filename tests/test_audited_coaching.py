"""One trusted coaching attempt, shared by interactive and queued callers."""

import json
from pathlib import Path

import pytest

from f1coach_core import load_sample_session, run_audited_coaching
from f1coach_core.audit import PRIOR_ADVICE_LIMIT, prior_advice
from f1coach_core.coach import MockCoach


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


def test_provider_failure_is_returned_and_audited_with_partial_response():
    lap = load_sample_session().laps[1]

    class BrokenProvider:
        name = "broken"

        def generate(self, _summary, on_progress=None):
            on_progress('{"findings": [')
            raise RuntimeError("model stopped")

    attempt = run_audited_coaching(
        lap,
        None,
        provider_name="broken",
        provider_factory=BrokenProvider,
    )

    assert attempt.report is None
    assert attempt.error == "model stopped"
    assert attempt.audit_path is not None and attempt.audit_path.is_file()
    record = json.loads(attempt.audit_path.read_text("utf-8"))
    assert record["ok"] is False
    assert record["error"] == "model stopped"
    assert record["raw_response"] == '{"findings": ['
    assert record["evidence_summary"]["lap"]["name"] == lap.source.stem
    assert record["prompt"]


def test_the_audit_says_whose_run_it_coached_not_just_which_file():
    """The trail is also the only record of what a participant was told.

    Lap files are renamed on import, pooled across machines, and read months
    later by somebody who was not there; a name cannot carry the participant.
    """
    from dataclasses import replace

    from f1coach_core import MockCoach
    from f1coach_core.lap import StudyIdentity

    lap = replace(
        load_sample_session().laps[1],
        identity=StudyIdentity(driver="P007", phase="baseline", setup="apex-study-v1"),
    )

    attempt = run_audited_coaching(
        lap, None, provider_name="mock", provider_factory=MockCoach
    )

    record = json.loads(attempt.audit_path.read_text("utf-8"))
    assert (record["driver"], record["phase"]) == ("P007", "baseline")
    assert record["setup"] == "apex-study-v1"


def test_a_lap_from_outside_the_study_is_audited_without_inventing_a_participant():
    from dataclasses import replace

    from f1coach_core import MockCoach
    from f1coach_core.lap import NO_IDENTITY

    lap = replace(load_sample_session().laps[1], identity=NO_IDENTITY)
    attempt = run_audited_coaching(
        lap, None, provider_name="mock", provider_factory=MockCoach
    )

    record = json.loads(attempt.audit_path.read_text("utf-8"))
    assert record["driver"] is None and record["phase"] is None


def test_a_later_lap_is_told_what_this_driver_has_already_heard():
    """A session is coached lap by lap and read the same way.

    Without this the same instruction arrives on every screen, because the
    same corner really is the biggest difference every time.
    """
    session = load_sample_session()
    heard: list[tuple[str, ...]] = []

    class Recording(MockCoach):
        def generate(self, summary, on_progress=None):
            heard.append(self.prior_advice)
            return super().generate(summary, on_progress)

    for lap in session.laps[:3]:
        run_audited_coaching(
            lap,
            session.best_lap,
            provider_name="mock",
            provider_factory=Recording,
        )

    assert heard[0] == (), "the first lap of a run has nothing behind it"
    assert heard[1], "the second lap should hear what the first was told"
    assert set(heard[1]) <= set(heard[2])


def test_a_lap_is_never_told_what_it_was_itself_told():
    """Otherwise a regenerated report would be asked not to repeat itself."""
    session = load_sample_session()
    lap = session.laps[0]
    for _ in range(2):
        run_audited_coaching(
            lap,
            session.best_lap,
            provider_name="mock",
            provider_factory=MockCoach,
        )

    assert prior_advice(lap) == ()


def test_what_the_driver_was_told_is_capped_for_a_small_model_s_context():
    session = load_sample_session()
    for lap in session.laps:
        run_audited_coaching(
            lap,
            session.best_lap,
            provider_name="mock",
            provider_factory=MockCoach,
        )

    assert len(prior_advice(session.laps[-1])) <= PRIOR_ADVICE_LIMIT


def test_two_records_written_in_the_same_second_come_back_newest_first():
    """"-2.json" sorts before ".json", so a plain sort returns the older one.

    `_unique_dest` resolves a collision by appending "-2", and "-" is 0x2D
    against "."'s 0x2E. Every "newest wins" scan in this module was built on a
    plain sort, which is the one thing those scans exist to get right. Records
    written now carry microseconds and barely collide; the ones already in the
    workspace do -- 20260828-105513-mock.json sits beside its "-2".
    """
    from f1coach_core.audit import audit_order

    names = [
        "20260828-105513-mock.json",
        "20260828-105513-mock-2.json",
        "20260828-105513-mock-10.json",
        "20260902-152541-000001-granite.json",
        "20260902-152541-662822-granite.json",
    ]

    ordered = [Path(n).stem for n in sorted(names, key=audit_order)]

    assert ordered == [
        "20260828-105513-mock",
        "20260828-105513-mock-2",
        "20260828-105513-mock-10",  # 10 after 2, not between 1 and 2
        "20260902-152541-000001-granite",
        "20260902-152541-662822-granite",
    ]


def test_a_filename_that_is_not_ours_sorts_oldest_rather_than_raising():
    """These folders are read to decide what to show somebody mid-session."""
    from f1coach_core.audit import audit_order

    names = ["20260828-105513-mock.json", "notes.json", "README.json"]

    assert [Path(n).stem for n in sorted(names, key=audit_order)][-1] == (
        "20260828-105513-mock"
    )
