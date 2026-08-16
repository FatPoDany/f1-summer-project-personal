"""Coaching contract v2, the provider interface, and the mock provider.

The response contract (fixed for the whole project):

    {
      "findings": [
        {"issue": str, "cause": str, "action": str, "confidence": 0..1,
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
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

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
    focus: str
    issue: str
    cause: str
    action: str
    confidence: float
    evidence: tuple[Evidence, ...]


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
                    "confidence": f.confidence,
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


def _require_measurement_free_prose(value: str, where: str) -> None:
    """Keep every numeric claim inside a citation, where it can be fact-checked."""
    _require(
        not any(character.isdigit() for character in value),
        f"{where} must not contain numeric values; put measurements in evidence",
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


def opportunity_catalog(evidence_summary: dict) -> dict[tuple[str, str], dict]:
    """Return only citations whose direction and size support a coaching action."""
    if evidence_summary.get("analysis_mode") == "single_lap":
        rules = {
            "coast_distance": lambda value, guide: value > guide,
            "brake_applications": lambda value, guide: value > guide,
            "throttle_applications": lambda value, guide: value > guide,
            "pedal_overlap": lambda value, guide: value >= guide,
        }
    else:
        rules = {
            "brake_point": lambda value, ref: value <= ref - 10.0,
            "entry_speed": lambda value, ref: value <= ref - 3.0,
            "min_speed": lambda value, ref: value <= ref - 3.0,
            "exit_speed": lambda value, ref: value <= ref - 3.0,
            "throttle_reapply": lambda value, ref: value >= ref + 15.0,
            "throttle_point": lambda value, ref: value >= ref + 15.0,
            "full_throttle": lambda value, ref: value >= ref + 15.0,
            "exit_throttle": lambda value, ref: value <= ref - 5.0,
            "coast_distance": lambda value, ref: value >= ref + 10.0,
        }
    return {
        citation: item
        for citation, item in evidence_catalog(evidence_summary).items()
        if item["metric"] in rules and rules[item["metric"]](item["value"], item["ref"])
    }


def _same_number(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-6)


def coaching_report_from_dict(
    data: dict,
    evidence_summary: dict | None = None,
    *,
    opportunities_only: bool = False,
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
        grounded = opportunity_catalog(evidence_summary)
    else:
        grounded = evidence_catalog(evidence_summary)
    findings = []
    for i, raw in enumerate(data["findings"]):
        where = f"findings[{i}]"
        _require(isinstance(raw, dict), f"{where} must be an object")
        required_finding = {"focus", "issue", "cause", "action", "confidence", "evidence"}
        _require(
            set(raw) == required_finding,
            f"{where} keys must be exactly {sorted(required_finding)}",
        )
        focus = raw.get("focus")
        _require(focus in FOCUS_AREAS, f"{where}.focus must be one of {list(FOCUS_AREAS)}")
        for key in ("issue", "cause", "action"):
            _require(
                isinstance(raw.get(key), str) and raw[key].strip(),
                f"{where}.{key} must be a non-empty string",
            )
            _require_measurement_free_prose(raw[key], f"{where}.{key}")
        confidence = _number(raw.get("confidence"), f"{where}.confidence")
        _require(0.0 <= confidence <= 1.0, f"{where}.confidence must be between 0 and 1")
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

    @staticmethod
    def _single_lap_finding(corner: dict, evidence_summary: dict) -> dict:
        catalog = opportunity_catalog(
            {**evidence_summary, "corners": [corner]}
        )
        citation = next(iter(catalog.values()))
        guidance = {
            "coast_distance": (
                "Throttle transition needs review",
                "The telemetry shows an extended neutral-pedal phase.",
                "Make the brake-release to throttle-pickup transition smooth and deliberate.",
            ),
            "brake_applications": (
                "Brake application needs review",
                "The braking input is split into repeated applications.",
                "Use one progressive brake application and one controlled release.",
            ),
            "throttle_applications": (
                "Throttle application needs review",
                "The exit input is interrupted by repeated throttle applications.",
                "Build throttle progressively as steering unwinds.",
            ),
            "pedal_overlap": (
                "Pedal transition needs review",
                "Brake and throttle are applied together through part of the zone.",
                "Separate brake release from throttle pickup with a controlled transition.",
            ),
        }
        issue, cause, action = guidance[citation["metric"]]
        return {
            "focus": citation["focus"],
            "issue": issue,
            "cause": cause,
            "action": action,
            "confidence": 0.7,
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
            focus: {"score": 0.0, "causes": [], "actions": [], "evidence": []}
            for focus in FOCUS_AREAS
        }

        def add(focus: str, score: float, cause: str, action: str, metric: str) -> None:
            value_key, ref_key, unit, _ = EVIDENCE_METRICS[metric]
            value, ref = corner.get(value_key), corner.get(ref_key)
            if value is None or ref is None:
                return
            item = opportunities[focus]
            item["score"] = max(item["score"], score)
            item["causes"].append(cause)
            item["actions"].append(action)
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
            add(
                "braking",
                metres / 10.0,
                "braking begins earlier than the reference",
                "move initial brake application toward the cited reference marker",
                "brake_point",
            )

        slower = corner["ref_min_speed_kmh"] - corner["min_speed_kmh"]
        if slower >= 3:
            add(
                "cornering",
                slower / 3.0,
                "minimum speed is lower than the reference",
                "release the brake smoothly and preserve speed through the corner",
                "min_speed",
            )

        exit_speed = corner.get("exit_speed_kmh")
        ref_exit_speed = corner.get("ref_exit_speed_kmh")
        if (
            exit_speed is not None
            and ref_exit_speed is not None
            and ref_exit_speed - exit_speed >= 3
        ):
            slower_exit = ref_exit_speed - exit_speed
            add(
                "cornering",
                slower_exit / 3.0,
                "exit speed is lower than the reference",
                "prioritise a clean exit and unwind steering progressively",
                "exit_speed",
            )

        throttle, ref_throttle = corner["throttle_point_m"], corner["ref_throttle_point_m"]
        if throttle is not None and ref_throttle is not None and throttle - ref_throttle >= 15:
            metres = throttle - ref_throttle
            add(
                "throttle",
                metres / 15.0,
                "half throttle arrives later than the reference on exit",
                "begin squeezing the throttle earlier as steering unwinds",
                "throttle_point",
            )

        full, ref_full = corner.get("full_throttle_m"), corner.get("ref_full_throttle_m")
        if full is not None and ref_full is not None and full - ref_full >= 15:
            metres = full - ref_full
            add(
                "throttle",
                metres / 15.0,
                "full throttle arrives later than the reference",
                "build throttle progressively toward the cited full-throttle point",
                "full_throttle",
            )

        focus = max(FOCUS_AREAS, key=lambda name: opportunities[name]["score"])
        selected = opportunities[focus]
        if selected["score"] == 0.0:
            focus = "cornering"
            selected = opportunities[focus]
            selected["causes"] = ["carrying less pace through the zone than the reference"]
            selected["actions"] = ["build a repeatable entry, apex and exit through the zone"]
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

        time_lost = corner["time_lost_s"]
        confidence = round(min(0.9, 0.5 + 0.8 * time_lost), 2)
        causes = selected["causes"]
        actions = selected["actions"]
        return {
            "focus": focus,
            "issue": f"{focus.capitalize()} is the clearest opportunity",
            "cause": (
                causes[0][:1].upper()
                + causes[0][1:]
                + "".join(f", and {extra}" for extra in causes[1:])
                + "."
            ),
            "action": ("; ".join(actions)[:1].upper() + "; ".join(actions)[1:] + "."),
            "confidence": confidence,
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
