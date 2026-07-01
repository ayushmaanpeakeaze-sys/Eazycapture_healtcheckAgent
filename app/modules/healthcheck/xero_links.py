"""Build deep-links into the Xero web UI for trapped documents.

Used by the trapped feed so the frontend can render an "Open in Xero"
button per row that works regardless of document type.

We use Xero's **officially-documented** deep-link method exclusively:

    https://go.xero.com/organisationlogin/default.aspx
        ?shortcode={shortcode}&redirecturl={classic_view_path}

This does two things the bare new-UI ``/app/{shortcode}/...`` paths could
not do reliably:

1. **Forces the correct tenant.** An accountant logged into many orgs
   would otherwise land on whichever org is active in their session and
   see a "document not found" page.
2. **Resolves to whatever UI now renders the document.** Xero maintains
   the classic ``…/View.aspx`` pages as stable redirect targets and
   forwards them to the current UI — including DRAFT documents — so we
   never need view-vs-edit branching or to track new-UI route names
   (``/invoicing/``, ``/bills/`` etc.) that differ per document type and
   404 when wrong.

When we don't yet have the org's shortcode on file we fall back to the
same classic path without the org-forcing wrapper — it relies on the
active session org, but still resolves the document.
"""
from __future__ import annotations

from typing import Optional, Union
from uuid import UUID

_BASE = "https://go.xero.com"
_BASE_ORGLOGIN = "https://go.xero.com/organisationlogin/default.aspx"

# Classic per-type view paths. Each resolves drafts + approved docs alike,
# so no view/edit branching is needed.
_PATH_AR = "/AccountsReceivable/View.aspx?InvoiceID={id}"     # sales invoice
_PATH_AP = "/AccountsPayable/View.aspx?InvoiceID={id}"        # bill
_PATH_CN = "/CreditNotes/View.aspx?creditNoteID={id}"         # credit note (sales OR purchase)
_PATH_CONTACT = "/Contacts/View.aspx?contactID={id}"         # contact
_PATH_BANK = "/Bank/ViewTransaction.aspx?bankTransactionID={id}"  # money in/out (RECEIVE/SPEND)
_PATH_BANK_ACCOUNT = "/Bank/BankRec.aspx?accountID={id}"     # bank ACCOUNT reconcile screen
_PATH_SEARCH = "/Account/AccountSearch.aspx?searchTerm={id}"  # unknown type fallback


def xero_deep_link(
    document_type: Optional[str],
    document_id: Union[str, UUID, None],
    xero_shortcode: Optional[str] = None,
    invoice_status: Optional[str] = None,  # retained for call-site compat; unused
) -> Optional[str]:
    """Return a deep-link to the matching Xero document, or ``None`` if we
    don't have an id.

    All document types route through the documented organisationlogin
    redirect (when a shortcode is known) so the link always opens the
    right document in the right org. ``invoice_status`` is no longer
    needed — the classic view pages handle drafts — but the parameter is
    kept so existing call sites don't have to change.
    """
    if document_id is None or str(document_id).strip() == "":
        return None
    doc_id = str(document_id).strip()
    kind = (document_type or "").strip().upper()
    shortcode = (xero_shortcode or "").strip() or None

    # Credit notes first — "ACCRECCREDIT" also starts with "ACCREC".
    if kind in {"ACCRECCREDIT", "ACCPAYCREDIT"}:
        path = _PATH_CN.format(id=doc_id)
    elif kind.startswith("ACCREC"):
        path = _PATH_AR.format(id=doc_id)
    elif kind.startswith("ACCPAY"):
        path = _PATH_AP.format(id=doc_id)
    elif kind == "CONTACT":
        path = _PATH_CONTACT.format(id=doc_id)
    elif kind == "BANK":                         # bank ACCOUNT → reconcile screen
        path = _PATH_BANK_ACCOUNT.format(id=doc_id)
    elif kind in {"RECEIVE", "SPEND"}:           # a single bank transaction (money in/out)
        path = _PATH_BANK.format(id=doc_id)
    else:
        path = _PATH_SEARCH.format(id=doc_id)

    if shortcode:
        return f"{_BASE_ORGLOGIN}?shortcode={shortcode}&redirecturl={path}"
    return f"{_BASE}{path}"
