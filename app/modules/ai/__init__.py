"""EazyCapture AI module — everything LLM-touching lives here.

New here? Read ``README.md`` in this folder first. Quick map:

  client.py          — the Groq LLM client (get_groq / close_groq)
  schemas.py         — Pydantic request/response models for AI
  templates.py       — deterministic so-what/solution per issue type (NO LLM)
  insight_service.py — per-row enrichment: facts → LLM → cached insight
  checks_llm.py      — LLM-based CHECKS (wrong_category, anomaly)
  router.py          — the /api/v1 enrichment + insight HTTP endpoints

Import the light helpers straight from the package; import the heavier
services by their module path (``from app.modules.ai.insight_service import …``).
"""
from app.modules.ai.client import close_groq, get_groq
from app.modules.ai.templates import get_context

__all__ = ["get_groq", "close_groq", "get_context"]
