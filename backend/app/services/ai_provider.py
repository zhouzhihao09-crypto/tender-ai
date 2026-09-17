"""AI provider abstraction for Phase 14.

The product must eventually support cloud deployment where Ollama is not
running on the developer's laptop.  This module defines a ``LLMProvider``
protocol and provides an :class:`OllamaProvider` implementation that preserves
the existing local behaviour.

A hosted provider (e.g. OpenAI) implements the same protocol without changing
calling code.  No API keys are stored or exposed by this module — provider
credentials come from environment configuration.

The prompt/JSON contract shared by every provider is defined here so that
grounding validation in :mod:`backend.app.services.analysis_service` remains
the single authority over whether an LLM result is usable.
"""

from __future__ import annotations

import json
from typing import Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen

from ..config import settings

# Grounding-oriented prompt shared by every provider.  Hosted providers must
# reproduce this contract exactly so the existing post-response grounding
# validation (``_llm_result_is_grounded``) remains the authority.
_GROUNDED_PROMPT_TEMPLATE = (
    "You are analysing a tender. Use ONLY the evidence below. "
    "Return valid JSON with keys summary, dates, requirements, "
    "documents, eligibility, risks, clarifications, bid_assessment. "
    "Every item must include source_page, source_snippet and "
    "evidence_type (EXPLICIT, INFERENCE, or UNKNOWN). Use null or "
    "'Not stated in the tender' when absent. Never claim automatic "
    "disqualification unless the evidence explicitly says so. "
    "Bid assessment must be REVIEW_REQUIRED because company "
    "qualifications are unknown.\n\nEVIDENCE:\n"
    "{evidence}"
)


def _format_evidence(evidence_by_category: dict[str, list[dict[str, object]]]) -> str:
    """Render the evidence dict into the prompt's EVIDENCE section."""
    return "\n\n".join(
        f"[{category}]\n"
        + "\n".join(f"Page {item['page']}: {item['text']}" for item in chunks)
        for category, chunks in evidence_by_category.items()
    )


class LLMProvider(Protocol):
    """Minimal contract for a grounded-JSON LLM provider."""

    def generate_grounded_json(
        self, evidence_by_category: dict[str, list[dict[str, object]]]
    ) -> dict | None:
        """Return structured JSON from evidence, or ``None`` on failure."""
        ...


class OllamaProvider:
    """Provider backed by a local Ollama server.

    Preserves the exact prompting and HTTP contract used by the original
    ``generate_grounded_json`` function in :mod:`backend.app.services.llm_service`.
    """

    def generate_grounded_json(
        self, evidence_by_category: dict[str, list[dict[str, object]]]
    ) -> dict | None:
        evidence = _format_evidence(evidence_by_category)
        prompt = _GROUNDED_PROMPT_TEMPLATE.format(evidence=evidence)
        payload = json.dumps(
            {"model": settings.ollama_model, "prompt": prompt, "format": "json", "stream": False}
        ).encode()
        request = Request(
            f"{settings.ollama_url.rstrip('/')}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=settings.ollama_timeout_seconds) as response:
                content = json.loads(response.read().decode()).get("response", "")
                result = json.loads(content)
                return result if isinstance(result, dict) else None
        except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
            return None


class OpenAIProvider:
    """Provider backed by the OpenAI Chat Completions API.

    Uses the official OpenAI Python SDK.  The same grounding-oriented prompt
    contract as :class:`OllamaProvider` is used, with ``response_format`` set
    to ``json_object`` so the model returns parseable JSON directly.

    Any failure — missing credentials, network error, authentication
    failure, rate limit, server error, malformed JSON — returns ``None`` so
    the caller falls back to the deterministic analysis path.  No evidence is
    constructed locally and grounding validation is never bypassed.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        # The client is constructed lazily on first use so that the provider
        # can be selected (and unit-tested) without configured credentials.
        # A missing key surfaces at call time and returns ``None``, matching
        # the Ollama provider's failure contract.
        self._api_key = api_key or settings.openai_api_key or None
        self._model = model or settings.openai_model
        self._client = None

    def _get_client(self):
        if self._client is None:
            import openai

            self._client = openai.OpenAI(api_key=self._api_key)
        return self._client

    def generate_grounded_json(
        self, evidence_by_category: dict[str, list[dict[str, object]]]
    ) -> dict | None:
        import openai

        evidence = _format_evidence(evidence_by_category)
        prompt = _GROUNDED_PROMPT_TEMPLATE.format(evidence=evidence)
        try:
            client = self._get_client()
            completion = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            content = completion.choices[0].message.content
            if not content:
                return None
            result = json.loads(content)
            return result if isinstance(result, dict) else None
        except (openai.OpenAIError, json.JSONDecodeError, ValueError, KeyError, IndexError):
            return None


class _HostedProviderStub:
    """Placeholder for a future hosted LLM provider.

    The Phase 14 abstraction is intentionally minimal: we only need to
    prove that swapping ``ai_provider`` in configuration selects a
    different backend.  Actual hosted-provider integration is future work.
    """

    def __init__(self, provider_name: str) -> None:
        self.provider_name = provider_name

    def generate_grounded_json(
        self, evidence_by_category: dict[str, list[dict[str, object]]]
    ) -> dict | None:
        # The default local behaviour (regex-based analysis) already covers
        # the case where an LLM provider is unavailable or unconfigured.
        return None


def get_llm_provider(name: str | None = None) -> LLMProvider:
    """Factory that returns the configured LLM provider.

    ``AI_PROVIDER`` defaults to ``ollama`` for local development.  Any
    unrecognised provider name returns the stub so that the application
    continues to function with its deterministic fallback.
    """
    name = (name or settings.ai_provider or "ollama").lower()
    if name == "ollama":
        return OllamaProvider()
    if name == "openai":
        return OpenAIProvider()
    # Hosted providers (anthropic, etc.) are future work.
    # For now, return the stub which degrades gracefully to the
    # deterministic analysis path.
    return _HostedProviderStub(name)


def generate_grounded_json(
    evidence_by_category: dict[str, list[dict[str, object]]]
) -> dict | None:
    """Backward-compatible module-level function.

    Delegates to the configured provider.  Existing callers in
    ``analysis_service.py`` are unchanged.
    """
    return get_llm_provider().generate_grounded_json(evidence_by_category)