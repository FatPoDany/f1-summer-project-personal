"""Ollama and watsonx providers, exercised through injected transports."""

import urllib.error

import pytest

from f1coach_core import build_evidence_summary, get_provider, load_sample_session
from f1coach_core.ollama_coach import OllamaCoach
from f1coach_core.watsonx_coach import WatsonxCoach, WatsonxCredentials, resolve_credentials
from test_llm import FINDINGS_JSON


@pytest.fixture(scope="module")
def summary():
    session = load_sample_session()
    return build_evidence_summary(session.laps[2], session.best_lap)


def chunked(text, size=40):
    return [text[i : i + size] for i in range(0, len(text), size)]


# -- ollama -------------------------------------------------------------------


def fake_ollama_transport(text, captured):
    def transport(url, payload, timeout):
        captured.update(url=url, payload=payload)
        pieces = chunked(text)
        for piece in pieces[:-1]:
            yield {"message": {"content": piece}, "done": False}
        yield {"message": {"content": pieces[-1]}, "done": True}

    return transport


def test_ollama_streams_and_validates(summary):
    captured: dict = {}
    seen: list[str] = []
    coach = OllamaCoach(transport=fake_ollama_transport(FINDINGS_JSON, captured))
    report = coach.generate(summary, on_progress=seen.append)

    assert report.model == "ollama/granite3.3:8b"
    assert report.findings[0].issue.startswith("T1")
    assert captured["url"].endswith("/api/chat")
    assert captured["payload"]["format"] == "json"
    assert "never invent values" in captured["payload"]["messages"][0]["content"]
    assert len(seen) > 1 and seen[-1] == FINDINGS_JSON  # accumulated text grows


def test_ollama_surfaces_server_errors(summary):
    def transport(url, payload, timeout):
        yield {"error": "model 'granite3.3:8b' not found"}

    with pytest.raises(RuntimeError, match="not found"):
        OllamaCoach(transport=transport).generate(summary)


def test_ollama_unreachable_is_actionable(summary):
    def transport(url, payload, timeout):
        raise urllib.error.URLError("connection refused")
        yield  # pragma: no cover — makes this a generator like the real transport

    with pytest.raises(RuntimeError, match="ollama serve"):
        OllamaCoach(transport=transport).generate(summary)


# -- watsonx ------------------------------------------------------------------


def test_watsonx_streams_and_stamps_model(summary):
    prompts: list[str] = []
    seen: list[str] = []

    def fake_stream(credentials, model_id, prompt):
        assert credentials.api_key == "k"
        prompts.append(prompt)
        yield from chunked(FINDINGS_JSON)

    coach = WatsonxCoach(
        stream_factory=fake_stream,
        credentials_resolver=lambda: WatsonxCredentials("k", "p", "https://example"),
    )
    report = coach.generate(summary, on_progress=seen.append)

    assert report.model == coach.model_id
    assert "lap_03" in prompts[0]
    assert seen[-1] == FINDINGS_JSON


def test_watsonx_missing_credentials_are_readable(summary, monkeypatch):
    import f1coach_core.watsonx_coach as wx

    for var in ("WATSONX_APIKEY", "WATSONX_PROJECT_ID", "WATSONX_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(wx.keyring, "get_password", lambda service, entry: None)
    with pytest.raises(RuntimeError, match="keyring set apex-watsonx"):
        resolve_credentials()


def test_watsonx_env_credentials_win(monkeypatch):
    monkeypatch.setenv("WATSONX_APIKEY", "env-key")
    monkeypatch.setenv("WATSONX_PROJECT_ID", "env-project")
    monkeypatch.delenv("WATSONX_URL", raising=False)
    import f1coach_core.watsonx_coach as wx

    monkeypatch.setattr(wx.keyring, "get_password", lambda service, entry: None)
    creds = resolve_credentials()
    assert creds == WatsonxCredentials("env-key", "env-project", wx.DEFAULT_URL)


# -- registry -----------------------------------------------------------------


def test_registry_knows_all_three_providers():
    assert get_provider("mock").name == "mock"
    assert isinstance(get_provider("ollama"), OllamaCoach)
    assert isinstance(get_provider("watsonx"), WatsonxCoach)
    with pytest.raises(ValueError, match="mock, ollama, watsonx"):
        get_provider("granite-cloud")
