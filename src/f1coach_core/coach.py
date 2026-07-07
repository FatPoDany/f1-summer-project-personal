"""Coaching contract v1, the provider interface, and the mock provider.

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

from abc import ABC, abstractmethod
from dataclasses import dataclass


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
    return float(value)


def coaching_report_from_dict(data: dict) -> CoachingReport:
    """Validate a raw coaching response and freeze it into dataclasses."""
    _require(
        isinstance(data, dict),
        f"coaching response must be an object (got {type(data).__name__})",
    )
    for key in ("findings", "model", "prompt_version"):
        _require(key in data, f"coaching response is missing '{key}'")
    _require(isinstance(data["findings"], list), "'findings' must be a list")
    _require(isinstance(data["model"], str) and data["model"], "'model' must be a non-empty string")
    _require(
        isinstance(data["prompt_version"], str) and data["prompt_version"],
        "'prompt_version' must be a non-empty string",
    )

    findings = []
    for i, raw in enumerate(data["findings"]):
        where = f"findings[{i}]"
        _require(isinstance(raw, dict), f"{where} must be an object")
        for key in ("issue", "cause", "action"):
            _require(
                isinstance(raw.get(key), str) and raw[key].strip(),
                f"{where}.{key} must be a non-empty string",
            )
        confidence = _number(raw.get("confidence"), f"{where}.confidence")
        _require(0.0 <= confidence <= 1.0, f"{where}.confidence must be between 0 and 1")
        raw_evidence = raw.get("evidence")
        _require(
            isinstance(raw_evidence, list) and raw_evidence,
            f"{where}.evidence must be a non-empty list — every claim cites evidence",
        )
        evidence = []
        for j, ev in enumerate(raw_evidence):
            ev_where = f"{where}.evidence[{j}]"
            _require(isinstance(ev, dict), f"{ev_where} must be an object")
            for key in ("metric", "corner", "unit"):
                _require(isinstance(ev.get(key), str), f"{ev_where}.{key} must be a string")
            span = ev.get("span_m")
            _require(
                isinstance(span, (list, tuple)) and len(span) == 2,
                f"{ev_where}.span_m must be [start_m, end_m]",
            )
            d0 = _number(span[0], f"{ev_where}.span_m[0]")
            d1 = _number(span[1], f"{ev_where}.span_m[1]")
            _require(d0 < d1, f"{ev_where}.span_m must satisfy start < end")
            evidence.append(
                Evidence(
                    metric=ev["metric"],
                    corner=ev["corner"],
                    value=_number(ev.get("value"), f"{ev_where}.value"),
                    ref=_number(ev.get("ref"), f"{ev_where}.ref"),
                    unit=ev["unit"],
                    span=(d0, d1),
                )
            )
        findings.append(
            Finding(
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
    """One interface, three backends (watsonx / Ollama / mock — A3 adds the
    first two, plus streaming)."""

    name: str

    @abstractmethod
    def generate(self, evidence_summary: dict) -> CoachingReport:
        """Turn an evidence summary (features.build_evidence_summary) into a report."""


class MockCoach(CoachProvider):
    """Deterministic, offline, schema-valid — for UI development, CI, and demos.

    Not canned text: findings are derived from the actual evidence summary
    (worst corners first), so the evidence-zoom points at real zones.
    """

    name = "mock"
    MAX_FINDINGS = 3
    MIN_TIME_LOST = 0.05  # seconds — below this a corner isn't worth a finding

    def generate(self, evidence_summary: dict) -> CoachingReport:
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
        return coaching_report_from_dict(
            {"findings": findings, "model": "mock", "prompt_version": "mock-1"}
        )

    def _finding_for(self, corner: dict) -> dict:
        label, span = corner["corner"], corner["span_m"]
        causes: list[str] = []
        actions: list[str] = []
        evidence = [
            {
                "metric": "min_speed",
                "corner": label,
                "value": corner["min_speed_kmh"],
                "ref": corner["ref_min_speed_kmh"],
                "unit": "km/h",
                "span_m": span,
            }
        ]

        brake, ref_brake = corner["brake_point_m"], corner["ref_brake_point_m"]
        if brake is not None and ref_brake is not None and brake - ref_brake <= -10:
            metres = ref_brake - brake
            causes.append(f"braking {metres:.0f} m earlier than the reference")
            actions.append(f"carry brake pressure {metres:.0f} m deeper into {label}")
            evidence.append(
                {
                    "metric": "brake_point",
                    "corner": label,
                    "value": brake,
                    "ref": ref_brake,
                    "unit": "m",
                    "span_m": span,
                }
            )

        slower = corner["ref_min_speed_kmh"] - corner["min_speed_kmh"]
        if slower >= 3:
            causes.append(f"arriving {slower:.0f} km/h slower at the apex")
            actions.append(f"aim for a {corner['ref_min_speed_kmh']:.0f} km/h minimum")

        throttle, ref_throttle = corner["throttle_point_m"], corner["ref_throttle_point_m"]
        if throttle is not None and ref_throttle is not None and throttle - ref_throttle >= 15:
            metres = throttle - ref_throttle
            causes.append(f"opening the throttle {metres:.0f} m later on exit")
            actions.append(f"get back to full throttle by {ref_throttle:.0f} m")
            evidence.append(
                {
                    "metric": "throttle_point",
                    "corner": label,
                    "value": throttle,
                    "ref": ref_throttle,
                    "unit": "m",
                    "span_m": span,
                }
            )

        if not causes:
            causes = ["carrying less speed through the zone than the reference"]
            actions = [f"match the reference commitment through {label}"]

        time_lost = corner["time_lost_s"]
        confidence = round(min(0.9, 0.5 + 0.8 * time_lost), 2)
        return {
            "issue": f"{label}: losing {time_lost:.2f} s to the reference",
            "cause": (causes[0][:1].upper() + causes[0][1:] + "".join(
                f", and {extra}" for extra in causes[1:]
            ) + "."),
            "action": ("; ".join(actions)[:1].upper() + "; ".join(actions)[1:] + "."),
            "confidence": confidence,
            "evidence": evidence,
        }


_PROVIDERS: dict[str, type[CoachProvider]] = {"mock": MockCoach}


def get_provider(name: str = "mock") -> CoachProvider:
    if name not in _PROVIDERS:
        available = ", ".join(sorted(_PROVIDERS))
        raise ValueError(
            f"Unknown coach provider '{name}'; available: {available} "
            "(watsonx and ollama arrive at milestone A3)"
        )
    return _PROVIDERS[name]()
