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
import uuid
from decimal import Decimal
from itertools import product as iterproduct

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
    ProductVideo,
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
    "swimbuddz-training-kickboard": "https://www.alibaba.com/product-detail/Children-s-Adult-s-EVA-Foam_1601048480430.html",
    "swimbuddz-pro-kickboard": "https://www.alibaba.com/product-detail/EVA-Hand-Paddles-Adult-Kickboard-Plate_1601590587735.html",
    "swimbuddz-kids-kickboard": "https://www.alibaba.com/product-detail/Kick-Board-For-Swimming-Training-EVA_1601697854582.html",
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

# ---------------------------------------------------------------------------
# PRODUCT MEDIA — Real images and videos scraped from Alibaba product pages
#
# Maps product slugs to their gallery image URLs and video URLs.
# Images are from Alibaba CDN (s.alicdn.com). First image is the primary.
# ---------------------------------------------------------------------------
PRODUCT_MEDIA = {
    "swim-resistance-parachute": {
        "images": [
            "https://s.alicdn.com/@sc01/kf/H013d438fd0f444c2bc30e7d7d8f58a6fS.jpg",
            "https://s.alicdn.com/@sc01/kf/H0d4c40b3a90c40498a5d494a7f5206a2a.png",
            "https://s.alicdn.com/@sc01/kf/Hc8d21612ea5f442a8207a772f358be71o.jpg",
            "https://s.alicdn.com/@sc01/kf/Hc6dd309b133b4ece8b90a29a59795c34H.jpg",
            "https://s.alicdn.com/@sc01/kf/H51b4dca75f40485b9e1f966bac58638al.jpg",
            "https://s.alicdn.com/@sc01/kf/Hb8d481a0ec2e4779be411159a64d5f3bg.png",
            "https://s.alicdn.com/@sc01/kf/H460b614033394b0e86b48ad3e250c974S.jpg",
            "https://s.alicdn.com/@sc01/kf/Hb54c59a50b8b4e5d8c10336d2a04faccw.jpg",
        ],
        "videos": [],
    },
    "finis-3m-swim-parachute": {
        "images": [
            "https://s.alicdn.com/@sc01/kf/H062d3daa109d4efcb795563661ea1901H.jpg",
            "https://s.alicdn.com/@sc01/kf/H46b4550f32b94de7972c283bcd160120t.jpg",
            "https://s.alicdn.com/@sc01/kf/Heba81cfe898a439899629ad6fb1e9f62A.jpg",
            "https://s.alicdn.com/@sc01/kf/Hcd31222c896543cab26ece93f10fc1bdX.jpg",
            "https://s.alicdn.com/@sc01/kf/Hed270fe347d04295b3e0e0d9b98a7ee45.jpg",
            "https://s.alicdn.com/@sc01/kf/H123a5fcf243547bab60414b5a7d8d463h.png",
            "https://s.alicdn.com/@sc01/kf/HTB1_pbdh5QnBKNjSZFmq6AApVXaI.jpg",
            "https://s.alicdn.com/@sc01/kf/H287649a3d3014d5c8decb00f19e09067r.jpg",
        ],
        "videos": [],
    },
    "adjustable-swimming-parachute": {
        "images": [
            "https://sc04.alicdn.com/kf/Hb90201209f974ff1bd2e72aa1271cf16g.jpg",
            "https://sc04.alicdn.com/kf/H02de11d78ba54a0fb47b9812007dda9eK.jpg",
            "https://sc04.alicdn.com/kf/H1dcc4516d6ce4fee9d8f656ac8f8f4893.jpg",
            "https://sc04.alicdn.com/kf/H8d81c4b05ede45048c2b2ea37b0cbf0ci.jpg",
            "https://sc04.alicdn.com/kf/H46d53ca9cff04032b90e5a0d89f7f61eU.jpg",
            "https://sc04.alicdn.com/kf/H0939b64096bb45f095d475dacaab191ce.jpg",
        ],
        "videos": [
            "https://cloud.video.taobao.com/play/u/null/p/1/e/6/t/1/387079046751.mp4",
        ],
    },
    "swimbuddz-training-kickboard": {
        "images": [
            "https://s.alicdn.com/@sc04/kf/H894d2c9d0bae437b9d901d807f52fc6ef.jpg",
            "https://s.alicdn.com/@sc04/kf/Hf22bd2cc12334dd28ce6828822f80e638.png",
            "https://s.alicdn.com/@sc04/kf/H04ec02911bb64404b58c631582bfe626B.jpg",
            "https://s.alicdn.com/@sc04/kf/Hec8ecf1265d444e782bf3fa957d16074a.png",
            "https://s.alicdn.com/@sc04/kf/H90e8d86799b047bea167619e75c05d54H.png",
            "https://s.alicdn.com/@sc04/kf/H60cb0e60f30d44a591986b3a7df69483t.png",
            "https://sc04.alicdn.com/kf/A8e9a867027ff458d9f0359ed11f7f1aau.jpg",
            "https://sc04.alicdn.com/kf/A5320d3640b564eb5b3569497bf149a85E.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/4f4e1c368ac918af/9aec281716a32225/20240306_df94e2ecf068d40a_451781548036_mp4_264_hd_unlimit_taobao.mp4",
        ],
    },
    "swimbuddz-pro-kickboard": {
        "images": [
            "https://sc04.alicdn.com/kf/Hbd9543343875452cb14dd4d31587599f8.jpg",
            "https://sc04.alicdn.com/kf/Hbe4935d75c5747459b5ce78383baa3342.jpg",
            "https://sc04.alicdn.com/kf/H5286d6bbe2b745aab70548182b552a60y.jpg",
            "https://sc04.alicdn.com/kf/H1f38fe7c69b34a82bc69af124a0b27d11.jpg",
            "https://sc04.alicdn.com/kf/He99d18a5879f45a49c891c537ee7e193I.jpg",
            "https://sc04.alicdn.com/kf/Hfaccf085bdd04061bb82c631e28657fes.jpg",
        ],
        "videos": [],
    },
    "swimbuddz-kids-kickboard": {
        "images": [
            "https://s.alicdn.com/@sc04/kf/H223a3b2f077f4fb7aa60fdf941ddd5c7v.jpg",
            "https://s.alicdn.com/@sc04/kf/He90255658f3c4300a48137359ab06326x.jpg",
            "https://s.alicdn.com/@sc04/kf/Hf86c598c787d459683d810e005ff075f9.jpg",
            "https://s.alicdn.com/@sc04/kf/H8579a32037ae448db5a01e86dfd28e33y.jpg",
            "https://s.alicdn.com/@sc04/kf/Hfa5f46de93374912a44188f261e1f766e.jpg",
        ],
        "videos": [
            "https://cloud.video.taobao.com/play/u/null/p/1/e/6/t/1/387079422182.mp4",
        ],
    },
    "eva-training-pull-buoy": {
        "images": [
            "https://sc04.alicdn.com/kf/H0f412632c63f4316a8af4aa338d6c1dcO.jpg",
            "https://sc04.alicdn.com/kf/Hbf160aa1958741a89ed7cb3125cf6bf3P.jpg",
            "https://sc04.alicdn.com/kf/H49d2525d4c8540e28e824abc82a32a7ac.jpg",
            "https://sc04.alicdn.com/kf/H9d2e1a959dee4952a5271218ef33e4671.jpg",
            "https://sc04.alicdn.com/kf/H0bc4001259e84f48838af796adf9ba8a8.jpg",
            "https://sc04.alicdn.com/kf/Hb7b2f9dc5aee494685b08df69dcc979e3.jpg",
        ],
        "videos": [],
    },
    "silicone-training-fins": {
        "images": [
            "https://sc04.alicdn.com/kf/H92b0c7f6f8eb45eba41d3f51215f8f96G.jpg",
            "https://sc04.alicdn.com/kf/H8c7f0f80040d407bbe4451458bac899fz.jpg",
            "https://sc04.alicdn.com/kf/Hc5a76993d9604d1e9393ee70d4963b3eU.jpg",
            "https://sc04.alicdn.com/kf/Hc470c5e3d3f04b90b39093660d46d4a5m.jpg",
            "https://sc04.alicdn.com/kf/H89f934c93311440d82f6107df602c3e0E.jpg",
            "https://sc04.alicdn.com/kf/H914d560ffa0549a8aff7b30d464ba2b9P.jpg",
        ],
        "videos": [],
    },
    "rubber-dive-swim-fins": {
        "images": [
            "https://sc04.alicdn.com/kf/H92bbcd67de984c528d59461b8a3a853bv.png",
            "https://sc04.alicdn.com/kf/H20c20eccea4148eab84f80dc2d31360aJ.png",
            "https://sc04.alicdn.com/kf/Hed47905590714727b547020d4960507cY.png",
            "https://sc04.alicdn.com/kf/H4fc17da9fc7f4b60addbbb1e94313f2a1.png",
            "https://sc04.alicdn.com/kf/H796b0949e16a41389e9c35ae4d0a4c8cq.png",
        ],
        "videos": [],
    },
    "short-blade-bodyboard-fins": {
        "images": [
            "https://sc04.alicdn.com/kf/Ha1f61c03a647448e85b5dcbde7ec2554Q.jpg",
            "https://sc04.alicdn.com/kf/H0a929337780b4488a3be0437977ec031a.jpg",
            "https://sc04.alicdn.com/kf/Heee2729001b54f2991445cdbceb04c77e.jpg",
            "https://sc04.alicdn.com/kf/H70c3725a1d18423ebe581c1942607ab1p.jpg",
            "https://sc04.alicdn.com/kf/Hf14e40e042714e18817d927c6306170dk.jpg",
            "https://sc04.alicdn.com/kf/He1fd5232e79e478a941b2036ca20d42fb.jpg",
        ],
        "videos": [
            "https://cloud.video.alibaba.com/play/u/2153292369/p/1/e/6/t/1/d/hd/266316024104.mp4",
        ],
    },
    "tpr-training-flippers": {
        "images": [
            "https://sc04.alicdn.com/kf/H758a825c7a454fa3b0af5c8ee1b450b16.png",
            "https://sc04.alicdn.com/kf/H103b6ed0d5cf4dd9a766ef6491818fd6h.jpg",
            "https://sc04.alicdn.com/kf/H74a248e4c14a47bfa87d9b6176c3c256A.jpg",
            "https://sc04.alicdn.com/kf/Hf9b9550b6ab746ebbae5bd1cbe0ee989g.jpg",
            "https://sc04.alicdn.com/kf/H684d0eb8a03a4c98885c0a2017b5c7c3c.jpg",
            "https://sc04.alicdn.com/kf/Ha161cfee50b6441e93c3c9c07847238ba.jpg",
            "https://sc04.alicdn.com/kf/H94bdd0612cf04a91a6a27dcebcf4419fl.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv99-28d67e3a-a1acb27a-990eaf5a-7f29/trans/aidc/gxa24w-h264-hd.mp4?bizCode=icbu_vod_video",
            "https://cloud.video.alibaba.com/play/u/2153292369/p/1/e/6/t/1/d/hd/6000309802969.mp4",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv99-28d67e3a-a1acb27a-990eaf5a-7f29/trans/aidc/jxqn8b-h264-sd.mp4",
        ],
    },
    "adjustable-mermaid-fins": {
        "images": [
            "https://sc04.alicdn.com/kf/Hec9a698cac9948b5ad4b0291f995c6dee.png",
            "https://sc04.alicdn.com/kf/H05a82c641de64badb2eb92964d12250bO.jpg",
            "https://sc04.alicdn.com/kf/H9d74d2b764f84f3784c093b45dc2e16fW.jpg",
            "https://sc04.alicdn.com/kf/Hd276b9537ad94110b6153d46b48f0f9eK.jpg",
            "https://sc04.alicdn.com/kf/H9656c23761584968a9d32438f4e61919b.jpg",
            "https://sc04.alicdn.com/kf/H82b0de55ea2f4442a14675afa70b0f5bV.jpg",
            "https://sc04.alicdn.com/kf/H1cbc31fb8f5645efb012812132bd3d97S.jpg",
        ],
        "videos": [],
    },
    "silicone-hand-training-paddles": {
        "images": [
            "https://sc04.alicdn.com/kf/H8d8c69e591f6490d9efb3b1e56c155557.png",
            "https://sc04.alicdn.com/kf/H5a029dbb66ef40dbaa9957505608a91fi.png",
            "https://sc04.alicdn.com/kf/Hbe7b2991ebb94c4bae62a265bd411e36q.jpg",
            "https://sc04.alicdn.com/kf/H9dfd0e94989d4910b1ae3d50306e6c74o.png",
            "https://sc04.alicdn.com/kf/H82bba19f120343038c254af58686a372R.jpg",
            "https://sc04.alicdn.com/kf/Hd04db4c2468148ebb07832c2ec82b71bT.jpg",
            "https://sc04.alicdn.com/kf/H493457c96bce401c92b95c7328bbe0c0u.jpg",
            "https://sc04.alicdn.com/kf/Hc2384370e18b4a2e9be17ca42bd123afB.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv98-a1493807-a1d01ea5-98835be5-28fc/trans/aidc/wub6lv-h264-hd.mp4?bizCode=icbu_vod_video",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv98-a1493807-a1d01ea5-98835be5-28fc/trans/aidc/swhvdd-h264-sd.mp4",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv98-a1493807-a1d01ea5-98835be5-28fc/trans/aidc/bja7td-h264-ld.mp4",
        ],
    },
    "classic-frontal-centre-snorkel": {
        "images": [
            "https://sc04.alicdn.com/kf/Hf057a27ffce54860bcb36b8e03ff0d71t.png",
            "https://sc04.alicdn.com/kf/Hd434e8998dd145e182f0b3e1e646a2bfb.jpg",
            "https://sc04.alicdn.com/kf/H065ab46b607c4138bc998e3b0ca7a0eaL.jpg",
            "https://sc04.alicdn.com/kf/H4621def7f05d47aa82865b4e4e3581b5A.jpg",
            "https://sc04.alicdn.com/kf/Hfe6a66418e4a4282ae2b4df6ddd4a02ea.jpg",
            "https://sc04.alicdn.com/kf/Hbf316ee3291049f89444a29ec6526077c.jpg",
            "https://sc04.alicdn.com/kf/Ha5972676e73242f188265c52c713cf96s.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv96-38ad410f-a1ace553-96340ce8-0f6b/trans/aidc/1gwkhy-h264-hd.mp4?bizCode=icbu_vod_video",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv96-38ad410f-a1ace553-96340ce8-0f6b/trans/aidc/ksyths-h264-sd.mp4",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv96-38ad410f-a1ace553-96340ce8-0f6b/trans/aidc/o33hrl-h264-ld.mp4",
        ],
    },
    "semi-dry-frontal-training-snorkel": {
        "images": [
            "https://sc04.alicdn.com/kf/H9a5fbb7f4ef549659fa3482aa939de448.png",
            "https://sc04.alicdn.com/kf/H0c961c956d09409bb9655114e7e186e0H.png",
            "https://sc04.alicdn.com/kf/H43d4315158f94e938cafdaac4b91b789a.png",
            "https://sc04.alicdn.com/kf/H15f735495f2a43e7b97d7f68454f88dfk.png",
            "https://sc04.alicdn.com/kf/Hbf8a8601261248019b3239af4da0c8876.png",
            "https://sc04.alicdn.com/kf/Hbbf120cd7d574e1aade39ea5bc6502d8D.png",
        ],
        "videos": [],
    },
    "epe-foam-pool-noodle": {
        "images": [
            "https://sc04.alicdn.com/kf/H8e186302c87a4827887daf3da70cc3b9q.jpg",
            "https://sc04.alicdn.com/kf/H7ebdede38e2e484aa3d33aa29ac951d6H.jpg",
            "https://sc04.alicdn.com/kf/Hb6ad779c85e3400eb3eb8579c1af4015i.jpg",
            "https://sc04.alicdn.com/kf/H434ccfbf94a44cd3891d15da8fdba49cd.jpg",
            "https://sc04.alicdn.com/kf/Hc36abe002c60420791370b10a71cab99f.jpg",
            "https://sc04.alicdn.com/kf/H25ccfb8c218a42478dfc4aff98fbfb5bi.jpg",
        ],
        "videos": [
            "https://cloud.video.taobao.com/play/u/null/p/1/e/6/t/1/387079046751.mp4",
        ],
    },
    "anti-fog-uv-swimming-goggles": {
        "images": [
            "https://sc04.alicdn.com/kf/H64941fd31a7841edb223fac87b6160e1b.jpg",
            "https://sc04.alicdn.com/kf/Hce22973b2cdc4329b97dec44c1790fb9E.jpg",
            "https://sc04.alicdn.com/kf/H6467695d48044a079f68f4e244d27ae8A.jpg",
            "https://sc04.alicdn.com/kf/H27f0113ea3c648698ac81d0122ac4d428.jpg",
            "https://sc04.alicdn.com/kf/H937fbd18173642b4b0611dc733b27590T.jpg",
            "https://sc04.alicdn.com/kf/H3530c1a1fedc4efaa08f28d93213cd44x.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/4f4e1c368ac918af/0ad089ac612909a6/20240802_c079495018f54f88_474883625500_mp4_264_hd_unlimit_taobao.mp4?bizCode=icbu_vod_video",
            "https://gv.videocdn.alibaba.com/4f4e1c368ac918af/0ad089ac612909a6/20240802_f9acaf4815fdc59a_474883625500_mp4_264_sd_unlimit_taobao.mp4",
            "https://gv.videocdn.alibaba.com/4f4e1c368ac918af/0ad089ac612909a6/20240802_0a0143906d29f3ea_474883625500_mp4_264_ld_unlimit_taobao.mp4",
        ],
    },
    "arena-racing-goggles": {
        "images": [
            "https://sc04.alicdn.com/kf/Hefd0ce06e4bc41cbb1c46cba96ea3aa8I.jpg",
            "https://sc04.alicdn.com/kf/Ha336b77416e64680bd58733e3b088ac93.jpg",
            "https://sc04.alicdn.com/kf/H1dc47bffe4ca42da9239075bc5ccbef0O.jpg",
            "https://sc04.alicdn.com/kf/H0994c3fe40ef4e80b4bcdaec16a444c38.jpg",
            "https://sc04.alicdn.com/kf/Hd3a4d787013e43389ba95e7636a455c0R.jpg",
            "https://sc04.alicdn.com/kf/H18f1965d6d87473d9594f13933a38819F.png",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/4f4e1c368ac918af/8863581d26656b1f/20240731_af562caa7d1f1bd0_474887479387_mp4_264_hd_unlimit_taobao.mp4?bizCode=icbu_vod_video",
            "https://gv.videocdn.alibaba.com/4f4e1c368ac918af/8863581d26656b1f/20240731_0e893fe661b748f1_474887479387_mp4_264_sd_unlimit_taobao.mp4",
            "https://gv.videocdn.alibaba.com/4f4e1c368ac918af/8863581d26656b1f/20240731_139614ee435487fb_474887479387_mp4_264_ld_unlimit_taobao.mp4",
        ],
    },
    "silver-mirrored-racing-goggles": {
        "images": [
            "https://sc04.alicdn.com/kf/H9ca3297233944ac892b0a239d5d47f7bM.png",
            "https://sc04.alicdn.com/kf/Hfc6ab1c4445a4aa1b06361f99315189fJ.jpg",
            "https://sc04.alicdn.com/kf/H5f9ea1df91ea44b9be03e0c9856f8d89F.png",
            "https://sc04.alicdn.com/kf/Hb6643aba1c914c55b063968cf7f1309eu.jpg",
            "https://sc04.alicdn.com/kf/H8672c4560d974cce9039bc15240dcd2ew.jpg",
            "https://sc04.alicdn.com/kf/H1c3a8d75f33a41c08432066dc7460e48L.jpg",
            "https://sc04.alicdn.com/kf/Hb69f2e6746824b28962b2ee77f92b1cdL.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv94-f3deaec0-8b397136-94edebcf-07d5/trans/4e9e726e-0d56-455a-a0d8-8732cb951529-h264-hd.mp4?bizCode=icbu_vod_video",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv94-f3deaec0-8b397136-94edebcf-07d5/trans/f0b148cc-1715-4e9d-87a4-f066cff35928-h264-sd.mp4",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv94-f3deaec0-8b397136-94edebcf-07d5/trans/5af8827b-f92d-4503-887f-5e862ca9e29c-h264-ld.mp4",
        ],
    },
    "marble-silicone-swim-cap": {
        "images": [
            "https://sc04.alicdn.com/kf/H810dbf3ce2de4b1aa26c65d7c2a6d2fbL.png",
            "https://sc04.alicdn.com/kf/H54868e8b88c74fdf870911a159f144da3.jpg",
            "https://sc04.alicdn.com/kf/Ha4a4047ab3de45ddbe236202245cfb67s.png",
            "https://sc04.alicdn.com/kf/Hf5a5f61f6af4454f8f745ad1f2fa5bc7C.png",
            "https://sc04.alicdn.com/kf/H06b8933b38b54e2fa4a1ad05ba508f26Q.png",
            "https://sc04.alicdn.com/kf/Ha439f553801b489694323b86254ea2b6w.png",
        ],
        "videos": [
            "https://cloud.video.alibaba.com/play/u/2153292369/p/1/e/6/t/1/d/hd/6000304962436.mp4",
        ],
    },
    "eva-hard-goggle-case": {
        "images": [
            "https://sc04.alicdn.com/kf/Hb7c2a9317ddd4f50841a5118f95077895.jpg",
            "https://sc04.alicdn.com/kf/H360221d7d82f49d591cb8829bf8b8179j.jpg",
            "https://sc04.alicdn.com/kf/H02b32b0f5c3248e08c028c459ba84388L.jpg",
            "https://sc04.alicdn.com/kf/Hcb1a4a79dd8a441eb824ea98d1392ba8W.jpg",
            "https://sc04.alicdn.com/kf/H825a6bb4b58e4929acf169975b088b7dM.jpg",
            "https://sc04.alicdn.com/kf/Hea364455b98e47ff83b16421a9da391fp.jpg",
            "https://sc04.alicdn.com/kf/H2c182b4d9de047898623026f5013f87dk.jpg",
            "https://sc04.alicdn.com/kf/H40ba57fec22e4bd69c9db066d707e9d16.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/4f4e1c368ac918af/2c600c9366dace9e/20220805_aa7420b8786892cf_372339091160_mp4_264_hd_unlimit_taobao.mp4?bizCode=icbu_vod_video",
            "https://cloud.video.alibaba.com/play/u/2153292369/p/1/e/6/t/1/d/hd/415464423600.mp4",
            "https://gv.videocdn.alibaba.com/4f4e1c368ac918af/2c600c9366dace9e/20220805_cf3a610dde0f46f2_372339091160_mp4_264_sd_unlimit_taobao.mp4",
        ],
    },
    "silicone-nose-clip": {
        "images": [
            "https://sc04.alicdn.com/kf/H4d0372a0d9b04d87bbd3445ad41ea3d9t.png",
            "https://sc04.alicdn.com/kf/Hbf507c7c809549b6b52828260407e00eG.png",
            "https://sc04.alicdn.com/kf/H8eb1df0b923e462fad19b386e5953f10k.png",
            "https://sc04.alicdn.com/kf/H605b074ec4be40bab673355b19649061V.png",
            "https://sc04.alicdn.com/kf/H6ced0002716f4030840d81cd1d05a218a.png",
        ],
        "videos": [],
    },
    "chlorine-resistant-jammer": {
        "images": [
            "https://sc04.alicdn.com/kf/H0c490bb0c95249d6b1da136c0a15dd30t.jpg",
            "https://sc04.alicdn.com/kf/H6ac6dbe019224621a4a2ec32902c3b52Q.jpg",
            "https://sc04.alicdn.com/kf/H890c8f9bc1d14204b2d7b0cd46288c0b4.jpg",
            "https://sc04.alicdn.com/kf/H71232e5477ea4c3a93e41dd7e17316eeu.jpg",
            "https://sc04.alicdn.com/kf/Ha98ec27a5d99407eba9c6dc552b26bb8O.jpg",
            "https://sc04.alicdn.com/kf/Hf110b53c868948a5995c90c4fe236e4c2.jpg",
        ],
        "videos": [],
    },
    "competition-racing-jammer": {
        "images": [
            "https://sc04.alicdn.com/kf/HTB1BnGEaIfrK1RjSszcq6xGGFXa9.jpg",
            "https://sc04.alicdn.com/kf/HTB1N.qCaOnrK1Rjy1Xcq6yeDVXaG.jpg",
            "https://sc04.alicdn.com/kf/HTB1HG1NaUrrK1RkSne1q6ArVVXaG.jpg",
            "https://sc04.alicdn.com/kf/HTB1StSzaOrxK1RkHFCcq6AQCVXa1.jpg",
            "https://sc04.alicdn.com/kf/HTB1.IaCaOfrK1RjSspbq6A4pFXak.jpg",
            "https://sc04.alicdn.com/kf/HTB1PG1NaUrrK1RkSne1q6ArVVXaH.jpg",
        ],
        "videos": [],
    },
    "quick-dry-performance-jammer": {
        "images": [
            "https://sc04.alicdn.com/kf/HTB1wHcRdfWG3KVjSZFPq6xaiXXaC.jpg",
            "https://sc04.alicdn.com/kf/HTB1HlsRdliE3KVjSZFMq6zQhVXaN.jpg",
            "https://sc04.alicdn.com/kf/HTB1iVZTdoGF3KVjSZFvq6z_nXXaG.jpg",
            "https://sc04.alicdn.com/kf/HTB18TZQdgmH3KVjSZKzq6z2OXXaV.jpg",
            "https://sc04.alicdn.com/kf/HTB1op7Qdf1H3KVjSZFHq6zKppXaO.jpg",
            "https://sc04.alicdn.com/kf/HTB1s0kQdf1H3KVjSZFBq6zSMXXa0.jpg",
        ],
        "videos": [],
    },
    "yingfa-mid-leg-trunks": {
        "images": [
            "https://sc04.alicdn.com/kf/H32fe9b34aa3f4da2b3d89ad4a28fdb84j.jpg",
            "https://sc04.alicdn.com/kf/H8a9a0a4144d94622bb93f5a3ed1a9a375.jpg",
            "https://sc04.alicdn.com/kf/He859f8cb547d4039865a65610bb3b57bZ.jpg",
            "https://sc04.alicdn.com/kf/H8a408b05183b495fb5ebba82b0100ad7Y.jpg",
            "https://sc04.alicdn.com/kf/H60953daee5f048c9bcfca91ef1694a33Y.jpg",
            "https://sc04.alicdn.com/kf/Hcd0be31eab3c40d3b6613962b81bec9fH.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/d567ba523987367c/c7aL2COyGPyHJvbK5ir/0oLWmtDzLhrFWDMCmn6_370503034655_mp4_264_hd_unlimit_taobao.mp4?bizCode=icbu_vod_video",
            "https://cloud.video.alibaba.com/play/u/2153292369/p/1/e/6/t/1/d/hd/371054839189.mp4",
            "https://gv.videocdn.alibaba.com/b7ee7bc9fb7ff343/c7aL2COyGPyHJvbK5ir/0oLWmtDzLhrFWDMCmn6_370503034655_mp4_264_sd_unlimit_taobao.mp4",
        ],
    },
    "sharkskin-performance-jammers": {
        "images": [
            "https://sc04.alicdn.com/kf/HTB1jEeBnk7mBKNjSZFyq6zydFXal.jpg",
            "https://sc04.alicdn.com/kf/HTB1YZZ2JVuWBuNjSszbq6AS7FXaT.jpg",
            "https://sc04.alicdn.com/kf/HTB1d4XdB8yWBuNkSmFPq6xguVXa4.jpg",
            "https://sc04.alicdn.com/kf/HTB1B4W6nbsrBKNjSZFpq6AXhFXa9.jpg",
            "https://sc04.alicdn.com/kf/HTB1ZVwVJ1uSBuNjSsplq6ze8pXak.jpg",
            "https://sc04.alicdn.com/kf/HTB1pniuKhGYBuNjy0Fnq6x5lpXav.jpg",
        ],
        "videos": [],
    },
    "mens-custom-swim-briefs": {
        "images": [
            "https://sc04.alicdn.com/kf/A1a9d33cc28de48e892f1926d8d54d4e7u.jpg",
            "https://sc04.alicdn.com/kf/A372bce737325492291d209ee8dbe2b5ab.jpg",
            "https://sc04.alicdn.com/kf/A746f73f49d88418e9b22b1a2aaeef22dR.jpg",
            "https://sc04.alicdn.com/kf/A755e731f3f7d480ca0faa4d1678f8c02F.jpg",
            "https://sc04.alicdn.com/kf/Ae76eb9404106430bb148006b280299e9G.jpg",
            "https://sc04.alicdn.com/kf/A2a303f2724ee4b20b430332a8237d992B.jpg",
        ],
        "videos": [],
    },
    "mens-full-body-swimsuit": {
        "images": [
            "https://sc04.alicdn.com/kf/Sf15114a4992146a48d5e58785078fc2fX.jpg",
            "https://sc04.alicdn.com/kf/Sf4bc2be152ba4ebd99bbd3cd05dd4e5aX.jpg",
            "https://sc04.alicdn.com/kf/S6f3e30a559be47a78e5df2548e272ef7V.jpg",
            "https://sc04.alicdn.com/kf/Sb99a35f9cb4442ef80d0e6c4e8de3824l.jpg",
            "https://sc04.alicdn.com/kf/S6e0da32c115d4b20b987233a98aa8c099.jpg",
            "https://sc04.alicdn.com/kf/S4f42acd2eccd47f78b6c2f89a5f661b4Z.jpg",
        ],
        "videos": [],
    },
    "fina-shark-skin-racing-jammer": {
        "images": [
            "https://sc04.alicdn.com/kf/Hd5c6423ba7364009bb5b6baaeea245e5w.jpg",
            "https://sc04.alicdn.com/kf/Hc5acaa6a2d244433b551f444f505e476h.jpg",
            "https://sc04.alicdn.com/kf/Ha200e7f29af24bb39f1a3aa2df0f4dcas.jpg",
            "https://sc04.alicdn.com/kf/Hb0fb71cf781044f4a53afdcaff8116c1b.jpg",
            "https://sc04.alicdn.com/kf/H5c64268a81f6426ab14bb7d400c91bff5.jpg",
            "https://sc04.alicdn.com/kf/Hbc13e9ba98074775bb5294e40a8984e1Y.jpg",
        ],
        "videos": [],
    },
    "womens-short-sleeve-one-piece": {
        "images": [
            "https://sc04.alicdn.com/kf/Ha883de13f51d4f228d1e90ed0c5c6e60O.jpg",
            "https://sc04.alicdn.com/kf/H6dd713094b814b67acbd757959d6fd787.jpg",
            "https://sc04.alicdn.com/kf/Hd038bd7089294d9f85c0d88175ce8474d.jpg",
            "https://sc04.alicdn.com/kf/Hef1326d61f4641ff8e3f5ea85ded31a1M.jpg",
            "https://sc04.alicdn.com/kf/H9f1f1047185647689fde4bc70ad2440dM.jpg",
            "https://sc04.alicdn.com/kf/Hc2ca9ca6d2114d928c312a64ab620f1bX.jpg",
        ],
        "videos": [
            "https://cloud.video.alibaba.com/play/u/2153292369/p/1/e/6/t/1/d/hd/6000289670387.mp4",
        ],
    },
    "womens-two-piece-sports-swimsuit": {
        "images": [
            "https://sc04.alicdn.com/kf/H6d4cbf7c1abe4a8da1b26bb9a869100c3.jpg",
            "https://sc04.alicdn.com/kf/Hace0f88a32a2435083fa3c1d145f0d416.jpg",
            "https://sc04.alicdn.com/kf/H5ea3b06e5a8441aaa34cbe17c192b1fbE.jpg",
            "https://sc04.alicdn.com/kf/Hbcddbd6b46ac46f89557e1d57e782097b.jpg",
            "https://sc04.alicdn.com/kf/Ha55191a0eb1e4ceeb81de6890ad84fe2w.jpg",
            "https://sc04.alicdn.com/kf/Hd797d3ef34974431a88a32e078d00e51T.jpg",
        ],
        "videos": [
            "https://cloud.video.alibaba.com/play/u/2153292369/p/1/e/6/t/1/d/hd/448510597463.mp4",
        ],
    },
    "womens-long-sleeve-eco-swimsuit": {
        "images": [
            "https://sc04.alicdn.com/kf/H73e7544cd4b447fc8275af6a8fe83998R.jpg",
            "https://sc04.alicdn.com/kf/Ha8abf5e82ba349728e4a66837b259e36C.jpg",
            "https://sc04.alicdn.com/kf/H770e2b4b632d49d3b073dcf6ecc533033.jpg",
            "https://sc04.alicdn.com/kf/Hca0944edb23d410d909f9e6436b649b9n.jpg",
            "https://sc04.alicdn.com/kf/H7d05f3c0411f4d0fb574e849eff6edb5Z.png",
            "https://sc04.alicdn.com/kf/H4879013da33145b38579bfb39c6568ebE.jpg",
            "https://sc04.alicdn.com/kf/Hef3fff301d6648b7a5be3b7adbd20b92h.jpg",
            "https://sc04.alicdn.com/kf/H0e6212450cfb429693428dae4816ff7dt.jpg",
        ],
        "videos": [
            "https://cloud.video.alibaba.com/play/u/2153292369/p/1/e/6/t/1/d/hd/448510597463.mp4",
        ],
    },
    "womens-plus-size-fitness-swimwear": {
        "images": [
            "https://sc04.alicdn.com/kf/A4284c73dcadf4bc288b37fceac273b819.jpg",
            "https://sc04.alicdn.com/kf/Aac3d23a821aa4741ac5e76f893edd731K.jpg",
            "https://sc04.alicdn.com/kf/Ad6b8e948e4d845e197849272d61dfabay.jpg",
            "https://sc04.alicdn.com/kf/A4536999a8a0a4bb7b264d05bcfe05f87g.jpg",
            "https://sc04.alicdn.com/kf/A52f0f5e1896a4d65850ed60ab3c625b5b.jpg",
            "https://sc04.alicdn.com/kf/Afbb59e1813b3482eb2f79507fac1a9eeW.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv98-54f2c99f-a1bfe0ad-98363640-669a/trans/aidc/f9x21g-h264-hd.mp4?bizCode=icbu_vod_video",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv98-54f2c99f-a1bfe0ad-98363640-669a/trans/aidc/yl6bzj-h264-sd.mp4",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv98-54f2c99f-a1bfe0ad-98363640-669a/trans/aidc/tnfm5n-h264-ld.mp4",
        ],
    },
    "womens-printed-sports-swimsuit": {
        "images": [
            "https://sc04.alicdn.com/kf/Aba5965d87d8a4c08929718ebd317fe90C.jpg",
            "https://sc04.alicdn.com/kf/A60890d4541dd47a1b95722eab48ff82ad.jpg",
            "https://sc04.alicdn.com/kf/A4ec79e86f9a541e7ac32872aeb06fe5f2.jpg",
            "https://sc04.alicdn.com/kf/A489bdbb465f64b4fb3f97edfa1a7a79aa.jpg",
            "https://sc04.alicdn.com/kf/A48f5c2c998544a96a644491c54ea5f123.jpg",
            "https://sc04.alicdn.com/kf/A37797e95e76c45b3aa6bf9558ce570dfn.jpg",
        ],
        "videos": [],
    },
    "yingfa-womens-competitive-swimsuit": {
        "images": [
            "https://sc04.alicdn.com/kf/He2ed6ef781494c198d6ccea88e6dca84P.png",
            "https://sc04.alicdn.com/kf/Hea7a6bc30ff04c00bf3ddb4495e86e40c.jpg",
            "https://sc04.alicdn.com/kf/H7cf8ec08803242d1a2e46b06e2e0cb594.jpg",
            "https://sc04.alicdn.com/kf/H180cadf5c19f4883885bc8fa326bdb00R.jpg",
            "https://sc04.alicdn.com/kf/H4e0e309f4579419793f4641d5c3e60dbD.jpg",
            "https://sc04.alicdn.com/kf/H8cc1c688a7a840b8aea15695be913602w.jpg",
            "https://sc04.alicdn.com/kf/He5093252a3334d47b80b7f32cc90d101n.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv9a-7bc75a88-a18409ca-99e6e2a2-1b10/trans/aidc/vst4kw-h264-hd.mp4?bizCode=icbu_vod_video",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv9a-7bc75a88-a18409ca-99e6e2a2-1b10/trans/aidc/kzolth-h264-sd.mp4",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv9a-7bc75a88-a18409ca-99e6e2a2-1b10/trans/aidc/vuhunf-h264-ld.mp4",
        ],
    },
    "full-coverage-two-piece-swim-set": {
        "images": [
            "https://sc04.alicdn.com/kf/A369f83174f394bc28b112f86bdb35d35B.jpeg",
            "https://sc04.alicdn.com/kf/A14ef0d04ae2947b4aeaa83b4ca109f53c.jpeg",
            "https://sc04.alicdn.com/kf/A05bfde40c52147f9a27ad6b8b9dd10586.jpeg",
            "https://sc04.alicdn.com/kf/A6222785e2ba048e7a10955431e56299ak.jpeg",
            "https://sc04.alicdn.com/kf/A1feec875bd6e4c38a5b3d0376d348290g.jpeg",
            "https://sc04.alicdn.com/kf/Ae2f86f37c5f74c9b843a7091a5e20706W.jpeg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv9a-34fccb4c-8bd1e1ce-99e5e708-1c3f/trans/aidc/82qbpm-h264-hd.mp4?bizCode=icbu_vod_video",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv9a-34fccb4c-8bd1e1ce-99e5e708-1c3f/trans/aidc/rbeczw-h264-sd.mp4",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv9a-34fccb4c-8bd1e1ce-99e5e708-1c3f/trans/aidc/5o8fy3-h264-ld.mp4",
        ],
    },
    "fina-womens-racing-swimsuit": {
        "images": [
            "https://sc04.alicdn.com/kf/He2ed6ef781494c198d6ccea88e6dca84P.png",
            "https://sc04.alicdn.com/kf/H1e19b82652ed4d8caf41d427202dd6ad7.jpg",
            "https://sc04.alicdn.com/kf/Hb6e3cdd184724532ae3f9d2006e5e325c.jpg",
            "https://sc04.alicdn.com/kf/Hf9341d487f0b456ca80772139cdd11047.jpg",
            "https://sc04.alicdn.com/kf/H546b90cadb204e6198a900c357a487f2p.jpg",
            "https://sc04.alicdn.com/kf/H5206ac85a1fc473583e7c03b07767696k.jpg",
            "https://sc04.alicdn.com/kf/H08133195d1a84e4fb60f21c0efbd4f4bK.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv9a-7bbd6a68-a1b350eb-99e6e272-5f0c/trans/aidc/f8ddct-h264-hd.mp4?bizCode=icbu_vod_video",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv9a-7bbd6a68-a1b350eb-99e6e272-5f0c/trans/aidc/xynkwz-h264-sd.mp4",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv9a-7bbd6a68-a1b350eb-99e6e272-5f0c/trans/aidc/u2wyh5-h264-ld.mp4",
        ],
    },
    "oxford-fabric-life-jacket": {
        "images": [
            "https://sc04.alicdn.com/kf/Hef924899e54544c38fb67f3547a2a938a.jpg",
            "https://sc04.alicdn.com/kf/H7f4b66b2a9fa4a468a3c31eb908718ffE.png",
            "https://sc04.alicdn.com/kf/Haedea2fc94e84acaa585d395039b33e8p.jpg",
            "https://sc04.alicdn.com/kf/Hb8c0f23c940f42c89234466e08c8e982F.jpg",
            "https://sc04.alicdn.com/kf/H58f90d4837674a30a03316f77a173155o.jpg",
            "https://sc04.alicdn.com/kf/Hbd17a4065bbf4b1da642d6ae867d7f4cv.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv99-fca23887-a1ab2d16-99e6a8af-04e7/trans/aidc/n9hm2o-h264-hd.mp4?bizCode=icbu_vod_video",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv99-fca23887-a1ab2d16-99e6a8af-04e7/trans/aidc/bm6fcp-h264-sd.mp4",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv99-fca23887-a1ab2d16-99e6a8af-04e7/trans/aidc/czystt-h264-ld.mp4",
        ],
    },
    "neoprene-performance-life-vest": {
        "images": [
            "https://sc04.alicdn.com/kf/Hc66854d6cf384a358d188916d14788bdc.jpg",
            "https://sc04.alicdn.com/kf/Ha48dce8f5d4f4016b0a9e04aa4ab2392z.jpg",
            "https://sc04.alicdn.com/kf/H0cf0f5bf9b4e4418994197d57962164dj.jpg",
            "https://sc04.alicdn.com/kf/H63439701272b473daad8c0329d796a84f.jpg",
            "https://sc04.alicdn.com/kf/H66a90ffa5d104bc7b002d2991f1c3136e.jpg",
            "https://sc04.alicdn.com/kf/H3f933ad1d6994468964dd895586cec3dj.jpg",
            "https://sc04.alicdn.com/kf/Hd6fa6919554c4a539fdf68e03d78687cP.jpg",
            "https://sc04.alicdn.com/kf/Hf5ad64c4cfa74052942f654d15e05433p.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv9a-3e2ccf15-a1bc35c4-99e67c62-6b00/trans/aidc/e42agh-h264-hd.mp4?bizCode=icbu_vod_video",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv9a-3e2ccf15-a1bc35c4-99e67c62-6b00/trans/aidc/ufd21w-h264-sd.mp4",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv9a-3e2ccf15-a1bc35c4-99e67c62-6b00/trans/aidc/wt5dll-h264-ld.mp4",
        ],
    },
    "mesh-swim-drawstring-bag": {
        "images": [
            "https://sc04.alicdn.com/kf/H2fbb2058c61744839ef41b9b97eeac9fj.jpg",
            "https://sc04.alicdn.com/kf/Hcc08c6e5ab01469d80a3177e42e4a2dbK.jpg",
            "https://sc04.alicdn.com/kf/H3144ee3e421b4f16ac95d67469c5ca9fb.jpg",
            "https://sc04.alicdn.com/kf/H161f67f2f7b442048173e4c78204f5c4f.jpg",
            "https://sc04.alicdn.com/kf/Hc18d8f51763f4609a0bec965878af2602.jpg",
            "https://sc04.alicdn.com/kf/H876b39dd8c82488eb404991b73828fabD.jpg",
        ],
        "videos": [],
    },
    "waterproof-pu-gym-duffle": {
        "images": [
            "https://sc04.alicdn.com/kf/Hebfcfede1a124b68b4458b6d34b0dc0c7.png",
            "https://sc04.alicdn.com/kf/H5f0f1bbb3d024ea29875b5e181aaa713s.png",
            "https://sc04.alicdn.com/kf/He0ae457b308546c290f392c76439b22c0.png",
            "https://sc04.alicdn.com/kf/Hda79101aa1e6416e8f32a08663dddc835.png",
            "https://sc04.alicdn.com/kf/H85ee49898b8d445db2f83ac2335537b3R.png",
            "https://sc04.alicdn.com/kf/Hc163bd6de6424e55a329d2c40be508c43.png",
            "https://sc04.alicdn.com/kf/H7a192c147e8247c8ad6050c287a2ac29o.jpg",
            "https://sc04.alicdn.com/kf/Hf0f95c2c14db43d5b60bdd1e579d1f6fz.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv99-192a023a-a18409ca-990eca4c-51e6/trans/aidc/4f8rlk-h264-hd.mp4?bizCode=icbu_vod_video",
            "https://cloud.video.alibaba.com/play/u/2153292369/p/1/e/6/t/1/d/hd/6000310092114.mp4",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv99-192a023a-a18409ca-990eca4c-51e6/trans/aidc/cnarab-h264-sd.mp4",
        ],
    },
    "waterproof-canvas-sports-backpack": {
        "images": [
            "https://sc04.alicdn.com/kf/Hebfcfede1a124b68b4458b6d34b0dc0c7.png",
            "https://sc04.alicdn.com/kf/H5f0f1bbb3d024ea29875b5e181aaa713s.png",
            "https://sc04.alicdn.com/kf/He0ae457b308546c290f392c76439b22c0.png",
            "https://sc04.alicdn.com/kf/Hda79101aa1e6416e8f32a08663dddc835.png",
            "https://sc04.alicdn.com/kf/H85ee49898b8d445db2f83ac2335537b3R.png",
            "https://sc04.alicdn.com/kf/Hc163bd6de6424e55a329d2c40be508c43.png",
            "https://sc04.alicdn.com/kf/H78a238bc94b445e998de8f4008720169x.jpg",
            "https://sc04.alicdn.com/kf/H084069fe0c1c479bb3857c691d46163cT.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv97-af4009a0-a18409ca-96eba1e5-40db/trans/aidc/dip9sv-h264-hd.mp4?bizCode=icbu_vod_video",
            "https://cloud.video.alibaba.com/play/u/2153292369/p/1/e/6/t/1/d/hd/350964833034.mp4",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv97-af4009a0-a18409ca-96eba1e5-40db/trans/aidc/qjmfkz-h264-sd.mp4",
        ],
    },
    "multi-compartment-gym-duffle": {
        "images": [
            "https://sc04.alicdn.com/kf/Hebfcfede1a124b68b4458b6d34b0dc0c7.png",
            "https://sc04.alicdn.com/kf/H5f0f1bbb3d024ea29875b5e181aaa713s.png",
            "https://sc04.alicdn.com/kf/He0ae457b308546c290f392c76439b22c0.png",
            "https://sc04.alicdn.com/kf/Hda79101aa1e6416e8f32a08663dddc835.png",
            "https://sc04.alicdn.com/kf/H85ee49898b8d445db2f83ac2335537b3R.png",
            "https://sc04.alicdn.com/kf/Hc163bd6de6424e55a329d2c40be508c43.png",
            "https://sc04.alicdn.com/kf/H955879d79be14d2bbb657e017f87b2d87.jpg",
            "https://sc04.alicdn.com/kf/He227382a787540eeabe4b54b9024f34ea.jpg",
        ],
        "videos": [
            "https://cloud.video.taobao.com/play/u/null/p/1/e/6/t/1/387079422182.mp4",
        ],
    },
    "pu-yoga-swim-duffel-tote": {
        "images": [
            "https://sc04.alicdn.com/kf/Hebfcfede1a124b68b4458b6d34b0dc0c7.png",
            "https://sc04.alicdn.com/kf/H5f0f1bbb3d024ea29875b5e181aaa713s.png",
            "https://sc04.alicdn.com/kf/He0ae457b308546c290f392c76439b22c0.png",
            "https://sc04.alicdn.com/kf/Hda79101aa1e6416e8f32a08663dddc835.png",
            "https://sc04.alicdn.com/kf/H85ee49898b8d445db2f83ac2335537b3R.png",
            "https://sc04.alicdn.com/kf/Hc163bd6de6424e55a329d2c40be508c43.png",
            "https://sc04.alicdn.com/kf/Haf8b24b5fbe44c99a31a03c9c4ed806ey.jpg",
            "https://sc04.alicdn.com/kf/H2915a00b02054fa6ae3d5eb075a6e4475.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv95-eb78b43e-a1ace553-9545fec9-292b/trans/aidc/lppavq-h264-hd.mp4?bizCode=icbu_vod_video",
            "https://cloud.video.alibaba.com/play/u/2153292369/p/1/e/6/t/1/d/hd/6000295418555.mp4",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv95-eb78b43e-a1ace553-9545fec9-292b/trans/aidc/h4ju5g-h264-sd.mp4",
        ],
    },
    "outdoor-sport-duffle-backpack": {
        "images": [
            "https://sc04.alicdn.com/kf/Hebfcfede1a124b68b4458b6d34b0dc0c7.png",
            "https://sc04.alicdn.com/kf/H5f0f1bbb3d024ea29875b5e181aaa713s.png",
            "https://sc04.alicdn.com/kf/He0ae457b308546c290f392c76439b22c0.png",
            "https://sc04.alicdn.com/kf/Hda79101aa1e6416e8f32a08663dddc835.png",
            "https://sc04.alicdn.com/kf/H85ee49898b8d445db2f83ac2335537b3R.png",
            "https://sc04.alicdn.com/kf/Hc163bd6de6424e55a329d2c40be508c43.png",
            "https://sc04.alicdn.com/kf/Hd02a84f456574a60ac6eea9ff0ac15b1m.jpg",
            "https://sc04.alicdn.com/kf/Hc75414436ae9497b9e20d7ca316c1077O.jpg",
        ],
        "videos": [
            "https://cloud.video.taobao.com/play/u/null/p/1/e/6/t/1/387079422182.mp4",
        ],
    },
    "pu-leather-travel-duffel": {
        "images": [
            "https://sc04.alicdn.com/kf/Hebfcfede1a124b68b4458b6d34b0dc0c7.png",
            "https://sc04.alicdn.com/kf/H5f0f1bbb3d024ea29875b5e181aaa713s.png",
            "https://sc04.alicdn.com/kf/He0ae457b308546c290f392c76439b22c0.png",
            "https://sc04.alicdn.com/kf/Hda79101aa1e6416e8f32a08663dddc835.png",
            "https://sc04.alicdn.com/kf/H85ee49898b8d445db2f83ac2335537b3R.png",
            "https://sc04.alicdn.com/kf/Hc163bd6de6424e55a329d2c40be508c43.png",
            "https://sc04.alicdn.com/kf/H4b1ed2df08e244ec8a2e408dc76d8b946.jpg",
            "https://sc04.alicdn.com/kf/Hff98c1f24d6b48a29113184c6227cd13s.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv98-e474b68a-a1ab2d16-98cbdec8-74b8/trans/aidc/k0wfjz-h264-hd.mp4?bizCode=icbu_vod_video",
            "https://cloud.video.alibaba.com/play/u/2153292369/p/1/e/6/t/1/d/hd/6000308986452.mp4",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv98-e474b68a-a1ab2d16-98cbdec8-74b8/trans/aidc/p7unlh-h264-sd.mp4",
        ],
    },
    "multifunctional-travel-backpack": {
        "images": [
            "https://sc04.alicdn.com/kf/Hc716dccdeeba49e39b5672f7bc0b4474p.png",
            "https://sc04.alicdn.com/kf/H4bc2d0510bd74eddbee919d2366836a41.jpg",
            "https://sc04.alicdn.com/kf/Hebfcfede1a124b68b4458b6d34b0dc0c7.png",
            "https://sc04.alicdn.com/kf/H5f0f1bbb3d024ea29875b5e181aaa713s.png",
            "https://sc04.alicdn.com/kf/He0ae457b308546c290f392c76439b22c0.png",
            "https://sc04.alicdn.com/kf/Hda79101aa1e6416e8f32a08663dddc835.png",
            "https://sc04.alicdn.com/kf/H85ee49898b8d445db2f83ac2335537b3R.png",
            "https://sc04.alicdn.com/kf/Hc163bd6de6424e55a329d2c40be508c43.png",
        ],
        "videos": [
            "https://cloud.video.taobao.com/play/u/null/p/1/e/6/t/1/387079422182.mp4",
        ],
    },
    "uv400-sports-sunglasses": {
        "images": [
            "https://sc04.alicdn.com/kf/H284f977d716942288a9d40a841d24af9w.jpg",
            "https://sc04.alicdn.com/kf/H4e88213721eb4af1ac3b8638c0a2f092i.jpg",
            "https://sc04.alicdn.com/kf/Hf3485f5e17fb48c080c316a01b7cc0a1O.jpg",
            "https://sc04.alicdn.com/kf/H4220426f6a7f4f95a28b6efa7a56ca95c.jpg",
            "https://sc04.alicdn.com/kf/H857ac520dd6b4c1dbc611ec5d48b4d0bk.jpg",
            "https://sc04.alicdn.com/kf/He30a4eb645ed4f66acd9ccc444657fe6B.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv93-bde96059-a1bf917b-93b8dcd9-151a/trans/d88c3ca5-1541-403e-acba-b4fee5060671-h264-hd.mp4?bizCode=icbu_vod_video",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv93-bde96059-a1bf917b-93b8dcd9-151a/trans/6ed76ef7-88b7-491a-afce-873b6f4e7fe3-h264-sd.mp4",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv93-bde96059-a1bf917b-93b8dcd9-151a/trans/dc6859b2-4a2c-4db1-af17-c3b5c41c446b-h264-ld.mp4",
        ],
    },
    "retro-polarised-sunglasses": {
        "images": [
            "https://sc04.alicdn.com/kf/Hc05a1c0e54684fe8b03c3c9a6429449ca.png",
            "https://sc04.alicdn.com/kf/Hc25cd5fe0ee64150a31a5817263ffd21R.jpg",
            "https://sc04.alicdn.com/kf/H4b152faa44fc4cd78f599ac875cbe1720.jpg",
            "https://sc04.alicdn.com/kf/H597cd61c8d474d61aeec378e3a0e52769.jpg",
            "https://sc04.alicdn.com/kf/H864cf651389e44f9b5811bd0cf5b041cy.jpg",
            "https://sc04.alicdn.com/kf/H269a0c0f49a6417d87027af183a517fap.jpg",
            "https://sc04.alicdn.com/kf/H5fb73fc8aa294e4da597eddb25a0d044x.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv99-8512c785-a1ab2d16-992daa51-0ff5/trans/aidc/op76ou-h264-hd.mp4?bizCode=icbu_vod_video",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv99-8512c785-a1ab2d16-992daa51-0ff5/trans/aidc/9vls7u-h264-sd.mp4",
            "https://gv.videocdn.alibaba.com/icbu_vod_video/video_target/gv99-8512c785-a1ab2d16-992daa51-0ff5/trans/aidc/ltcrii-h264-ld.mp4",
        ],
    },
    "goggle-anti-fog-solution": {
        "images": [
            "https://sc04.alicdn.com/kf/Hcf8f3cc6d7d44d87b8ad8cc936bd3f0cb.jpg",
            "https://sc04.alicdn.com/kf/H667ac054a05346feb7d44e36609d14056.jpg",
            "https://sc04.alicdn.com/kf/H22447972df894d6dbc249c589540dd4bR.jpg",
            "https://sc04.alicdn.com/kf/Habf4acf83f2b4316ac369316e0637e82G.jpg",
            "https://sc04.alicdn.com/kf/Hb55e4724ce2640dfbe79f5c099edf2a7R.jpg",
            "https://sc04.alicdn.com/kf/Hc2e8ede8bb554c3f835fda3e73512cc2X.jpg",
        ],
        "videos": [],
    },
    "chlorine-removal-shampoo-240ml": {
        "images": [
            "https://sc04.alicdn.com/kf/Hb0ea85800f974c22a09f6108f0e238760.jpg",
            "https://sc04.alicdn.com/kf/He613a6ba43b54717a38ba2b9c6c0781aQ.jpg",
            "https://sc04.alicdn.com/kf/H152cdf4cbdc34a738d4748916a2e9445A.jpg",
            "https://sc04.alicdn.com/kf/H49ea240127254be0a620b4741966212dT.jpg",
            "https://sc04.alicdn.com/kf/Hd7efe0bdb04046939fd7dd16f31e8ec6B.jpg",
        ],
        "videos": [],
    },
    "post-swim-cleansing-gel-251ml": {
        "images": [
            "https://sc04.alicdn.com/kf/H227552dc4e494a209bcc359554caf729t.jpg",
            "https://sc04.alicdn.com/kf/H7fddb390dc424ad3ba2010e6da875d49n.jpg",
            "https://sc04.alicdn.com/kf/Hb42ad73fc4ab487aa10c19742230f3e2Y.jpg",
            "https://sc04.alicdn.com/kf/H8e504360bd914592a0616dc4717044e8J.jpg",
            "https://sc04.alicdn.com/kf/H69015ecb36834619ac86ebd8ae945816O.jpg",
            "https://sc04.alicdn.com/kf/H7da49c04ed0f4007bce7dbc22594da55p.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/4f4e1c368ac918af/34c81995c92bc885/20231231_63050014fcc99333_444035032908_mp4_264_hd_unlimit_taobao.mp4?bizCode=icbu_vod_video",
            "https://gv.videocdn.alibaba.com/4f4e1c368ac918af/34c81995c92bc885/20231231_66f5acc26250c76c_444035032908_mp4_264_sd_unlimit_taobao.mp4",
            "https://gv.videocdn.alibaba.com/4f4e1c368ac918af/34c81995c92bc885/20231231_abda083eba7dfa89_444035032908_mp4_264_ld_unlimit_taobao.mp4",
        ],
    },
    "chlorine-removal-body-wash": {
        "images": [
            "https://sc04.alicdn.com/kf/H2c3e2bcbb0d54850b12d744ef2290487i.jpg",
            "https://sc04.alicdn.com/kf/H4149ee967e2b4c58943f6c99f9da28a5R.png",
            "https://sc04.alicdn.com/kf/H58a3ceb7eadd47cf9bc8ab22187560abk.png",
            "https://sc04.alicdn.com/kf/Hcbf7958e4a79433a86aa2e9fdc291d90k.jpg",
        ],
        "videos": [
            "https://gv.videocdn.alibaba.com/avpl/icbu_video/copy/0188-b3a756a1-a1d0cc07-8847c5e7-6a4c/20230613_764a43c007b2be17_414973326952_mp4_264_hd_unlimit_taobao.mp4?bizCode=icbu_vod_video",
            "https://gv.videocdn.alibaba.com/avpl/icbu_video/copy/0188-b3a753a2-a1bcc3f3-8847bf24-1973/20230613_eb4015e800e99f06_414973326952_mp4_264_sd_unlimit_taobao.mp4",
            "https://gv.videocdn.alibaba.com/avpl/icbu_video/copy/0188-b3a75228-a18745a8-8847a862-14c6/20230613_269bfa2658c28662_414973326952_mp4_264_ld_unlimit_taobao.mp4",
        ],
    },
}

# ---------------------------------------------------------------------------
# PRODUCT SWATCHES — Color swatch/thumbnail image URLs from Alibaba
#
# Maps product slugs to {color_name: swatch_image_url} for colors with images.
# Used on product detail pages for Temu-style color swatch selectors.
# ---------------------------------------------------------------------------
PRODUCT_SWATCHES = {
    "chlorine-resistant-jammer": {
        "Black": "https://sc04.alicdn.com/kf/Hf9a95d8793c5449d89a9cd9ee79a02c6Z.jpg",
    },
    "eva-hard-goggle-case": {
        "Black": "https://sc04.alicdn.com/kf/H02640dfea924483b9555a87284dbece6n.jpg",
        "Green": "https://sc04.alicdn.com/kf/H433224383436400d84e3745e733f471eM.jpg",
        "Blue": "https://sc04.alicdn.com/kf/H859abd01834a42e69281db7333007ac1B.jpg",
        "Purple": "https://sc04.alicdn.com/kf/H08b4402329b646e985f1393c64fc8609o.jpg",
        "Rose": "https://sc04.alicdn.com/kf/H19af3bfabc2645a381a889e2de71c4ecz.jpg",
        "Red": "https://sc04.alicdn.com/kf/Hd24ebd398e2b4e85aaefba65bde688c5j.jpg",
        "Grey": "https://sc04.alicdn.com/kf/H175004da61164e5fbe8cdb06d79adb830.jpg",
        "Orange": "https://sc04.alicdn.com/kf/H13995f2eb20548dbbf81b36b4ca854cdQ.jpg",
    },
    "swimbuddz-training-kickboard": {
        "Blue": "https://s.alicdn.com/@sc04/kf/H5c938d040a2b4d9391b375580cccfe95z.jpg",
        "Green": "https://s.alicdn.com/@sc04/kf/H5310f2dba2df4aab9d19f7625fdbb08dF.jpg",
        "Orange": "https://s.alicdn.com/@sc04/kf/H55e0afaaeedd4af1967e678e16e404b7q.jpg",
        "Pink": "https://s.alicdn.com/@sc04/kf/Hd513de12eabf4025875d2806ffb7ddf3W.jpg",
    },
    "fina-shark-skin-racing-jammer": {
        "Black": "https://sc04.alicdn.com/kf/H648e78a719bf46c1b18804b340da040ay.jpg",
        "Blue": "https://sc04.alicdn.com/kf/He0da381f56fb459880ce06b77756db70q.jpg",
    },
    "finis-3m-swim-parachute": {
        "Light Blue": "https://sc04.alicdn.com/kf/H1412b982c51c47f3948a3744dfc3e4d6K.jpg",
        "Yellow": "https://sc04.alicdn.com/kf/Hf4cd72ed8f094c689fcd7c5a259189f6z.png",
    },
    "marble-silicone-swim-cap": {
        "Black": "https://sc04.alicdn.com/kf/Hc6f0bec4317b4eccacfb61d176ab5c0fX.jpg",
        "Pink": "https://sc04.alicdn.com/kf/H523e90fe58224537b7437e80e8e61e98y.jpg",
        "White": "https://sc04.alicdn.com/kf/He433c5ce1ba94470af59cd6f32935606x.jpg",
        "Red": "https://sc04.alicdn.com/kf/H24d1fffc2e674b87b6e3b8566fdfb36bL.jpg",
        "Yellow": "https://sc04.alicdn.com/kf/H732169dbaa0e4b1181b68aa1f72f6c09R.jpg",
        "Orange": "https://sc04.alicdn.com/kf/H6c893edd0cd64a55bbbe45b9b2a8e957C.jpg",
        "Purple": "https://sc04.alicdn.com/kf/H11c72cbbbfa443818100d826d8c99d96v.jpg",
        "Green": "https://sc04.alicdn.com/kf/H63b4639dc5da4c399aa495d1f52805cak.jpg",
    },
    "mesh-swim-drawstring-bag": {
        "Black": "https://sc04.alicdn.com/kf/H19c30fa075da4adb81e4efabf8450a8bD.jpg",
        "Fluorescent Green": "https://sc04.alicdn.com/kf/Hc088e93a4430432ebd6ac101022dd4f7D.jpg",
        "Blue": "https://sc04.alicdn.com/kf/H0a84b9efe22c4938beb14d2869576e84t.jpg",
        "Orange": "https://sc04.alicdn.com/kf/Hc58eddd7a8344ab08fbbc49dda934d2a4.jpg",
        "Gray": "https://sc04.alicdn.com/kf/Hd42837e6de72483a822b4e2dcda074ccO.jpg",
        "Yellow-Green Gradient": "https://sc04.alicdn.com/kf/Hea6136374f654968a814c7d82e5db4e8S.jpg",
        "Orange Green Gradient": "https://sc04.alicdn.com/kf/H0b6542e589d04ac38d2ef6cd16d03cdcr.jpg",
        "Black Gray Gradient": "https://sc04.alicdn.com/kf/H536126446be244e796a584d6ba77f9f1f.jpg",
    },
    "multi-compartment-gym-duffle": {
        "Black": "https://sc04.alicdn.com/kf/H2a25a216f28a4b5ab2280f7de797782al.jpg",
        "Gray": "https://sc04.alicdn.com/kf/Hb2f78b26aa1e4f47b5ef785118fbb867g.jpg",
        "Green": "https://sc04.alicdn.com/kf/Haed8a4694ad04f2caac252ff29bdd1313.jpg",
    },
    "multifunctional-travel-backpack": {
        "Black": "https://sc04.alicdn.com/kf/H3ab264fd485745ed9f9ca00f54f15e759.jpg",
        "Gray": "https://sc04.alicdn.com/kf/He2626e490a8f499a8797049e6e1e597cx.png",
    },
    "neoprene-performance-life-vest": {
        "Black": "https://sc04.alicdn.com/kf/Hdb135757dd8b4b6d8f174faa8ff71299J.jpg",
    },
    "outdoor-sport-duffle-backpack": {
        "Gray": "https://sc04.alicdn.com/kf/H1cc6ad6b5b614aa8a27127ac928e8538G.jpg",
        "Black": "https://sc04.alicdn.com/kf/Haa7ae2c73dd64a4090432ff2f4745b5eZ.jpg",
    },
    "pu-leather-travel-duffel": {
        "Black": "https://sc04.alicdn.com/kf/H3be4ce6e12574c669270454c045e6a260.jpg",
        "Gray": "https://sc04.alicdn.com/kf/H6e020dd1b41d460fba09a3df8e901106N.jpg",
        "Deep Blue": "https://sc04.alicdn.com/kf/H2fa2e7eadf6446a281f37b88ea5864f1e.jpg",
        "White": "https://sc04.alicdn.com/kf/H8857a586647143749e65e14d3a0155904.jpg",
        "Green": "https://sc04.alicdn.com/kf/Hf256d258ffef4383beb8a824af6d41car.jpg",
    },
    "quick-dry-performance-jammer": {
        "Black": "https://sc04.alicdn.com/kf/HTB15PARdf1G3KVjSZFkq6yK4XXa7.jpg",
        "Navy": "https://sc04.alicdn.com/kf/HTB1kNp4aLBj_uVjSZFpq6A0SXXaP.jpg",
    },
    "semi-dry-frontal-training-snorkel": {
        "Blue": "https://sc04.alicdn.com/kf/H90dfe74926884c6cb5cebde339f80999M.png",
        "Green": "https://sc04.alicdn.com/kf/H047c9fdf3e794853b9453734e8a4d0bfK.png",
        "Yellow": "https://sc04.alicdn.com/kf/H63544bad48fc4686b3878701926a5d2c5.png",
        "Black": "https://sc04.alicdn.com/kf/Hf349a8e0932541a4846ec005f45718eeS.png",
        "White": "https://sc04.alicdn.com/kf/H7735057a76014e26a1ae4467df788af1R.png",
    },
    "sharkskin-performance-jammers": {
        "Orange": "https://sc04.alicdn.com/kf/HTB1A6n.m9MmBKNjSZTE761sKpXaH.png",
        "Black": "https://sc04.alicdn.com/kf/HTB1U.yInbArBKNjSZFLq6A_dVXa8.jpg",
        "Blue": "https://sc04.alicdn.com/kf/HTB1ZsFpKeSSBuNjy0Flq6zBpVXau.jpg",
        "Dark Grey": "https://sc04.alicdn.com/kf/HTB1wX6Mm0knBKNjSZKP7606OFXa4.png",
    },
    "silicone-hand-training-paddles": {
        "Black": "https://sc04.alicdn.com/kf/H65e12ae3069a4a6d99a9dbeb6d221881j.jpg",
        "Blue": "https://sc04.alicdn.com/kf/Hfafc64077abc43beb74e9c87a0d6e3c5c.jpg",
        "Silver": "https://sc04.alicdn.com/kf/H9dc37b7b797f4f27a207ce08ec80f842x.jpg",
        "Red": "https://sc04.alicdn.com/kf/Hb9356cacdda743648a1bcbb7c3b175b0H.jpg",
        "Pink": "https://sc04.alicdn.com/kf/Ha45221492af34d5f8ed571e7188a3df0w.jpg",
        "Green": "https://sc04.alicdn.com/kf/Hf8e06bf3eeb44c75a4b3c728c1c58ce7q.jpg",
        "Orange-Pink": "https://sc04.alicdn.com/kf/H5365b18dbaa9483cb199efa4d1319dddM.jpg",
        "Fluorescent Green": "https://sc04.alicdn.com/kf/H9894aec2ca1244a283f284ea9cb9d74dE.jpg",
    },
    "silicone-nose-clip": {
        "Black": "https://sc04.alicdn.com/kf/H99b3af8b1eb5469cb921eeb8c60e8953H.png",
        "White": "https://sc04.alicdn.com/kf/H2cd3ffa46ba74f4896c54f5c23187022x.png",
        "Pink": "https://sc04.alicdn.com/kf/He94007dafeae4ed0bb02adb69baecbeaN.png",
        "Green": "https://sc04.alicdn.com/kf/H7b4f464175c54eccb320ddea18396c25e.png",
        "Yellow": "https://sc04.alicdn.com/kf/Hd551b960f22240d4bf69d30bea65a5e9F.png",
        "Blue": "https://sc04.alicdn.com/kf/Hddd57b7ad17644a28a9311cc5703e96af.png",
    },
    "silver-mirrored-racing-goggles": {
        "Black": "https://sc04.alicdn.com/kf/H912a3c000ba344c196ca0884f23505feJ.jpg",
        "White": "https://sc04.alicdn.com/kf/H232f3920ec3b4ab99d71ba8e2d48fe56U.jpg",
        "Blue": "https://sc04.alicdn.com/kf/H4865f29246264c9baa2504f28e25ac407.jpg",
    },
    "waterproof-canvas-sports-backpack": {
        "Black": "https://sc04.alicdn.com/kf/H79749b0581b1470088ad8717bb259a80x.jpg",
        "Gray": "https://sc04.alicdn.com/kf/H682f30b3f8b741a88d400eb21ce483cb4.jpg",
    },
    "womens-long-sleeve-eco-swimsuit": {
        "Sky Blue": "https://sc04.alicdn.com/kf/H215a8a798db8490ab4b92a61c81667573.jpg",
        "Orange": "https://sc04.alicdn.com/kf/H6490af365d5f469fa3a8c7dcf6d49d589.jpg",
        "Floral": "https://sc04.alicdn.com/kf/H18ed0f1d32944817af90386721ff4c5f6.jpg",
        "Flower": "https://sc04.alicdn.com/kf/Hb7d162639e53489494ae7b87a86a91c4H.jpg",
    },
    "womens-short-sleeve-one-piece": {
        "Green": "https://sc04.alicdn.com/kf/Hbf9996771a3b423ba245b13e37806990j.jpg",
        "Black": "https://sc04.alicdn.com/kf/H31015fba50e84a0699439b1b4847848fh.jpg",
        "White": "https://sc04.alicdn.com/kf/Ha3a98643bb874c96a22c091883b463a4O.jpg",
    },
    "yingfa-mid-leg-trunks": {
        "Black": "https://sc04.alicdn.com/kf/H2f425078690f4722ba4269463cf69197Y.jpg",
        "Dark Blue": "https://sc04.alicdn.com/kf/H9cf334e42f2c45988ed689f26cbbed4aD.jpg",
    },
}

# ---------------------------------------------------------------------------
# Maps colour variant names → gallery image index for that product.
# When a colour is selected on the frontend the gallery jumps to this image.
# Only products you explicitly list here get variant→image linking.
# ---------------------------------------------------------------------------
VARIANT_GALLERY_MAP: dict[str, dict[str, int]] = {
    # "product-slug": {"ColourName": gallery_image_index, ...}
    "swimbuddz-training-kickboard": {
        "Orange": 1,
        "Blue": 2,
        "Pink": 3,
        "Green": 4,
    },
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
        "variant_options": {
            "Color": ["Yellow", "Black"],
            "Size": ["Small (20cm)", "Large (30cm)"],
        },
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
        "has_variants": True,
        "variant_options": {"Color": ["Light Blue", "Yellow"]},
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
        "variant_options": {
            "Color": ["Black", "Yellow"],
            "Size": ["Small (20cm)", "Medium (30cm)", "Large (40cm)"],
        },
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-003",
        "preorder_lead_days": 7,
        "image_seed": "parachute3",
    },
    {
        "name": "SwimBuddz Training Kickboard",
        "slug": "swimbuddz-training-kickboard",
        "category_key": "training",
        "description": (
            "The official SwimBuddz training kickboard — built for daily pool use. "
            "Durable EVA foam construction with smooth rounded edges and ergonomic "
            "hand grips. Available in four vibrant colours. Perfect for leg-focused "
            "drills, kick sets, and swim lessons."
        ),
        "short_description": "Official SwimBuddz EVA kickboard for training",
        "base_price_ngn": "6500",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "2500",
        "is_featured": True,
        "has_variants": True,
        "variant_options": {"Color": ["Blue", "Green", "Orange", "Pink"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-004",
        "preorder_lead_days": 7,
        "image_seed": "kickboard1",
    },
    {
        "name": "SwimBuddz Pro Kickboard",
        "slug": "swimbuddz-pro-kickboard",
        "category_key": "training",
        "description": (
            "The SwimBuddz Pro kickboard features a multi-layered EVA foam design "
            "with enhanced buoyancy for serious swim training. The stacked colour "
            "layers provide extra rigidity while the ergonomic shape supports "
            "proper body alignment during kick sets and drill work. "
            "Available in five vibrant colour combinations."
        ),
        "short_description": "SwimBuddz pro-grade multi-layer EVA kickboard",
        "base_price_ngn": "6500",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "2544",
        "is_featured": True,
        "has_variants": True,
        "variant_options": {"Color": ["Pink", "Blue", "Green", "Purple", "Orange"]},
        "requires_size_chart_ack": False,
        "sku_prefix": "SB-TRN-005",
        "preorder_lead_days": 7,
        "image_seed": "kickboard2",
    },
    {
        "name": "SwimBuddz Kids Kickboard",
        "slug": "swimbuddz-kids-kickboard",
        "category_key": "training",
        "description": (
            "The SwimBuddz Kids kickboard is designed for young swimmers aged 3+. "
            "Compact 40 cm EVA foam construction is lightweight and easy to grip. "
            "Fun multicolour design helps kids build confidence and leg strength "
            "during swim lessons and pool play."
        ),
        "short_description": "SwimBuddz kid-sized EVA kickboard for young swimmers",
        "base_price_ngn": "4000",
        "compare_at_price_ngn": None,
        "cost_price_ngn": "2078",
        "is_featured": True,
        "has_variants": True,
        "variant_options": {"Color": ["Pink", "Yellow", "Blue", "Green", "Orange"]},
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
        "variant_options": {"Color": ["Light Blue"], "Size": ["Standard", "Large"]},
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
            "Color": ["Black", "Green", "Blue", "Rose"],
            "Size": ["S (36-38)", "M (39-41)", "L (42-44)", "XL (45-46)"],
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
            "Color": ["Black", "Pink", "Red", "Yellow", "Sky Blue", "Green"],
            "Size": ["S (36-38)", "M (39-41)", "L (42-44)", "XL (45-46)"],
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
            "Color": ["Black"],
            "Size": ["S (35-37)", "M (38-40)", "L (41-43)", "XL (44-46)"],
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
            "Color": ["Black", "Yellow", "Blue"],
            "Size": ["XS (34-36)", "S (37-39)", "M (40-42)", "L (43-45)"],
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
        "variant_options": {
            "Color": [
                "Green",
                "Black",
                "Sky Blue",
                "Pink",
                "Yellow",
                "Navy Blue",
                "White",
            ],
            "Size": ["S (34-38)", "M/L (39-43)", "XL (44-47)"],
        },
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
        "variant_options": {
            "Color": [
                "Black",
                "Blue",
                "Silver",
                "Red",
                "Pink",
                "Green",
                "Orange-Pink",
                "Fluorescent Green",
            ],
            "Size": ["Child (S)", "Adult (M/L)"],
        },
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
        "has_variants": True,
        "variant_options": {"Color": ["Black", "Blue", "Green", "Orange"]},
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
        "has_variants": True,
        "variant_options": {"Color": ["Blue", "Green", "Yellow", "Black", "White"]},
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
        "has_variants": True,
        "variant_options": {
            "Color": ["Orange", "Yellow", "Purple", "Pink", "Green", "Red", "Blue"]
        },
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
        "variant_options": {"Color": ["Black"]},
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
        "variant_options": {"Color": ["Black"]},
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
        "variant_options": {"Color": ["Black", "White", "Blue"]},
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
                "Pink",
                "White",
                "Red",
                "Yellow",
                "Orange",
                "Purple",
                "Green",
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
        "variant_options": {
            "Color": [
                "Black",
                "Green",
                "Blue",
                "Purple",
                "Rose",
                "Red",
                "Grey",
                "Orange",
            ]
        },
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
            "Color": ["Black", "White", "Pink", "Green", "Yellow", "Blue"]
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
        "variant_options": {
            "Color": ["Black"],
            "Size": ["L", "XL", "XXL", "3XL", "4XL", "5XL"],
        },
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
        "variant_options": {"Size": ["XS", "S", "M", "L", "XL", "XXL"]},
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
        "variant_options": {
            "Color": ["Black", "Navy"],
            "Size": ["L", "XL", "XXL", "3XL", "4XL"],
        },
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
        "variant_options": {
            "Color": ["Black", "Dark Blue"],
            "Size": ["S", "M", "L", "XL", "XXL"],
        },
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
        "variant_options": {
            "Color": ["Orange", "Black", "Blue", "Dark Grey"],
            "Size": ["M", "L", "XL", "XXL", "2XL", "3XL", "4XL", "5XL"],
        },
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
        "variant_options": {
            "Color": ["Light Blue", "Pink", "Brown", "Champagne"],
            "Size": ["S", "M", "L", "XL"],
        },
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
        "variant_options": {
            "Color": ["Light Blue", "Golden", "Green", "Army Green", "Red", "Pink"],
            "Size": ["XS", "S", "M", "L", "XL", "XXL"],
        },
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
        "variant_options": {
            "Color": ["Black", "Blue"],
            "Size": ["XS", "S", "M", "L", "XL", "2XL", "3XL"],
        },
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
        "variant_options": {
            "Color": ["Green", "Black", "White"],
            "Size": ["S", "M", "L", "XL", "XXL"],
        },
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
        "variant_options": {
            "Color": ["Blue & Teal", "Black & Red", "Black & Grey"],
            "Size": ["S", "M", "L", "XL", "XXL"],
        },
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
        "variant_options": {
            "Color": ["Sky Blue", "Orange", "Floral", "Navy"],
            "Size": ["S", "M", "L", "XL", "XXL"],
        },
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
        "variant_options": {
            "Color": ["Light Blue", "Golden", "Green", "Army Green", "Red", "Pink"]
        },
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
        "variant_options": {
            "Color": ["Light Blue", "Golden", "Green", "Army Green", "Red", "Pink"],
            "Size": ["XS", "S", "M", "L", "XL", "XXL"],
        },
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
        "variant_options": {"Color": ["Black"], "Size": ["S", "M", "L", "XL", "XXL"]},
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
        "variant_options": {
            "Color": ["Light Blue", "Golden", "Green", "Army Green", "Red", "Pink"],
            "Size": ["M", "L", "XL", "XXL"],
        },
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
        "variant_options": {"Color": ["Black"], "Size": ["S", "M", "L", "XL", "XXL"]},
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
        "variant_options": {
            "Color": ["Orange", "Green", "Blue", "Red"],
            "Size": ["S", "M", "L", "XL"],
        },
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
        "variant_options": {"Color": ["Black"], "Size": ["S", "M", "L"]},
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
        "variant_options": {
            "Color": [
                "Black",
                "Fluorescent Green",
                "Blue",
                "Orange",
                "Gray",
                "Yellow-Green Gradient",
                "Orange Green Gradient",
                "Black Gray Gradient",
            ],
            "Size": ["Small (35x45cm)", "Large (45x55cm)"],
        },
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
        "has_variants": True,
        "variant_options": {"Color": ["Black"]},
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
        "has_variants": True,
        "variant_options": {"Color": ["Black", "Gray"]},
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
        "has_variants": True,
        "variant_options": {"Color": ["Black", "Gray", "Green"]},
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
        "has_variants": True,
        "variant_options": {"Color": ["Black"]},
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
        "has_variants": True,
        "variant_options": {"Color": ["Gray", "Black"]},
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
        "has_variants": True,
        "variant_options": {"Color": ["Black", "Gray", "Deep Blue", "White", "Green"]},
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
        "has_variants": True,
        "variant_options": {"Color": ["Black", "Gray"]},
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
        "variant_options": {"Color": ["Black", "Blue", "Orange", "Green", "Yellow"]},
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
        "has_variants": True,
        "variant_options": {"Color": ["Black Frame", "Gold Frame", "Silver Frame"]},
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
        "has_variants": True,
        "variant_options": {"Size": ["240ml"]},
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
        "has_variants": True,
        "variant_options": {"Size": ["251ml"]},
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
            "swimbuddz-training-kickboard",
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


def _variant_code(label: str) -> str:
    """Extract a short code from a variant label for SKU generation.

    Examples:
        "M"            -> "M"
        "S (35-36)"    -> "S"
        "S/M"          -> "SM"
        "2 (1-2yr)"    -> "2"
        "Blue"         -> "BLU"
        "Black"        -> "BLK"
        "Navy"         -> "NAV"
        "Rainbow A"    -> "RAIA"
        "Black & Red"  -> "BLKR"
    """
    code = label.split("(")[0].strip()
    code = code.replace("/", "")
    # For multi-word names, take first 3 chars of first word + first char of rest
    words = code.split()
    if len(words) > 1:
        first = words[0].replace("&", "")[:3].upper()
        rest = "".join(w[0].upper() for w in words[1:] if w != "&")
        code = first + rest
    else:
        code = code.replace(" ", "").replace("&", "")
        if len(code) > 4:
            code = code[:3].upper()
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


def _build_variant_options(p: dict) -> dict | None:
    """Build variant_options JSON including _color_swatches metadata."""
    opts = p.get("variant_options")
    if not opts:
        return opts
    # Inject color swatch URLs from PRODUCT_SWATCHES if available
    swatches = PRODUCT_SWATCHES.get(p["slug"], {})
    if swatches and "Color" in opts:
        opts = dict(opts)  # Don't mutate the original
        opts["_color_swatches"] = swatches
    return opts


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
            variant_options=_build_variant_options(p),
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

        # --- variants & inventory (cross-product for multi-dimensional) ---
        # Track colour→variant_id for gallery-image linking
        color_variant_ids: dict[str, uuid.UUID] = {}

        if p["has_variants"] and p["variant_options"]:
            keys = list(p["variant_options"].keys())
            value_lists = [p["variant_options"][k] for k in keys]
            for combo in iterproduct(*value_lists):
                options = dict(zip(keys, combo))
                # Build SKU suffix from each dimension
                codes = [_variant_code(v) for v in combo]
                sku_suffix = "-".join(codes)
                name = " / ".join(combo)
                variant = ProductVariant(
                    product_id=product.id,
                    sku=f"{p['sku_prefix']}-{sku_suffix}",
                    name=name,
                    options=options,
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
                # Remember first variant for each colour value
                color_val = options.get("Color")
                if color_val and color_val not in color_variant_ids:
                    color_variant_ids[color_val] = variant.id
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

        # --- product images (from Alibaba gallery) ---
        media = PRODUCT_MEDIA.get(p["slug"], {})
        media_images = media.get("images", [])
        media_videos = media.get("videos", [])

        # Build gallery-index → variant_id from VARIANT_GALLERY_MAP
        gallery_map = VARIANT_GALLERY_MAP.get(p["slug"], {})
        idx_to_variant: dict[int, uuid.UUID] = {}
        for color_name, img_idx in gallery_map.items():
            vid = color_variant_ids.get(color_name)
            if vid is not None:
                idx_to_variant[img_idx] = vid

        if media_images:
            for img_idx, img_url in enumerate(media_images):
                db.add(
                    ProductImage(
                        product_id=product.id,
                        variant_id=idx_to_variant.get(img_idx),
                        url=img_url,
                        alt_text=f"{p['name']} - image {img_idx + 1}",
                        is_primary=(img_idx == 0),
                        sort_order=img_idx,
                    )
                )
        else:
            # Fallback to placeholder if no scraped images
            db.add(
                ProductImage(
                    product_id=product.id,
                    url=f"https://picsum.photos/seed/{p['image_seed']}/600/600",
                    alt_text=p["name"],
                    is_primary=True,
                )
            )

        # --- product videos ---
        for vid_idx, vid_url in enumerate(media_videos):
            db.add(
                ProductVideo(
                    product_id=product.id,
                    url=vid_url,
                    title=f"{p['name']} - video {vid_idx + 1}",
                    sort_order=vid_idx,
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
