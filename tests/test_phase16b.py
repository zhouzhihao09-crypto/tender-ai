import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.app.config import settings
from backend.app.services.ai_provider import OpenAIProvider, get_llm_provider


def _import_config(env_extra: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_extra)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    return subprocess.run(
        [sys.executable, "-c", "import backend.app.config"],
        env=env, capture_output=True, text=True,
    )


def test_ollama_provider_does_not_require_openai_key_in_production() -> None:
    """AI_PROVIDER=ollama must be valid in production without OPENAI_API_KEY."""
    result = _import_config({
        "APP_ENVIRONMENT": "production",
        "AI_PROVIDER": "ollama",
        "OPENAI_API_KEY": "",
        "AUTH_REQUIRED": "true",
        "AUTH_SECRET": "prod-secret-123",
        "ALLOWED_ORIGINS": "http://example.com",
        "ALLOWED_HOSTS": "example.com",
        "DATABASE_URL": "sqlite:///:memory:",
    })
    assert result.returncode == 0, result.stderr


def test_openai_provider_requires_key_in_production() -> None:
    """AI_PROVIDER=openai without OPENAI_API_KEY must fail startup."""
    result = _import_config({
        "APP_ENVIRONMENT": "production",
        "AI_PROVIDER": "openai",
        "OPENAI_API_KEY": "",
        "AUTH_REQUIRED": "true",
        "AUTH_SECRET": "prod-secret-123",
        "ALLOWED_ORIGINS": "http://example.com",
        "ALLOWED_HOSTS": "example.com",
        "DATABASE_URL": "sqlite:///:memory:",
    })
    assert result.returncode != 0
    assert "OPENAI_API_KEY must be set when AI_PROVIDER=openai" in result.stderr


def test_openai_provider_with_key_loads_in_production() -> None:
    """AI_PROVIDER=openai with a key must load cleanly in production."""
    result = _import_config({
        "APP_ENVIRONMENT": "production",
        "AI_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-test-placeholder-123",
        "OPENAI_MODEL": "gpt-4o-mini",
        "AUTH_REQUIRED": "true",
        "AUTH_SECRET": "prod-secret-123",
        "ALLOWED_ORIGINS": "http://example.com",
        "ALLOWED_HOSTS": "example.com",
        "DATABASE_URL": "sqlite:///:memory:",
    })
    assert result.returncode == 0, result.stderr


def test_openai_model_is_configurable() -> None:
    provider = OpenAIProvider(model="gpt-4o")
    assert provider._model == "gpt-4o"


def test_openai_provider_selection_does_not_leak_key_into_error() -> None:
    """A provider failure must not expose the configured API key."""
    import openai

    provider = OpenAIProvider(api_key="test-not-a-real-openai-key", model="gpt-4o-mini")
    provider._client = MagicMock()
    provider._client.chat.completions.create.side_effect = openai.AuthenticationError(
        message="Invalid API key", response=MagicMock(), body=None
    )
    result = provider.generate_grounded_json({"dates": []})
    assert result is None


def test_openai_provider_returns_none_when_key_unconfigured() -> None:
    """With no key configured, calling the provider returns None safely."""
    provider = OpenAIProvider(api_key=None, model="gpt-4o-mini")
    provider._api_key = None
    assert provider.generate_grounded_json({"dates": []}) is None