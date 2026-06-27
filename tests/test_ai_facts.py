"""The AI grounding contract (app/modules/ai/facts.py).

`build_row_facts` is the ONLY data the LLM sees for a flagged row, so it must:
- carry the row's real figures (vendor, amount, account, status),
- use UK contact nouns (supplier vs customer),
- pass the COMPLETE flagged detail (not aggressively truncated → the model can't
  invent the missing bit).
Pure + testable — no LLM, no I/O.
"""
from app.modules.ai.facts import (
    build_row_facts,
    contact_noun,
    extract_doc_type,
    _FLAGGED_DETAIL_MAX_CHARS,
)
from app.modules.ai.schemas import TrappedRow


def test_contact_noun_uk_terminology():
    assert contact_noun("ACCPAY") == "supplier"
    assert contact_noun("ACCPAYCREDIT") == "supplier"
    assert contact_noun("ACCREC") == "customer"
    assert contact_noun("ACCRECCREDIT") == "customer"
    assert contact_noun(None) == "contact"
    assert contact_noun("SPEND") == "contact"


def test_extract_doc_type_handles_key_variants():
    assert extract_doc_type({"type": "accpay"}) == "ACCPAY"
    assert extract_doc_type({"DocumentType": "ACCREC"}) == "ACCREC"
    assert extract_doc_type({}) is None
    assert extract_doc_type("not a dict") is None


def test_build_row_facts_carries_real_figures():
    row = TrappedRow(
        transaction_id="t1",
        rule_ids=["duplicate_bill"],
        messages="ABC Ltd: duplicate bill DUP-99",
        transaction={
            "type": "ACCPAY", "vendor_name": "ABC Ltd", "amount": "500.00",
            "currency_code": "GBP", "current_account_code": "710",
            "invoice_number": "DUP-99", "status": "AUTHORISED",
        },
        flagged_items=[{"issue_type": "duplicate_bill", "match_reasons": {"same_reference": True}}],
    )
    f = build_row_facts(row, 0)
    assert f["id"] == 0
    assert f["doc_type"] == "ACCPAY"
    assert f["contact_noun"] == "supplier"        # ACCPAY → supplier
    assert f["vendor"] == "ABC Ltd"
    assert f["amount"] == "500.00"
    assert f["account_code"] == "710"
    assert f["invoice_number"] == "DUP-99"
    assert f["status"] == "AUTHORISED"
    assert f["rule_ids"] == ["duplicate_bill"]
    # business_impact + recommended_action pulled from the deterministic template
    assert f["business_impact"] and f["recommended_action"]


def test_build_row_facts_passes_complete_flagged_detail():
    # A small flagged_items list must reach the model STRUCTURED + intact — not
    # collapsed to a 400-char string like the old behaviour.
    detail = [{"issue_type": "low_cost_fixed_asset", "match_reasons": {
        "account_code": "710", "account_name": "Office Equipment",
        "line_amount": "1000.00", "threshold": "10000.00"}}]
    row = TrappedRow(transaction_id="t2", rule_ids=["low_cost_fixed_asset"],
                     transaction={"type": "ACCPAY"}, flagged_items=detail)
    f = build_row_facts(row, 1)
    assert f["flagged_detail"] == detail          # structured + complete, not a string


def test_build_row_facts_trims_only_pathological_detail():
    # A huge payload is capped (token-budget guard) — but only then.
    big = [{"k": "x" * (_FLAGGED_DETAIL_MAX_CHARS + 500)}]
    row = TrappedRow(transaction_id="t3", rule_ids=["x"],
                     transaction={"type": "ACCREC"}, flagged_items=big)
    f = build_row_facts(row, 0)
    assert isinstance(f["flagged_detail"], str)   # fell back to truncated string
    assert len(f["flagged_detail"]) == _FLAGGED_DETAIL_MAX_CHARS


def test_build_row_facts_defaults_when_data_missing():
    row = TrappedRow(transaction_id="t4", rule_ids=[], transaction={})
    f = build_row_facts(row, 0)
    assert f["vendor"] == "Unknown"
    assert f["amount"] == "unknown"
    assert f["account_code"] == "unknown"
    assert f["contact_noun"] == "contact"
