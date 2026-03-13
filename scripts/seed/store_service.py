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

# ---------------------------------------------------------------------------
# ALIBABA SUPPLIER LINKS — Sourcing URLs for pre-order products
#
# Maps product slugs to their Alibaba product URL.
# These are internal sourcing references, NOT customer-facing data.
# ---------------------------------------------------------------------------
SUPPLIER_LINKS = {
    "swim-resistance-parachute": "https://www.alibaba.com/product-detail/Swimming-Training-Belts-Resistance-Parachute-Aquatic_1601712335024.html",
    "finis-3m-swim-parachute": "https://www.alibaba.com/product-detail/3M-FINIS-Replacement-Parachute-for-Pool_1601045001371.html",
    "adjustable-swimming-parachute": "https://www.alibaba.com/product-detail/Swimming-Parachute-Trainer-for-Children-and_1601227474500.html",
    "eva-kickboard-standard": "https://www.alibaba.com/product-detail/Custom-Accept-Durable-EVA-Swimming-Kickboard_1601260709954.html",
    "eva-kickboard-pro": "https://www.alibaba.com/product-detail/Customized-Logo-Kids-Adults-Swimming-Swim_1600991086813.html",
    "childrens-eva-kickboard": "https://www.alibaba.com/product-detail/Children-s-Boys-Water-Floating-Board_1601591012818.html",
    "eva-training-pull-buoy": "https://www.alibaba.com/product-detail/Customized-Design-Swimming-Legs-Floating-Training_1601047254870.html",
    "silicone-training-fins": "https://www.alibaba.com/product-detail/Manufacturer-Price-Open-Heel-Design-Adult_1601458003885.html",
    "rubber-dive-swim-fins": "https://www.alibaba.com/product-detail/OEM-Swimming-Fins-Dive-Free-Diving_1600132665137.html",
    "short-blade-bodyboard-fins": "https://www.alibaba.com/product-detail/Wave-Sport-Fins-Water-Sports-Equipment_60676166503.html",
    "tpr-training-flippers": "https://www.alibaba.com/product-detail/Custom-Logo-Adult-Diving-Training-Flippers_1601587055182.html",
    "adjustable-mermaid-fins": "https://www.alibaba.com/product-detail/Wholesale-Mermaid-Fins-for-Adults-Kids_1601444089992.html",
    "silicone-hand-training-paddles": "https://www.alibaba.com/product-detail/Swimming-Hand-Training-Paddles-Silicone-PC_1601563740123.html",
    "classic-frontal-centre-snorkel": "https://www.alibaba.com/product-detail/Classic-Frontal-Snorkel-Waspo-Design_1601319993845.html",
    "semi-dry-frontal-training-snorkel": "https://www.alibaba.com/product-detail/Silicone-Frontal-Snorkel-Semi-Dry_1601257238004.html",
    "epe-foam-pool-noodle": "https://www.alibaba.com/product-detail/EPE-Pool-Noodle-Swimming_1601565261987.html",
    "anti-fog-uv-swimming-goggles": "https://www.alibaba.com/product-detail/Hot-Selling-Custom-OEM-Factory-Wholesale_1601221349520.html",
    "arena-racing-goggles": "https://www.alibaba.com/product-detail/Best-Quality-Swimming-Goggles-Arena-Racing_1601378313163.html",
    "silver-mirrored-racing-goggles": "https://www.alibaba.com/product-detail/UV-Swimming-Goggles-Racing-Silver-Plated_1601254897770.html",
    "marble-silicone-swim-cap": "https://www.alibaba.com/product-detail/OEM-Custom-Durable-Silicone-Swim-Cap_1601520869417.html",
    "eva-hard-goggle-case": "https://www.alibaba.com/product-detail/Custom-Sunglasses-Box-Glasses-Bag-Portable_1600401895484.html",
    "silicone-nose-clip": "https://www.alibaba.com/product-detail/Professional-Silicone-Nose-Clip_1601713089994.html",
    "chlorine-resistant-jammer": "https://www.alibaba.com/product-detail/Wholesale-price-Chlorine-Resistant-Endurance-Swim_1600246434962.html",
    "competition-racing-jammer": "https://www.alibaba.com/product-detail/Swimwear-men-s-swimming-trunks-beachwear_60810186461.html",
    "quick-dry-performance-jammer": "https://www.alibaba.com/product-detail/Quick-Dry-Man-Swim-Swimsuit-Jammer_62169597652.html",
    "yingfa-mid-leg-trunks": "https://www.alibaba.com/product-detail/Yingfa-9205-Men-s-Mid-leg_1600570203467.html",
    "sharkskin-performance-jammers": "https://www.alibaba.com/product-detail/Big-Size-Sharkskin-Outdoor-Diving-Rashguard_60789327808.html",
    "mens-custom-swim-briefs": "https://www.alibaba.com/product-detail/High-Quality-Customized-Men-s-Swim_11000013400069.html",
    "mens-full-body-swimsuit": "https://www.alibaba.com/product-detail/Mens-Swim-Jammer-One-Piece-Short_10000039990519.html",
    "fina-shark-skin-racing-jammer": "https://www.alibaba.com/product-detail/Fina-Approved-Mens-Professional-Shark-Skin_1600868345143.html",
    "womens-short-sleeve-one-piece": "https://www.alibaba.com/product-detail/OEM-ODEM-Women-s-One-Piece_1601374965726.html",
    "womens-two-piece-sports-swimsuit": "https://www.alibaba.com/product-detail/Factory-Direct-Sale-Women-s-Short_1601363313836.html",
    "womens-long-sleeve-eco-swimsuit": "https://www.alibaba.com/product-detail/Eco-Friendly-Sportswear-Swimsuit-Bathing-Suit_1601027966858.html",
    "womens-plus-size-fitness-swimwear": "https://www.alibaba.com/product-detail/2025-High-Quality-OEM-Design-Women_11000027852820.html",
    "womens-printed-sports-swimsuit": "https://www.alibaba.com/product-detail/Women-s-Sports-Swimsuits-Animal-Letter_11000029158213.html",
    "yingfa-womens-competitive-swimsuit": "https://www.alibaba.com/product-detail/YINGFA-Professional-Women-s-Competitive-Swim_1601623773892.html",
    "full-coverage-two-piece-swim-set": "https://www.alibaba.com/product-detail/High-Quality-Men-Women-Swim-Jammer_10000035859334.html",
    "fina-womens-racing-swimsuit": "https://www.alibaba.com/product-detail/Fina-Approved-One-Piece-White-Racing_1601623769867.html",
    "oxford-fabric-life-jacket": "https://www.alibaba.com/product-detail/Professional-Oxford-Fabric-Thickened-Adult-Children_1601489484673.html",
    "neoprene-performance-life-vest": "https://www.alibaba.com/product-detail/JIURAN-Neoprene-Adult-Life-Jacket-Vest_1601614678797.html",
    "mesh-swim-drawstring-bag": "https://www.alibaba.com/product-detail/In-Stock-Outdoor-Sports-Fitness-Waterproof_1601428960652.html",
    "waterproof-pu-gym-duffle": "https://www.alibaba.com/product-detail/Waterproof-Gym-Duffle-Bag_1601579526270.html",
    "waterproof-canvas-sports-backpack": "https://www.alibaba.com/product-detail/Canvas-Sports-Backpack_1601496788503.html",
    "multi-compartment-gym-duffle": "https://www.alibaba.com/product-detail/Gym-Duffle-Multiple-Compartments_1601570187639.html",
    "pu-yoga-swim-duffel-tote": "https://www.alibaba.com/product-detail/Yoga-Duffel-Tote-Bag_1601411337411.html",
    "outdoor-sport-duffle-backpack": "https://www.alibaba.com/product-detail/Outdoor-Sport-Duffle_1601396976569.html",
    "pu-leather-travel-duffel": "https://www.alibaba.com/product-detail/PU-Leather-Travel-Duffel_1601572294202.html",
    "multifunctional-travel-backpack": "https://www.alibaba.com/product-detail/Multifunctional-Travel-Backpack_1601429117824.html",
    "uv400-sports-sunglasses": "https://www.alibaba.com/product-detail/Outdoor-Cycling-High-Quality-Polarized-Glasses_1601348182851.html",
    "retro-polarised-sunglasses": "https://www.alibaba.com/product-detail/Partagas-Retro-Designer-Custom-Logo-Round_1601585110117.html",
    "goggle-anti-fog-solution": "https://www.alibaba.com/product-detail/Nano-Goggles-Agent-Diving-Mask-Anti_1601666522292.html",
    "chlorine-removal-shampoo-240ml": "https://www.alibaba.com/product-detail/2-in-1-Chlorine-Removal-Shampoo_1601634590098.html",
    "post-swim-cleansing-gel-251ml": "https://www.alibaba.com/product-detail/MELAO-Post-Swim-Cleansing-Gel_1601016411119.html",
    "chlorine-removal-body-wash": "https://www.alibaba.com/product-detail/KORMESIC-Chlorine-Body-Wash_1600778907963.html",
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
# PRODUCTS — 54 real Alibaba-sourced products across 7 categories, all PREORDER
# ---------------------------------------------------------------------------
PRODUCTS_DATA = [
    # ===== TRAINING EQUIPMENT =====
    {
        "name": "Swim Training Resistance Parachute",
        "slug": "swim-resistance-parachute",
        "category_key": "training",
        "description": (
            "Neoprene and Oxford fabric resistance parachute for swim training. "
            "Attaches to the waist to create drag, building power and endurance "
            "during pool sessions. Ideal for sprint and interval training."
        ),
        "short_description": "Resistance parachute for power and endurance training",
        "base_price_ngn": "7500",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "3899",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["Small (20cm)", "Large (30cm)"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-001",
        "preorder_lead_days": 7,
        "image_seed": "parachute1",
    },
    {
        "name": "FINIS 3M Replacement Swim Parachute",
        "slug": "finis-3m-swim-parachute",
        "category_key": "training",
        "description": (
            "Premium 3-metre polyester swim parachute compatible with FINIS drag "
            "belts. High-density 210D fabric provides consistent resistance at "
            "any speed. Reinforced stitching for long-term durability."
        ),
        "short_description": "Premium 3M replacement parachute for drag training",
        "base_price_ngn": "15000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "8731",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-002",
        "preorder_lead_days": 7,
        "image_seed": "parachute2",
    },
    {
        "name": "Adjustable Swimming Parachute",
        "slug": "adjustable-swimming-parachute",
        "category_key": "training",
        "description": (
            "Versatile neoprene nylon swim parachute available in three sizes for "
            "progressive resistance training. Suitable for children and adults "
            "alike. Lightweight and easy to attach with adjustable belt."
        ),
        "short_description": "Adjustable parachute in 3 sizes for all swimmers",
        "base_price_ngn": "12000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "6503",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["Small (20cm)", "Medium (30cm)", "Large (40cm)"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-003",
        "preorder_lead_days": 7,
        "image_seed": "parachute3",
    },
    {
        "name": "EVA Training Kickboard – Standard",
        "slug": "eva-kickboard-standard",
        "category_key": "training",
        "description": (
            "Durable EVA foam kickboard (44×29 cm) for leg-focused swim drills. "
            "Lightweight with smooth rounded edges for a comfortable grip. "
            "Available in five vibrant colours."
        ),
        "short_description": "Standard EVA kickboard for leg training drills",
        "base_price_ngn": "5000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "2258",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Color": ["Blue", "Yellow", "Orange", "Green", "Pink"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-004",
        "preorder_lead_days": 7,
        "image_seed": "kickboard1",
    },
    {
        "name": "EVA Training Kickboard – Pro",
        "slug": "eva-kickboard-pro",
        "category_key": "training",
        "description": (
            "Professional-grade EVA kickboard (45×29 cm) with enhanced buoyancy "
            "for serious swim training. Firm foam construction supports proper "
            "body alignment during kick sets and drill work."
        ),
        "short_description": "Pro-grade EVA kickboard with enhanced buoyancy",
        "base_price_ngn": "6500",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "3462",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-005",
        "preorder_lead_days": 7,
        "image_seed": "kickboard2",
    },
    {
        "name": "Children’s EVA Kickboard",
        "slug": "childrens-eva-kickboard",
        "category_key": "training",
        "description": (
            "Fun, lightweight EVA kickboard sized for young swimmers. Bright "
            "colours and easy-grip shape help kids build confidence and leg "
            "strength during swim lessons."
        ),
        "short_description": "Kid-sized EVA kickboard in bright colours",
        "base_price_ngn": "2500",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "904",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Color": ["Blue", "Pink", "Yellow", "Green", "Orange"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-006",
        "preorder_lead_days": 7,
        "image_seed": "kickboardkid",
    },
    {
        "name": "EVA Training Pull Buoy",
        "slug": "eva-training-pull-buoy",
        "category_key": "training",
        "description": (
            "Contoured EVA pull buoy that immobilises the legs for upper-body "
            "focused swim drills. Ergonomic figure-eight shape stays in place "
            "between the thighs during laps."
        ),
        "short_description": "Ergonomic EVA pull buoy for upper-body training",
        "base_price_ngn": "7000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "3763",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["Standard", "Large"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-007",
        "preorder_lead_days": 3,
        "image_seed": "pullbuoy",
    },
    {
        "name": "Open Heel Silicone Training Fins",
        "slug": "silicone-training-fins",
        "category_key": "training",
        "description": (
            "Premium 100% silicone training fins with open-heel design for a "
            "secure, adjustable fit. Soft yet responsive blade improves ankle "
            "flexibility and kick technique. Available in four sizes."
        ),
        "short_description": "100% silicone open-heel training fins",
        "base_price_ngn": "25000",
        "compare_at_price_ngn": "30000",
        "cost_price_ngn": "15805",
        "is_featured": True,
        "has_variants": True,
        "variant_options": {
            "Size": ["S (36-38)", "M (39-41)", "L (42-44)", "XL (45-46)"]
        },
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-008",
        "preorder_lead_days": 7,
        "image_seed": "siliconefins",
    },
    {
        "name": "Rubber Dive & Swim Fins",
        "slug": "rubber-dive-swim-fins",
        "category_key": "training",
        "description": (
            "Versatile rubber fins suitable for both pool training and open-water "
            "diving. Full-foot pocket provides a snug fit while the flexible "
            "blade delivers efficient propulsion with minimal effort."
        ),
        "short_description": "Versatile rubber fins for pool and open-water use",
        "base_price_ngn": "12000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "6021",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {
            "Size": ["S (36-38)", "M (39-41)", "L (42-44)", "XL (45-46)"]
        },
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-009",
        "preorder_lead_days": 3,
        "image_seed": "rubberfins",
    },
    {
        "name": "Short Blade Bodyboard Fins",
        "slug": "short-blade-bodyboard-fins",
        "category_key": "training",
        "description": (
            "Compact TPR short-blade fins designed for kick training and "
            "bodyboarding. Short blade forces a faster kick tempo, building leg "
            "speed and ankle flexibility."
        ),
        "short_description": "Short-blade TPR fins for kick speed training",
        "base_price_ngn": "15000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "9032",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {
            "Size": ["S (35-37)", "M (38-40)", "L (41-43)", "XL (44-46)"]
        },
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-010",
        "preorder_lead_days": 15,
        "image_seed": "shortfins",
    },
    {
        "name": "TPR Training Flippers",
        "slug": "tpr-training-flippers",
        "category_key": "training",
        "description": (
            "Polypropylene and TPR training flippers with a medium-length blade "
            "for balanced resistance. Available in three colour options with "
            "sizes from XS to L to suit most swimmers."
        ),
        "short_description": "Medium-blade TPR training flippers in 4 sizes",
        "base_price_ngn": "16000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "9483",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {
            "Size": ["XS (34-36)", "S (37-39)", "M (40-42)", "L (43-45)"]
        },
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-011",
        "preorder_lead_days": 7,
        "image_seed": "tprflippers",
    },
    {
        "name": "Adjustable Mermaid Swim Fins",
        "slug": "adjustable-mermaid-fins",
        "category_key": "training",
        "description": (
            "Fun and functional mermaid-style monofin with adjustable straps. "
            "PVC, EVA, and PE construction provides buoyancy and power. Great for "
            "recreational swimming and dolphin kick training."
        ),
        "short_description": "Adjustable mermaid monofin for fun and training",
        "base_price_ngn": "18000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "11064",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["S (34-38)", "M/L (39-43)", "XL (44-47)"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-012",
        "preorder_lead_days": 7,
        "image_seed": "mermaidfin",
    },
    {
        "name": "Silicone Hand Training Paddles",
        "slug": "silicone-hand-training-paddles",
        "category_key": "training",
        "description": (
            "Silicone and polycarbonate hand paddles that increase surface area "
            "for upper-body resistance training. Ergonomic design promotes proper "
            "catch technique. Available in child and adult sizes."
        ),
        "short_description": "Silicone hand paddles for catch and pull training",
        "base_price_ngn": "5500",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "2845",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["Child (S)", "Adult (M/L)"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-013",
        "preorder_lead_days": 7,
        "image_seed": "handpaddles",
    },
    {
        "name": "Classic Frontal Centre Snorkel",
        "slug": "classic-frontal-centre-snorkel",
        "category_key": "training",
        "description": (
            "PVC, polycarbonate, and silicone centre-mount snorkel for focused "
            "stroke technique training. Eliminates the need to turn for breath, "
            "letting swimmers concentrate on body position and pull mechanics."
        ),
        "short_description": "Centre-mount snorkel for focused stroke technique",
        "base_price_ngn": "9000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "4757",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-014",
        "preorder_lead_days": 7,
        "image_seed": "snorkel1",
    },
    {
        "name": "Semi-Dry Frontal Training Snorkel",
        "slug": "semi-dry-frontal-training-snorkel",
        "category_key": "training",
        "description": (
            "Silicone frontal snorkel with a semi-dry top valve that reduces "
            "water entry during flip turns and surface chop. Comfortable "
            "mouthpiece and adjustable head strap for extended training sets."
        ),
        "short_description": "Semi-dry silicone frontal snorkel for training",
        "base_price_ngn": "9500",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "4907",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-016",
        "preorder_lead_days": 6,
        "image_seed": "snorkel2",
    },
    {
        "name": "EPE Foam Pool Noodle",
        "slug": "epe-foam-pool-noodle",
        "category_key": "training",
        "description": (
            "Lightweight EPE foam noodle (6.5 cm × 150 cm) for aquatic exercises, "
            "flotation support, and learn-to-swim sessions. Soft, buoyant foam is "
            "safe for all ages and skill levels."
        ),
        "short_description": "EPE foam pool noodle for flotation and exercises",
        "base_price_ngn": "4000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "1731",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-015",
        "preorder_lead_days": 15,
        "image_seed": "poolnoodle",
    },
    # ===== SWIM GEAR =====
    {
        "name": "Anti-Fog UV Swimming Goggles",
        "slug": "anti-fog-uv-swimming-goggles",
        "category_key": "swim_gear",
        "description": (
            "Polycarbonate lens goggles with anti-fog coating and UV protection. "
            "Soft silicone gasket and adjustable nose bridge (S/M/L) ensure a "
            "comfortable, leak-free fit for training and recreational swimming."
        ),
        "short_description": "Anti-fog UV goggles with adjustable nose bridge",
        "base_price_ngn": "5500",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "2710",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Color": ["Black", "Blue", "Pink", "Clear"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-GER-001",
        "preorder_lead_days": 10,
        "image_seed": "goggles1",
    },
    {
        "name": "Arena-Style Racing Goggles",
        "slug": "arena-racing-goggles",
        "category_key": "swim_gear",
        "description": (
            "Low-profile racing goggles inspired by arena competition designs. "
            "Polycarbonate lenses with anti-fog treatment and interchangeable "
            "nose bridges for a personalised, hydrodynamic fit."
        ),
        "short_description": "Low-profile anti-fog racing goggles",
        "base_price_ngn": "5000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "2424",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Color": ["Black", "Blue", "Red"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-GER-002",
        "preorder_lead_days": 10,
        "image_seed": "goggles2",
    },
    {
        "name": "Silver Mirrored Racing Goggles",
        "slug": "silver-mirrored-racing-goggles",
        "category_key": "swim_gear",
        "description": (
            "Mirrored silver-plated racing goggles with UV protection. Reduces "
            "glare for outdoor and well-lit pool environments. Silicone strap and "
            "cushion provide a secure, comfortable seal."
        ),
        "short_description": "Mirrored racing goggles with UV and glare protection",
        "base_price_ngn": "6000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "2981",
        "is_featured": True,
        "has_variants": True,
        "variant_options": {"Color": ["Silver", "Gold", "Blue"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-GER-003",
        "preorder_lead_days": 10,
        "image_seed": "goggles3",
    },
    {
        "name": "Marble Design Silicone Swim Cap",
        "slug": "marble-silicone-swim-cap",
        "category_key": "swim_gear",
        "description": (
            "Premium silicone swim cap with a unique marble-swirl pattern. "
            "Durable, tear-resistant silicone protects hair from chlorine while "
            "providing a snug, comfortable fit for all head sizes."
        ),
        "short_description": "Stylish marble-pattern silicone swim cap",
        "base_price_ngn": "3000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "1069",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {
            "Color": [
                "Black",
                "White",
                "Blue",
                "Pink",
                "Purple",
                "Red",
                "Green",
                "Orange",
            ]
        },
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-GER-004",
        "preorder_lead_days": 5,
        "image_seed": "swimcap",
    },
    {
        "name": "EVA Hard Goggle Case",
        "slug": "eva-hard-goggle-case",
        "category_key": "swim_gear",
        "description": (
            "Protective EVA hard-shell case that keeps goggles safe from "
            "scratches and crushing in your swim bag. Zippered closure with mesh "
            "interior lining. Compact and lightweight."
        ),
        "short_description": "Protective EVA hard-shell goggle case",
        "base_price_ngn": "2500",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "828",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Color": ["Black", "Blue", "Pink", "Red", "White"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-GER-005",
        "preorder_lead_days": 5,
        "image_seed": "gogglecase",
    },
    {
        "name": "Professional Silicone Nose Clip",
        "slug": "silicone-nose-clip",
        "category_key": "swim_gear",
        "description": (
            "Soft silicone nose clip with a secure spring mechanism to keep water "
            "out during swimming, diving, and synchronised routines. Lightweight "
            "and comfortable for extended wear."
        ),
        "short_description": "Soft silicone nose clip for swimming",
        "base_price_ngn": "1500",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "211",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {
            "Color": ["Black", "Blue", "Pink", "Yellow", "Orange", "Clear"]
        },
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-GER-006",
        "preorder_lead_days": 7,
        "image_seed": "noseclip",
    },
    # ===== SWIMWEAR =====
    {
        "name": "Chlorine Resistant Training Jammer",
        "slug": "chlorine-resistant-jammer",
        "category_key": "swimwear",
        "description": (
            "Chlorine-resistant spandex/nylon jammer built for regular pool "
            "sessions. Retains shape and colour after extended exposure to "
            "chlorinated water. Comfortable compression fit from L to 5XL."
        ),
        "short_description": "Chlorine-resistant training jammer for daily use",
        "base_price_ngn": "9500",
        "compare_at_price_ngn": "12000",
        "cost_price_ngn": "5208",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["L", "XL", "XXL", "3XL", "4XL", "5XL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-001",
        "preorder_lead_days": 9,
        "image_seed": "jammer1",
    },
    {
        "name": "Competition Racing Jammer",
        "slug": "competition-racing-jammer",
        "category_key": "swimwear",
        "description": (
            "Polyamide and spandex racing jammer designed for competition. "
            "Low-drag fabric with four-way stretch ensures freedom of movement "
            "and a streamlined profile in the water."
        ),
        "short_description": "Polyamide racing jammer for competition swimmers",
        "base_price_ngn": "15000",
        "compare_at_price_ngn": "18000",
        "cost_price_ngn": "8595",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["XS", "S", "M", "L", "XL", "XXL", "3XL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-002",
        "preorder_lead_days": 25,
        "image_seed": "jammer2",
    },
    {
        "name": "Quick Dry Performance Jammer",
        "slug": "quick-dry-performance-jammer",
        "category_key": "swimwear",
        "description": (
            "Polyamide/spandex performance jammer with rapid-dry technology. "
            "Lightweight compression fabric reduces drag and dries quickly "
            "between heats. Ideal for training and race days."
        ),
        "short_description": "Quick-drying polyamide performance jammer",
        "base_price_ngn": "19500",
        "compare_at_price_ngn": "24000",
        "cost_price_ngn": "11711",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["L", "XL", "XXL", "3XL", "4XL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-003",
        "preorder_lead_days": 7,
        "image_seed": "jammer3",
    },
    {
        "name": "Yingfa Mid-Leg Training Trunks",
        "slug": "yingfa-mid-leg-trunks",
        "category_key": "swimwear",
        "description": (
            "Premium Yingfa 9205 mid-leg trunks in spandex/polyester blend. "
            "Professional-grade construction used by competitive swimmers "
            "worldwide. Excellent chlorine resistance and shape retention."
        ),
        "short_description": "Premium Yingfa mid-leg competitive trunks",
        "base_price_ngn": "48000",
        "compare_at_price_ngn": "55000",
        "cost_price_ngn": "30104",
        "is_featured": True,
        "has_variants": True,
        "variant_options": {"Size": ["S", "M", "L", "XL", "XXL", "3XL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-004",
        "preorder_lead_days": 10,
        "image_seed": "yingfatrunks",
    },
    {
        "name": "Sharkskin Performance Jammers",
        "slug": "sharkskin-performance-jammers",
        "category_key": "swimwear",
        "description": (
            "Sharkskin-texture spandex/polyester jammers that mimic low-drag "
            "aquatic surfaces. Extended size range (M–5XL) accommodates all body "
            "types. Three colour options for personal style."
        ),
        "short_description": "Sharkskin-texture jammers in extended sizes",
        "base_price_ngn": "14000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "7752",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["M", "L", "XL", "XXL", "3XL", "4XL", "5XL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-005",
        "preorder_lead_days": 14,
        "image_seed": "sharkskin",
    },
    {
        "name": "Men’s Custom Swim Briefs",
        "slug": "mens-custom-swim-briefs",
        "category_key": "swimwear",
        "description": (
            "High-quality spandex/polyester swim briefs with a customisable "
            "design. Comfortable V-cut silhouette with inner drawstring. Ideal "
            "for training and competition in sizes S–XL."
        ),
        "short_description": "Custom-fit men’s swim briefs in spandex/polyester",
        "base_price_ngn": "30000",
        "compare_at_price_ngn": "35000",
        "cost_price_ngn": "18966",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["S", "M", "L", "XL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-006",
        "preorder_lead_days": 7,
        "image_seed": "swimbriefs",
    },
    {
        "name": "Men’s Short Sleeve Full Body Swimsuit",
        "slug": "mens-full-body-swimsuit",
        "category_key": "swimwear",
        "description": (
            "Full-body one-piece swimsuit with short sleeves and front zip. 100% "
            "polyester construction provides full UV coverage and a streamlined "
            "fit. Available in 17+ colourways from XS to XXL."
        ),
        "short_description": "Full-body short-sleeve swimsuit with front zip",
        "base_price_ngn": "55000",
        "compare_at_price_ngn": "65000",
        "cost_price_ngn": "37615",
        "is_featured": True,
        "has_variants": True,
        "variant_options": {"Size": ["XS", "S", "M", "L", "XL", "XXL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-007",
        "preorder_lead_days": 7,
        "image_seed": "fullbody",
    },
    {
        "name": "FINA Approved Shark Skin Racing Jammer",
        "slug": "fina-shark-skin-racing-jammer",
        "category_key": "swimwear",
        "description": (
            "FINA-approved racing jammer in 92% polyester / 8% spandex. "
            "Engineered shark-skin texture minimises drag for competitive events. "
            "Trusted by professional swimmers worldwide."
        ),
        "short_description": "FINA-approved shark-skin racing jammer",
        "base_price_ngn": "38000",
        "compare_at_price_ngn": "45000",
        "cost_price_ngn": "23933",
        "is_featured": True,
        "has_variants": True,
        "variant_options": {"Size": ["XS", "S", "M", "L", "XL", "XXL", "3XL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-008",
        "preorder_lead_days": 15,
        "image_seed": "finaracing",
    },
    {
        "name": "Women’s Short Sleeve One-Piece Swimsuit",
        "slug": "womens-short-sleeve-one-piece",
        "category_key": "swimwear",
        "description": (
            "Modest short-sleeve one-piece swimsuit in spandex/polyester blend. "
            "Provides comfortable coverage for training and leisure swimming. "
            "Soft, quick-drying fabric available in sizes S–XXL."
        ),
        "short_description": "Short-sleeve one-piece swimsuit for women",
        "base_price_ngn": "18000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "10507",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["S", "M", "L", "XL", "XXL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-009",
        "preorder_lead_days": 7,
        "image_seed": "womens1piece1",
    },
    {
        "name": "Women’s Two-Piece Sports Swimsuit",
        "slug": "womens-two-piece-sports-swimsuit",
        "category_key": "swimwear",
        "description": (
            "Sporty two-piece swimsuit with crop top and high-waist bottoms. "
            "Spandex/polyester fabric offers four-way stretch and chlorine "
            "resistance. Three colourway options in sizes S–XXL."
        ),
        "short_description": "Sporty two-piece swimsuit for active women",
        "base_price_ngn": "20000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "11741",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["S", "M", "L", "XL", "XXL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-010",
        "preorder_lead_days": 9,
        "image_seed": "womens2piece",
    },
    {
        "name": "Women’s Long Sleeve Eco Swimsuit",
        "slug": "womens-long-sleeve-eco-swimsuit",
        "category_key": "swimwear",
        "description": (
            "Eco-friendly long-sleeve one-piece in spandex/nylon blend. Full arm "
            "coverage protects against UV and chlorine. Sustainable fabric "
            "sourcing with the same performance qualities swimmers expect."
        ),
        "short_description": "Eco-friendly long-sleeve swimsuit with UV coverage",
        "base_price_ngn": "21000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "12494",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["S", "M", "L", "XL", "XXL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-011",
        "preorder_lead_days": 7,
        "image_seed": "ecowomens",
    },
    {
        "name": "Women’s Plus Size Fitness Swimwear",
        "slug": "womens-plus-size-fitness-swimwear",
        "category_key": "swimwear",
        "description": (
            "Inclusive plus-size fitness swimsuit in spandex/polyester with "
            "flattering seam placement and supportive construction. Extended size "
            "range (L–5XL) with five vibrant colour options."
        ),
        "short_description": "Plus-size fitness swimwear in sizes L–5XL",
        "base_price_ngn": "18000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "10537",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["L", "XL", "XXL", "3XL", "4XL", "5XL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-012",
        "preorder_lead_days": 6,
        "image_seed": "plussize",
    },
    {
        "name": "Women’s Printed Sports Swimsuit",
        "slug": "womens-printed-sports-swimsuit",
        "category_key": "swimwear",
        "description": (
            "Eye-catching animal and letter print sports swimsuit in "
            "spandex/nylon. Bold designs with athletic cut for active swimmers. "
            "Extended sizes from M to 5XL with multiple print options."
        ),
        "short_description": "Bold printed sports swimsuit in extended sizes",
        "base_price_ngn": "34000",
        "compare_at_price_ngn": "40000",
        "cost_price_ngn": "21073",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["M", "L", "XL", "XXL", "3XL", "4XL", "5XL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-013",
        "preorder_lead_days": 7,
        "image_seed": "printswimsuit",
    },
    {
        "name": "Yingfa Women’s Competitive Racing Swimsuit",
        "slug": "yingfa-womens-competitive-swimsuit",
        "category_key": "swimwear",
        "description": (
            "Professional Yingfa competitive swimsuit engineered for racing. "
            "Spandex/polyester blend with compression fit reduces drag and muscle "
            "vibration. Trusted by national-level swimmers."
        ),
        "short_description": "Professional Yingfa women’s racing swimsuit",
        "base_price_ngn": "72000",
        "compare_at_price_ngn": "85000",
        "cost_price_ngn": "49522",
        "is_featured": True,
        "has_variants": True,
        "variant_options": {"Size": ["S", "M", "L", "XL", "XXL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-014",
        "preorder_lead_days": 15,
        "image_seed": "yingfawomens",
    },
    {
        "name": "Full Coverage 2-Piece Swim Set",
        "slug": "full-coverage-two-piece-swim-set",
        "category_key": "swimwear",
        "description": (
            "Modest full-coverage two-piece swim set in 100% polyester. Long "
            "sleeves and full-length bottoms provide maximum UV protection and "
            "coverage. Available in 13+ colours from M to L+."
        ),
        "short_description": "Full-coverage modest 2-piece swim set",
        "base_price_ngn": "30000",
        "compare_at_price_ngn": "36000",
        "cost_price_ngn": "18815",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["M", "L", "XL", "XXL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-015",
        "preorder_lead_days": 7,
        "image_seed": "fullcoverage",
    },
    {
        "name": "FINA Approved Women’s Racing Swimsuit",
        "slug": "fina-womens-racing-swimsuit",
        "category_key": "swimwear",
        "description": (
            "FINA-approved one-piece racing swimsuit by Yingfa. Engineered "
            "spandex/polyester blend with competition-grade compression and "
            "hydrodynamic seam placement. For serious competitive swimmers."
        ),
        "short_description": "FINA-approved women’s one-piece racing suit",
        "base_price_ngn": "75000",
        "compare_at_price_ngn": "89000",
        "cost_price_ngn": "51027",
        "is_featured": True,
        "has_variants": True,
        "variant_options": {"Size": ["S", "M", "L", "XL", "XXL"]},
        "requires_size_chart_ack": True,
        "sku_prefix": "SB-SWR-016",
        "preorder_lead_days": 15,
        "image_seed": "finaracingw",
    },
    # ===== POOL & WATER SAFETY =====
    {
        "name": "Oxford Fabric Safety Life Jacket",
        "slug": "oxford-fabric-life-jacket",
        "category_key": "safety",
        "description": (
            "Thickened Oxford fabric life jacket with adjustable straps and "
            "reflective strips. Multiple EPE foam panels provide reliable "
            "buoyancy for beginners and recreational open-water swimmers."
        ),
        "short_description": "Oxford fabric life jacket with reflective strips",
        "base_price_ngn": "12000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "6021",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["S", "M", "L", "XL", "XXL", "3XL"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-SAF-001",
        "preorder_lead_days": 7,
        "image_seed": "lifejacket1",
    },
    {
        "name": "Neoprene Performance Life Vest",
        "slug": "neoprene-performance-life-vest",
        "category_key": "safety",
        "description": (
            "Premium neoprene life vest designed for active water sports. "
            "Flexible neoprene construction allows full range of motion while "
            "maintaining buoyancy. Front-zip closure with secure buckle system."
        ),
        "short_description": "Premium neoprene life vest for active water sports",
        "base_price_ngn": "24000",
        "compare_at_price_ngn": "28000",
        "cost_price_ngn": "14751",
        "is_featured": True,
        "has_variants": True,
        "variant_options": {"Size": ["S", "M", "L"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-SAF-002",
        "preorder_lead_days": 7,
        "image_seed": "lifevest",
    },
    # ===== BAGS & STORAGE =====
    {
        "name": "Mesh Swim Drawstring Bag",
        "slug": "mesh-swim-drawstring-bag",
        "category_key": "bags",
        "description": (
            "Breathable polyester mesh drawstring bag for carrying wet swim gear. "
            "Allows air circulation to prevent odours. Available in two sizes and "
            "a range of colours."
        ),
        "short_description": "Breathable mesh drawstring bag for swim gear",
        "base_price_ngn": "4000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "1762",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Size": ["Small (35x45cm)", "Large (45x55cm)"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-BAG-001",
        "preorder_lead_days": 15,
        "image_seed": "meshbag",
    },
    {
        "name": "Waterproof PU Gym Duffle",
        "slug": "waterproof-pu-gym-duffle",
        "category_key": "bags",
        "description": (
            "Stylish PU leather gym duffle with waterproof lining to keep wet and "
            "dry items separate. Spacious main compartment with shoe pocket and "
            "multiple organiser pockets."
        ),
        "short_description": "Waterproof PU gym duffle with wet/dry compartments",
        "base_price_ngn": "20000",
        "compare_at_price_ngn": "24000",
        "cost_price_ngn": "12418",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-BAG-002",
        "preorder_lead_days": 35,
        "image_seed": "gymduffle1",
    },
    {
        "name": "Waterproof Canvas Sports Backpack",
        "slug": "waterproof-canvas-sports-backpack",
        "category_key": "bags",
        "description": (
            "Durable canvas sports backpack (48×25×24 cm) with waterproof "
            "coating. Padded laptop sleeve, multiple pockets, and adjustable "
            "straps. Perfect for swimmers who go straight from pool to work."
        ),
        "short_description": "Waterproof canvas backpack for active lifestyles",
        "base_price_ngn": "17500",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "10507",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-BAG-003",
        "preorder_lead_days": 31,
        "image_seed": "canvasbackpack",
    },
    {
        "name": "Multi-Compartment Gym Duffle",
        "slug": "multi-compartment-gym-duffle",
        "category_key": "bags",
        "description": (
            "PU leather gym duffle (48×23×25 cm) with multiple dedicated "
            "compartments for shoes, wet gear, and valuables. Detachable shoulder "
            "strap and reinforced handles for comfortable carry."
        ),
        "short_description": "PU duffle with dedicated shoe and wet-gear compartments",
        "base_price_ngn": "19000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "11666",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-BAG-004",
        "preorder_lead_days": 35,
        "image_seed": "gymduffle2",
    },
    {
        "name": "PU Yoga & Swim Duffel Tote",
        "slug": "pu-yoga-swim-duffel-tote",
        "category_key": "bags",
        "description": (
            "Versatile PU duffel tote (57×10×29 cm) that converts between "
            "shoulder bag and tote modes. Separate wet compartment with "
            "waterproof lining ideal for post-swim gear."
        ),
        "short_description": "Convertible PU duffel tote with wet compartment",
        "base_price_ngn": "25000",
        "compare_at_price_ngn": "29000",
        "cost_price_ngn": "15489",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-BAG-005",
        "preorder_lead_days": 35,
        "image_seed": "duffeltote",
    },
    {
        "name": "Outdoor Sport Duffle Backpack",
        "slug": "outdoor-sport-duffle-backpack",
        "category_key": "bags",
        "description": (
            "Large PU duffle (60×28×30 cm) with backpack straps for hands-free "
            "carry. Generous capacity fits all your swim and gym gear with room "
            "to spare. Water-resistant exterior."
        ),
        "short_description": "Large PU duffle with backpack straps",
        "base_price_ngn": "20000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "12524",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-BAG-006",
        "preorder_lead_days": 31,
        "image_seed": "sportduffle",
    },
    {
        "name": "PU Leather Travel Duffel",
        "slug": "pu-leather-travel-duffel",
        "category_key": "bags",
        "description": (
            "Premium PU leather travel duffel (56×23×25 cm) with classic styling. "
            "Multiple internal pockets and padded base keep your belongings "
            "organised and protected during swim meets and travel."
        ),
        "short_description": "Premium PU leather travel duffel for swim meets",
        "base_price_ngn": "27000",
        "compare_at_price_ngn": "32000",
        "cost_price_ngn": "17145",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-BAG-007",
        "preorder_lead_days": 35,
        "image_seed": "leatherduffel",
    },
    {
        "name": "Multifunctional Travel Backpack",
        "slug": "multifunctional-travel-backpack",
        "category_key": "bags",
        "description": (
            "Versatile PU travel backpack (65×10×30 cm) with multiple access "
            "points and organiser pockets. Anti-theft back panel and USB charging "
            "port. Ideal for swim commuters and travellers."
        ),
        "short_description": "Multifunctional PU travel backpack with USB port",
        "base_price_ngn": "19000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "11681",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-BAG-008",
        "preorder_lead_days": 7,
        "image_seed": "travelbackpack",
    },
    # ===== SUN PROTECTION =====
    {
        "name": "UV400 Sports Sunglasses",
        "slug": "uv400-sports-sunglasses",
        "category_key": "sun_protection",
        "description": (
            "Lightweight PC frame sports sunglasses with UV400 protection. "
            "Wrap-around design shields eyes from glare during poolside coaching, "
            "outdoor meets, and open-water events."
        ),
        "short_description": "UV400 wrap-around sports sunglasses",
        "base_price_ngn": "4500",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "1822",
        "is_featured": False,
        "has_variants": True,
        "variant_options": {"Color": ["Black", "Blue", "Red", "White"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-SUN-001",
        "preorder_lead_days": 7,
        "image_seed": "sportsunglasses",
    },
    {
        "name": "Retro Polarised Sunglasses",
        "slug": "retro-polarised-sunglasses",
        "category_key": "sun_protection",
        "description": (
            "Stylish retro-round metal frame sunglasses with TAC polarised "
            "lenses. Eliminates glare from water surfaces for clear poolside "
            "vision. Premium hinges and comfortable nose pads."
        ),
        "short_description": "Retro polarised sunglasses with metal frame",
        "base_price_ngn": "10000",
        "compare_at_price_ngn": "12000",
        "cost_price_ngn": "5479",
        "is_featured": True,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-SUN-002",
        "preorder_lead_days": 7,
        "image_seed": "retrosunglasses",
    },
    # ===== MAINTENANCE & CARE =====
    {
        "name": "Goggle Anti-Fog Solution 15ml",
        "slug": "goggle-anti-fog-solution",
        "category_key": "maintenance",
        "description": (
            "Professional-grade anti-fog solution for swimming goggles and dive "
            "masks. A single drop creates a clear, fog-free coating that lasts an "
            "entire session. Compact 15 ml bottle fits in any swim bag."
        ),
        "short_description": "Professional anti-fog drops for goggles and masks",
        "base_price_ngn": "2000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "500",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-MNT-001",
        "preorder_lead_days": 14,
        "image_seed": "antifog",
    },
    {
        "name": "2-in-1 Chlorine Removal Shampoo 240ml",
        "slug": "chlorine-removal-shampoo-240ml",
        "category_key": "maintenance",
        "description": (
            "Gentle 2-in-1 shampoo and conditioner formulated to remove chlorine "
            "buildup from hair. Natural aloe vera and herbal extracts moisturise "
            "and protect colour-treated hair. 240 ml bottle."
        ),
        "short_description": "2-in-1 chlorine removal shampoo with aloe vera",
        "base_price_ngn": "6500",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "3161",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-MNT-002",
        "preorder_lead_days": 25,
        "image_seed": "chlorineshampoo",
    },
    {
        "name": "Post-Swim Cleansing Gel 251ml",
        "slug": "post-swim-cleansing-gel-251ml",
        "category_key": "maintenance",
        "description": (
            "Organic cleansing gel designed to neutralise chlorine and restore "
            "skin after swimming. Natural botanical extracts soothe irritation "
            "and replenish moisture. 251 ml pump bottle."
        ),
        "short_description": "Organic post-swim cleansing gel for skin recovery",
        "base_price_ngn": "5000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "2138",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-MNT-003",
        "preorder_lead_days": 20,
        "image_seed": "cleansinggel",
    },
    {
        "name": "Chlorine Removal Body Wash",
        "slug": "chlorine-removal-body-wash",
        "category_key": "maintenance",
        "description": (
            "Natural chlorine-neutralising body wash that removes pool chemicals "
            "while nourishing the skin. Cherry-scented organic formula is gentle "
            "enough for daily use by frequent swimmers."
        ),
        "short_description": "Natural chlorine-neutralising body wash for swimmers",
        "base_price_ngn": "7000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "3613",
        "is_featured": False,
        "has_variants": False,
        "variant_options": None,
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-MNT-004",
        "preorder_lead_days": 15,
        "image_seed": "bodywash",
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
            "silver-mirrored-racing-goggles",
            "marble-silicone-swim-cap",
            "silicone-training-fins",
            "fina-shark-skin-racing-jammer",
            "yingfa-womens-competitive-swimsuit",
        ],
    },
    {
        "name": "Competition Essentials",
        "slug": "competition-essentials",
        "description": "FINA-approved and professional-grade gear for competitive swimmers.",
        "sort_order": 2,
        "product_slugs": [
            "fina-shark-skin-racing-jammer",
            "fina-womens-racing-swimsuit",
            "yingfa-mid-leg-trunks",
            "yingfa-womens-competitive-swimsuit",
            "quick-dry-performance-jammer",
        ],
    },
    {
        "name": "Training Must-Haves",
        "slug": "training-must-haves",
        "description": "Essential equipment for structured swim training sessions.",
        "sort_order": 3,
        "product_slugs": [
            "eva-kickboard-standard",
            "eva-training-pull-buoy",
            "silicone-hand-training-paddles",
            "classic-frontal-centre-snorkel",
            "silicone-training-fins",
        ],
    },
    {
        "name": "Poolside Favourites",
        "slug": "poolside-favourites",
        "description": "Most popular accessories and care products for regular swimmers.",
        "sort_order": 4,
        "product_slugs": [
            "retro-polarised-sunglasses",
            "neoprene-performance-life-vest",
            "mens-full-body-swimsuit",
            "goggle-anti-fog-solution",
            "mesh-swim-drawstring-bag",
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
            cost_price_ngn=(
                Decimal(p["cost_price_ngn"]) if p.get("cost_price_ngn") else None
            ),
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
