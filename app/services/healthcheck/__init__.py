"""Health-check engine — deterministic rules + LLM detection, split by concern.

* shared.py            — constants + context/COA helpers
* deterministic.py     — non-LLM rules (duplicates, tax, ageing, etc.)
* llm_rules.py         — LLM detection (wrong_category, capital review)
* invoice_firewall.py  — validate_invoice (pre-ledger single document)
* orchestrator.py      — run_batch_health_check (ties it together)
"""
from app.services.healthcheck.invoice_firewall import validate_invoice
from app.services.healthcheck.orchestrator import run_batch_health_check

__all__ = ["validate_invoice", "run_batch_health_check"]
