"""Extract the Net Assets / (Liabilities) figure from a UK iXBRL accounts
document (as filed at Companies House).

Filed accounts are *iXBRL* — XHTML with embedded XBRL facts tagged as
``<ix:nonFraction name="..." contextRef="..." ...>VALUE</ix:nonFraction>``.
The balance-sheet date lives in the referenced ``<xbrli:context>`` as an
``<xbrli:instant>``. We pull every net-assets-ish fact, resolve its
period-end date, apply the iXBRL transforms (``sign`` / ``scale`` /
``decimals``), and return ``{period_end: net_assets}``.

Stdlib only (``xml.etree``) — no lxml/bs4 dependency. Companies House iXBRL
is well-formed XHTML, so the standard XML parser handles it.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

logger = logging.getLogger("eazycapture.companies_house.ixbrl")

# XBRL concept local-names that represent "Net Assets / (Liabilities)".
# Order matters only for the preference rule below — the canonical
# ``NetAssetsLiabilities`` always wins over equity-style fallbacks.
_PRIMARY_CONCEPT = "NetAssetsLiabilities"
_NET_ASSET_CONCEPTS: tuple[str, ...] = (
    _PRIMARY_CONCEPT,
    "NetAssetsLiabilitiesIncludingPensionAssetLiability",
    "NetAssetsLiabilitiesSubtotal",
    # Equity-side fallbacks — for a solvent company these equal net assets.
    "Equity",
    "ShareholdersFunds",
    "TotalShareholdersFunds",
    "TotalEquity",
    "CapitalAndReserves",
)


def _local(tag: str) -> str:
    """Strip the ``{namespace}`` prefix ElementTree prepends to tags."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _to_decimal(text: str) -> Decimal | None:
    cleaned = re.sub(r"[,\s ]", "", text or "")
    cleaned = cleaned.replace("(", "-").replace(")", "")  # (1,234) → -1234
    if cleaned in ("", "-", "."):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _context_dates(root: ET.Element) -> dict[str, str]:
    """Map every ``context id`` → its period-end (instant) date.

    For a *duration* context we use the end date; for an *instant* context
    (the usual balance-sheet shape) we use the instant.
    """
    out: dict[str, str] = {}
    for ctx in root.iter():
        if _local(ctx.tag) != "context":
            continue
        cid = ctx.get("id")
        if not cid:
            continue
        instant = end = None
        for e in ctx.iter():
            ln = _local(e.tag)
            if ln == "instant":
                instant = (e.text or "").strip()
            elif ln == "endDate":
                end = (e.text or "").strip()
        date = instant or end
        if date:
            out[cid] = date
    return out


def extract_net_assets(ixbrl: bytes | str) -> dict[str, Decimal]:
    """Return ``{period_end (YYYY-MM-DD): net_assets}`` for every period the
    document tags. Empty dict if the document is not parseable iXBRL or has
    no net-assets fact (e.g. a PDF-only or untagged filing).
    """
    if isinstance(ixbrl, str):
        ixbrl = ixbrl.encode("utf-8")
    try:
        root = ET.fromstring(ixbrl)
    except ET.ParseError as exc:
        logger.warning("iXBRL not XML-parseable: %s", exc)
        return {}

    ctx_date = _context_dates(root)
    # date -> (concept_name, value); keep the best concept per date.
    best: dict[str, tuple[str, Decimal]] = {}

    for el in root.iter():
        if _local(el.tag) != "nonFraction":
            continue
        concept = (el.get("name") or "").split(":")[-1]
        if concept not in _NET_ASSET_CONCEPTS:
            continue
        value = _to_decimal(el.text or "")
        if value is None:
            continue
        scale = el.get("scale")
        if scale:
            try:
                value *= Decimal(10) ** int(scale)
            except (ValueError, InvalidOperation):
                pass
        if (el.get("sign") or "").strip() == "-":
            value = -value
        date = ctx_date.get(el.get("contextRef", ""))
        if not date:
            continue
        prev = best.get(date)
        # Prefer the canonical NetAssetsLiabilities; otherwise first-seen wins.
        if prev is None or (concept == _PRIMARY_CONCEPT and prev[0] != _PRIMARY_CONCEPT):
            best[date] = (concept, value)

    return {date: val for date, (_, val) in best.items()}
