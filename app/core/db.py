"""Async SQLAlchemy engine + session factory + FastAPI dependency.

Used by the DB-backed routes under /api/v1/health/*. The existing
AI service endpoints (validate-invoice, health-check/batch, etc.) are
stateless and do not import from here.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)

from app.core.config import settings, sync_database_url


class Base(DeclarativeBase):
    """Declarative base shared by every model in the project."""


def uuid_pk() -> Mapped[uuid.UUID]:
    """Standard UUID primary-key column. Lives in the shared ORM layer so
    every module's models reuse the one pattern instead of redefining it."""
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    future=True,
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a session, rolls back on error."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Close all pooled connections — call on app shutdown."""
    await engine.dispose()


# Sync session for Celery workers, whose prefork model doesn't share
# asyncio event loops cleanly. Uses the psycopg driver against the same
# Postgres; the async engine above remains canonical for FastAPI.

sync_engine: Engine = create_engine(
    sync_database_url(),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    future=True,
)

SyncSessionLocal: sessionmaker[Session] = sessionmaker(
    bind=sync_engine,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
    class_=Session,
)
