import json
from unittest.mock import MagicMock, patch

import pytest

from backend.app.analysis_schemas import TenderAnalysis
from backend.app.services import ai_provider
from backend.app.services.ai_provider import (
    OpenAIProvider,
    OllamaProvider,
    get_llm_provider,
)
from backend.app.services.analysis_service import _llm_result_is_grounded

EVIDENCE = {
    "requirements": [{"page": 3, "text": "Tender Submission Requirements: bidders must submit the following."}],
    "dates": [{"page": 1, "text": "Closing date: 28/08/2025 at 5:30 PM."}],
    "risks": [],
    "clarifications": [],
}


def _make_completion(content: str):
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    completion = MagicMock()
    completion.choices = [choice]
    return completion


def test_get_llm_provider_selects_openai() -> None:
    provider = get_llm_provider("openai")
    assert isinstance(provider, OpenAIProvider)


def test_get_llm_provider_preserves_ollama() -> None:
    assert isinstance(get_llm_provider("ollama"), OllamaProvider)


def test_openai_provider_calls_sdk_with_json_response_format() -> None:
    provider = OpenAIProvider(api_key="test-key", model="gpt-4o-mini")
    provider._client = MagicMock()
    provider._client.chat.completions.create.return_value = _make_completion(json.dumps({"summary": {}}))

    result = provider.generate_grounded_json(EVIDENCE)

    assert result == {"summary": {}}
    call_kwargs = provider._client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert call_kwargs["response_format"] == {"type": "json_object"}
    assert "EVIDENCE" in call_kwargs["messages"][0]["content"]
    assert "Tender Submission Requirements" in call_kwargs["messages"][0]["content"]


def test_openai_provider_returns_none_on_auth_failure() -> None:
    import openai

    provider = OpenAIProvider(api_key="bad", model="gpt-4o-mini")
    provider._client = MagicMock()
    provider._client.chat.completions.create.side_effect = openai.AuthenticationError(
        message="Invalid API key", response=MagicMock(), body=None
    )
    assert provider.generate_grounded_json(EVIDENCE) is None


def test_openai_provider_returns_none_on_connection_failure() -> None:
    import openai

    provider = OpenAIProvider(api_key="x", model="gpt-4o-mini")
    provider._client = MagicMock()
    provider._client.chat.completions.create.side_effect = openai.APIConnectionError(
        message="network down", request=MagicMock()
    )
    assert provider.generate_grounded_json(EVIDENCE) is None


def test_openai_provider_returns_none_on_malformed_json() -> None:
    provider = OpenAIProvider(api_key="x", model="gpt-4o-mini")
    provider._client = MagicMock()
    provider._client.chat.completions.create.return_value = _make_completion("not json at all")
    assert provider.generate_grounded_json(EVIDENCE) is None


def test_openai_provider_returns_none_on_empty_content() -> None:
    provider = OpenAIProvider(api_key="x", model="gpt-4o-mini")
    provider._client = MagicMock()
    provider._client.chat.completions.create.return_value = _make_completion("")
    assert provider.generate_grounded_json(EVIDENCE) is None


def test_openai_provider_returns_none_on_server_error() -> None:
    import openai

    provider = OpenAIProvider(api_key="x", model="gpt-4o-mini")
    provider._client = MagicMock()
    provider._client.chat.completions.create.side_effect = openai.InternalServerError(
        message="overloaded", response=MagicMock(), body=None
    )
    assert provider.generate_grounded_json(EVIDENCE) is None


def test_openai_provider_uses_config_defaults(monkeypatch) -> None:
    monkeypatch.setattr(ai_provider.settings, "openai_api_key", "config-key")
    monkeypatch.setattr(ai_provider.settings, "openai_model", "gpt-4o")
    provider = OpenAIProvider()
    assert provider._model == "gpt-4o"
    assert provider._api_key == "config-key"
    # The client is constructed lazily on first use (see _get_client).
    assert provider._client is None


def test_grounding_remains_effective_for_openai_shaped_result() -> None:
    """A valid grounded result is accepted; a fabricated source is rejected."""
    chunks = [{"page": 1, "text": "Closing date: 28/08/2025 at 5:30 PM."}]

    grounded = TenderAnalysis.model_validate({
        "summary": {},
        "dates": [{"event": "Closing Date", "date": "28/08/2025", "time": "5:30 PM",
                   "evidence_type": "EXPLICIT", "source_page": 1,
                   "source_snippet": "Closing date: 28/08/2025 at 5:30 PM."}],
        "bid_assessment": {"recommendation": "REVIEW_REQUIRED", "confidence": "LOW", "rationale": "Unknown."},
    })
    assert _llm_result_is_grounded(grounded, chunks)

    fabricated = TenderAnalysis.model_validate({
        "summary": {},
        "dates": [{"event": "Closing Date", "date": "1 January 2099",
                   "evidence_type": "EXPLICIT", "source_page": 1,
                   "source_snippet": "This sentence is not in the tender."}],
        "bid_assessment": {"recommendation": "REVIEW_REQUIRED", "confidence": "LOW", "rationale": "Unknown."},
    })
    assert not _llm_result_is_grounded(fabricated, chunks)


def test_openai_provider_does_not_send_full_tender_or_company_data() -> None:
    """The provider must only receive the evidence_by_category contract."""
    provider = OpenAIProvider(api_key="x", model="gpt-4o-mini")
    provider._client = MagicMock()
    provider._client.chat.completions.create.return_value = _make_completion(json.dumps({"summary": {}}))

    provider.generate_grounded_json(EVIDENCE)

    content = provider._client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "EVIDENCE" in content
    assert "Tender Submission Requirements" in content
    # No company/evidence-vault/auth/billing markers should be injected.
    for forbidden in ("company_profile", "evidence_vault", "Authorization", "Bearer ", "stripe", "password"):
        assert forbidden.lower() not in content.lower()


def test_openai_provider_is_constructible_without_key_for_testing() -> None:
    """Constructing the provider must not require a real key at import time."""
    provider = OpenAIProvider(api_key="test-only", model="gpt-4o-mini")
    assert provider._model == "gpt-4o-mini"