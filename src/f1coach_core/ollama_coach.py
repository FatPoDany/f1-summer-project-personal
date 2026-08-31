"""Local Ollama Granite provider — the offline demo insurance.

Talks plain HTTP to a local Ollama server (stdlib only, no extra deps) and
streams the response. Model and endpoint are overridable via environment:
APEX_OLLAMA_MODEL (default granite3.3:8b), APEX_OLLAMA_URL.
"""

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator

from f1coach_core.coach import CoachingReport, CoachProvider
from f1coach_core.llm import build_coach_prompt, report_from_llm_text

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "granite3.3:8b"


def _http_error_message(exc: urllib.error.HTTPError, model: str) -> str:
    """The server answered but refused — usually a model that was never pulled."""
    detail = ""
    try:
        body = json.loads(exc.read().decode("utf-8", errors="replace"))
        detail = str(body.get("error", ""))
    except Exception:  # a body is optional; the status line alone still reads fine
        pass
    if exc.code == 404 or "not found" in detail:
        return (
            f"Ollama is running but can't serve '{model}' ({detail or exc}). "
            f"Pull it once: `ollama pull {model}`."
        )
    return f"Ollama request failed ({exc}{': ' + detail if detail else ''})."


def _http_stream(url: str, payload: dict, timeout: float) -> Iterator[dict]:
    """POST JSON, yield the JSONL chunks Ollama streams back."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for line in response:
            line = line.strip()
            if line:
                yield json.loads(line)


class OllamaCoach(CoachProvider):
    name = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        transport: Callable[[str, dict, float], Iterable[dict]] | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get("APEX_OLLAMA_URL", DEFAULT_URL)).rstrip("/")
        self.model = model or os.environ.get("APEX_OLLAMA_MODEL", DEFAULT_MODEL)
        self._transport = transport or _http_stream
        self._timeout = timeout

    def stream_completion(
        self,
        prompt: str,
        on_progress: Callable[[str], None] | None = None,
    ) -> str:
        """Send one prompt, stream back the full text. The lap-coach contract
        and racecoach's run-level feedback engine both ride on this."""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "format": "json",
            "options": {"temperature": 0.2, "num_predict": 1500},
        }
        text = ""
        try:
            for chunk in self._transport(f"{self.base_url}/api/chat", payload, self._timeout):
                if "error" in chunk:
                    raise RuntimeError(f"Ollama error: {chunk['error']}")
                text += chunk.get("message", {}).get("content", "")
                if on_progress is not None and text:
                    on_progress(text)
                if chunk.get("done"):
                    break
        except urllib.error.HTTPError as exc:  # before URLError: HTTPError is a subclass
            raise RuntimeError(_http_error_message(exc, self.model)) from exc
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            raise RuntimeError(
                f"Ollama isn't reachable at {self.base_url} ({exc}). Start it with "
                f"`ollama serve` and pull the model once: `ollama pull {self.model}`."
            ) from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Ollama sent a line that isn't JSON ({exc}) — is something else "
                f"listening on {self.base_url}?"
            ) from exc
        return text

    def generate(
        self,
        evidence_summary: dict,
        on_progress: Callable[[str], None] | None = None,
    ) -> CoachingReport:
        text = self.stream_completion(
            build_coach_prompt(evidence_summary, self.scatter, self.prior_advice), on_progress
        )
        return report_from_llm_text(
            text,
            model=f"ollama/{self.model}",
            evidence_summary=evidence_summary,
            device=self.device,
            scatter=self.scatter,
        )
