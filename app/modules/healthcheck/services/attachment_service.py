"""Undocumented-Bills actions: re-check a document's attachment status in Xero,
and upload a file as an attachment. Both drop the row from the issue list on
success (the bill is now documented).
"""
from __future__ import annotations

import base64
import logging
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.healthcheck.models import Company
from app.modules.healthcheck.repository import HealthCheckResultRepository
from app.modules.healthcheck.schemas import (
    RecheckAttachmentResponse,
    UploadAttachmentResponse,
)
from app.modules.integrations.service import IntegrationService

logger = logging.getLogger("eazycapture.attachment_service")


def _extract_has_attachments(doc: object) -> bool:
    """Read HasAttachments off a single-document Xero response
    ({"Invoices": [{...}]} or {"BankTransactions": [{...}]})."""
    if not isinstance(doc, dict):
        return False
    for key in ("Invoices", "BankTransactions"):
        arr = doc.get(key)
        if isinstance(arr, list) and arr and isinstance(arr[0], dict):
            return bool(arr[0].get("HasAttachments"))
    return False


class AttachmentService:
    def __init__(self, db: AsyncSession, integration: Optional[IntegrationService] = None) -> None:
        self._db = db
        self._repo = HealthCheckResultRepository(db)
        self._integration = integration or IntegrationService()

    async def _load(self, row_id: UUID, company_id: UUID):
        row = await self._repo.find_by_id(row_id, company_id)
        if row is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Trapped row not found for this company.",
            )
        company = await self._db.get(Company, company_id)
        conn = getattr(company, "nango_connection_id", None)
        tenant = getattr(company, "xero_tenant_id", None)
        return row, conn, tenant

    async def recheck(self, *, row_id: UUID, company_id: UUID) -> RecheckAttachmentResponse:
        """"Check Again": re-fetch the document and, if it now has an attachment,
        resolve the issue."""
        row, conn, tenant = await self._load(row_id, company_id)
        if not self._integration.is_connected(conn, tenant):
            return RecheckAttachmentResponse(
                row_id=row.id, attached=False, resolved=False, stub=True)
        try:
            doc = await self._integration.fetch_attachable(
                conn, tenant, row.document_type, str(row.document_id))
        except Exception:
            logger.exception("attachment re-check failed row=%s", row_id)
            raise HTTPException(
                status_code=http_status.HTTP_502_BAD_GATEWAY,
                detail="Could not re-check the document in Xero.",
            )
        if _extract_has_attachments(doc):
            await self._repo.mark_resolved(
                row.id, company_id,
                resolution_notes="Attachment found on re-check.")
            await self._db.commit()
            return RecheckAttachmentResponse(row_id=row.id, attached=True, resolved=True)
        return RecheckAttachmentResponse(row_id=row.id, attached=False, resolved=False)

    async def upload(
        self, *, row_id: UUID, company_id: UUID,
        filename: str, content_type: str, content_base64: str,
    ) -> UploadAttachmentResponse:
        """Upload a file to the Xero document and resolve the issue on success."""
        row, conn, tenant = await self._load(row_id, company_id)
        try:
            content = base64.b64decode(content_base64, validate=True)
        except Exception:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="content_base64 is not valid base64.",
            )
        if not content:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="The uploaded file is empty.",
            )
        if not self._integration.is_connected(conn, tenant):
            return UploadAttachmentResponse(
                row_id=row.id, uploaded=False, resolved=False,
                filename=filename, stub=True)
        result = await self._integration.upload_attachment(
            conn, tenant, row.document_type, str(row.document_id),
            filename, content, content_type)
        if not result:
            raise HTTPException(
                status_code=http_status.HTTP_502_BAD_GATEWAY,
                detail="Xero rejected the attachment upload.",
            )
        await self._repo.mark_resolved(
            row.id, company_id,
            resolution_notes=f"Attachment '{filename}' uploaded.",
            xero_response=result if isinstance(result, dict) else None)
        await self._db.commit()
        return UploadAttachmentResponse(
            row_id=row.id, uploaded=True, resolved=True, filename=filename)
