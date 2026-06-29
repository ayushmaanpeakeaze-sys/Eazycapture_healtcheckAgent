"""Bank Balance Check — review annotations (notes + supporting documents).

Internal EazyCapture data, NOT Xero: an accountant's notes and uploaded files
(bank statements, reconciliation spreadsheets) against ONE bank account at ONE
period end. Provides the "Add Note" + "Upload Supporting Documentation" workflow.

File bytes are stored in the DB (statements are small). Swap for
object storage when volumes grow.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.healthcheck.models import BankDocument, BankNote

logger = logging.getLogger("eazycapture.bank_annotations")

# Reject uploads larger than this (DB-blob storage; keep it sane).
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


class BankAnnotationsService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ---------------- notes ----------------

    async def add_note(
        self,
        company_id: UUID,
        account_code: str,
        period_end: str,
        body: str,
        *,
        author_user_id: Optional[UUID],
        tagged_user_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        note = BankNote(
            company_id=company_id,
            account_code=account_code.strip(),
            period_end=period_end.strip(),
            author_user_id=author_user_id,
            body=body.strip(),
            tagged_user_ids=[str(u) for u in (tagged_user_ids or [])] or None,
        )
        self._db.add(note)
        await self._db.commit()
        await self._db.refresh(note)
        return _note_dict(note)

    async def list_notes(
        self, company_id: UUID, account_code: str, period_end: str,
    ) -> list[dict[str, Any]]:
        rows = (
            await self._db.execute(
                select(BankNote)
                .where(
                    BankNote.company_id == company_id,
                    BankNote.account_code == account_code.strip(),
                    BankNote.period_end == period_end.strip(),
                )
                .order_by(BankNote.created_at.desc())
            )
        ).scalars().all()
        return [_note_dict(n) for n in rows]

    async def delete_note(self, company_id: UUID, note_id: UUID) -> bool:
        res = await self._db.execute(
            delete(BankNote).where(
                BankNote.id == note_id, BankNote.company_id == company_id,
            )
        )
        await self._db.commit()
        return bool(res.rowcount)

    # ---------------- documents ----------------

    async def upload_document(
        self,
        company_id: UUID,
        account_code: str,
        period_end: str,
        *,
        filename: str,
        content_type: str,
        content: bytes,
        uploaded_by: Optional[UUID],
    ) -> dict[str, Any]:
        doc = BankDocument(
            company_id=company_id,
            account_code=account_code.strip(),
            period_end=period_end.strip(),
            filename=filename,
            content_type=content_type or "application/octet-stream",
            size_bytes=len(content),
            content=content,
            uploaded_by=uploaded_by,
        )
        self._db.add(doc)
        await self._db.commit()
        await self._db.refresh(doc)
        return _doc_dict(doc)

    async def list_documents(
        self, company_id: UUID, account_code: str, period_end: str,
    ) -> list[dict[str, Any]]:
        # Metadata only — never load the bytes for a list.
        rows = (
            await self._db.execute(
                select(
                    BankDocument.id, BankDocument.filename,
                    BankDocument.content_type, BankDocument.size_bytes,
                    BankDocument.uploaded_by, BankDocument.created_at,
                )
                .where(
                    BankDocument.company_id == company_id,
                    BankDocument.account_code == account_code.strip(),
                    BankDocument.period_end == period_end.strip(),
                )
                .order_by(BankDocument.created_at.desc())
            )
        ).all()
        return [
            {
                "id": str(r.id),
                "filename": r.filename,
                "content_type": r.content_type,
                "size_bytes": r.size_bytes,
                "uploaded_by": str(r.uploaded_by) if r.uploaded_by else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    async def get_document(
        self, company_id: UUID, doc_id: UUID,
    ) -> Optional[BankDocument]:
        return (
            await self._db.execute(
                select(BankDocument).where(
                    BankDocument.id == doc_id,
                    BankDocument.company_id == company_id,
                )
            )
        ).scalar_one_or_none()

    async def delete_document(self, company_id: UUID, doc_id: UUID) -> bool:
        res = await self._db.execute(
            delete(BankDocument).where(
                BankDocument.id == doc_id, BankDocument.company_id == company_id,
            )
        )
        await self._db.commit()
        return bool(res.rowcount)

    async def counts(
        self, company_id: UUID, account_code: str, period_end: str,
    ) -> dict[str, int]:
        """Note + document counts for one account+period (for the check UI)."""
        from sqlalchemy import func

        notes = await self._db.scalar(
            select(func.count()).select_from(BankNote).where(
                BankNote.company_id == company_id,
                BankNote.account_code == account_code.strip(),
                BankNote.period_end == period_end.strip(),
            )
        )
        docs = await self._db.scalar(
            select(func.count()).select_from(BankDocument).where(
                BankDocument.company_id == company_id,
                BankDocument.account_code == account_code.strip(),
                BankDocument.period_end == period_end.strip(),
            )
        )
        return {"notes": int(notes or 0), "documents": int(docs or 0)}


def _note_dict(n: BankNote) -> dict[str, Any]:
    return {
        "id": str(n.id),
        "account_code": n.account_code,
        "period_end": n.period_end,
        "author_user_id": str(n.author_user_id) if n.author_user_id else None,
        "body": n.body,
        "tagged_user_ids": n.tagged_user_ids or [],
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


def _doc_dict(d: BankDocument) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "account_code": d.account_code,
        "period_end": d.period_end,
        "filename": d.filename,
        "content_type": d.content_type,
        "size_bytes": d.size_bytes,
        "uploaded_by": str(d.uploaded_by) if d.uploaded_by else None,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


__all__ = ["BankAnnotationsService", "MAX_UPLOAD_BYTES"]
