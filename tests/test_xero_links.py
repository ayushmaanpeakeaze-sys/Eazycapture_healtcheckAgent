"""Xero deep-links: the org-login ``redirecturl`` is passed RAW (per Xero's own
docs). Encoding the inner ``?`` to ``%3F`` breaks the redirect and bounces the
user to the "My Xero" org chooser.
"""
from urllib.parse import parse_qs, unquote, urlparse

from app.modules.healthcheck.xero_links import xero_deep_link

_ID = "6e74d1a5-e95b-406d-8d96-7d8752fa388e"
_SC = "!S9bXm"


def _redirect_target(url: str) -> str:
    """Decode the redirecturl back to the path Xero will actually open."""
    qs = parse_qs(urlparse(url).query)
    return unquote(qs["redirecturl"][0])


def test_bill_link_keeps_inner_query_raw():
    url = xero_deep_link("ACCPAY", _ID, _SC)
    assert "%3F" not in url and "%3D" not in url
    assert f"redirecturl=/AccountsPayable/View.aspx?InvoiceID={_ID}" in url
    assert url.count("?") == 2
    assert parse_qs(urlparse(url).query)["shortcode"][0] == _SC
    assert _redirect_target(url) == f"/AccountsPayable/View.aspx?InvoiceID={_ID}"


def test_invoice_link_routes_to_accounts_receivable():
    assert _redirect_target(xero_deep_link("ACCREC", _ID, _SC)) == \
        f"/AccountsReceivable/View.aspx?InvoiceID={_ID}"


def test_credit_note_routes_to_credit_notes():
    assert _redirect_target(xero_deep_link("ACCPAYCREDIT", _ID, _SC)) == \
        f"/CreditNotes/View.aspx?creditNoteID={_ID}"


def test_bank_transaction_routes_to_bank_view():
    url = xero_deep_link("SPEND", _ID, _SC)
    assert url.count("?") == 2
    assert _redirect_target(url) == f"/Bank/ViewTransaction.aspx?bankTransactionID={_ID}"


def test_bank_account_routes_to_reconcile_screen():
    url = xero_deep_link("BANK", _ID, _SC)
    assert url.count("?") == 2
    assert _redirect_target(url) == f"/Bank/BankRec.aspx?accountID={_ID}"
    assert "AccountSearch" not in url


def test_no_shortcode_falls_back_to_direct_path():
    # Without a shortcode there is no redirecturl wrapper, so a raw '?' is fine.
    url = xero_deep_link("ACCREC", _ID, None)
    assert url == f"https://go.xero.com/AccountsReceivable/View.aspx?InvoiceID={_ID}"


def test_no_document_id_returns_none():
    assert xero_deep_link("ACCREC", None, _SC) is None
    assert xero_deep_link("ACCREC", "  ", _SC) is None
