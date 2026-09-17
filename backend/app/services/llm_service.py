"""LLM service — backward-compatible module-level entry point.

Phase 14 introduced an AI-provider abstraction in
:mod:`backend.app.services.ai_provider`.  This module re-exports the
factory-provided function so that existing callers (e.g.
``analysis_service.py``) are unchanged.
"""

from .ai_provider import generate_grounded_json, get_llm_provider

__all__ = ["generate_grounded_json", "get_llm_provider"]
