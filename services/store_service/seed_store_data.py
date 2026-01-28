"""Seed script for store test data.

Creates sample categories, products with variants, inventory, and pickup locations
so you can test the checkout flow end-to-end.

Usage:
    cd swimbuddz-backend
    python -m services.store_service.seed_store_data
"""

import asyncio
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from libs.common.config import get_settings
from services.store_service.models import (
    Category,
    Collection,
    CollectionProduct,
    InventoryItem,
    PickupLocation,
    Product,
    ProductImage,
    ProductStatus,
    ProductVariant,
    SourcingType,
)

settings = get_settings()

# Use the same DB URL as the app
DATABASE_URL = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

engine = create_async_engine(DATABASE_URL, echo=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def seed_store_data():
    async with AsyncSessionLocal() as db:
        print("Seeding store data...")

        # Check if data already exists
        existing = await db.execute(text("SELECT COUNT(*) FROM store_categories"))
        count = existing.scalar()
        if count and count > 0:
            print(f"Store data already exists ({count} categories). Skipping seed.")
            return

        # =========================================================================
        # 1. CATEGORIES
        # =========================================================================
        categories = {
            "goggles": Category(
                name="Goggles",
                slug="goggles",
                description="Swimming goggles for training and competition",
                sort_order=1,
            ),
            "caps": Category(
                name="Swim Caps",
                slug="caps",
                description="Silicone and latex swim caps",
                sort_order=2,
            ),
            "training": Category(
                name="Training Equipment",
                slug="training",
                description="Kickboards, pull buoys, fins, and paddles",
                sort_order=3,
            ),
            "swimwear": Category(
                name="Swimwear",
                slug="swimwear",
                description="Swimsuits and jammers for men and women",
                sort_order=4,
            ),
            "accessories": Category(
                name="Accessories",
                slug="accessories",
                description="Towels, bags, and other swimming accessories",
                sort_order=5,
            ),
        }
        db.add_all(categories.values())
        await db.flush()

        # =========================================================================
        # 2. PRODUCTS
        # =========================================================================
        products = []

        # --- Goggles ---
        speedo_goggles = Product(
            name="Speedo Vanquisher 2.0",
            slug="speedo-vanquisher-2",
            category_id=categories["goggles"].id,
            description="The Speedo Vanquisher 2.0 offers crystal-clear vision and a comfortable, leak-free fit. Perfect for lap swimming and training.",
            short_description="Premium training goggles with anti-fog coating",
            base_price_ngn=Decimal("15000"),
            compare_at_price_ngn=Decimal("18000"),
            status=ProductStatus.ACTIVE,
            is_featured=True,
            sourcing_type=SourcingType.STOCKED,
        )
        products.append(speedo_goggles)

        arena_goggles = Product(
            name="Arena Cobra Ultra Swipe",
            slug="arena-cobra-ultra-swipe",
            category_id=categories["goggles"].id,
            description="Racing goggles with innovative anti-fog technology. Swipe to restore anti-fog properties.",
            short_description="Competition racing goggles",
            base_price_ngn=Decimal("35000"),
            status=ProductStatus.ACTIVE,
            is_featured=True,
            sourcing_type=SourcingType.STOCKED,
        )
        products.append(arena_goggles)

        # --- Caps ---
        silicone_cap = Product(
            name="SwimBuddz Silicone Cap",
            slug="swimbuddz-silicone-cap",
            category_id=categories["caps"].id,
            description="Premium silicone swim cap with SwimBuddz logo. Durable, comfortable, and long-lasting.",
            short_description="Official SwimBuddz swim cap",
            base_price_ngn=Decimal("5000"),
            status=ProductStatus.ACTIVE,
            is_featured=True,
            sourcing_type=SourcingType.STOCKED,
        )
        products.append(silicone_cap)

        # --- Training Equipment ---
        kickboard = Product(
            name="Premium Kickboard",
            slug="premium-kickboard",
            category_id=categories["training"].id,
            description="High-density EVA foam kickboard for leg training. Comfortable grip and excellent buoyancy.",
            short_description="Essential training tool for leg strength",
            base_price_ngn=Decimal("8000"),
            status=ProductStatus.ACTIVE,
            sourcing_type=SourcingType.STOCKED,
        )
        products.append(kickboard)

        pull_buoy = Product(
            name="Pull Buoy",
            slug="pull-buoy",
            category_id=categories["training"].id,
            description="Ergonomic pull buoy for upper body focused training. Improves arm technique and core stability.",
            short_description="Upper body training essential",
            base_price_ngn=Decimal("6500"),
            status=ProductStatus.ACTIVE,
            sourcing_type=SourcingType.STOCKED,
        )
        products.append(pull_buoy)

        fins = Product(
            name="Training Fins",
            slug="training-fins",
            category_id=categories["training"].id,
            description="Short blade training fins for improved kick technique and ankle flexibility.",
            short_description="Short blade training fins",
            base_price_ngn=Decimal("18000"),
            status=ProductStatus.ACTIVE,
            has_variants=True,
            variant_options={
                "Size": ["S (35-36)", "M (37-38)", "L (39-40)", "XL (41-42)"]
            },
            sourcing_type=SourcingType.STOCKED,
        )
        products.append(fins)

        # --- Swimwear ---
        mens_jammer = Product(
            name="Men's Training Jammer",
            slug="mens-training-jammer",
            category_id=categories["swimwear"].id,
            description="Chlorine-resistant training jammer with comfortable fit. Ideal for regular training sessions.",
            short_description="Durable training jammer",
            base_price_ngn=Decimal("12000"),
            status=ProductStatus.ACTIVE,
            has_variants=True,
            variant_options={"Size": ["S", "M", "L", "XL", "XXL"]},
            requires_size_chart_ack=True,
            size_chart_url="https://example.com/size-chart-mens.png",
            sourcing_type=SourcingType.STOCKED,
        )
        products.append(mens_jammer)

        womens_suit = Product(
            name="Women's One-Piece Swimsuit",
            slug="womens-one-piece-swimsuit",
            category_id=categories["swimwear"].id,
            description="Stylish and comfortable one-piece swimsuit for training. Quick-dry fabric with UV protection.",
            short_description="Comfortable training swimsuit",
            base_price_ngn=Decimal("15000"),
            status=ProductStatus.ACTIVE,
            has_variants=True,
            variant_options={"Size": ["XS", "S", "M", "L", "XL"]},
            requires_size_chart_ack=True,
            size_chart_url="https://example.com/size-chart-womens.png",
            sourcing_type=SourcingType.STOCKED,
        )
        products.append(womens_suit)

        # --- Accessories ---
        mesh_bag = Product(
            name="Mesh Swim Bag",
            slug="mesh-swim-bag",
            category_id=categories["accessories"].id,
            description="Ventilated mesh bag for wet gear. Large capacity with drawstring closure.",
            short_description="Breathable gear bag",
            base_price_ngn=Decimal("4500"),
            status=ProductStatus.ACTIVE,
            sourcing_type=SourcingType.STOCKED,
        )
        products.append(mesh_bag)

        towel = Product(
            name="Microfiber Sports Towel",
            slug="microfiber-sports-towel",
            category_id=categories["accessories"].id,
            description="Quick-dry microfiber towel. Compact and lightweight, perfect for swimming.",
            short_description="Quick-dry compact towel",
            base_price_ngn=Decimal("7000"),
            status=ProductStatus.ACTIVE,
            sourcing_type=SourcingType.STOCKED,
        )
        products.append(towel)

        db.add_all(products)
        await db.flush()

        # =========================================================================
        # 3. PRODUCT VARIANTS & INVENTORY
        # =========================================================================
        variants = []

        # Simple products (no size variants) - one default variant each
        simple_products = [
            speedo_goggles,
            arena_goggles,
            silicone_cap,
            kickboard,
            pull_buoy,
            mesh_bag,
            towel,
        ]
        for product in simple_products:
            variant = ProductVariant(
                product_id=product.id,
                sku=f"{product.slug.upper().replace('-', '')}-DEFAULT",
                name="Default",
            )
            variants.append(variant)

        # Fins - size variants
        fin_sizes = ["S (35-36)", "M (37-38)", "L (39-40)", "XL (41-42)"]
        for size in fin_sizes:
            size_code = size.split()[0]
            variant = ProductVariant(
                product_id=fins.id,
                sku=f"TRAINFINS-{size_code}",
                name=size,
                options={"Size": size},
            )
            variants.append(variant)

        # Men's Jammer - size variants
        jammer_sizes = ["S", "M", "L", "XL", "XXL"]
        for size in jammer_sizes:
            variant = ProductVariant(
                product_id=mens_jammer.id,
                sku=f"MENSJAMMER-{size}",
                name=size,
                options={"Size": size},
            )
            variants.append(variant)

        # Women's Swimsuit - size variants
        womens_sizes = ["XS", "S", "M", "L", "XL"]
        for size in womens_sizes:
            variant = ProductVariant(
                product_id=womens_suit.id,
                sku=f"WOMENSSUIT-{size}",
                name=size,
                options={"Size": size},
            )
            variants.append(variant)

        db.add_all(variants)
        await db.flush()

        # Create inventory for all variants
        inventory_items = []
        for variant in variants:
            inv = InventoryItem(
                variant_id=variant.id,
                quantity_on_hand=10,  # Start with 10 of each
                low_stock_threshold=3,
            )
            inventory_items.append(inv)
        db.add_all(inventory_items)

        # =========================================================================
        # 4. PRODUCT IMAGES (placeholder URLs)
        # =========================================================================
        images = [
            ProductImage(
                product_id=speedo_goggles.id,
                url="https://placehold.co/600x600/0066cc/white?text=Speedo+Goggles",
                alt_text="Speedo Vanquisher 2.0 Goggles",
                is_primary=True,
            ),
            ProductImage(
                product_id=arena_goggles.id,
                url="https://placehold.co/600x600/ff6600/white?text=Arena+Cobra",
                alt_text="Arena Cobra Ultra Swipe",
                is_primary=True,
            ),
            ProductImage(
                product_id=silicone_cap.id,
                url="https://placehold.co/600x600/00cc66/white?text=Swim+Cap",
                alt_text="SwimBuddz Silicone Cap",
                is_primary=True,
            ),
            ProductImage(
                product_id=kickboard.id,
                url="https://placehold.co/600x600/ffcc00/black?text=Kickboard",
                alt_text="Premium Kickboard",
                is_primary=True,
            ),
            ProductImage(
                product_id=pull_buoy.id,
                url="https://placehold.co/600x600/cc0066/white?text=Pull+Buoy",
                alt_text="Pull Buoy",
                is_primary=True,
            ),
            ProductImage(
                product_id=fins.id,
                url="https://placehold.co/600x600/6600cc/white?text=Training+Fins",
                alt_text="Training Fins",
                is_primary=True,
            ),
            ProductImage(
                product_id=mens_jammer.id,
                url="https://placehold.co/600x600/000066/white?text=Mens+Jammer",
                alt_text="Men's Training Jammer",
                is_primary=True,
            ),
            ProductImage(
                product_id=womens_suit.id,
                url="https://placehold.co/600x600/cc0099/white?text=Womens+Suit",
                alt_text="Women's One-Piece Swimsuit",
                is_primary=True,
            ),
            ProductImage(
                product_id=mesh_bag.id,
                url="https://placehold.co/600x600/666666/white?text=Mesh+Bag",
                alt_text="Mesh Swim Bag",
                is_primary=True,
            ),
            ProductImage(
                product_id=towel.id,
                url="https://placehold.co/600x600/00cccc/white?text=Towel",
                alt_text="Microfiber Sports Towel",
                is_primary=True,
            ),
        ]
        db.add_all(images)

        # =========================================================================
        # 5. PICKUP LOCATIONS
        # =========================================================================
        pickup_locations = [
            PickupLocation(
                name="Rowe Park Pool",
                address="Rowe Park, Yaba, Lagos",
                description="Tue-Sun: 6am-8pm. Collect from reception desk. Show order confirmation.",
                contact_phone="+234 800 000 0001",
                sort_order=1,
            ),
            PickupLocation(
                name="Lekki Swimming Center",
                address="123 Admiralty Way, Lekki Phase 1, Lagos",
                description="Mon-Sun: 7am-7pm. Ask for the store manager at the front desk.",
                contact_phone="+234 800 000 0002",
                sort_order=2,
            ),
            PickupLocation(
                name="VI Sports Complex",
                address="Victoria Island, Lagos",
                description="Mon-Sat: 8am-6pm. Available during all training sessions.",
                contact_phone="+234 800 000 0003",
                sort_order=3,
            ),
        ]
        db.add_all(pickup_locations)

        # =========================================================================
        # 6. FEATURED COLLECTION
        # =========================================================================
        featured_collection = Collection(
            name="New Arrivals",
            slug="new-arrivals",
            description="Check out our latest swimming gear!",
            sort_order=1,
        )
        db.add(featured_collection)
        await db.flush()

        # Add featured products to collection
        featured_products = [speedo_goggles, arena_goggles, silicone_cap]
        for i, product in enumerate(featured_products):
            cp = CollectionProduct(
                collection_id=featured_collection.id,
                product_id=product.id,
                sort_order=i,
            )
            db.add(cp)

        await db.commit()
        print("=" * 60)
        print("Store data seeded successfully!")
        print("=" * 60)
        print(f"  Categories: {len(categories)}")
        print(f"  Products: {len(products)}")
        print(f"  Variants: {len(variants)}")
        print(f"  Pickup Locations: {len(pickup_locations)}")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(seed_store_data())
