"""Putting the measured debrief into words a driver can act on.

The division of labour matters here. ``f1coach_core.debrief`` decides which
stretches of track cost time and by how much, from the telemetry alone. This
module only rephrases that finding. It never decides what happened and never
adds a figure of its own -- a model that invents "you braked 15 m too late" for
a participant who did nothing of the sort would corrupt the study rather than
help the driver.

That is enforced, not merely asked for: every number a narration contains has to
be one of the numbers it was given, or the narration is dropped and the measured
line stands on its own. A coach that is silent is fine; a coach that is confident
and wrong is not.
"""

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from f1coach_core.debrief import DebriefPoint
from racecoach.granite.client import GraniteError, JsonTransport, _post_json

PROMPT_VERSION = "granite-debrief-v1"
DEFAULT_MAX_TOKENS = 700

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


class NarrationError(RuntimeError):
    """The model could not be reached or answered in an unusable shape."""


@dataclass(frozen=True)
class NarratedPoint:
    """One measured stretch, with prose if the model produced usable prose."""

    point: DebriefPoint
    narration: str = ""

    @property
    def spoken(self) -> bool:
        return bool(self.narration)


@dataclass(frozen=True)
class NarratedDebrief:
    summary: str
    points: tuple[NarratedPoint, ...]
    model: str = ""
    prompt_version: str = PROMPT_VERSION
    raw_text: str = ""
    provenance: dict = field(default_factory=dict)

    @property
    def spoken_count(self) -> int:
        return sum(1 for item in self.points if item.spoken)


def facts_for(point: DebriefPoint) -> dict:
    """Exactly what the model is allowed to know, and to repeat."""
    start, end = point.span_m
    return {
        "corner": point.corner,
        "time_lost_s": round(point.time_lost_s, 2),
        "from_m": round(start, 1),
        "to_m": round(end, 1),
        "difference": point.difference,
        "detail": point.detail,
    }


def allowed_numbers(point: DebriefPoint) -> set[str]:
    """Every numeric token the narration of this point may use.

    Drawn from the measurements themselves, so a narration cannot smuggle in a
    figure nobody measured. Trailing zeros are normalised because "0.30" and
    "0.3" are the same measurement written two ways.

    The corner name counts as a source too: it is a label we handed the model,
    and "Turn 3" would otherwise make the digit 3 look invented every time the
    narration named the corner it was talking about.
    """
    source = " ".join(str(value) for value in facts_for(point).values())
    return {_normalise(token) for token in _NUMBER.findall(source)}


def _normalise(token: str) -> str:
    try:
        return f"{float(token):g}"
    except ValueError:  # pragma: no cover - the regex only matches numbers
        return token


def invented_numbers(narration: str, point: DebriefPoint) -> list[str]:
    """Numbers in the prose that no measurement backs up."""
    allowed = allowed_numbers(point)
    return [
        token for token in _NUMBER.findall(narration) if _normalise(token) not in allowed
    ]


def build_prompt(summary: str, points: list[DebriefPoint]) -> str:
    measured = [facts_for(point) for point in points]
    return (
        "You are a driving coach talking to a participant in a driving study who "
        "has just finished a lap. Below are measurements already taken from their "
        "telemetry. Explain each one in plain language: what it means and what to "
        "try next lap.\n\n"
        "Rules you must follow:\n"
        "- Use only the numbers given. Do not introduce any other number.\n"
        "- Do not claim a cause the measurements do not show.\n"
        "- Two sentences per stretch at most. No jargon, no lap-time predictions.\n"
        "- If a stretch has an empty 'difference', say the time was lost there "
        "without saying why, because the reason was not measured.\n\n"
        f"Overall measurement: {summary}\n"
        f"Stretches: {json.dumps(measured, ensure_ascii=False)}\n\n"
        'Reply with JSON only: {"summary": "one sentence", "stretches": '
        '["one entry per stretch, in the same order"]}'
    )


def narrate_debrief(
    summary: str,
    points: list[DebriefPoint],
    *,
    base_url: str,
    model: str,
    api_key: str | None = None,
    timeout_s: float = 120.0,
    transport: JsonTransport | None = None,
) -> NarratedDebrief:
    """Narrate the measured debrief, keeping the measurements authoritative.

    The returned object always carries every measured point. Points the model
    failed to narrate acceptably simply come back without prose.
    """
    if not points:
        return NarratedDebrief(summary=summary, points=())

    prompt = build_prompt(summary, points)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    send = transport or _post_json
    try:
        response = send(base_url.rstrip("/") + "/chat/completions", payload, headers, timeout_s)
    except GraniteError as exc:
        raise NarrationError(str(exc)) from exc

    try:
        raw_text = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise NarrationError("The model's reply had no message content.") from exc
    if not isinstance(raw_text, str):
        raise NarrationError("The model returned non-text content.")

    spoken_summary, spoken = _parse(raw_text, points)
    return NarratedDebrief(
        summary=spoken_summary or summary,
        points=tuple(
            NarratedPoint(point=point, narration=text)
            for point, text in zip(points, spoken, strict=True)
        ),
        model=str(response.get("model") or model),
        raw_text=raw_text,
        provenance={
            "endpoint": base_url,
            "requested_model": model,
            "prompt_version": PROMPT_VERSION,
            # What the caller declared about the weights. An HTTP client cannot
            # prove which file a server loaded behind an alias.
            "model_file": os.environ.get("GRANITE_MODEL_FILE"),
            "model_sha256": os.environ.get("GRANITE_MODEL_SHA256"),
        },
    )


def _parse(raw_text: str, points: list[DebriefPoint]) -> tuple[str, list[str]]:
    """Pull usable prose out of the reply, discarding anything unsupported."""
    empty = ["" for _ in points]
    try:
        data = json.loads(_json_slice(raw_text))
    except (ValueError, TypeError):
        return "", empty
    if not isinstance(data, dict):
        return "", empty

    stretches = data.get("stretches")
    spoken = empty
    if isinstance(stretches, list) and len(stretches) == len(points):
        spoken = [
            _accept(text, point) for text, point in zip(stretches, points, strict=True)
        ]

    summary = data.get("summary")
    # The summary spans every stretch, so any of their numbers are fair game.
    allowed: set[str] = set()
    for point in points:
        allowed |= allowed_numbers(point)
    if isinstance(summary, str) and summary.strip():
        if any(_normalise(t) not in allowed for t in _NUMBER.findall(summary)):
            summary = ""
    else:
        summary = ""
    return summary.strip(), spoken


def _accept(text: object, point: DebriefPoint) -> str:
    if not isinstance(text, str) or not text.strip():
        return ""
    if invented_numbers(text, point):
        return ""
    return text.strip()


def _json_slice(raw_text: str) -> str:
    """Small models like to wrap JSON in prose or a fenced block."""
    start, end = raw_text.find("{"), raw_text.rfind("}")
    if start == -1 or end <= start:
        return raw_text
    return raw_text[start : end + 1]


CoachFactory = Callable[[], NarratedDebrief]
