"""Canonical pagination primitives shared across services.

Three pieces:

* :class:`PaginationParams` — a FastAPI dependency that captures ``page`` /
  ``page_size`` query params with sane bounds. Use as a router dependency
  to avoid duplicating the same ``Query(1, ge=1)`` boilerplate per route.
* :class:`PaginatedResponse` — the canonical wire shape for paginated
  responses (``items``, ``total``, ``page``, ``page_size``). Generic over
  the row type ``T`` so each service can specialise it.
* :func:`paginate` — async helper that executes a SQLAlchemy query against
  an :class:`AsyncSession` and returns ``(items, total)`` in one call.

Migration note: a handful of older services use ad-hoc shapes (e.g.
``{transactions, skip, limit}`` in wallet_service). Those stay as-is for
wire-compat with the frontend; new endpoints should use the canonical
shape, and existing endpoints should migrate when next touched.
"""

from __future__ import annotations

from typing import Generic, Sequence, Tuple, TypeVar

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

T = TypeVar("T")


class PaginationParams(BaseModel):
    """Captured ``?page=&page_size=`` query params with sane bounds."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


def pagination_params(
    page: int = Query(1, ge=1, description="1-indexed page number"),
    page_size: int = Query(
        20, ge=1, le=100, description="Items per page (max 100)"
    ),
) -> PaginationParams:
    """FastAPI dependency form of :class:`PaginationParams`.

    Usage::

        from libs.common.pagination import PaginationParams, pagination_params

        @router.get("/items", response_model=PaginatedResponse[ItemResponse])
        async def list_items(
            pagination: PaginationParams = Depends(pagination_params),
            db: AsyncSession = Depends(get_async_db),
        ):
            ...
    """
    return PaginationParams(page=page, page_size=page_size)


class PaginatedResponse(BaseModel, Generic[T]):
    """Canonical wire shape for paginated list endpoints."""

    items: list[T]
    total: int
    page: int
    page_size: int

    model_config = ConfigDict(from_attributes=True)


async def paginate(
    db: AsyncSession,
    base_query: Select,
    pagination: PaginationParams,
) -> Tuple[Sequence, int]:
    """Run ``base_query`` paginated and return ``(items, total)``.

    Issues two SQL queries:

    1. A ``COUNT(*)`` over the un-paginated subquery for ``total``.
    2. The paginated query (``base_query`` + offset/limit) for ``items``.

    Caller is responsible for shaping ``items`` into response models and
    wrapping them in :class:`PaginatedResponse`.
    """
    total_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(total_query)).scalar_one()

    page_query = base_query.offset(pagination.offset).limit(pagination.limit)
    rows = (await db.execute(page_query)).scalars().all()
    return rows, int(total)


__all__ = [
    "PaginatedResponse",
    "PaginationParams",
    "pagination_params",
    "paginate",
]
