"""Thin httpx client for the Companies House public API.

Auth is HTTP Basic with the API key as the username and an empty password.
The API is free (register at developer.company-information.service.gov.uk).
When no key is configured the client is *disabled* and every call returns
None — callers fall back to manually-entered figures.

Two hosts are involved:
  * ``COMPANIES_HOUSE_BASE_URL``     — company profile + filing history (JSON)
  * ``COMPANIES_HOUSE_DOCUMENT_URL`` — the Document API that serves the actual
    iXBRL/PDF accounts content
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("hcpoc.companies_house.client")

# iXBRL is served as XHTML; this is the content type the Document API uses.
IXBRL_ACCEPT = "application/xhtml+xml"
JSON_ACCEPT = "application/json"


class CompaniesHouseClient:
    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key if api_key is not None else settings.COMPANIES_HOUSE_API_KEY
        self._base_url = settings.COMPANIES_HOUSE_BASE_URL.rstrip("/")
        self._doc_url = settings.COMPANIES_HOUSE_DOCUMENT_URL.rstrip("/")

    def is_enabled(self) -> bool:
        return bool(self._api_key)

    @property
    def _auth(self) -> tuple[str, str]:
        # Basic auth: key as username, blank password.
        return (self._api_key, "")

    async def get_filing_history(
        self,
        company_number: str,
        *,
        category: str = "accounts",
        items_per_page: int = 100,
    ) -> Optional[dict[str, Any]]:
        """``GET /company/{number}/filing-history`` — the list of filings.
        Returns the raw JSON body (``{"items": [...]}``) or None."""
        if not self.is_enabled():
            return None
        url = f"{self._base_url}/company/{company_number}/filing-history"
        params = {"category": category, "items_per_page": str(items_per_page)}
        try:
            async with httpx.AsyncClient(timeout=20.0) as http:
                resp = await http.get(
                    url, params=params, auth=self._auth,
                    headers={"Accept": JSON_ACCEPT},
                )
        except httpx.HTTPError as exc:
            logger.warning("CH filing-history transport error %s: %s", company_number, exc)
            return None
        if resp.status_code == 404:
            logger.info("CH company %s not found / no filings", company_number)
            return None
        if resp.status_code >= 400:
            logger.warning("CH filing-history HTTP %s for %s", resp.status_code, company_number)
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    async def get_document_content(
        self,
        document_metadata_url: str,
        *,
        accept: str = IXBRL_ACCEPT,
    ) -> Optional[bytes]:
        """Fetch the actual accounts document content (iXBRL bytes).

        ``document_metadata_url`` comes from a filing's
        ``links.document_metadata``. Content lives at ``{that}/content``.
        """
        if not self.is_enabled():
            return None
        # Normalise: the metadata link may be absolute or just a document id.
        if document_metadata_url.startswith("http"):
            base = document_metadata_url.rstrip("/")
        else:
            base = f"{self._doc_url}/document/{document_metadata_url.strip('/')}"
        url = f"{base}/content"
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
                resp = await http.get(
                    url, auth=self._auth, headers={"Accept": accept},
                )
        except httpx.HTTPError as exc:
            logger.warning("CH document transport error %s: %s", url, exc)
            return None
        if resp.status_code >= 400:
            logger.warning("CH document HTTP %s for %s", resp.status_code, url)
            return None
        return resp.content
