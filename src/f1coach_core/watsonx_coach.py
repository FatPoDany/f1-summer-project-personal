"""watsonx.ai Granite provider — the project's primary coach.

Credentials resolve from environment variables first, then the OS keychain
(service "apex-watsonx" via `keyring`), and every failure is a readable
sentence, because live demos die on cryptic auth errors:

    env:      WATSONX_APIKEY, WATSONX_PROJECT_ID, WATSONX_URL (optional)
    keychain: python -m keyring set apex-watsonx api_key
              python -m keyring set apex-watsonx project_id
              python -m keyring set apex-watsonx url        (optional)

The ibm-watsonx-ai SDK ships in the optional `[watsonx]` extra and is only
imported when this provider actually runs.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass

import keyring
import keyring.errors

from f1coach_core.coach import CoachingReport, CoachProvider
from f1coach_core.llm import build_coach_prompt, report_from_llm_text

KEYRING_SERVICE = "apex-watsonx"
DEFAULT_URL = "https://eu-gb.ml.cloud.ibm.com"
DEFAULT_MODEL = "ibm/granite-3-3-8b-instruct"

CREDENTIALS_HELP = (
    "watsonx credentials are missing. Set WATSONX_APIKEY and WATSONX_PROJECT_ID "
    "(and optionally WATSONX_URL), or store them in the OS keychain:\n"
    "  python -m keyring set apex-watsonx api_key\n"
    "  python -m keyring set apex-watsonx project_id"
)


@dataclass(frozen=True)
class WatsonxCredentials:
    api_key: str
    project_id: str
    url: str


def _from_keychain(entry: str) -> str | None:
    try:
        return keyring.get_password(KEYRING_SERVICE, entry)
    except keyring.errors.KeyringError:
        return None  # locked/absent keychain must not crash credential lookup


def resolve_credentials() -> WatsonxCredentials:
    api_key = os.environ.get("WATSONX_APIKEY") or _from_keychain("api_key")
    project_id = os.environ.get("WATSONX_PROJECT_ID") or _from_keychain("project_id")
    url = os.environ.get("WATSONX_URL") or _from_keychain("url") or DEFAULT_URL
    if not api_key or not project_id:
        raise RuntimeError(CREDENTIALS_HELP)
    return WatsonxCredentials(api_key=api_key, project_id=project_id, url=url)


def _sdk_stream(credentials: WatsonxCredentials, model_id: str, prompt: str):
    """Real SDK call, kept separate so tests can inject a fake stream factory."""
    try:
        from ibm_watsonx_ai import Credentials
        from ibm_watsonx_ai.foundation_models import ModelInference
    except ImportError as exc:
        raise RuntimeError(
            "the ibm-watsonx-ai SDK is not installed — run from source after "
            "pip install 'apex-f1coach[watsonx]' (packaged builds exclude the SDK "
            "by design; use the mock or ollama provider there)"
        ) from exc
    model = ModelInference(
        model_id=model_id,
        credentials=Credentials(url=credentials.url, api_key=credentials.api_key),
        project_id=credentials.project_id,
        params={"decoding_method": "greedy", "max_new_tokens": 1500},
    )
    return model.generate_text_stream(prompt=prompt)


class WatsonxCoach(CoachProvider):
    name = "watsonx"

    def __init__(
        self,
        model_id: str | None = None,
        stream_factory: Callable | None = None,
        credentials_resolver: Callable[[], WatsonxCredentials] | None = None,
    ) -> None:
        self.model_id = model_id or os.environ.get("APEX_WATSONX_MODEL", DEFAULT_MODEL)
        self._stream_factory = stream_factory or _sdk_stream
        self._resolve = credentials_resolver or resolve_credentials

    def stream_completion(
        self,
        prompt: str,
        on_progress: Callable[[str], None] | None = None,
    ) -> str:
        """Send one prompt, stream back the full text. The lap-coach contract
        and racecoach's run-level feedback engine both ride on this."""
        credentials = self._resolve()
        text = ""
        try:
            for chunk in self._stream_factory(credentials, self.model_id, prompt):
                text += chunk
                if on_progress is not None and text:
                    on_progress(text)
        except RuntimeError:
            raise  # already a readable sentence (missing SDK, credentials help, ...)
        except Exception as exc:  # SDK/network errors are cryptic; demos need a sentence
            raise RuntimeError(
                f"watsonx call failed: {exc}. Check the API key and project id, "
                f"the endpoint ({credentials.url}), and that this machine is online."
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
            model=self.model_id,
            evidence_summary=evidence_summary,
            device=self.device,
            scatter=self.scatter,
        )
