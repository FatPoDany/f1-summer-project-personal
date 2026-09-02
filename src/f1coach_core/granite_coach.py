"""Local IBM Granite 4.1 provider for post-lap coaching.

The live pit-wall client has a deliberately different, single-snapshot
contract. This provider sends either a single-lap technique packet or a
lap-vs-reference evidence summary through the shared coaching prompt and
validates the answer through the same path as the other post-lap providers.
"""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator

from f1coach_core.coach import CoachingReport, CoachProvider
from f1coach_core.llm import (
    build_coach_prompt,
    build_coach_response_format,
    report_from_llm_text,
)

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_MODEL = "ibm-granite/granite-4.1-3b"
DEFAULT_TIMEOUT_S = 60.0
MAX_TOKENS = 1500

JsonTransport = Callable[[str, dict, dict[str, str], float], dict | Iterable[dict]]


class GraniteCoachError(RuntimeError):
    """A local Granite failure written to be safe to show in the UI."""


def _post_stream(
    url: str,
    payload: dict,
    headers: dict[str, str],
    timeout_s: float,
) -> Iterator[dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").rstrip("\r\n")
            if not line.startswith("data:"):
                continue
            data = line[5:].lstrip()
            if data == "[DONE]":
                break
            if data:
                yield json.loads(data)


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        detail = exc.read(1000).decode("utf-8", errors="replace").strip()
    except Exception:
        detail = ""
    return f": {detail}" if detail else ""


class GraniteCoach(CoachProvider):
    """Post-lap coach backed by Granite's OpenAI-compatible local endpoint."""

    name = "granite"

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
        configured_timeout = os.environ.get("GRANITE_TIMEOUT_S", str(DEFAULT_TIMEOUT_S))
        try:
            self.timeout_s = float(timeout_s if timeout_s is not None else configured_timeout)
        except (TypeError, ValueError) as exc:
            raise GraniteCoachError("Granite timeout must be a positive finite number") from exc
        if not math.isfinite(self.timeout_s) or self.timeout_s <= 0:
            raise GraniteCoachError("Granite timeout must be a positive finite number")
        self._transport = transport or _post_stream

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def generate(
        self,
        evidence_summary: dict,
        on_progress: Callable[[str], None] | None = None,
    ) -> CoachingReport:
        prompt = build_coach_prompt(evidence_summary, self.scatter, self.prior_advice)
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": MAX_TOKENS,
            "stream": True,
            "response_format": build_coach_response_format(
                evidence_summary, self.scatter
            ),
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        response, streamed = self._request(payload, headers, on_progress)
        try:
            choice = response["choices"][0]
            raw_text = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise GraniteCoachError(
                "Granite server response is missing choices[0].message.content"
            ) from exc
        if not isinstance(raw_text, str):
            raise GraniteCoachError("Granite server returned non-text message content")
        # An answer that ran out of room is unfinished, not malformed, and the
        # two look identical to the JSON parser downstream. Saying which one it
        # is here is the difference between raising --ctx-size and hunting for a
        # schema bug that is not there.
        if isinstance(choice, dict) and choice.get("finish_reason") == "length":
            raise GraniteCoachError(
                "The model ran out of room before it finished its answer "
                f"({len(raw_text)} characters written). Start llama-server with "
                "a larger --ctx-size so the evidence and the answer both fit."
            )
        if on_progress is not None and not streamed:
            on_progress(raw_text)

        served_model = str(response.get("model") or self.model)
        return report_from_llm_text(
            raw_text,
            model=served_model,
            evidence_summary=evidence_summary,
            device=self.device,
            scatter=self.scatter,
        )

    def _request(
        self,
        payload: dict,
        headers: dict[str, str],
        on_progress: Callable[[str], None] | None,
    ) -> tuple[dict, bool]:
        try:
            response = self._transport(self.endpoint, payload, headers, self.timeout_s)
            streamed = not isinstance(response, dict)
            if streamed:
                response = self._collect_stream(response, on_progress)
        except urllib.error.HTTPError as exc:
            raise GraniteCoachError(
                f"Granite server returned HTTP {exc.code}{_http_error_detail(exc)}"
            ) from exc
        except TimeoutError as exc:
            # Not the same failure as an unreachable server, and it used to be
            # reported as one. A 3B model answering on a laptop CPU is slow, not
            # absent, and "the coach stopped responding" sent everybody looking
            # for a connection problem that was not there.
            raise GraniteCoachError(
                f"The model did not finish within {self.timeout_s:.0f} s. It is "
                "running, just slowly -- on a CPU the first answer of a session "
                "takes the longest. Set GRANITE_TIMEOUT_S higher to wait longer."
            ) from exc
        except (urllib.error.URLError, ConnectionError) as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, TimeoutError):
                raise GraniteCoachError(
                    f"The model did not finish within {self.timeout_s:.0f} s. It is "
                    "running, just slowly -- on a CPU the first answer of a session "
                    "takes the longest. Set GRANITE_TIMEOUT_S higher to wait longer."
                ) from exc
            raise GraniteCoachError(
                f"Granite server is not reachable at {self.endpoint} ({exc})."
            ) from exc
        except json.JSONDecodeError as exc:
            raise GraniteCoachError(f"Granite server returned non-JSON data: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise GraniteCoachError("Granite server returned non-UTF-8 stream data") from exc
        if not isinstance(response, dict):
            raise GraniteCoachError(
                f"Granite server returned {type(response).__name__}, expected a JSON object"
            )
        return response, streamed

    def _collect_stream(
        self,
        chunks: Iterable[dict],
        on_progress: Callable[[str], None] | None,
    ) -> dict:
        text = ""
        served_model = self.model
        finish_reason: str | None = None
        for chunk in chunks:
            if not isinstance(chunk, dict):
                raise GraniteCoachError(
                    f"Granite server returned {type(chunk).__name__}, expected a JSON object"
                )
            if "error" in chunk:
                error = chunk["error"]
                detail = error.get("message") if isinstance(error, dict) else error
                raise GraniteCoachError(
                    f"Granite server stream failed: {str(detail or error)[:1000]}"
                )
            served_model = str(chunk.get("model") or served_model)
            choices = chunk.get("choices")
            if not choices:
                continue
            try:
                content = choices[0].get("delta", {}).get("content")
                finish_reason = choices[0].get("finish_reason") or finish_reason
            except (AttributeError, IndexError, TypeError) as exc:
                raise GraniteCoachError(
                    "Granite server stream is missing choices[0].delta.content"
                ) from exc
            if content is None:
                continue
            if not isinstance(content, str):
                raise GraniteCoachError("Granite server returned non-text message content")
            text += content
            if on_progress is not None and text:
                on_progress(text)
        return {
            "model": served_model,
            "choices": [{"message": {"content": text}, "finish_reason": finish_reason}],
        }
