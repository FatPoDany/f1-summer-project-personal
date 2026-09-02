"""Shared LLM plumbing: compact prompt, strict schema, extraction and parsing."""

import json
from collections import Counter
from copy import deepcopy

import pytest

from f1coach_core import CoachingSchemaError, build_evidence_summary, load_sample_session
from f1coach_core.coach import (
    coachable_corners,
    evidence_catalog,
    opportunity_catalog,
)
from f1coach_core.guidance import guidance_for
from f1coach_core.llm import (
    PROMPT_VERSION,
    build_coach_prompt,
    build_coach_response_format,
    extract_json_object,
    report_from_llm_text,
    report_over_rounds,
)
from sample_laps import slow_and_best, slow_lap

WRAPPED_JSON = json.dumps({"findings": []})
EVIDENCE_KEYS = ("metric", "corner", "value", "ref", "unit", "span_m")


@pytest.fixture(scope="module")
def summary():
    return build_evidence_summary(*slow_and_best())


def available_citation(summary: dict, focus: str = "braking") -> dict:
    citation = next(
        item for item in opportunity_catalog(summary).values() if item["focus"] == focus
    )
    return {key: citation[key] for key in EVIDENCE_KEYS}


def grounded_findings_json(summary: dict, focus: str = "braking") -> str:
    citation = available_citation(summary, focus)
    return json.dumps(
        {
            "findings": [
                {
                    "focus": focus,
                    "issue": f"The main opportunity is {focus}",
                    "cause": "The cited telemetry differs from the reference.",
                    "action": "Use a smoother, more repeatable technique through this zone.",
                    "confidence": 0.8,
                    "evidence": [citation],
                }
            ]
        }
    )


def test_prompt_carries_only_compact_coachable_evidence_and_the_contract(summary):
    packet = deepcopy(summary)
    packet["raw_samples"] = ["must-not-enter-the-model"]
    template = deepcopy(summary["corners"][0])
    packet["corners"] = []
    for index in range(8):
        corner = deepcopy(template)
        corner["corner"] = f"X{index + 1}"
        corner["time_lost_s"] = float(index + 1)
        packet["corners"].append(corner)
    # One corner that lost nothing worth saying, to prove the prompt still drops
    # what is not worth a model's time now that it no longer drops by rank.
    quick = deepcopy(template)
    quick["corner"] = "X9"
    quick["time_lost_s"] = 0.004
    packet["corners"].append(quick)

    prompt = build_coach_prompt(packet)

    assert summary["lap"]["name"] in prompt and summary["reference"]["name"] in prompt
    assert '"findings"' in prompt and "span_m" in prompt and '"focus"' in prompt
    assert "Never invent" in prompt
    assert "at most once across the entire response" in prompt
    assert "must-not-enter-the-model" not in prompt  # unrelated bulk is removed
    # Every corner that lost time reaches the model, not the worst six. Ranking
    # still decides the order they are read in, and now nothing but the
    # threshold decides whether they are read at all.
    for index in range(8):
        assert f'"X{index + 1}"' in prompt
    assert '"X9"' not in prompt  # below the threshold, so nothing to advise on


def test_response_format_is_strict_and_allows_only_available_citations(summary):
    response_format = build_coach_response_format(summary)
    assert response_format["type"] == "json_schema"
    envelope = response_format["json_schema"]
    assert envelope["name"] == "apex_lap_coaching" and envelope["strict"] is True

    schema = envelope["schema"]
    assert schema["additionalProperties"] is False
    findings = schema["properties"]["findings"]
    assert findings["minItems"] == 1
    assert findings["maxItems"] == 3
    finding = findings["items"]
    assert finding["additionalProperties"] is False
    assert finding["properties"]["focus"]["enum"] == [
        "braking",
        "cornering",
        "throttle",
    ]
    for field in ("issue", "cause", "action"):
        assert finding["properties"][field]["type"] == "string"
        assert "pattern" not in finding["properties"][field]

    choices = finding["properties"]["evidence"]["items"]["oneOf"]
    assert len(choices) == len(opportunity_catalog(summary))
    expected_item = next(iter(opportunity_catalog(summary).values()))
    expected = {key: expected_item[key] for key in EVIDENCE_KEYS}
    assert any(
        all(choice["properties"][key]["const"] == expected[key] for key in EVIDENCE_KEYS)
        for choice in choices
    )
    assert all(choice["additionalProperties"] is False for choice in choices)


def test_single_lap_prompt_and_schema_use_review_guides_not_a_reference():
    solo = build_evidence_summary(slow_lap())

    prompt = build_coach_prompt(solo)
    response_format = build_coach_response_format(solo)

    assert "single lap" in prompt.lower()
    assert "review threshold" in prompt.lower()
    assert '"reference": null' in prompt
    assert opportunity_catalog(solo)
    findings = response_format["json_schema"]["schema"]["properties"]["findings"]
    assert findings["minItems"] == 1


def test_empty_evidence_schema_forces_an_empty_findings_list():
    response_format = build_coach_response_format({"corners": []})
    findings = response_format["json_schema"]["schema"]["properties"]["findings"]
    assert findings["minItems"] == 0
    assert findings["maxItems"] == 0


@pytest.mark.parametrize(
    "wrapper",
    [
        "{payload}",
        "```json\n{payload}\n```",
        "Here is my analysis:\n{payload}\nHope this helps!",
    ],
)
def test_extract_json_survives_wrapping(wrapper):
    text = wrapper.format(payload=WRAPPED_JSON)
    assert extract_json_object(text) == json.loads(WRAPPED_JSON)


def test_extract_json_handles_braces_inside_strings():
    tricky = '{"findings": [], "note": "a {brace} in a string"}'
    assert extract_json_object(tricky)["note"] == "a {brace} in a string"


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("the driver should brake later", "no JSON object"),
        ('{"findings": [', "never closes"),
        ('{"findings": [}]}', "does not parse"),
    ],
)
def test_extract_json_failures_are_readable(text, fragment):
    with pytest.raises(CoachingSchemaError, match=fragment):
        extract_json_object(text)


def test_report_from_llm_text_stamps_model_prompt_and_checks_evidence(summary):
    text = grounded_findings_json(summary)
    report = report_from_llm_text(
        text,
        model="ibm-granite/granite-4.1-3b",
        evidence_summary=summary,
    )
    expected = available_citation(summary)
    assert report.model == "ibm-granite/granite-4.1-3b"
    assert report.prompt_version == PROMPT_VERSION
    assert report.findings[0].focus == "braking"
    assert report.findings[0].evidence[0].span == tuple(expected["span_m"])


@pytest.mark.parametrize("field", ["issue", "cause", "action"])
def test_report_from_llm_text_replaces_model_prose_with_grounded_guidance(summary, field):
    payload = json.loads(grounded_findings_json(summary))
    payload["findings"][0][field] = "Brake release differs by 42 metres."

    report = report_from_llm_text(
        json.dumps(payload),
        model="granite-test",
        evidence_summary=summary,
    )

    value = getattr(report.findings[0], field)
    assert value and not any(character.isdigit() for character in value)
    assert "42" not in value


def test_single_lap_repeated_claims_merge_into_one_finding():
    solo = build_evidence_summary(slow_lap())
    catalog = list(opportunity_catalog(solo).values())
    # Whichever deterministic check fired most on this lap. The merge under test
    # is about a model repeating itself, not about which metric it repeated, and
    # pinning one metric is how this test started depending on a generator that
    # obligingly produced four of everything.
    metric = Counter(item["metric"] for item in catalog).most_common(1)[0][0]
    repeated = [
        {key: item[key] for key in EVIDENCE_KEYS}
        for item in catalog
        if item["metric"] == metric
    ]
    focus = next(item["focus"] for item in catalog if item["metric"] == metric)
    assert len(repeated) >= 4

    findings = []
    for confidence, evidence in (
        (0.95, repeated[:2]),
        (0.90, repeated[:2]),
        (0.85, repeated[2:4]),
    ):
        findings.append(
            {
                "focus": focus,
                "issue": "Review the throttle transition.",
                "cause": "The cited coast distance crossed the review guide.",
                "action": "Use a smoother pedal transition.",
                "confidence": confidence,
                "evidence": evidence,
            }
        )

    report = report_from_llm_text(
        json.dumps({"findings": findings}),
        model="granite-test",
        evidence_summary=solo,
    )

    assert len(report.findings) == 1
    # The model offered three different numbers for how sure it was, and none
    # of them measured anything. They are dropped where the prose is grounded,
    # so no audit record can carry one back out as though something had.
    assert report.findings[0].confidence is None
    assert len(report.findings[0].evidence) == 4
    assert len(
        {(item.corner, item.metric) for item in report.findings[0].evidence}
    ) == 4


def test_duplicate_mislabelled_comparison_finding_is_grounded_and_merged():
    session = load_sample_session()
    # The pair whose braking actually differs in three separate corners.
    comparison = build_evidence_summary(session.laps[0], session.laps[4])
    brake_points = {
        corner: {key: item[key] for key in EVIDENCE_KEYS}
        for (corner, metric), item in opportunity_catalog(comparison).items()
        if metric == "brake_point"
    }
    assert {"T1", "T5", "T7"} <= brake_points.keys()

    def finding(focus, evidence, confidence):
        return {
            "focus": focus,
            "issue": "Review this corner.",
            "cause": "The cited telemetry supports the review.",
            "action": "Use a smooth and repeatable technique.",
            "confidence": confidence,
            "evidence": evidence,
        }

    raw = {
        "findings": [
            finding("braking", [brake_points["T5"]], 0.9),
            finding("throttle", [brake_points["T1"], brake_points["T1"]], 0.8),
            finding("braking", [brake_points["T7"]], 0.7),
        ]
    }

    report = report_from_llm_text(
        json.dumps(raw),
        model="granite-test",
        evidence_summary=comparison,
    )

    assert len(report.findings) == 1
    assert report.findings[0].focus == "braking"
    assert [item.corner for item in report.findings[0].evidence] == ["T5", "T1", "T7"]


def test_fabricated_sibling_does_not_suppress_grounded_findings():
    session = load_sample_session()
    comparison = build_evidence_summary(session.laps[0], session.laps[4])
    brake_points = [
        {key: item[key] for key in EVIDENCE_KEYS}
        for (_corner, metric), item in opportunity_catalog(comparison).items()
        if metric == "brake_point"
    ]
    fabricated = deepcopy(brake_points[1])
    fabricated["value"] += 1.0

    def finding(evidence):
        return {
            "focus": "braking",
            "issue": "Review this corner.",
            "cause": "The cited telemetry supports the review.",
            "action": "Use a smooth and repeatable technique.",
            "confidence": 0.8,
            "evidence": [evidence],
        }

    report = report_from_llm_text(
        json.dumps(
            {
                "findings": [
                    finding(brake_points[0]),
                    finding(fabricated),
                    finding(brake_points[2]),
                ]
            }
        ),
        model="granite-test",
        evidence_summary=comparison,
    )

    published = [item for result in report.findings for item in result.evidence]
    assert [(item.corner, item.value) for item in published] == [
        (brake_points[0]["corner"], brake_points[0]["value"]),
        (brake_points[2]["corner"], brake_points[2]["value"]),
    ]


def test_opportunity_catalog_keeps_only_directionally_actionable_metrics(summary):
    catalog = opportunity_catalog(summary)

    assert catalog
    ambiguous = {"peak_brake", "brake_release", "min_speed_point"}
    assert all(metric not in ambiguous for _, metric in catalog)
    assert any(metric == "min_speed" for _, metric in catalog)
    assert all(
        item["value"] >= item["ref"] + 15.0
        for (_, metric), item in catalog.items()
        if metric == "throttle_reapply"
    )


def test_llm_parser_rejects_real_but_non_actionable_citation(summary):
    actionable = opportunity_catalog(summary)
    non_actionable = next(
        item for citation, item in evidence_catalog(summary).items() if citation not in actionable
    )
    citation = {key: non_actionable[key] for key in EVIDENCE_KEYS}
    payload = {
        "findings": [
            {
                "focus": non_actionable["focus"],
                "issue": "Telemetry shows an opportunity.",
                "cause": "The cited telemetry differs from the reference.",
                "action": "Use a smooth and repeatable technique.",
                "confidence": 0.8,
                "evidence": [citation],
            }
        ]
    }

    with pytest.raises(CoachingSchemaError, match="not available"):
        report_from_llm_text(
            json.dumps(payload),
            model="granite-test",
            evidence_summary=summary,
        )


def test_report_from_llm_text_requires_findings_key(summary):
    with pytest.raises(CoachingSchemaError, match="'findings'"):
        report_from_llm_text(
            '{"analysis": "great lap"}',
            model="m",
            evidence_summary=summary,
        )


def test_the_schema_never_asks_the_model_how_sure_it_is():
    """A model asked will answer, and the answer measures nothing.

    Removing it downstream is not enough on its own: while the schema demanded
    it, a server enforcing that schema forced one into every response, and the
    only thing standing between it and an audit record was a later line of
    code remembering to drop it.
    """
    schema = json.dumps(build_coach_response_format(build_evidence_summary(slow_lap())))

    assert "confidence" not in schema


def _one_brake_point_finding(comparison, prose):
    """One schema-valid finding citing a real braking difference."""
    citation = next(
        {key: item[key] for key in EVIDENCE_KEYS}
        for (_corner, metric), item in opportunity_catalog(comparison).items()
        if metric == "brake_point"
    )
    return citation, {
        "findings": [
            {
                "focus": "braking",
                "issue": prose,
                "cause": "You are on the brakes before the reference is.",
                "action": "Carry the same entry a little further before braking.",
                "evidence": [citation],
            }
        ]
    }


def test_the_model_s_own_wording_never_reaches_a_card():
    """The model chooses the corner; the template says the words.

    Letting its prose through when it cited its own numbers was implemented
    and measured against the real weights, and it is deliberately not what
    happens: across five laps the model's words reached a card zero times out
    of eight, and the one finding that would have gone up verbatim described
    braking earlier than the reference as braking later and advised braking
    earlier still. Checking that a number is cited is not checking that the
    sentence around it is true, so the sentence is never the model's -- while
    the finding itself, and the difference it rests on, still reach the screen.
    """
    session = load_sample_session()
    comparison = build_evidence_summary(session.laps[0], session.laps[4])
    citation, raw = _one_brake_point_finding(comparison, "PLACEHOLDER")
    # A number the finding really does cite, so nothing but the decision above
    # keeps this sentence off the card: a check on where numbers come from
    # would pass it.
    gap = round(abs(citation["value"] - citation["ref"]))
    assert gap, "this fixture needs a corner where the two numbers differ"
    raw["findings"][0]["issue"] = f"You brake about {gap} metres early here."

    report = report_from_llm_text(
        json.dumps(raw), model="granite-test", evidence_summary=comparison
    )

    assert len(report.findings) == 1
    assert [item.corner for item in report.findings[0].evidence] == [citation["corner"]]
    assert report.findings[0].issue == guidance_for("brake_point").issue
    assert str(gap) not in report.findings[0].issue


# --- reading a whole lap, three corners at a time -----------------------------
#
# One request carries three findings and a lap routinely has eight or nine
# corners worth advising on, so a single request could never cover a lap. It was
# not trying to: measured across the four collected sessions plus the sample,
# 72 corners lost time, 60 reached the model, and 36 could ever be answered for.
# The other half told the participant that no validated advice cited that
# stretch, while its evidence sat in the pack the model had been handed.


# Prose has to differ per finding or the validator merges them as one repeated
# claim, and it may not contain digits, so the corner name cannot be used to
# tell them apart.
_ORDINALS = ("first", "second", "third", "fourth")


def _round_answer(round_summary: dict) -> tuple[str, str]:
    """One grounded finding for every corner this round is about."""
    best: dict[str, dict] = {}
    for citation in opportunity_catalog(round_summary).values():
        best.setdefault(citation["corner"], citation)
    findings = [
        {
            "focus": citation["focus"],
            "issue": f"The {_ORDINALS[index]} opportunity is {citation['focus']}",
            "cause": "The cited telemetry differs from the reference.",
            "action": "Use a smoother and more repeatable technique here.",
            "evidence": [{key: citation[key] for key in EVIDENCE_KEYS}],
        }
        for index, citation in enumerate(best.values())
    ]
    return json.dumps({"findings": findings}), "test-model"


def test_every_coachable_corner_is_asked_about(summary):
    asked = []

    def ask(round_summary):
        asked.append([corner["corner"] for corner in round_summary["corners"]])
        return _round_answer(round_summary)

    report_over_rounds(summary, ask=ask)

    corners = [corner["corner"] for corner in coachable_corners(summary, limit=None)]
    assert len(corners) > 3  # otherwise this asserts nothing about rounds
    assert [name for batch in asked for name in batch] == corners
    assert all(len(batch) <= 3 for batch in asked)


def test_a_round_is_only_shown_the_corners_it_is_about(summary):
    """So the citation schema cannot offer the model a corner to wander onto."""
    seen = []

    def ask(round_summary):
        seen.append(round_summary)
        return _round_answer(round_summary)

    report_over_rounds(summary, ask=ask)

    first, second = seen[0], seen[1]
    assert not {c["corner"] for c in first["corners"]} & {
        c["corner"] for c in second["corners"]
    }
    # Everything else about the lap still travels, or the model would be reading
    # corners with no idea whose lap they are.
    assert first["lap"] == summary["lap"]


def test_findings_from_every_round_reach_one_report(summary):
    report = report_over_rounds(summary, ask=_round_answer)

    cited = {item.corner for finding in report.findings for item in finding.evidence}
    # Every corner the model had something citable for. A corner can lose time
    # and still carry no opportunity metric, and there is nothing to ground a
    # finding on there -- that gap is upstream of the model, not a round it
    # missed.
    wanted = {citation["corner"] for citation in opportunity_catalog(summary).values()}
    coachable = {corner["corner"] for corner in coachable_corners(summary, limit=None)}
    assert len(coachable) > 3  # otherwise one request would have covered the lap
    assert cited == wanted


def test_one_unusable_round_does_not_cost_the_others(summary):
    """A round that comes back as nonsense is three corners, not the lap."""
    calls = []

    def ask(round_summary):
        calls.append(round_summary)
        if len(calls) == 1:
            return "not json at all", "test-model"
        return _round_answer(round_summary)

    report = report_over_rounds(summary, ask=ask)

    assert len(calls) > 1
    assert report.findings  # the later rounds still landed


def test_when_every_round_fails_the_first_reason_is_the_one_reported(summary):
    with pytest.raises(CoachingSchemaError):
        report_over_rounds(summary, ask=lambda _s: ("not json at all", "test-model"))


def test_a_corner_gets_one_round_and_is_not_asked_again(summary):
    """The same request twice is the same answer twice, on a local model."""
    asked = []

    def ask(round_summary):
        asked.append([corner["corner"] for corner in round_summary["corners"]])
        return json.dumps({"findings": []}), "test-model"

    report_over_rounds(summary, ask=ask)

    names = [name for batch in asked for name in batch]
    assert len(names) == len(set(names))
    assert set(names) == {c["corner"] for c in coachable_corners(summary, limit=None)}


def test_a_lap_with_nothing_to_advise_on_asks_nothing(summary):
    quiet = {**summary, "corners": []}
    asked = []

    report = report_over_rounds(
        quiet, ask=lambda s: (asked.append(s), ("", ""))[1], fallback_model="test-model"
    )

    assert asked == []
    assert report.findings == ()
    assert report.model == "test-model"  # stamped even though nothing was asked
