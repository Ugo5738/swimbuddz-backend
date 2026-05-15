"""Admin collection endpoints (collections + collection-product membership)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.store_service.models import Collection, CollectionProduct, Product
from services.store_service.schemas import (
    CollectionCreate,
    CollectionResponse,
    CollectionUpdate,
    CollectionWithProducts,
    ProductResponse,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

router = APIRouter()


@router.get("/collections", response_model=list[CollectionResponse])
async def list_all_collections(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all collections."""
    query = select(Collection).order_by(Collection.sort_order, Collection.name)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/collections/{collection_id}", response_model=CollectionWithProducts)
async def get_collection(
    collection_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get a single collection with its products."""
    query = (
        select(Collection)
        .where(Collection.id == collection_id)
        .options(
            selectinload(Collection.collection_products).selectinload(
                CollectionProduct.product
            )
        )
    )
    result = await db.execute(query)
    collection = result.scalar_one_or_none()
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    products = [
        cp.product
        for cp in sorted(collection.collection_products, key=lambda x: x.sort_order)
    ]

    return CollectionWithProducts(
        id=collection.id,
        name=collection.name,
        slug=collection.slug,
        description=collection.description,
        image_media_id=collection.image_media_id,
        is_active=collection.is_active,
        sort_order=collection.sort_order,
        created_at=collection.created_at,
        updated_at=collection.updated_at,
        products=[ProductResponse.model_validate(p) for p in products],
    )


@router.post(
    "/collections",
    response_model=CollectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_collection(
    collection_in: CollectionCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new collection."""
    collection = Collection(**collection_in.model_dump())
    db.add(collection)
    await db.commit()
    await db.refresh(collection)
    return collection


@router.patch("/collections/{collection_id}", response_model=CollectionResponse)
async def update_collection(
    collection_id: uuid.UUID,
    collection_in: CollectionUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a collection."""
    query = select(Collection).where(Collection.id == collection_id)
    result = await db.execute(query)
    collection = result.scalar_one_or_none()

    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    update_data = collection_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(collection, field, value)

    await db.commit()
    await db.refresh(collection)
    return collection


@router.post(
    "/collections/{collection_id}/products/{product_id}",
    status_code=status.HTTP_201_CREATED,
)
async def add_product_to_collection(
    collection_id: uuid.UUID,
    product_id: uuid.UUID,
    sort_order: int = 0,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Add a product to a collection."""
    # Verify both exist
    collection = await db.get(Collection, collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Check if already in collection
    existing = await db.execute(
        select(CollectionProduct).where(
            CollectionProduct.collection_id == collection_id,
            CollectionProduct.product_id == product_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Product already in collection")

    cp = CollectionProduct(
        collection_id=collection_id,
        product_id=product_id,
        sort_order=sort_order,
    )
    db.add(cp)
    await db.commit()

    return {"message": "Product added to collection"}


@router.delete(
    "/collections/{collection_id}/products/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_product_from_collection(
    collection_id: uuid.UUID,
    product_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Remove a product from a collection."""
    query = select(CollectionProduct).where(
        CollectionProduct.collection_id == collection_id,
        CollectionProduct.product_id == product_id,
    )
    result = await db.execute(query)
    cp = result.scalar_one_or_none()

    if not cp:
        raise HTTPException(status_code=404, detail="Product not in collection")

    await db.delete(cp)
    await db.commit()
    return None
