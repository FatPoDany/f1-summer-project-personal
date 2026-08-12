"""Strict Granite 4.1 client and its live-telemetry response contract.

The default endpoint is llama.cpp's OpenAI-compatible server. The same
client also works with vLLM, SGLang, or an authenticated compatible gateway.
Only compact numeric telemetry crosses this boundary; model prose can never
become an actuator command.
"""

from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_MODEL = "ibm-granite/granite-4.1-3b"
PROMPT_VERSION = "granite-torcs-live-v1"

FOCUS_AREAS = frozenset({"braking", "cornering", "throttle", "position", "pace", "safety"})
URGENCY_LEVELS = frozenset({"info", "soon", "now"})

JsonTransport = Callable[[str, dict, dict[str, str], float], dict]


class GraniteError(RuntimeError):
    """A Granite transport or contract failure safe to show to a user."""


@dataclass(frozen=True)
class TelemetrySnapshot:
    """One compact, rounded view of an SCR tick supplied to Granite."""

    tick: int
    lap: int
    sim_time_s: float
    speed_kmh: float
    track_position: float
    heading_angle_rad: float
    clear_road_ahead_m: float
    fuel_l: float
    damage: float
    rpm: float
    gear: int
    nearest_opponent_m: float | None
    throttle_cmd: float
    brake_cmd: float
    steer_cmd: float
    tire_wear: tuple[float, float, float, float] | None = None
    tire_temp_c: tuple[float, float, float, float] | None = None
    tire_pressure_kpa: tuple[float, float, float, float] | None = None
    tire_graining: tuple[float, float, float, float] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tick, int) or isinstance(self.tick, bool) or self.tick < 0:
            raise ValueError("TelemetrySnapshot.tick must be a non-negative integer")
        if not isinstance(self.lap, int) or isinstance(self.lap, bool) or self.lap < 1:
            raise ValueError("TelemetrySnapshot.lap must be a positive integer")
        if not isinstance(self.gear, int) or isinstance(self.gear, bool):
            raise ValueError("TelemetrySnapshot.gear must be an integer")
        float_fields = (
            "sim_time_s",
            "speed_kmh",
            "track_position",
            "heading_angle_rad",
            "clear_road_ahead_m",
            "fuel_l",
            "damage",
            "rpm",
            "throttle_cmd",
            "brake_cmd",
            "steer_cmd",
        )
        for name in float_fields:
            value = getattr(self, name)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"TelemetrySnapshot.{name} must be finite")
        if self.nearest_opponent_m is not None and not math.isfinite(
            float(self.nearest_opponent_m)
        ):
            raise ValueError("TelemetrySnapshot.nearest_opponent_m must be finite")
        for name in ("tire_wear", "tire_temp_c", "tire_pressure_kpa", "tire_graining"):
            readings = getattr(self, name)
            if readings is not None and (
                len(readings) != 4
                or not all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    for value in readings
                )
            ):
                raise ValueError(f"TelemetrySnapshot.{name} must contain four finite numbers")

    @classmethod
    def from_scr(
        cls,
        tick: int,
        lap: int,
        state: dict,
        actions: dict,
        *,
        tick_s: float = 0.02,
    ) -> TelemetrySnapshot:
        def scalar(tag: str, default: float = 0.0) -> float:
            value = state.get(tag, default)
            if isinstance(value, list):
                return default
            try:
                number = float(value)
            except (TypeError, ValueError):
                return default
            return number if math.isfinite(number) else default

        def vector4(tag: str, digits: int) -> tuple[float, float, float, float] | None:
            value = state.get(tag)
            if not isinstance(value, list) or len(value) != 4:
                return None
            try:
                numbers = tuple(round(float(item), digits) for item in value)
            except (TypeError, ValueError):
                return None
            if not all(math.isfinite(item) for item in numbers):
                return None
            return numbers  # type: ignore[return-value]

        def action(name: str) -> float:
            try:
                number = float(actions.get(name, 0.0))
            except (TypeError, ValueError):
                return 0.0
            return number if math.isfinite(number) else 0.0

        track = state.get("track")
        ahead = 200.0
        if isinstance(track, list) and len(track) == 19:
            try:
                candidate_ahead = float(track[9])
                if math.isfinite(candidate_ahead):
                    ahead = candidate_ahead
            except (TypeError, ValueError):
                pass
        opponents = state.get("opponents")
        finite_opponents: list[float] = []
        if isinstance(opponents, list):
            for value in opponents:
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(number):
                    finite_opponents.append(number)
        nearest = min(finite_opponents) if finite_opponents else None
        return cls(
            tick=int(tick),
            lap=int(lap),
            sim_time_s=round(scalar("simTime", tick * tick_s), 3),
            speed_kmh=round(scalar("speedX"), 3),
            track_position=round(scalar("trackPos"), 4),
            heading_angle_rad=round(scalar("angle"), 4),
            clear_road_ahead_m=round(ahead, 3),
            fuel_l=round(scalar("fuel"), 3),
            damage=round(scalar("damage"), 3),
            rpm=round(scalar("rpm"), 1),
            gear=int(scalar("gear", 1.0)),
            nearest_opponent_m=None if nearest is None else round(nearest, 3),
            throttle_cmd=round(action("accel"), 4),
            brake_cmd=round(action("brake"), 4),
            steer_cmd=round(action("steer"), 4),
            tire_wear=vector4("tireWear", 6),
            tire_temp_c=vector4("tireTempC", 3),
            tire_pressure_kpa=vector4("tirePressureKPa", 3),
            tire_graining=vector4("tireGraining", 6),
        )

    def to_dict(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value is not None}

    def evidence_values(self) -> dict[str, tuple[float, str]]:
        values: dict[str, tuple[float, str]] = {
            "speed_kmh": (self.speed_kmh, "km/h"),
            "track_position": (self.track_position, "track widths"),
            "heading_angle_rad": (self.heading_angle_rad, "rad"),
            "clear_road_ahead_m": (self.clear_road_ahead_m, "m"),
            "fuel_l": (self.fuel_l, "L"),
            "damage": (self.damage, "points"),
            "rpm": (self.rpm, "rpm"),
            "gear": (float(self.gear), "gear"),
            "throttle_cmd": (self.throttle_cmd, "ratio"),
            "brake_cmd": (self.brake_cmd, "ratio"),
            "steer_cmd": (self.steer_cmd, "ratio"),
        }
        if self.nearest_opponent_m is not None:
            values["nearest_opponent_m"] = (self.nearest_opponent_m, "m")
        wheel_names = ("front_right", "front_left", "rear_right", "rear_left")
        tire_channels = (
            ("tire_wear", self.tire_wear, "ratio (0=new, 1=worn)"),
            ("tire_temp_c", self.tire_temp_c, "degC"),
            ("tire_pressure_kpa", self.tire_pressure_kpa, "kPa"),
            ("tire_graining", self.tire_graining, "ratio"),
        )
        for channel, readings, unit in tire_channels:
            if readings is not None:
                for wheel, reading in zip(wheel_names, readings, strict=True):
                    values[f"{channel}_{wheel}"] = (reading, unit)
        return values


@dataclass(frozen=True)
class AdviceEvidence:
    metric: str
    value: float
    unit: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class GraniteAdvice:
    """Validated advisory output. It intentionally contains no controls."""

    message: str
    focus: str
    urgency: str
    evidence: tuple[AdviceEvidence, ...]

    def to_dict(self) -> dict:
        return {
            "message": self.message,
            "focus": self.focus,
            "urgency": self.urgency,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class GraniteResult:
    advice: GraniteAdvice
    model: str
    prompt_version: str
    raw_text: str
    prompt: str
    provenance: dict


def build_prompt(snapshot: TelemetrySnapshot) -> str:
    schema = {
        "message": "one short actionable coaching sentence",
        "focus": "one allowed focus string",
        "urgency": "one allowed urgency string",
        "evidence": [
            {"metric": "exact evidence key", "value": "exact number", "unit": "exact unit"}
        ],
    }
    return (
        "You are IBM Granite 4.1 acting as a TORCS race engineer. "
        "Use only the numeric snapshot below. Return exactly one JSON object and no markdown. "
        "Every evidence metric, value, and unit must exactly match the snapshot and its units. "
        "The evidence array is mandatory and must contain at least one citation. "
        "The message itself must contain no digits; cite all numbers only in evidence. "
        "Give advisory coaching only: never emit steering, throttle, brake, or gear commands, "
        "and discuss tyres only when tyre evidence keys are present. Tyre wear means zero is "
        "new and one is fully worn; wheel order is front-right, front-left, rear-right, rear-left. "
        "If evidence is weak, use urgency 'info'.\n\n"
        f"Required shape:\n{json.dumps(schema, separators=(',', ':'))}\n\n"
        "Evidence units:\n"
        f"{json.dumps(snapshot.evidence_values(), separators=(',', ':'))}\n\n"
        f"Telemetry snapshot:\n{json.dumps(snapshot.to_dict(), separators=(',', ':'))}"
    )


def response_format_for(snapshot: TelemetrySnapshot) -> dict:
    """OpenAI-compatible JSON Schema that makes every contract field mandatory."""
    available = snapshot.evidence_values()
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "message": {"type": "string", "minLength": 1, "maxLength": 300},
            "focus": {"type": "string", "enum": sorted(FOCUS_AREAS)},
            "urgency": {"type": "string", "enum": sorted(URGENCY_LEVELS)},
            "evidence": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "metric": {"type": "string", "enum": sorted(available)},
                        "value": {"type": "number"},
                        "unit": {
                            "type": "string",
                            "enum": sorted({unit for _, unit in available.values()}),
                        },
                    },
                    "required": ["metric", "value", "unit"],
                },
            },
        },
        "required": ["message", "focus", "urgency", "evidence"],
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "torcs_granite_advice",
            "strict": True,
            "schema": schema,
        },
    }


def advice_from_dict(data: dict, snapshot: TelemetrySnapshot) -> GraniteAdvice:
    if not isinstance(data, dict):
        raise GraniteError("Granite advice must be a JSON object")
    required_keys = {"message", "focus", "urgency", "evidence"}
    if set(data) != required_keys:
        raise GraniteError(
            f"Granite advice keys must be exactly {sorted(required_keys)}; "
            f"received {sorted(str(key) for key in data)}"
        )
    message = data.get("message")
    focus = data.get("focus")
    urgency = data.get("urgency")
    raw_evidence = data.get("evidence")
    if not isinstance(message, str) or not message.strip():
        raise GraniteError("Granite advice.message must be non-empty text")
    if len(message) > 300:
        raise GraniteError("Granite advice.message must be at most 300 characters")
    if re.search(r"\d", message):
        raise GraniteError("Granite advice.message must not contain unvalidated numbers")
    if focus not in FOCUS_AREAS:
        raise GraniteError(f"Granite advice.focus must be one of {sorted(FOCUS_AREAS)}")
    if urgency not in URGENCY_LEVELS:
        raise GraniteError(f"Granite advice.urgency must be one of {sorted(URGENCY_LEVELS)}")
    if not isinstance(raw_evidence, list) or not 1 <= len(raw_evidence) <= 4:
        raise GraniteError("Granite advice.evidence must contain 1 to 4 telemetry citations")

    available = snapshot.evidence_values()
    evidence: list[AdviceEvidence] = []
    seen_metrics: set[str] = set()
    for index, item in enumerate(raw_evidence):
        if not isinstance(item, dict):
            raise GraniteError(f"Granite advice.evidence[{index}] must be an object")
        if set(item) != {"metric", "value", "unit"}:
            raise GraniteError(
                f"Granite advice.evidence[{index}] keys must be metric, value, and unit"
            )
        metric = item.get("metric")
        if metric not in available:
            raise GraniteError(f"Granite cited unknown telemetry metric {metric!r}")
        if metric in seen_metrics:
            raise GraniteError(f"Granite cited telemetry metric {metric!r} more than once")
        seen_metrics.add(metric)
        value = item.get("value")
        unit = item.get("unit")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise GraniteError(f"Granite evidence value for {metric} must be numeric")
        if not math.isfinite(float(value)):
            raise GraniteError(f"Granite evidence value for {metric} must be finite")
        expected, expected_unit = available[metric]
        tolerance = max(0.0001, abs(expected) * 0.00001)
        if abs(float(value) - expected) > tolerance:
            raise GraniteError(
                f"Granite cited {metric}={value}, but the snapshot contains {expected}"
            )
        if unit != expected_unit:
            raise GraniteError(
                f"Granite cited unit {unit!r} for {metric}; expected {expected_unit!r}"
            )
        evidence.append(AdviceEvidence(metric, expected, expected_unit))
    return GraniteAdvice(message.strip(), focus, urgency, tuple(evidence))


def advice_from_text(text: str, snapshot: TelemetrySnapshot) -> GraniteAdvice:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]).strip() if len(lines) >= 3 else cleaned
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end < start:
        raise GraniteError("Granite returned no JSON object")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise GraniteError(f"Granite returned invalid JSON: {exc}") from exc
    return advice_from_dict(data, snapshot)


def _post_json(url: str, payload: dict, headers: dict[str, str], timeout_s: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", errors="replace")
        raise GraniteError(f"Granite server returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise GraniteError(
            f"Granite server is not reachable at {url} ({exc}). Start llama-server "
            "or use --granite-base-url to select another OpenAI-compatible endpoint."
        ) from exc
    except json.JSONDecodeError as exc:
        raise GraniteError(f"Granite server returned non-JSON data: {exc}") from exc


class GraniteClient:
    """OpenAI-compatible client for the Granite 4.1 instruct model."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout_s: float | None = None,
        transport: JsonTransport | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("GRANITE_BASE_URL", DEFAULT_BASE_URL)).rstrip(
            "/"
        )
        self.model = model or os.environ.get("GRANITE_MODEL", DEFAULT_MODEL)
        self.api_key = api_key if api_key is not None else os.environ.get("GRANITE_API_KEY")
        configured_timeout = os.environ.get("GRANITE_TIMEOUT_S", "60")
        try:
            self.timeout_s = float(
                timeout_s if timeout_s is not None else configured_timeout
            )
        except (TypeError, ValueError) as exc:
            raise GraniteError("Granite timeout must be a positive finite number") from exc
        if not math.isfinite(self.timeout_s) or self.timeout_s <= 0:
            raise GraniteError("Granite timeout must be a positive finite number")
        self._transport = transport or _post_json

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def generate(self, snapshot: TelemetrySnapshot) -> GraniteResult:
        prompt = build_prompt(snapshot)
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 300,
            "stream": False,
            "response_format": response_format_for(snapshot),
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = self._transport(self.endpoint, payload, headers, self.timeout_s)
        try:
            raw_text = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise GraniteError(
                "Granite server response is missing choices[0].message.content"
            ) from exc
        if not isinstance(raw_text, str):
            raise GraniteError("Granite server returned non-text message content")
        configured_provenance = any(
            os.environ.get(name)
            for name in (
                "GRANITE_MODEL_REVISION",
                "GRANITE_MODEL_FILE",
                "GRANITE_MODEL_SHA256",
                "GRANITE_QUANTIZATION",
            )
        )

        return GraniteResult(
            advice=advice_from_text(raw_text, snapshot),
            model=str(response.get("model") or self.model),
            prompt_version=PROMPT_VERSION,
            raw_text=raw_text,
            prompt=prompt,
            provenance={
                "transport": "openai-compatible",
                "endpoint": self.endpoint,
                "requested_model": self.model,
                # These values describe what the caller declared. An HTTP client
                # cannot prove which weights a server loaded from an alias alone.
                "model_revision": os.environ.get("GRANITE_MODEL_REVISION"),
                "model_file": os.environ.get("GRANITE_MODEL_FILE"),
                "model_sha256": os.environ.get("GRANITE_MODEL_SHA256"),
                "quantization": os.environ.get("GRANITE_QUANTIZATION"),
                "llama_cpp_revision": os.environ.get("GRANITE_LLAMA_CPP_REVISION"),
                "provenance_verified": False,
                "provenance_source": (
                    "environment-declared" if configured_provenance else "unverified"
                ),
                "temperature": 0.0,
                "max_tokens": 300,
            },
        )


class MockGraniteClient:
    """Deterministic Granite contract double for CI and no-model demos."""

    model = "mock/granite-4.1-3b-contract"

    def generate(self, snapshot: TelemetrySnapshot) -> GraniteResult:
        if snapshot.tire_wear is not None and max(snapshot.tire_wear) >= 0.7:
            worn_index = max(range(4), key=lambda index: snapshot.tire_wear[index])
            wheel = ("front_right", "front_left", "rear_right", "rear_left")[worn_index]
            message, focus, urgency, metric = (
                "Protect the most worn tyre and prepare a conservative strategy decision.",
                "safety",
                "now",
                f"tire_wear_{wheel}",
            )
        elif abs(snapshot.track_position) > 0.8:
            message, focus, urgency, metric = (
                "Re-centre the car smoothly before increasing pace.",
                "position",
                "now",
                "track_position",
            )
        elif snapshot.clear_road_ahead_m < 70.0 and snapshot.speed_kmh > 100.0:
            message, focus, urgency, metric = (
                "Prepare a progressive braking phase for the restricted road ahead.",
                "braking",
                "soon",
                "clear_road_ahead_m",
            )
        elif snapshot.brake_cmd > 0.05:
            message, focus, urgency, metric = (
                "Release the brake progressively as the car rotates toward the apex.",
                "cornering",
                "soon",
                "brake_cmd",
            )
        else:
            message, focus, urgency, metric = (
                "Hold a consistent line and build speed only while the road remains clear.",
                "pace",
                "info",
                "speed_kmh",
            )
        value, unit = snapshot.evidence_values()[metric]
        payload = {
            "message": message,
            "focus": focus,
            "urgency": urgency,
            "evidence": [{"metric": metric, "value": value, "unit": unit}],
        }
        raw_text = json.dumps(payload, separators=(",", ":"))
        return GraniteResult(
            advice=advice_from_dict(payload, snapshot),
            model=self.model,
            prompt_version=PROMPT_VERSION,
            raw_text=raw_text,
            prompt=build_prompt(snapshot),
            provenance={
                "transport": "deterministic-mock",
                "requested_model": self.model,
                "temperature": 0.0,
                "max_tokens": 0,
            },
        )
