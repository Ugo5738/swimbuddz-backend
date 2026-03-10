#!/usr/bin/env python3
"""Flexible, idempotent seed script for the SwimBuddz store.

Modes:
    Default (upsert):  Add missing data, skip existing records (matched by slug).
    --fresh:           Wipe ALL store data and reseed from scratch.

Options:
    --fresh            Truncate all store tables before seeding.
    --yes              Skip confirmation prompt for destructive operations.
    --verbose          Show detailed output for every record.

Usage:
    # First time or add missing data (safe to run repeatedly)
    python scripts/seed/store_service.py

    # Nuclear reset - wipe everything and start fresh
    python scripts/seed/store_service.py --fresh --yes

    # See detailed output
    python scripts/seed/store_service.py --verbose
"""

import argparse
import asyncio
import os
import sys
from decimal import Decimal

# Add project root to path
project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, project_root)

from libs.db.config import AsyncSessionLocal
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
    Supplier,
    SupplierStatus,
)
from sqlalchemy import select, text

# =============================================================================
# DATA DEFINITIONS
# =============================================================================

SUPPLIER_DATA = {
    "name": "SwimBuddz",
    "slug": "swimbuddz",
    "contact_name": "SwimBuddz Team",
    "contact_email": "store@swimbuddz.com",
    "description": "SwimBuddz internal supplier. All first-party products.",
    "commission_percent": "0",
}

CATEGORIES_DATA = [
    {
        "key": "swimwear",
        "name": "Swimwear",
        "slug": "swimwear",
        "description": (
            "Clothing designed specifically for swimming and water activities, "
            "built for comfort, flexibility, and durability in the pool or open water."
        ),
        "sort_order": 1,
    },
    {
        "key": "swim_gear",
        "name": "Swim Gear",
        "slug": "swim-gear",
        "description": (
            "Essential swimming gear used during training and regular pool "
            "sessions, helping swimmers stay comfortable and perform better "
            "in the water."
        ),
        "sort_order": 2,
    },
    {
        "key": "training",
        "name": "Training Equipment",
        "slug": "training-equipment",
        "description": (
            "Specialized swim training tools designed to improve technique, "
            "strength, and endurance during structured swim practice."
        ),
        "sort_order": 3,
    },
    {
        "key": "safety",
        "name": "Pool & Water Safety",
        "slug": "pool-water-safety",
        "description": (
            "Safety products that support confident and secure swimming "
            "for beginners, children, and recreational swimmers."
        ),
        "sort_order": 4,
    },
    {
        "key": "towels",
        "name": "Towels & Changing",
        "slug": "towels-changing",
        "description": (
            "Practical essentials designed to keep swimmers dry and "
            "comfortable before and after pool sessions."
        ),
        "sort_order": 5,
    },
    {
        "key": "bags",
        "name": "Bags & Storage",
        "slug": "bags-storage",
        "description": (
            "Bags designed to carry and organize swim gear, keeping "
            "items ventilated and easy to transport."
        ),
        "sort_order": 6,
    },
    {
        "key": "sun_protection",
        "name": "Sun Protection",
        "slug": "sun-protection",
        "description": (
            "Protective sun care products and UV-rated clothing for "
            "swimmers training outdoors or poolside."
        ),
        "sort_order": 7,
    },
    {
        "key": "kids",
        "name": "Kids & Learn-to-Swim",
        "slug": "kids-learn-to-swim",
        "description": (
            "Products designed to support children and beginner swimmers "
            "as they build confidence and learn essential water safety skills."
        ),
        "sort_order": 8,
    },
    {
        "key": "maintenance",
        "name": "Maintenance & Care",
        "slug": "maintenance-care",
        "description": (
            "Products that help maintain and extend the life of swim gear and swimwear."
        ),
        "sort_order": 9,
    },
]

# ---------------------------------------------------------------------------
# PRODUCTS — 46 products across 9 categories, all PREORDER
# ---------------------------------------------------------------------------
PRODUCTS_DATA = [
    # ===== SWIMWEAR =====
    {
        "name": "Men's Training Jammer",
        "slug": "mens-training-jammer",
        "category_key": "swimwear",
        "description": (
            "Chlorine-resistant training jammer built for regular pool sessions. "
            "Offers a comfortable, streamlined fit with durable fabric that "
            "holds its shape through months of training."
        ),
        "short_description": "Durable chlorine-resistant training jammer",
        "base_price_ngn": "17000",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["S", "M", "L", "XL", "XXL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-001",
        "preorder_lead_days": 14,
        "image_seed": "jammer",
    },
    {
        "name": "Women's One-Piece Swimsuit",
        "slug": "womens-one-piece-swimsuit",
        "category_key": "swimwear",
        "description": (
            "Stylish and comfortable one-piece swimsuit for training and "
            "fitness swimming. Quick-dry fabric with built-in UV protection "
            "and a flattering athletic cut."
        ),
        "short_description": "Comfortable training swimsuit with UV protection",
        "base_price_ngn": "20000",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["XS", "S", "M", "L", "XL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-002",
        "preorder_lead_days": 14,
        "image_seed": "swimsuit",
    },
    {
        "name": "Men's Swim Briefs",
        "slug": "mens-swim-briefs",
        "category_key": "swimwear",
        "description": (
            "Classic competitive swim briefs for training and racing. "
            "Lightweight, minimal drag design with a secure internal drawcord."
        ),
        "short_description": "Classic competitive swim briefs",
        "base_price_ngn": "15000",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["S", "M", "L", "XL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-003",
        "preorder_lead_days": 14,
        "image_seed": "briefs",
    },
    {
        "name": "Women's Two-Piece Swimsuit",
        "slug": "womens-two-piece-swimsuit",
        "category_key": "swimwear",
        "description": (
            "Sporty two-piece swimsuit designed for training and leisure. "
            "Secure fit with adjustable straps and chlorine-resistant fabric."
        ),
        "short_description": "Sporty two-piece for training and leisure",
        "base_price_ngn": "18000",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["XS", "S", "M", "L", "XL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-004",
        "preorder_lead_days": 14,
        "image_seed": "twopieceswim",
    },
    {
        "name": "Men's Board Shorts",
        "slug": "mens-board-shorts",
        "category_key": "swimwear",
        "description": (
            "Versatile quick-dry board shorts for casual swimming and "
            "poolside wear. Comfortable elastic waistband with mesh lining."
        ),
        "short_description": "Quick-dry swim shorts for casual swimming",
        "base_price_ngn": "15000",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["S", "M", "L", "XL", "XXL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-005",
        "preorder_lead_days": 14,
        "image_seed": "boardshorts",
    },
    {
        "name": "Long Sleeve Rash Guard",
        "slug": "long-sleeve-rash-guard",
        "category_key": "swimwear",
        "description": (
            "UPF 50+ long sleeve rash guard for sun protection during outdoor "
            "swimming. Flatlock seams prevent chafing. Quick-dry stretch fabric."
        ),
        "short_description": "UV-protective long sleeve swim shirt",
        "base_price_ngn": "15000",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["S", "M", "L", "XL", "XXL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-006",
        "preorder_lead_days": 14,
        "image_seed": "rashguard",
    },
    {
        "name": "Kids' One-Piece Swimsuit",
        "slug": "kids-one-piece-swimsuit",
        "category_key": "swimwear",
        "description": (
            "Colourful and durable one-piece swimsuit for kids. "
            "Chlorine-resistant fabric with a comfortable fit for "
            "swim lessons and pool play."
        ),
        "short_description": "Durable kids' swimsuit for lessons and play",
        "base_price_ngn": "10000",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["4", "6", "8", "10", "12"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-007",
        "preorder_lead_days": 14,
        "image_seed": "kidswimsuit",
    },
    # ===== SWIM GEAR =====
    {
        "name": "Speedo Vanquisher 2.0",
        "slug": "speedo-vanquisher-2",
        "category_key": "swim_gear",
        "description": (
            "Crystal-clear vision with a comfortable, leak-free fit. "
            "Features anti-fog coating and UV protection, perfect for "
            "lap swimming and training."
        ),
        "short_description": "Premium training goggles with anti-fog coating",
        "base_price_ngn": "18000",
        "compare_at_price_ngn": "20000",
        "is_featured": True,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-GER-001",
        "preorder_lead_days": 14,
        "image_seed": "goggles1",
    },
    {
        "name": "Swimming Goggles Anti-Fog UV Protection",
        "slug": "swimming-goggles-anti-fog-uv-protection",
        "category_key": "swim_gear",
        "description": (
            "Comfortable swimming goggles with anti-fog coated lenses and "
            "UV protection. Wide-vision design with soft silicone gaskets "
            "for a leak-free seal. Adjustable split strap fits all head sizes."
        ),
        "short_description": "Anti-fog UV protection swim goggles",
        "base_price_ngn": "10000",
        "compare_at_price_ngn": "12000",
        "is_featured": True,
        "has_variants": True,
        "variant_options": {"Colour": ["Black", "Blue", "Clear", "Pink"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-GER-002",
        "preorder_lead_days": 14,
        "image_seed": "goggles2",
    },
    {
        "name": "Arena Cobra Ultra Swipe",
        "slug": "arena-cobra-ultra-swipe",
        "category_key": "swim_gear",
        "description": (
            "Competition racing goggles with innovative anti-fog technology. "
            "Swipe the inner lens to restore anti-fog properties. Low-profile "
            "hydrodynamic design for minimal drag."
        ),
        "short_description": "Competition racing goggles",
        "base_price_ngn": "35000",
        "is_featured": True,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-GER-003",
        "preorder_lead_days": 21,
        "image_seed": "goggles3",
    },
    {
        "name": "SwimBuddz Silicone Cap",
        "slug": "swimbuddz-silicone-cap",
        "category_key": "swim_gear",
        "description": (
            "Premium silicone swim cap with the SwimBuddz logo. Durable, "
            "comfortable, and designed to reduce drag. Fits all head sizes."
        ),
        "short_description": "Official SwimBuddz branded swim cap",
        "base_price_ngn": "5000",
        "is_featured": True,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-GER-004",
        "preorder_lead_days": 14,
        "image_seed": "cap1",
    },
    {
        "name": "Latex Swim Cap",
        "slug": "latex-swim-cap",
        "category_key": "swim_gear",
        "description": (
            "Lightweight latex swim cap for everyday pool use. Thin, "
            "stretchy, and affordable. Available in multiple colours."
        ),
        "short_description": "Lightweight latex pool cap",
        "base_price_ngn": "4000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-GER-005",
        "preorder_lead_days": 14,
        "image_seed": "latexcap",
    },
    {
        "name": "Nose Clip & Ear Plug Set",
        "slug": "nose-clip-ear-plug-set",
        "category_key": "swim_gear",
        "description": (
            "Comfortable nose clip and soft silicone ear plugs in a "
            "convenient carry case. Keeps water out during training "
            "and recreational swimming."
        ),
        "short_description": "Essential comfort accessories set",
        "base_price_ngn": "4000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-GER-006",
        "preorder_lead_days": 14,
        "image_seed": "noseclip",
    },
    {
        "name": "Junior Swimming Goggles",
        "slug": "junior-swimming-goggles",
        "category_key": "swim_gear",
        "description": (
            "Colourful, leak-free goggles sized for young swimmers aged "
            "6-12. Anti-fog lenses with easy-adjust split strap for a "
            "secure fit."
        ),
        "short_description": "Leak-free goggles for young swimmers",
        "base_price_ngn": "8000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-GER-007",
        "preorder_lead_days": 14,
        "image_seed": "jrgoggles",
    },
    # ===== TRAINING EQUIPMENT =====
    {
        "name": "Premium Kickboard",
        "slug": "premium-kickboard",
        "category_key": "training",
        "description": (
            "High-density EVA foam kickboard for focused leg training. "
            "Ergonomic shape with comfortable grip edges. Excellent "
            "buoyancy for swimmers of all levels."
        ),
        "short_description": "High-density EVA foam kickboard",
        "base_price_ngn": "8000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-001",
        "preorder_lead_days": 14,
        "image_seed": "kickboard",
    },
    {
        "name": "Pull Buoy",
        "slug": "pull-buoy",
        "category_key": "training",
        "description": (
            "Ergonomic figure-eight pull buoy for upper body focused "
            "training. Isolates the arms to build stroke power while "
            "improving core stability."
        ),
        "short_description": "Ergonomic upper body training tool",
        "base_price_ngn": "6500",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-002",
        "preorder_lead_days": 14,
        "image_seed": "pullbuoy",
    },
    {
        "name": "Training Fins",
        "slug": "training-fins",
        "category_key": "training",
        "description": (
            "Short blade training fins designed to improve kick technique "
            "and ankle flexibility without over-relying on fin propulsion. "
            "Closed-heel design with comfortable foot pocket."
        ),
        "short_description": "Short blade fins for kick technique",
        "base_price_ngn": "20000",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {
            "Size": ["S (35-36)", "M (37-38)", "L (39-40)", "XL (41-42)"]
        },
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-TRN-003",
        "preorder_lead_days": 21,
        "image_seed": "fins1",
    },
    {
        "name": "Hand Paddles",
        "slug": "hand-paddles",
        "category_key": "training",
        "description": (
            "Contoured hand paddles that increase resistance and build "
            "upper body strength. Adjustable straps for a secure, "
            "customisable fit. Reinforces proper catch technique."
        ),
        "short_description": "Contoured paddles for stroke power",
        "base_price_ngn": "9000",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["S", "M", "L"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-004",
        "preorder_lead_days": 14,
        "image_seed": "paddles",
    },
    {
        "name": "Centre Snorkel",
        "slug": "centre-snorkel",
        "category_key": "training",
        "description": (
            "Front-mount centre snorkel for focused stroke training. "
            "Eliminates the need to turn for breath, allowing swimmers "
            "to concentrate on body position and arm technique."
        ),
        "short_description": "Front-mount training snorkel",
        "base_price_ngn": "15000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-005",
        "preorder_lead_days": 14,
        "image_seed": "snorkel",
    },
    {
        "name": "Resistance Band Set",
        "slug": "resistance-band-set",
        "category_key": "training",
        "description": (
            "Set of three swim-specific resistance bands (light, medium, "
            "heavy) for dryland training. Includes door anchor and exercise "
            "guide. Builds swim-specific strength and flexibility."
        ),
        "short_description": "Dryland swim-specific resistance bands",
        "base_price_ngn": "9000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-006",
        "preorder_lead_days": 14,
        "image_seed": "bands",
    },
    {
        "name": "Swim Parachute",
        "slug": "swim-parachute",
        "category_key": "training",
        "description": (
            "Drag resistance parachute that attaches to the waist for "
            "sprint and power training. Adjustable belt with quick-release "
            "buckle. Builds explosive speed when removed."
        ),
        "short_description": "Drag resistance trainer for speed work",
        "base_price_ngn": "25000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-007",
        "preorder_lead_days": 21,
        "image_seed": "parachute",
    },
    {
        "name": "Tempo Trainer Pro",
        "slug": "tempo-trainer-pro",
        "category_key": "training",
        "description": (
            "Waterproof audible pace trainer that clips under the swim cap. "
            "Set your target stroke tempo and the device beeps at precise "
            "intervals. Essential for pace discipline and stroke rate control."
        ),
        "short_description": "Audible pace trainer for stroke tempo",
        "base_price_ngn": "22000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-008",
        "preorder_lead_days": 21,
        "image_seed": "tempotrainer",
    },
    # ===== POOL & WATER SAFETY =====
    {
        "name": "Adult Life Jacket",
        "slug": "adult-life-jacket",
        "category_key": "safety",
        "description": (
            "Coast-guard approved adult flotation vest for open water "
            "safety. Adjustable straps, reflective panels, and whistle "
            "included. Suitable for pool beginners and open water swimming."
        ),
        "short_description": "Approved adult flotation vest",
        "base_price_ngn": "20000",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["S/M", "L/XL"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-SAF-001",
        "preorder_lead_days": 14,
        "image_seed": "lifejacket",
    },
    {
        "name": "Kids Life Jacket",
        "slug": "kids-life-jacket",
        "category_key": "safety",
        "description": (
            "Bright-coloured kids safety jacket with secure buckle closures. "
            "Designed for children learning to swim or playing near water. "
            "Meets safety certification standards."
        ),
        "short_description": "Bright-coloured kids safety jacket",
        "base_price_ngn": "15000",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["S (15-25kg)", "M (25-35kg)", "L (35-50kg)"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-SAF-002",
        "preorder_lead_days": 14,
        "image_seed": "kidslifejacket",
    },
    {
        "name": "Swim Noodle",
        "slug": "swim-noodle",
        "category_key": "safety",
        "description": (
            "Flexible high-density foam pool noodle for flotation support "
            "and fun. Can be used for swim training drills, water aerobics, "
            "or recreational play."
        ),
        "short_description": "Flexible foam pool noodle",
        "base_price_ngn": "3500",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-SAF-003",
        "preorder_lead_days": 14,
        "image_seed": "noodle",
    },
    {
        "name": "Kids Arm Band Floats",
        "slug": "kids-arm-band-floats",
        "category_key": "safety",
        "description": (
            "Inflatable arm bands for beginner swimmers and children. "
            "Double air chamber for safety with bright colours for "
            "easy visibility."
        ),
        "short_description": "Inflatable arm bands for beginners",
        "base_price_ngn": "7000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-SAF-004",
        "preorder_lead_days": 14,
        "image_seed": "armbands",
    },
    {
        "name": "Puddle Jumper Kids Float",
        "slug": "puddle-jumper-kids-float",
        "category_key": "safety",
        "description": (
            "Full-torso flotation device that combines arm bands and a "
            "chest float in one piece. Secure buckle closure at the back. "
            "Keeps young children upright and safe in the water."
        ),
        "short_description": "Full-torso flotation for toddlers",
        "base_price_ngn": "8000",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["S (14-23kg)", "L (23-30kg)"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-SAF-005",
        "preorder_lead_days": 14,
        "image_seed": "puddlejumper",
    },
    # ===== TOWELS & CHANGING =====
    {
        "name": "Microfiber Sports Towel",
        "slug": "microfiber-sports-towel",
        "category_key": "towels",
        "description": (
            "Ultra-absorbent, quick-dry microfiber towel that packs down "
            "small. Lightweight and compact, perfect for fitting in any "
            "swim bag."
        ),
        "short_description": "Quick-dry compact microfiber towel",
        "base_price_ngn": "7000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TWL-001",
        "preorder_lead_days": 14,
        "image_seed": "towel1",
    },
    {
        "name": "Shammy Towel",
        "slug": "shammy-towel",
        "category_key": "towels",
        "description": (
            "Super-absorbent PVA chamois towel used by competitive swimmers. "
            "Wring it out and it's ready to absorb again. Comes in a "
            "protective storage tube."
        ),
        "short_description": "Super absorbent PVA chamois towel",
        "base_price_ngn": "5000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TWL-002",
        "preorder_lead_days": 14,
        "image_seed": "shammy",
    },
    {
        "name": "Changing Robe",
        "slug": "changing-robe",
        "category_key": "towels",
        "description": (
            "Hooded towelling poncho for poolside changing. Provides "
            "privacy and warmth after training. Oversized fit makes "
            "changing easy."
        ),
        "short_description": "Hooded poncho for poolside changing",
        "base_price_ngn": "25000",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["S/M", "L/XL"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TWL-003",
        "preorder_lead_days": 21,
        "image_seed": "changingrobe",
    },
    {
        "name": "SwimBuddz Beach Towel",
        "slug": "swimbuddz-beach-towel",
        "category_key": "towels",
        "description": (
            "Oversized cotton beach towel with the SwimBuddz logo. "
            "Soft, absorbent, and perfect for pool meets and beach days."
        ),
        "short_description": "Oversized branded cotton beach towel",
        "base_price_ngn": "10000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TWL-004",
        "preorder_lead_days": 14,
        "image_seed": "beachtowel",
    },
    # ===== BAGS & STORAGE =====
    {
        "name": "Mesh Swim Bag",
        "slug": "mesh-swim-bag",
        "category_key": "bags",
        "description": (
            "Ventilated mesh bag for carrying wet swim gear. Large capacity "
            "with drawstring closure and shoulder strap. Allows gear to "
            "air-dry on the go."
        ),
        "short_description": "Ventilated drawstring gear bag",
        "base_price_ngn": "4500",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-BAG-001",
        "preorder_lead_days": 14,
        "image_seed": "meshbag",
    },
    {
        "name": "Waterproof Swim Backpack",
        "slug": "waterproof-swim-backpack",
        "category_key": "bags",
        "description": (
            "35-litre waterproof backpack with a dedicated wet compartment, "
            "padded laptop sleeve, and ventilated shoe pocket. Built for "
            "swimmers who go straight from the pool to work or school."
        ),
        "short_description": "35L backpack with wet compartment",
        "base_price_ngn": "16000",
        "is_featured": True,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-BAG-002",
        "preorder_lead_days": 21,
        "image_seed": "swimbackpack",
    },
    {
        "name": "Wet/Dry Bag",
        "slug": "wet-dry-bag",
        "category_key": "bags",
        "description": (
            "Dual-compartment bag that separates wet swimwear from dry "
            "items. Waterproof lining in the wet section prevents leaks."
        ),
        "short_description": "Dual compartment wet and dry bag",
        "base_price_ngn": "6000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-BAG-003",
        "preorder_lead_days": 14,
        "image_seed": "wetdrybag",
    },
    {
        "name": "SwimBuddz Drawstring Bag",
        "slug": "swimbuddz-drawstring-bag",
        "category_key": "bags",
        "description": (
            "Lightweight branded drawstring bag with the SwimBuddz logo. "
            "Perfect for carrying essentials to the pool. Water-resistant base."
        ),
        "short_description": "Lightweight branded drawstring bag",
        "base_price_ngn": "7500",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-BAG-004",
        "preorder_lead_days": 14,
        "image_seed": "drawstringbag",
    },
    # ===== SUN PROTECTION =====
    {
        "name": "Sport Sunscreen SPF 50",
        "slug": "sport-sunscreen-spf-50",
        "category_key": "sun_protection",
        "description": (
            "Water-resistant, reef-safe sport sunscreen with SPF 50 "
            "broad-spectrum protection. Non-greasy formula that won't "
            "sting eyes. Stays on through sweat and swimming."
        ),
        "short_description": "Water-resistant reef-safe sunscreen",
        "base_price_ngn": "8000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-SUN-001",
        "preorder_lead_days": 14,
        "image_seed": "sunscreen",
    },
    {
        "name": "UV Protective Swim Shirt",
        "slug": "uv-protective-swim-shirt",
        "category_key": "sun_protection",
        "description": (
            "UPF 50+ rated short-sleeve swim top for maximum sun protection. "
            "Lightweight, quick-dry fabric that moves with you in the water."
        ),
        "short_description": "UPF 50+ short sleeve swim top",
        "base_price_ngn": "15000",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["S", "M", "L", "XL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SUN-002",
        "preorder_lead_days": 14,
        "image_seed": "uvshirt",
    },
    {
        "name": "Poolside Sunglasses",
        "slug": "poolside-sunglasses",
        "category_key": "sun_protection",
        "description": (
            "Polarised UV400 sunglasses designed for poolside use. "
            "Lightweight, rubberised nose pads and temples for a "
            "secure, comfortable fit around water."
        ),
        "short_description": "Polarised UV400 poolside shades",
        "base_price_ngn": "11000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-SUN-003",
        "preorder_lead_days": 14,
        "image_seed": "sunglasses",
    },
    # ===== KIDS & LEARN-TO-SWIM =====
    {
        "name": "Kids Float Suit",
        "slug": "kids-float-suit",
        "category_key": "kids",
        "description": (
            "One-piece swimsuit with built-in removable foam floats. "
            "Provides buoyancy support as children build water confidence. "
            "Floats can be gradually removed as skills improve."
        ),
        "short_description": "Built-in flotation swimsuit for kids",
        "base_price_ngn": "14000",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {
            "Size": [
                "2 (1-2yr)",
                "3 (2-3yr)",
                "4 (3-4yr)",
                "5 (4-5yr)",
                "6 (5-6yr)",
            ]
        },
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-KID-001",
        "preorder_lead_days": 21,
        "image_seed": "floatsuit",
    },
    {
        "name": "Reusable Swim Diapers",
        "slug": "reusable-swim-diapers",
        "category_key": "kids",
        "description": (
            "Washable, waterproof swim diapers with adjustable snaps. "
            "Eco-friendly alternative to disposables. Secure fit prevents "
            "leaks in the pool."
        ),
        "short_description": "Washable waterproof swim diapers",
        "base_price_ngn": "5500",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["S (3-6m)", "M (6-12m)", "L (12-24m)"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-KID-002",
        "preorder_lead_days": 14,
        "image_seed": "swimdiaper",
    },
    {
        "name": "Kids Swim Belt",
        "slug": "kids-swim-belt",
        "category_key": "kids",
        "description": (
            "Adjustable flotation belt with removable foam pieces. "
            "Allows progressive reduction of buoyancy as the child "
            "gains confidence and swimming ability."
        ),
        "short_description": "Adjustable flotation belt with removable floats",
        "base_price_ngn": "7000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-KID-003",
        "preorder_lead_days": 14,
        "image_seed": "swimbelt",
    },
    {
        "name": "Back Float Trainer",
        "slug": "back-float-trainer",
        "category_key": "kids",
        "description": (
            "Clip-on back float that attaches to the swimsuit strap. "
            "Provides gentle buoyancy support during swim lessons "
            "while encouraging proper body position."
        ),
        "short_description": "Clip-on back float for swim lessons",
        "base_price_ngn": "7500",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-KID-004",
        "preorder_lead_days": 14,
        "image_seed": "backfloat",
    },
    {
        "name": "Kids Swim Starter Kit",
        "slug": "kids-swim-starter-kit",
        "category_key": "kids",
        "description": (
            "Everything a young swimmer needs to get started: junior "
            "goggles, silicone swim cap, mini kickboard, and a mesh "
            "carry bag. Perfect gift for new swimmers."
        ),
        "short_description": "Goggles, cap, kickboard and bag bundle",
        "base_price_ngn": "20000",
        "is_featured": True,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-KID-005",
        "preorder_lead_days": 21,
        "image_seed": "starterkit",
    },
    # ===== MAINTENANCE & CARE =====
    {
        "name": "Anti-Fog Spray",
        "slug": "anti-fog-spray",
        "category_key": "maintenance",
        "description": (
            "Long-lasting anti-fog treatment for swim goggles. A few "
            "drops keep lenses clear for multiple training sessions. "
            "Safe for all lens types."
        ),
        "short_description": "Long-lasting goggle anti-fog treatment",
        "base_price_ngn": "3500",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-MNT-001",
        "preorder_lead_days": 14,
        "image_seed": "antifog",
    },
    {
        "name": "Hard Goggle Case",
        "slug": "hard-goggle-case",
        "category_key": "maintenance",
        "description": (
            "Rigid protective case that keeps goggles safe from scratches "
            "and crushing in your swim bag. Fits most goggle styles with "
            "room for a spare set of lenses."
        ),
        "short_description": "Protective hard case for goggles",
        "base_price_ngn": "4000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-MNT-002",
        "preorder_lead_days": 14,
        "image_seed": "gogglecase",
    },
    {
        "name": "Chlorine Removal Body Wash",
        "slug": "chlorine-removal-body-wash",
        "category_key": "maintenance",
        "description": (
            "Gentle body wash and shampoo that neutralises chlorine and "
            "removes that pool smell after swimming. Moisturising formula "
            "prevents dry skin and hair."
        ),
        "short_description": "Post-swim shampoo and body wash",
        "base_price_ngn": "6000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-MNT-003",
        "preorder_lead_days": 14,
        "image_seed": "bodywash",
    },
    {
        "name": "Swimsuit Rinse & Care",
        "slug": "swimsuit-rinse-care",
        "category_key": "maintenance",
        "description": (
            "Specially formulated rinse that removes chlorine, salt, and "
            "sunscreen from swimwear. Extends the life of your swimsuit "
            "and keeps colours vibrant."
        ),
        "short_description": "Extends swimsuit life, removes chlorine",
        "base_price_ngn": "5000",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-MNT-004",
        "preorder_lead_days": 14,
        "image_seed": "suitrinse",
    },
]

PICKUP_LOCATIONS_DATA = [
    {
        "name": "Rowe Park Pool",
        "address": "Rowe Park, Yaba, Lagos",
        "description": (
            "Mon-Sun: 9am-6pm. Collect from reception desk. Show order confirmation."
        ),
        "contact_phone": "+234 703 358 8400",
        "sort_order": 1,
    },
    # {
    #     "name": "Sunfit - Fitness Spa Accommodation Pool",
    #     "address": "Plot 327/329 Rafiu Babatunde Tinubu Road, Amuwo Odofin Residential Scheme, Festac, Lagos, Nigeria",
    #     "description": (
    #         "Mon-Sun: 9am-6pm. Collect from reception desk. Show order confirmation."
    #     ),
    #     "contact_phone": "+234 703 358 8400",
    #     "sort_order": 2,
    # },
    # {
    #     "name": "Federal Palace Hotel Pool",
    #     "address": "6-8 Ahmadu Bello Wy, Victoria Island, Lagos 101241, Lagos, Nigeria",
    #     "description": ("Mon-Sat: 9am-6pm. Available during all swimming sessions."),
    #     "contact_phone": "+234 703 358 8400",
    #     "sort_order": 3,
    # },
]

COLLECTIONS_DATA = [
    {
        "name": "New Arrivals",
        "slug": "new-arrivals",
        "description": "Check out our latest swimming gear!",
        "sort_order": 1,
        "product_slugs": [
            "speedo-vanquisher-2",
            "arena-cobra-ultra-swipe",
            "swimbuddz-silicone-cap",
            "kids-swim-starter-kit",
            "waterproof-swim-backpack",
        ],
    },
]


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


async def find_by_slug(db, model, slug):
    """Find a record by slug. Returns None if not found."""
    result = await db.execute(select(model).where(model.slug == slug))
    return result.scalar_one_or_none()


async def find_by_name(db, model, name):
    """Find a record by name. Returns None if not found."""
    result = await db.execute(select(model).where(model.name == name))
    return result.scalar_one_or_none()


async def truncate_store_tables(db):
    """Truncate all store tables. DESTRUCTIVE — use only with --fresh."""
    tables = [
        "store_collection_products",
        "store_collections",
        "store_product_images",
        "store_inventory_movements",
        "store_inventory_items",
        "store_product_variants",
        "store_products",
        "store_categories",
        "store_pickup_locations",
        "store_credit_transactions",
        "store_credits",
        "store_order_items",
        "store_orders",
        "store_cart_items",
        "store_carts",
        "store_supplier_payouts",
        "store_suppliers",
    ]
    for table in tables:
        await db.execute(text(f"TRUNCATE {table} CASCADE"))
    await db.commit()


def _size_code(size_label: str) -> str:
    """Extract a short code from a size label for SKU generation.

    Examples:
        "M"           -> "M"
        "S (35-36)"   -> "S"
        "S/M"         -> "SM"
        "2 (1-2yr)"   -> "2"
        "S (3-6m)"    -> "S"
    """
    code = size_label.split("(")[0].strip()
    code = code.replace("/", "")
    return code


# =============================================================================
# SEED FUNCTIONS
# =============================================================================


async def seed_supplier(db, verbose=False):
    """Find or create the SwimBuddz internal supplier."""
    existing = await find_by_slug(db, Supplier, SUPPLIER_DATA["slug"])
    if existing:
        if verbose:
            print(f"  skip  Supplier '{existing.name}' already exists")
        return existing

    supplier = Supplier(
        name=SUPPLIER_DATA["name"],
        slug=SUPPLIER_DATA["slug"],
        contact_name=SUPPLIER_DATA["contact_name"],
        contact_email=SUPPLIER_DATA["contact_email"],
        description=SUPPLIER_DATA["description"],
        commission_percent=Decimal(SUPPLIER_DATA["commission_percent"]),
        is_verified=True,
        status=SupplierStatus.ACTIVE,
        is_active=True,
    )
    db.add(supplier)
    await db.flush()
    if verbose:
        print(f"  +     Supplier '{supplier.name}'")
    return supplier


async def seed_categories(db, verbose=False):
    """Find or create categories. Returns {key: Category} lookup."""
    categories = {}
    created = 0
    skipped = 0

    for cat_data in CATEGORIES_DATA:
        existing = await find_by_slug(db, Category, cat_data["slug"])
        if existing:
            categories[cat_data["key"]] = existing
            skipped += 1
            if verbose:
                print(f"  skip  Category '{existing.name}'")
        else:
            cat = Category(
                name=cat_data["name"],
                slug=cat_data["slug"],
                description=cat_data["description"],
                sort_order=cat_data["sort_order"],
            )
            db.add(cat)
            await db.flush()
            categories[cat_data["key"]] = cat
            created += 1
            if verbose:
                print(f"  +     Category '{cat.name}'")

    print(f"  Categories: {created} created, {skipped} existed")
    return categories


async def seed_products(db, categories, supplier, verbose=False):
    """Find or create products with variants, inventory, and images.

    Returns {slug: Product} lookup for collection linking.
    """
    product_map = {}
    created = 0
    skipped = 0
    total_variants = 0

    for p in PRODUCTS_DATA:
        existing = await find_by_slug(db, Product, p["slug"])
        if existing:
            product_map[p["slug"]] = existing
            skipped += 1
            if verbose:
                print(f"  skip  Product '{existing.name}'")
            continue

        cat = categories.get(p["category_key"])
        if not cat:
            print(
                f"  WARN  Category '{p['category_key']}' not found "
                f"for '{p['name']}' — skipping"
            )
            continue

        product = Product(
            name=p["name"],
            slug=p["slug"],
            category_id=cat.id,
            description=p["description"],
            short_description=p["short_description"],
            base_price_ngn=Decimal(p["base_price_ngn"]),
            compare_at_price_ngn=(
                Decimal(p["compare_at_price_ngn"])
                if p.get("compare_at_price_ngn")
                else None
            ),
            status=ProductStatus.ACTIVE,
            is_featured=p.get("is_featured", False),
            has_variants=p["has_variants"],
            variant_options=p["variant_options"],
            requires_size_chart_ack=p.get("requires_size_chart_ack", False),
            sourcing_type=SourcingType.PREORDER,
            preorder_lead_days=p.get("preorder_lead_days", 14),
            supplier_id=supplier.id,
        )
        db.add(product)
        await db.flush()
        product_map[p["slug"]] = product

        # --- variants & inventory ---
        if p["has_variants"] and p["variant_options"]:
            dimension = list(p["variant_options"].keys())[0]
            for size_label in p["variant_options"][dimension]:
                code = _size_code(size_label)
                variant = ProductVariant(
                    product_id=product.id,
                    sku=f"{p['sku_prefix']}-{code}",
                    name=size_label,
                    options={dimension: size_label},
                )
                db.add(variant)
                await db.flush()
                db.add(
                    InventoryItem(
                        variant_id=variant.id,
                        quantity_on_hand=0,
                        low_stock_threshold=3,
                    )
                )
                total_variants += 1
        else:
            variant = ProductVariant(
                product_id=product.id,
                sku=p["sku_prefix"],
                name="Default",
            )
            db.add(variant)
            await db.flush()
            db.add(
                InventoryItem(
                    variant_id=variant.id,
                    quantity_on_hand=0,
                    low_stock_threshold=3,
                )
            )
            total_variants += 1

        # --- primary image ---
        db.add(
            ProductImage(
                product_id=product.id,
                url=f"https://picsum.photos/seed/{p['image_seed']}/600/600",
                alt_text=p["name"],
                is_primary=True,
            )
        )

        created += 1
        if verbose:
            print(f"  +     Product '{product.name}'")

    print(
        f"  Products: {created} created, {skipped} existed "
        f"({total_variants} new variants)"
    )
    return product_map


async def seed_pickup_locations(db, verbose=False):
    """Find or create pickup locations (matched by name)."""
    created = 0
    skipped = 0

    for loc in PICKUP_LOCATIONS_DATA:
        existing = await find_by_name(db, PickupLocation, loc["name"])
        if existing:
            skipped += 1
            if verbose:
                print(f"  skip  Location '{existing.name}'")
            continue

        db.add(
            PickupLocation(
                name=loc["name"],
                address=loc["address"],
                description=loc["description"],
                contact_phone=loc["contact_phone"],
                sort_order=loc["sort_order"],
            )
        )
        created += 1
        if verbose:
            print(f"  +     Location '{loc['name']}'")

    print(f"  Locations: {created} created, {skipped} existed")


async def seed_collections(db, product_map, verbose=False):
    """Find or create collections and link featured products."""
    created = 0
    skipped = 0

    for col in COLLECTIONS_DATA:
        existing = await find_by_slug(db, Collection, col["slug"])
        if existing:
            skipped += 1
            if verbose:
                print(f"  skip  Collection '{existing.name}'")
            continue

        collection = Collection(
            name=col["name"],
            slug=col["slug"],
            description=col["description"],
            sort_order=col["sort_order"],
        )
        db.add(collection)
        await db.flush()

        linked = 0
        for i, slug in enumerate(col.get("product_slugs", [])):
            product = product_map.get(slug)
            if product:
                db.add(
                    CollectionProduct(
                        collection_id=collection.id,
                        product_id=product.id,
                        sort_order=i,
                    )
                )
                linked += 1
            elif verbose:
                print(f"  WARN  Product '{slug}' not found for collection")

        created += 1
        if verbose:
            print(f"  +     Collection '{col['name']}' ({linked} products)")

    print(f"  Collections: {created} created, {skipped} existed")


# =============================================================================
# MAIN
# =============================================================================


async def seed_store_data(fresh=False, verbose=False):
    """Orchestrate the full store seed."""
    async with AsyncSessionLocal() as db:
        if fresh:
            print("\nTruncating all store tables...")
            await truncate_store_tables(db)
            print("  Done. Starting fresh.\n")

        print("Seeding store data...\n")

        print("[Supplier]")
        supplier = await seed_supplier(db, verbose)

        print("\n[Categories]")
        categories = await seed_categories(db, verbose)

        print("\n[Products + Variants + Inventory + Images]")
        product_map = await seed_products(db, categories, supplier, verbose)

        print("\n[Pickup Locations]")
        await seed_pickup_locations(db, verbose)

        print("\n[Collections]")
        await seed_collections(db, product_map, verbose)

        await db.commit()

        print("\n" + "=" * 60)
        print("Store seed complete!")
        print("=" * 60)
        print(f"  Categories defined:       {len(CATEGORIES_DATA)}")
        print(f"  Products defined:         {len(PRODUCTS_DATA)}")
        print(f"  Pickup locations defined: {len(PICKUP_LOCATIONS_DATA)}")
        print(f"  Collections defined:      {len(COLLECTIONS_DATA)}")
        print("  Sourcing type:            All PREORDER")
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Seed the SwimBuddz store with categories, products, and more.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/seed/store_service.py                  # Upsert (safe to rerun)
  python scripts/seed/store_service.py --fresh          # Wipe + reseed
  python scripts/seed/store_service.py --fresh --yes    # Wipe without prompt
  python scripts/seed/store_service.py --verbose        # Detailed per-record output
        """,
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Truncate ALL store tables before seeding (destructive!)",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompts",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed output for every record",
    )

    args = parser.parse_args()

    if args.fresh and not args.yes:
        confirm = input(
            "\n--fresh will DELETE all store data "
            "(categories, products, orders, etc.)\n"
            "Are you sure? [y/N]: "
        )
        if confirm.lower() not in ("y", "yes"):
            print("Aborted.")
            return

    asyncio.run(seed_store_data(fresh=args.fresh, verbose=args.verbose))


if __name__ == "__main__":
    main()
