"""Narrating the debrief without letting the model decide what happened."""

import json

import pytest

from f1coach_core.debrief import DebriefPoint
from racecoach.granite import narrate
from racecoach.granite.client import GraniteError


def point(corner="Turn 3", lost=0.31, difference="braked 12 m earlier",
          detail="you 118 m, best 130 m", category="braking"):
    return DebriefPoint(
        corner=corner,
        apex_m=412.0,
        span_m=(380.0, 470.0),
        time_lost_s=lost,
        difference=difference,
        detail=detail,
        category=category,
    )


def reply(content: str, model: str = "granite") -> dict:
    return {"model": model, "choices": [{"message": {"content": content}}]}


def transport_returning(content, model="granite"):
    captured = {}

    def send(url, payload, headers, timeout_s):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return reply(content, model)

    return send, captured


def narrate_with(content, points, summary="0.9 s off your best lap."):
    send, captured = transport_returning(content)
    result = narrate.narrate_debrief(
        summary, points, base_url="http://127.0.0.1:8080/v1",
        model="granite", transport=send,
    )
    return result, captured


def test_prose_is_kept_when_every_number_in_it_was_measured():
    points = [point()]
    body = json.dumps({
        "summary": "You lost 0.31 s at Turn 3.",
        "stretches": ["You braked about 12 m earlier than your best lap. Try carrying "
                      "the brake a little later there."],
    })

    result, captured = narrate_with(body, points)

    assert result.spoken_count == 1
    assert "12 m earlier" in result.points[0].narration
    assert result.summary == "You lost 0.31 s at Turn 3."
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["payload"]["temperature"] == 0.0


def test_a_number_nobody_measured_costs_that_stretch_its_prose():
    """The measurement still stands; only the sentence that invented a figure goes."""
    points = [point()]
    body = json.dumps({
        "summary": "Close lap.",
        "stretches": ["You were 25 km/h slower through there."],
    })

    result, _ = narrate_with(body, points)

    assert result.spoken_count == 0
    assert result.points[0].point is points[0]
    assert result.points[0].narration == ""


def test_one_bad_stretch_does_not_silence_the_others():
    points = [point(corner="Turn 1"), point(corner="Turn 7", lost=0.22)]
    body = json.dumps({
        "summary": "Two places to look at.",
        "stretches": [
            "You gave away 0.31 s here by braking 12 m earlier.",
            "You lost 4.5 seconds of momentum.",  # 4.5 was never measured
        ],
    })

    result, _ = narrate_with(body, points)

    assert result.points[0].spoken
    assert not result.points[1].spoken
    assert result.spoken_count == 1


def test_an_invented_number_in_the_summary_leaves_the_measured_one():
    points = [point()]
    body = json.dumps({
        "summary": "You were 3.4 s off the pace overall.",
        "stretches": ["You braked 12 m earlier."],
    })

    result, _ = narrate_with(body, points, summary="0.9 s off your best lap.")

    assert result.summary == "0.9 s off your best lap."
    assert result.points[0].spoken


def test_a_summary_may_use_any_number_from_any_stretch():
    points = [point(corner="Turn 1"), point(corner="Turn 7", lost=0.22)]
    body = json.dumps({
        "summary": "Turn 1 cost 0.31 s and Turn 7 cost 0.22 s.",
        "stretches": ["a", "b"],
    })

    result, _ = narrate_with(body, points)
    assert result.summary.startswith("Turn 1 cost 0.31 s")


def test_json_wrapped_in_prose_or_a_code_fence_is_still_read():
    points = [point()]
    body = (
        "Sure! Here is the JSON:\n```json\n"
        + json.dumps({"summary": "ok", "stretches": ["You braked 12 m earlier."]})
        + "\n```\nHope that helps."
    )

    result, _ = narrate_with(body, points)
    assert result.points[0].spoken


def test_a_reply_that_is_not_json_leaves_the_measurements_untouched():
    points = [point()]
    result, _ = narrate_with("I could not do that.", points)

    assert result.spoken_count == 0
    assert result.summary == "0.9 s off your best lap."
    assert len(result.points) == 1


def test_a_stretch_list_of_the_wrong_length_is_refused_entirely():
    """Mismatched lengths mean the prose cannot be attributed to a stretch."""
    points = [point(corner="Turn 1"), point(corner="Turn 7")]
    body = json.dumps({"summary": "ok", "stretches": ["only one"]})

    result, _ = narrate_with(body, points)
    assert result.spoken_count == 0


def test_a_stretch_with_no_measured_difference_can_still_be_narrated():
    """Time was lost; the reason was not measured, and the prose must not supply one."""
    points = [point(difference="", detail="")]
    body = json.dumps({
        "summary": "ok",
        "stretches": ["You lost 0.31 s along this stretch."],
    })

    result, _ = narrate_with(body, points)
    assert result.points[0].spoken


def test_nothing_measured_means_nothing_asked_of_the_model():
    def refuse(*_a, **_k):  # pragma: no cover - proves no call is made
        raise AssertionError("called the model with no measurements to narrate")

    result = narrate.narrate_debrief(
        "This was your quickest lap of the session.", [],
        base_url="http://127.0.0.1:8080/v1", model="granite", transport=refuse,
    )
    assert result.points == () and result.spoken_count == 0


def test_a_server_that_cannot_be_reached_is_reported_as_such():
    def fail(*_a, **_k):
        raise GraniteError("connection refused")

    with pytest.raises(narrate.NarrationError, match="connection refused"):
        narrate.narrate_debrief(
            "s", [point()], base_url="http://127.0.0.1:8080/v1",
            model="granite", transport=fail,
        )


def test_the_prompt_carries_the_measurements_and_forbids_inventing_more():
    prompt = narrate.build_prompt("0.9 s off your best lap.", [point()])

    assert "Turn 3" in prompt and "0.31" in prompt
    assert "Do not introduce any other number" in prompt
    assert "Do not claim a cause the measurements do not show" in prompt
    # An unmeasured reason must be reported as unexplained, not guessed at.
    assert "leave the advice empty" in prompt
    # The advice is about a kind of driving, not about a stretch of graph.
    assert "where to start braking" in prompt


def test_an_api_key_is_sent_only_when_one_is_configured():
    send, captured = transport_returning(json.dumps({"summary": "", "stretches": [""]}))
    narrate.narrate_debrief(
        "s", [point()], base_url="http://x/v1", model="m",
        api_key="secret", transport=send,
    )
    assert captured["headers"]["Authorization"] == "Bearer secret"

    send, captured = transport_returning(json.dumps({"summary": "", "stretches": [""]}))
    narrate.narrate_debrief(
        "s", [point()], base_url="http://x/v1", model="m", transport=send
    )
    assert "Authorization" not in captured["headers"]


def test_advice_is_kept_apart_from_the_observation_it_follows():
    """A sound observation should not be lost because the advice beside it strayed."""
    points = [point()]
    body = json.dumps({
        "summary": "ok",
        "stretches": [{
            "observation": "You braked 12 m earlier than on your best lap.",
            "advice": "Carry 25 km/h more into the corner.",  # 25 was never measured
        }],
    })

    result, _ = narrate_with(body, points)

    assert "12 m earlier" in result.points[0].narration
    assert result.points[0].advice == ""


def test_a_stretch_nothing_explains_gets_no_instruction():
    """Nothing measured says what to change, so an instruction would be invented."""
    points = [point(difference="", detail="", category="")]
    body = json.dumps({
        "summary": "ok",
        "stretches": [{
            "observation": "You lost 0.31 s along this stretch.",
            "advice": "Brake later into the corner.",
        }],
    })

    result, _ = narrate_with(body, points)

    assert result.points[0].narration  # the loss is measured, so it is reported
    assert result.points[0].advice == ""  # the remedy is not


def test_both_halves_reach_the_reader_when_both_are_supported():
    points = [point()]
    body = json.dumps({
        "summary": "ok",
        "stretches": [{
            "observation": "You braked 12 m earlier than on your best lap.",
            "advice": "Try holding on to 130 m before you brake.",
        }],
    })

    result, _ = narrate_with(body, points)
    spoken = result.points[0]

    assert spoken.narration and spoken.advice
    assert spoken.full_text.startswith("You braked")
    assert "130 m" in spoken.full_text


def test_the_category_is_handed_over_so_advice_is_about_driving():
    """Without it the model can only talk about a stretch of graph."""
    prompt = narrate.build_prompt("s", [point(category="throttle")])
    assert "when to get back on the throttle" in prompt
