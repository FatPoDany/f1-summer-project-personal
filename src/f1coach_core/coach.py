"""Coaching contract v2, the provider interface, and the mock provider.

The response contract (fixed for the whole project):

    {
      "findings": [
        {"issue": str, "cause": str, "action": str,
         "evidence": [{"metric": str, "corner": str, "value": num,
                       "ref": num, "unit": str, "span_m": [d0, d1]}, ...]}
      ],
      "model": str,
      "prompt_version": str
    }

"Every AI claim is clickable" is an experience requirement, so the validator
enforces at least one evidence item per finding — `span_m` is what the
evidence-zoom anchors to. All providers funnel their output through
`coaching_report_from_dict`, the mock included: schema drift fails loudly
here, not in the UI. watsonx and Ollama providers arrive at A3 behind this
same interface (plus streaming).
"""

import math
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from f1coach_core.features import (
    NOTABLE_THRESHOLDS,
    CornerScatter,
    notable_bar,
)
from f1coach_core.guidance import guidance_for

FOCUS_AREAS = ("braking", "cornering", "throttle")
MAX_FINDINGS = 3
MIN_TIME_LOST = 0.05
MAX_COACHING_CORNERS = 6

# Public metric names in the coaching contract -> fields in one corner's
# deterministic evidence packet.  Providers may choose the prose, but every
# numeric citation is checked against this table before Apex will render it.
EVIDENCE_METRICS = {
    "brake_point": ("brake_point_m", "ref_brake_point_m", "m", "braking"),
    "peak_brake": ("peak_brake_pct", "ref_peak_brake_pct", "%", "braking"),
    "brake_release": ("brake_release_m", "ref_brake_release_m", "m", "braking"),
    "entry_speed": ("entry_speed_kmh", "ref_entry_speed_kmh", "km/h", "cornering"),
    "min_speed": ("min_speed_kmh", "ref_min_speed_kmh", "km/h", "cornering"),
    "min_speed_point": (
        "min_speed_point_m",
        "ref_min_speed_point_m",
        "m",
        "cornering",
    ),
    "exit_speed": ("exit_speed_kmh", "ref_exit_speed_kmh", "km/h", "cornering"),
    "throttle_reapply": (
        "throttle_reapply_m",
        "ref_throttle_reapply_m",
        "m",
        "throttle",
    ),
    "throttle_point": ("throttle_point_m", "ref_throttle_point_m", "m", "throttle"),
    "full_throttle": ("full_throttle_m", "ref_full_throttle_m", "m", "throttle"),
    "exit_throttle": (
        "exit_throttle_pct",
        "ref_exit_throttle_pct",
        "%",
        "throttle",
    ),
    "coast_distance": ("coast_distance_m", "ref_coast_distance_m", "m", "throttle"),
    "brake_applications": (
        "brake_applications",
        "ref_brake_applications",
        "count",
        "braking",
    ),
    "throttle_applications": (
        "throttle_applications",
        "ref_throttle_applications",
        "count",
        "throttle",
    ),
    "pedal_overlap": (
        "pedal_overlap_pct",
        "ref_pedal_overlap_pct",
        "%",
        "braking",
    ),
}


class CoachingSchemaError(ValueError):
    """A coaching response that Apex refuses to render. Messages are written
    to be shown to a person (and logged for the reliability audit)."""


@dataclass(frozen=True)
class Evidence:
    metric: str
    corner: str
    value: float
    ref: float
    unit: str
    span: tuple[float, float]  # [start_m, end_m] — anchors the evidence-zoom


@dataclass(frozen=True)
class Finding:
    """One piece of advice, and the measurements it is allowed to rest on.

    There is no confidence here any more. There was, it was a number between
    zero and one rendered as a green chip, and nothing measured it: the mock
    computed a linear function of time lost, and the LLM path let the model
    invent one and then kept it while overwriting every word around it. Across
    the eighty-nine findings actually served to the study's laps it ran from
    0.80 to 0.98 -- every card, every participant, a green "high". A number
    that never varies carries no information, and one that looks like a
    probability while carrying none is worse than no number at all.

    Nothing replaces it, because after ``features.notable_bar`` there is
    nothing left for it to say: a finding is published only if its difference
    cleared a bar set above that driver's own scatter through that corner, so
    every finding on screen has already passed the test a confidence would have
    been claiming to report. What still varies between findings -- how big the
    difference is, and what it cost -- is on the card already, in numbers that
    came off the lap.
    """

    focus: str
    issue: str
    cause: str
    action: str
    evidence: tuple[Evidence, ...]
    # Kept only so audits written before it was removed still load and can be
    # shown as what that participant was given. Never set on a new finding.
    confidence: float | None = None


@dataclass(frozen=True)
class CoachingReport:
    findings: tuple[Finding, ...]
    model: str
    prompt_version: str

    def to_dict(self) -> dict:
        return {
            "findings": [
                {
                    "focus": f.focus,
                    "issue": f.issue,
                    "cause": f.cause,
                    "action": f.action,
                    "evidence": [
                        {
                            "metric": e.metric,
                            "corner": e.corner,
                            "value": e.value,
                            "ref": e.ref,
                            "unit": e.unit,
                            "span_m": list(e.span),
                        }
                        for e in f.evidence
                    ],
                }
                for f in self.findings
            ],
            "model": self.model,
            "prompt_version": self.prompt_version,
        }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CoachingSchemaError(message)


def _number(value, where: str) -> float:
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool),
        f"{where} must be a number (got {value!r})",
    )
    number = float(value)
    _require(math.isfinite(number), f"{where} must be finite (got {value!r})")
    return number


_NUMBER_IN_PROSE = re.compile(r"\d+(?:\.\d+)?")


def supported_measurements(evidence: list["Evidence"]) -> set[float]:
    """The numbers a finding is allowed to say, from the evidence it cites.

    Both measured numbers, the gap between them, and the span the reader can
    zoom to. The gap is included because it is the quantity the finding is
    actually about -- "three km/h slower here" is the sentence a driver needs,
    and it is derived from two numbers already checked against the telemetry,
    not invented beside them.

    Digits inside a corner label are allowed too: a corner called T3 is not a
    measurement, and refusing it would only teach the model to avoid naming
    the place it is talking about.
    """
    allowed: set[float] = set()
    for item in evidence:
        allowed.update((item.value, item.ref, item.value - item.ref, *item.span))
        allowed.update(float(found) for found in _NUMBER_IN_PROSE.findall(item.corner))
    return {number for number in allowed} | {abs(number) for number in allowed}


def _quotes_a_measurement(token: str, allowed: set[float]) -> bool:
    """Whether a number written in prose is one of the measurements.

    Compared at the precision the model chose, so 7.63 may be written "7.63",
    "7.6" or "8" and all three are the same claim about the same telemetry.
    Rounding is the only freedom: a number that is not one of these at any
    precision is a different number, whatever it is near.
    """
    try:
        spoken = float(token)
    except ValueError:  # a number too long for a float is not one of ours
        return False
    places = len(token.partition(".")[2])
    return any(round(candidate, places) == spoken for candidate in allowed)


def _require_supported_measurements(value: str, where: str, allowed: set[float]) -> None:
    """Let a finding quote its own evidence, and nothing else.

    This replaced a rule that banned every digit from prose. That rule did keep
    numbers honest, but it also meant no sentence the model wrote could carry
    the size of what it had found, which is most of what makes coaching worth
    reading -- so the sentences were overwritten by templates and the model's
    only remaining job was choosing which corner to talk about. Checking a
    number against the citation costs the same guarantee and keeps the sentence.
    """
    for token in _NUMBER_IN_PROSE.findall(value):
        _require(
            _quotes_a_measurement(token, allowed),
            f"{where} says {token}, which is not a measurement this finding cites",
        )


def coachable_corners(evidence_summary: dict) -> list[dict]:
    """Return the most relevant corner packets that fit the model context."""
    single_lap = evidence_summary.get("analysis_mode") == "single_lap"
    eligible: list[dict] = []
    for corner in evidence_summary.get("corners", []):
        if not isinstance(corner, dict):
            continue
        score_key = "technique_score" if single_lap else "time_lost_s"
        try:
            score = float(corner.get(score_key, 0.0))
        except (TypeError, ValueError):
            continue
        below_threshold = score <= 0.0 if single_lap else score < MIN_TIME_LOST
        if not math.isfinite(score) or below_threshold:
            continue
        eligible.append(corner)
    return sorted(eligible, key=lambda corner: float(corner[score_key]), reverse=True)[
        :MAX_COACHING_CORNERS
    ]


def evidence_catalog(evidence_summary: dict) -> dict[tuple[str, str], dict]:
    """Flatten significant deterministic corner metrics for exact LLM fact checking."""
    catalog: dict[tuple[str, str], dict] = {}
    for corner in coachable_corners(evidence_summary):
        label = corner.get("corner")
        span = corner.get("span_m")
        if (
            not isinstance(label, str)
            or not label
            or not isinstance(span, (list, tuple))
            or len(span) != 2
        ):
            continue
        for metric, (value_key, ref_key, unit, focus) in EVIDENCE_METRICS.items():
            value, ref = corner.get(value_key), corner.get(ref_key)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or not isinstance(ref, (int, float))
                or isinstance(ref, bool)
                or not math.isfinite(float(ref))
            ):
                continue
            catalog[(label, metric)] = {
                "metric": metric,
                "corner": label,
                "value": float(value),
                "ref": float(ref),
                "unit": unit,
                "span_m": [float(span[0]), float(span[1])],
                "focus": focus,
            }
    return catalog


# Which way a metric has to move before it is worth coaching on. The size of
# the move comes from `_opportunity_bar`, not from here, so the two cannot be
# changed independently.
_OPPORTUNITY_DIRECTIONS = {
    "brake_point": -1,
    "entry_speed": -1,
    "min_speed": -1,
    "exit_speed": -1,
    "throttle_reapply": +1,
    "throttle_point": +1,
    "full_throttle": +1,
    "exit_throttle": -1,
    "coast_distance": +1,
}

# Bars for the five metrics `features.NOTABLE_THRESHOLDS` does not calibrate.
# The four it does are taken from there instead of copied, because a card and
# the debrief sentence beside it must not disagree about what counts as a
# difference worth mentioning. Before this they did: the card asked 15 m of
# late throttle where the debrief asked 10, and 10 m of coasting where the
# debrief asked 15.
_UNCALIBRATED_BARS = {
    "entry_speed": 3.0,
    "exit_speed": 3.0,
    "throttle_point": 15.0,
    "full_throttle": 15.0,
    "exit_throttle": 5.0,
}


def _opportunity_bar(metric: str, corner: str, scatter: CornerScatter | None) -> float:
    fact_key = EVIDENCE_METRICS[metric][0]
    if fact_key in NOTABLE_THRESHOLDS:
        return notable_bar(fact_key, corner, scatter)
    return _UNCALIBRATED_BARS[metric]


def opportunity_catalog(
    evidence_summary: dict, scatter: CornerScatter | None = None
) -> dict[tuple[str, str], dict]:
    """Return only citations whose direction and size support a coaching action.

    ``scatter`` is this driver's own lap-to-lap movement through each corner.
    Where it is wider than the fixed bar it replaces it, exactly as
    ``features.notable_bar`` does for the debrief: a driver whose minimum speed
    wanders 8 km/h between laps has not been told anything by a card reporting
    that one of them was 4 km/h down. The cards were the last screen still
    reading a driver's noise as a fact about their driving.

    Passing nothing keeps the fixed bars. That direction is the safe one and it
    is why this is a parameter rather than a field of the evidence packet: the
    bar can only ever rise, so the scatter-aware catalog is a **subset** of the
    fixed one, and a report generated against a driver's scatter still
    validates for a reader that has none. The packet stays byte-for-byte what
    it was, so every stored audit still matches on it.
    """
    if evidence_summary.get("analysis_mode") == "single_lap":
        rules = {
            "coast_distance": lambda value, guide: value > guide,
            "brake_applications": lambda value, guide: value > guide,
            "throttle_applications": lambda value, guide: value > guide,
            "pedal_overlap": lambda value, guide: value >= guide,
        }
        return {
            citation: item
            for citation, item in evidence_catalog(evidence_summary).items()
            if item["metric"] in rules
            and rules[item["metric"]](item["value"], item["ref"])
        }
    return {
        citation: item
        for citation, item in evidence_catalog(evidence_summary).items()
        if _supports_coaching(item, scatter)
    }


def _supports_coaching(item: dict, scatter: CornerScatter | None) -> bool:
    direction = _OPPORTUNITY_DIRECTIONS.get(item["metric"])
    if direction is None:
        return False
    bar = _opportunity_bar(item["metric"], item["corner"], scatter)
    gap = item["value"] - item["ref"]
    return gap <= -bar if direction < 0 else gap >= bar


def _same_number(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-6)


def coaching_report_from_dict(
    data: dict,
    evidence_summary: dict | None = None,
    *,
    opportunities_only: bool = False,
    scatter: CornerScatter | None = None,
) -> CoachingReport:
    """Validate a response and, when supplied, fact-check every citation.

    LLM adapters set ``opportunities_only`` so a citation must not merely be a
    real telemetry value: its direction and size must support coaching. The
    deterministic mock keeps access to the broader catalog for its quiet
    fallback when time loss cannot be attributed to a thresholded technique.
    """
    _require(
        isinstance(data, dict),
        f"coaching response must be an object (got {type(data).__name__})",
    )
    required_top = {"findings", "model", "prompt_version"}
    for key in required_top:
        _require(key in data, f"coaching response is missing '{key}'")
    _require(
        set(data) == required_top,
        f"coaching response keys must be exactly {sorted(required_top)}",
    )
    _require(isinstance(data["findings"], list), "'findings' must be a list")
    _require(
        len(data["findings"]) <= MAX_FINDINGS,
        f"'findings' must contain at most {MAX_FINDINGS} items",
    )
    _require(isinstance(data["model"], str) and data["model"], "'model' must be a non-empty string")
    _require(
        isinstance(data["prompt_version"], str) and data["prompt_version"],
        "'prompt_version' must be a non-empty string",
    )

    if evidence_summary is None:
        grounded = None
    elif opportunities_only:
        grounded = opportunity_catalog(evidence_summary, scatter)
    else:
        grounded = evidence_catalog(evidence_summary)
    findings = []
    for i, raw in enumerate(data["findings"]):
        where = f"findings[{i}]"
        _require(isinstance(raw, dict), f"{where} must be an object")
        required_finding = {"focus", "issue", "cause", "action", "evidence"}
        # `confidence` is tolerated rather than required: it is no longer
        # written (see Finding), but every audit recorded before it was removed
        # carries one, and those records are the only account of what a
        # participant was told. Refusing them here would not remove the number,
        # it would hide the evidence that it was ever shown.
        _require(
            required_finding <= set(raw) <= required_finding | {"confidence"},
            f"{where} keys must be {sorted(required_finding)}",
        )
        focus = raw.get("focus")
        _require(focus in FOCUS_AREAS, f"{where}.focus must be one of {list(FOCUS_AREAS)}")
        for key in ("issue", "cause", "action"):
            _require(
                isinstance(raw.get(key), str) and raw[key].strip(),
                f"{where}.{key} must be a non-empty string",
            )
        confidence = None
        if "confidence" in raw:
            confidence = _number(raw["confidence"], f"{where}.confidence")
            _require(
                0.0 <= confidence <= 1.0,
                f"{where}.confidence must be between 0 and 1",
            )
        raw_evidence = raw.get("evidence")
        _require(
            isinstance(raw_evidence, list) and 1 <= len(raw_evidence) <= 4,
            f"{where}.evidence must contain 1 to 4 items — every claim cites evidence",
        )
        evidence = []
        cited: set[tuple[str, str]] = set()
        cites_focus = False
        for j, ev in enumerate(raw_evidence):
            ev_where = f"{where}.evidence[{j}]"
            _require(isinstance(ev, dict), f"{ev_where} must be an object")
            required_evidence = {"metric", "corner", "value", "ref", "unit", "span_m"}
            _require(
                set(ev) == required_evidence,
                f"{ev_where} keys must be exactly {sorted(required_evidence)}",
            )
            for key in ("metric", "corner", "unit"):
                _require(
                    isinstance(ev.get(key), str) and ev[key].strip(),
                    f"{ev_where}.{key} must be a non-empty string",
                )
            span = ev.get("span_m")
            _require(
                isinstance(span, (list, tuple)) and len(span) == 2,
                f"{ev_where}.span_m must be [start_m, end_m]",
            )
            d0 = _number(span[0], f"{ev_where}.span_m[0]")
            d1 = _number(span[1], f"{ev_where}.span_m[1]")
            _require(d0 < d1, f"{ev_where}.span_m must satisfy start < end")
            value = _number(ev.get("value"), f"{ev_where}.value")
            ref = _number(ev.get("ref"), f"{ev_where}.ref")
            citation = (ev["corner"], ev["metric"])
            _require(citation not in cited, f"{ev_where} duplicates citation {citation}")
            cited.add(citation)
            if grounded is not None:
                expected = grounded.get(citation)
                _require(
                    expected is not None,
                    f"{ev_where} cites {citation}, which is not available in the evidence summary",
                )
                _require(ev["unit"] == expected["unit"], f"{ev_where}.unit does not match evidence")
                _require(
                    _same_number(value, expected["value"]),
                    f"{ev_where}.value does not match evidence",
                )
                _require(
                    _same_number(ref, expected["ref"]), f"{ev_where}.ref does not match evidence"
                )
                _require(
                    _same_number(d0, expected["span_m"][0])
                    and _same_number(d1, expected["span_m"][1]),
                    f"{ev_where}.span_m does not match evidence",
                )
                cites_focus = cites_focus or expected["focus"] == focus
            evidence.append(
                Evidence(
                    metric=ev["metric"],
                    corner=ev["corner"],
                    value=value,
                    ref=ref,
                    unit=ev["unit"],
                    span=(d0, d1),
                )
            )
        if grounded is not None:
            _require(cites_focus, f"{where} must cite at least one {focus} metric")
        allowed = supported_measurements(evidence)
        for key in ("issue", "cause", "action"):
            _require_supported_measurements(raw[key], f"{where}.{key}", allowed)
        findings.append(
            Finding(
                focus=focus,
                issue=raw["issue"],
                cause=raw["cause"],
                action=raw["action"],
                confidence=confidence,
                evidence=tuple(evidence),
            )
        )
    return CoachingReport(
        findings=tuple(findings), model=data["model"], prompt_version=data["prompt_version"]
    )


class CoachProvider(ABC):
    """One interface, three backends: watsonx / Ollama / mock."""

    name: str
    # What the participant drove with, when anybody measured it. Not part of
    # the evidence packet: that packet is the key every stored audit is matched
    # on, and this changes only how a finding is worded, never which findings
    # are true. Set by ``run_audited_coaching``, which is the layer that holds
    # the lap; unset means the wording that existed before it was asked.
    device: str = ""
    # This driver's own spread through each corner, for the same reason and by
    # the same route as ``device``: it belongs to the session, not to the
    # packet. Unlike ``device`` it changes which findings are offered, but only
    # ever by removing them -- see ``opportunity_catalog``. Unset means the
    # fixed bars, which is what every reader without a session sees.
    scatter: CornerScatter | None = None
    # What this driver has already been told on the other laps of this run, so
    # the same instruction does not arrive on every screen. Set by
    # ``run_audited_coaching`` like the two above; it reaches the model through
    # the prompt, and the prompt is stored verbatim in the audit record, so a
    # report generated against it stays re-derivable from what was kept.
    prior_advice: tuple[str, ...] = ()

    @abstractmethod
    def generate(
        self,
        evidence_summary: dict,
        on_progress: Callable[[str], None] | None = None,
    ) -> CoachingReport:
        """Turn an evidence summary (features.build_evidence_summary) into a report.

        `on_progress` receives the accumulated raw model text as it streams,
        for live display; the validated report only exists at the end.
        """


class MockCoach(CoachProvider):
    """Deterministic, offline, schema-valid — for UI development, CI, and demos.

    Not canned text: findings are derived from the actual evidence summary
    (worst corners first), so the evidence-zoom points at real zones.
    """

    name = "mock"
    MAX_FINDINGS = MAX_FINDINGS
    MIN_TIME_LOST = MIN_TIME_LOST  # seconds — below this a corner isn't worth a finding

    def generate(
        self,
        evidence_summary: dict,
        on_progress: Callable[[str], None] | None = None,
    ) -> CoachingReport:
        if evidence_summary.get("analysis_mode") == "single_lap":
            corners = coachable_corners(evidence_summary)
            findings = [
                self._single_lap_finding(corner, evidence_summary)
                for corner in corners[: self.MAX_FINDINGS]
            ]
        else:
            corners = sorted(
                evidence_summary.get("corners", []),
                key=lambda c: c["time_lost_s"],
                reverse=True,
            )
            findings = [
                self._finding_for(corner)
                for corner in corners[: self.MAX_FINDINGS]
                if corner["time_lost_s"] >= self.MIN_TIME_LOST
            ]
        payload = {"findings": findings, "model": "mock", "prompt_version": "mock-3"}
        if on_progress is not None:  # exercise the same streaming path as real providers
            import json

            on_progress(json.dumps(payload, indent=2))
        return coaching_report_from_dict(payload, evidence_summary)

    def _single_lap_finding(self, corner: dict, evidence_summary: dict) -> dict:
        catalog = opportunity_catalog({**evidence_summary, "corners": [corner]})
        citation = next(iter(catalog.values()))
        spoken = guidance_for(citation["metric"], device=self.device, single_lap=True)
        return {
            "focus": citation["focus"],
            "issue": spoken.issue,
            "cause": spoken.cause,
            "action": spoken.action,
            "evidence": [
                {
                    key: citation[key]
                    for key in ("metric", "corner", "value", "ref", "unit", "span_m")
                }
            ],
        }

    def _finding_for(self, corner: dict) -> dict:
        label, span = corner["corner"], corner["span_m"]
        opportunities: dict[str, dict] = {
            focus: {"score": 0.0, "evidence": []}
            for focus in FOCUS_AREAS
        }

        def add(focus: str, score: float, metric: str) -> None:
            value_key, ref_key, unit, _ = EVIDENCE_METRICS[metric]
            value, ref = corner.get(value_key), corner.get(ref_key)
            if value is None or ref is None:
                return
            item = opportunities[focus]
            item["score"] = max(item["score"], score)
            item["evidence"].append(
                {
                    "metric": metric,
                    "corner": label,
                    "value": value,
                    "ref": ref,
                    "unit": unit,
                    "span_m": span,
                }
            )

        brake, ref_brake = corner["brake_point_m"], corner["ref_brake_point_m"]
        if brake is not None and ref_brake is not None and brake - ref_brake <= -10:
            metres = ref_brake - brake
            add("braking", metres / 10.0, "brake_point")

        slower = corner["ref_min_speed_kmh"] - corner["min_speed_kmh"]
        if slower >= 3:
            add("cornering", slower / 3.0, "min_speed")

        exit_speed = corner.get("exit_speed_kmh")
        ref_exit_speed = corner.get("ref_exit_speed_kmh")
        if (
            exit_speed is not None
            and ref_exit_speed is not None
            and ref_exit_speed - exit_speed >= 3
        ):
            slower_exit = ref_exit_speed - exit_speed
            add("cornering", slower_exit / 3.0, "exit_speed")

        throttle, ref_throttle = corner["throttle_point_m"], corner["ref_throttle_point_m"]
        if throttle is not None and ref_throttle is not None and throttle - ref_throttle >= 15:
            metres = throttle - ref_throttle
            add("throttle", metres / 15.0, "throttle_point")

        full, ref_full = corner.get("full_throttle_m"), corner.get("ref_full_throttle_m")
        if full is not None and ref_full is not None and full - ref_full >= 15:
            metres = full - ref_full
            add("throttle", metres / 15.0, "full_throttle")

        focus = max(FOCUS_AREAS, key=lambda name: opportunities[name]["score"])
        selected = opportunities[focus]
        if selected["score"] == 0.0:
            # Nothing cleared a threshold, but the corner still cost time. Say
            # so against the one measurement every corner has, rather than
            # inventing a technique fault to explain it.
            focus = "cornering"
            selected = opportunities[focus]
            selected["evidence"] = [
                {
                    "metric": "min_speed",
                    "corner": label,
                    "value": corner["min_speed_kmh"],
                    "ref": corner["ref_min_speed_kmh"],
                    "unit": "km/h",
                    "span_m": span,
                }
            ]

        # The sentences come from the same table the LLM path is grounded on, so
        # one metric cannot mean two different things depending on which
        # provider happened to answer.
        spoken = guidance_for(selected["evidence"][0]["metric"], device=self.device)
        return {
            "focus": focus,
            "issue": spoken.issue,
            "cause": spoken.cause,
            "action": spoken.action,
            "evidence": selected["evidence"][:4],
        }


PROVIDER_NAMES = ("granite", "mock", "ollama", "watsonx")


def available_providers() -> tuple[str, ...]:
    return PROVIDER_NAMES


def get_provider(name: str = "mock") -> CoachProvider:
    if name == "granite":
        from f1coach_core.granite_coach import GraniteCoach

        return GraniteCoach()
    if name == "mock":
        return MockCoach()
    if name == "ollama":  # imported lazily: providers pull in transport machinery
        from f1coach_core.ollama_coach import OllamaCoach

        return OllamaCoach()
    if name == "watsonx":
        from f1coach_core.watsonx_coach import WatsonxCoach

        return WatsonxCoach()
    raise ValueError(f"Unknown coach provider '{name}'; available: {', '.join(PROVIDER_NAMES)}")
