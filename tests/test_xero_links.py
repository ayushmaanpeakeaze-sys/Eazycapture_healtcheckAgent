"""Xero deep-links: the org-login wrapper's ``redirecturl`` value carries its
own ``?InvoiceID=…`` query, so it MUST be percent-encoded — otherwise Xero
parses the inner ``?``/``=`` as sibling params, drops the document id, and
bounces the user to the "My Xero" org chooser instead of the document.
"""
from urllib.parse import parse_qs, unquote, urlparse

from app.modules.healthcheck.xero_links import xero_deep_link

_ID = "6e74d1a5-e95b-406d-8d96-7d8752fa388e"
_SC = "!S9bXm"


def _redirect_target(url: str) -> str:
    """Decode the redirecturl back to the path Xero will actually open."""
    qs = parse_qs(urlparse(url).query)
    return unquote(qs["redirecturl"][0])


def test_bill_link_encodes_inner_query():
    url = xero_deep_link("ACCPAY", _ID, _SC)
    # Exactly one raw '?' (the outer query) — the inner one is encoded.
    assert url.count("?") == 1
    assert "%3FInvoiceID%3D" in url
    # Shortcode is preserved literally (incl. the leading '!').
    assert parse_qs(urlparse(url).query)["shortcode"][0] == _SC
    # And the redirect decodes back to the real bill path.
    assert _redirect_target(url) == f"/AccountsPayable/View.aspx?InvoiceID={_ID}"


def test_invoice_link_routes_to_accounts_receivable():
    assert _redirect_target(xero_deep_link("ACCREC", _ID, _SC)) == \
        f"/AccountsReceivable/View.aspx?InvoiceID={_ID}"


def test_credit_note_routes_to_credit_notes():
    assert _redirect_target(xero_deep_link("ACCPAYCREDIT", _ID, _SC)) == \
        f"/CreditNotes/View.aspx?creditNoteID={_ID}"


def test_bank_transaction_routes_to_bank_view():
    # Money In / Money Out (RECEIVE / SPEND) → the bank view, also encoded.
    url = xero_deep_link("SPEND", _ID, _SC)
    assert url.count("?") == 1
    assert _redirect_target(url) == f"/Bank/ViewTransaction.aspx?bankTransactionID={_ID}"


def test_no_shortcode_falls_back_to_direct_path():
    # Without a shortcode there is no redirecturl wrapper, so a raw '?' is fine.
    url = xero_deep_link("ACCREC", _ID, None)
    assert url == f"https://go.xero.com/AccountsReceivable/View.aspx?InvoiceID={_ID}"


def test_no_document_id_returns_none():
    assert xero_deep_link("ACCREC", None, _SC) is None
    assert xero_deep_link("ACCREC", "  ", _SC) is None
