"""Automatic, serial, exact-context coaching for Garage sessions."""

import json

import pytest

from apex.coaching_queue import CoachingProgress, CoachingStage, GarageCoachingQueue
from f1coach_core import ensure_sample_session, load_session
from f1coach_core.coach import opportunity_catalog
from f1coach_core.llm import report_from_llm_text


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_WORKSPACE", str(tmp_path / "ws"))


class ValidProvider:
    name = "granite"

    def generate(self, summary, on_progress=None):
        grounded = next(iter(opportunity_catalog(summary).values()))
        evidence = {
            key: grounded[key]
            for key in ("metric", "corner", "value", "ref", "unit", "span_m")
        }
        raw = json.dumps(
            {
                "findings": [
                    {
                        "focus": grounded["focus"],
                        "issue": f"{grounded['corner']} has a technique opportunity.",
                        "cause": "The cited trace differs from its comparison guide.",
                        "action": "Make this input more progressive on the next lap.",
                        "confidence": 0.8,
                        "evidence": [evidence],
                    }
                ]
            }
        )
        if on_progress is not None:
            on_progress(raw)
        return report_from_llm_text(raw, "granite-test", summary)


def test_every_lap_is_queued_and_reports_live_progress(qtbot):
    session = load_session(ensure_sample_session())
    queue = GarageCoachingQueue(
        provider_factory=ValidProvider,
    )
    states: list[CoachingProgress] = []
    queue.progress.connect(states.append)

    queue.queue_session(session)
    qtbot.waitUntil(
        lambda: sum(item.stage is CoachingStage.READY for item in states)
        == len(session.laps),
        timeout=5000,
    )

    by_lap = {
        lap.source: [item.stage for item in states if item.lap_source == lap.source]
        for lap in session.laps
    }
    assert all(stages[0] is CoachingStage.QUEUED for stages in by_lap.values())
    assert all(CoachingStage.GENERATING in stages for stages in by_lap.values())
    assert all(stages[-1] is CoachingStage.READY for stages in by_lap.values())

    records = [
        json.loads(path.read_text("utf-8"))
        for path in sorted((session.path / "coaching").glob("*.json"))
    ]
    assert len(records) == len(session.laps)
    best = session.best_lap
    references = {record["lap"]: record["reference"] for record in records}
    # The quickest lap has nothing quicker to read against, so it is read
    # against the best each of its own corners was driven rather than being
    # dropped to a single-lap technique review with no corner speeds in it.
    composite = GarageCoachingQueue._composite(session)
    assert references[best.source.stem] == f"best corners of {len(composite.sources)} laps"
    assert all(
        references[lap.source.stem] == best.source.stem
        for lap in session.laps
        if lap is not best
    )


def test_a_fresh_queue_restores_exact_reports_without_calling_the_model(qtbot):
    session = load_session(ensure_sample_session())
    first = GarageCoachingQueue(
        provider_factory=ValidProvider,
    )
    completed: list[CoachingProgress] = []
    first.progress.connect(completed.append)
    first.queue_session(session)
    qtbot.waitUntil(
        lambda: sum(item.stage is CoachingStage.READY for item in completed)
        == len(session.laps),
        timeout=5000,
    )

    def should_not_run():
        raise AssertionError("an exact saved report should have been restored")

    restored = GarageCoachingQueue(
        provider_factory=should_not_run,
    )
    states: list[CoachingProgress] = []
    restored.progress.connect(states.append)
    restored.queue_session(load_session(session.path))
    qtbot.waitUntil(
        lambda: sum(item.stage is CoachingStage.READY for item in states)
        == len(session.laps),
        timeout=5000,
    )

    assert CoachingStage.GENERATING not in {item.stage for item in states}
    assert all(item.audit_path is not None for item in states if item.stage is CoachingStage.READY)


def test_missing_weights_are_reported_without_starting_or_downloading(qtbot):
    session = load_session(ensure_sample_session())

    def should_not_run():
        raise AssertionError("Garage must not silently prepare the model")

    queue = GarageCoachingQueue(
        provider_factory=should_not_run,
        availability=lambda: (
            CoachingStage.SETUP_NEEDED,
            "A one-off model download is required.",
        ),
    )
    states: list[CoachingProgress] = []
    queue.progress.connect(states.append)

    queue.queue_session(session)

    assert len(states) == len(session.laps)
    assert {item.stage for item in states} == {CoachingStage.SETUP_NEEDED}
    assert not (session.path / "coaching").exists()


def test_reloading_a_session_retries_failed_laps_but_not_ready_ones(qtbot):
    session = load_session(ensure_sample_session())

    class FailsFirst(ValidProvider):
        attempts = 0

        def generate(self, summary, on_progress=None):
            type(self).attempts += 1
            if type(self).attempts == 1:
                raise RuntimeError("temporary model failure")
            return super().generate(summary, on_progress=on_progress)

    queue = GarageCoachingQueue(provider_factory=FailsFirst)
    states: list[CoachingProgress] = []
    queue.progress.connect(states.append)
    queue.queue_session(session)
    qtbot.waitUntil(
        lambda: sum(
            item.stage in {CoachingStage.READY, CoachingStage.FAILED}
            for item in states
        )
        == len(session.laps),
        timeout=5000,
    )
    failed_source = next(
        item.lap_source for item in states if item.stage is CoachingStage.FAILED
    )
    qtbot.waitUntil(lambda: failed_source.resolve() not in queue._tasks, timeout=1000)

    queue.queue_session(session)
    qtbot.waitUntil(
        lambda: [
            item.stage for item in states if item.lap_source == failed_source
        ][-1]
        is CoachingStage.READY,
        timeout=5000,
    )

    failed_lap_states = [
        item.stage for item in states if item.lap_source == failed_source
    ]
    assert failed_lap_states.count(CoachingStage.QUEUED) == 2
    for lap in session.laps:
        if lap.source != failed_source:
            lap_states = [item.stage for item in states if item.lap_source == lap.source]
            assert lap_states.count(CoachingStage.QUEUED) == 1


def test_shutdown_prevents_queued_work_from_restarting_the_model(qtbot):
    session = load_session(ensure_sample_session())

    class Server:
        def start(self):
            raise AssertionError("a closing app must not restart its model server")

    queue = GarageCoachingQueue(server=Server())
    states: list[CoachingProgress] = []
    queue.progress.connect(states.append)

    queue.shutdown()
    queue.queue_session(session)

    assert states == []
    with pytest.raises(RuntimeError, match="closing"):
        queue._managed_provider()
