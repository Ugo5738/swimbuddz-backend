#!/usr/bin/env python3
"""
Seed the canonical SwimBuddz Academy V2 curriculum catalog (F1-F4, S1, A1).

PRODUCTION-SAFE & IDEMPOTENT:
- Only seeds reusable V2 catalog definitions, version 1 published releases, and
  module/lesson compositions (drills -> lessons -> modules, milestones & quizzes -> modules).
- Never mutates existing legacy programs, cohorts, enrollments, student_progress,
  or already-published V2 catalog versions.
- Safe to re-run at any time.
"""

import asyncio
import os
import sys
from decimal import Decimal

from sqlalchemy import select

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, project_root)

from libs.common.datetime_utils import utc_now  # noqa: E402
from libs.db.config import AsyncSessionLocal  # noqa: E402
from services.academy_service.models import (  # noqa: E402
    AcademyDrill,
    AcademyDrillVersion,
    AcademyKnowledgeQuiz,
    AcademyKnowledgeQuizVersion,
    AcademyLessonBlueprint,
    AcademyLessonDrill,
    AcademyLessonVersion,
    AcademyMilestoneDefinition,
    AcademyMilestoneRemedialDrill,
    AcademyMilestoneVersion,
    AcademyModule,
    AcademyModuleLesson,
    AcademyModuleMilestone,
    AcademyModuleQuiz,
    AcademyModuleVersion,
)

SEED_ACTOR = "seed:academy-curriculum-v2"

DRILLS = [
    {
        "code": "DRL-F1-WALL-WALK",
        "title": "Shallow-End Wall Walk & Calm Exit",
        "description": "Controlled lateral travel along the pool wall building orientation and calm exits.",
        "primary_focal_cue": "Soft grip on the coping; shoulders relaxed below water.",
        "steps_json": [
            "Enter via ladder or controlled sit-and-pivot in chest-deep water.",
            "Walk laterally 10m along the wall using light fingertip contact.",
            "Pause, exhale gently, and practice two calm ladder/edge exits.",
        ],
        "safety_json": {
            "min_depth_m": 1.0,
            "max_depth_m": 1.3,
            "notes": "Chest-deep water only; coach positioned pool-side.",
        },
        "requires_supervision": True,
    },
    {
        "code": "DRL-F1-CHIN-TUCK-STAND",
        "title": "Chin-Tuck Stand Recovery",
        "description": "Reliable recovery from horizontal front/back position to standing feet on the pool floor.",
        "primary_focal_cue": "Tuck knees to chest first, lift head last.",
        "steps_json": [
            "Lean gently forward with hands resting on coach rail or pool wall.",
            "Draw both knees tightly toward the chest while sweeping palms down.",
            "Plant both feet flat on the floor before lifting the chin.",
        ],
        "safety_json": {"min_depth_m": 1.0, "max_depth_m": 1.3},
        "requires_supervision": True,
    },
    {
        "code": "DRL-F2-HUMMING-BOBS",
        "title": "Continuous Humming Sink-Down Bobs",
        "description": "Rhythmic underwater nasal/mouth exhalation eliminating breath-holding panic.",
        "primary_focal_cue": "Continuous gentle hum underwater until eyes clear the surface.",
        "steps_json": [
            "Stand in chest-deep water with relaxed shoulders.",
            "Take a small relaxed inhale, submerge mouth and eyes while humming continuously.",
            "Rise smoothly, finish exhaling at the surface, and repeat without wiping eyes.",
        ],
        "safety_json": {"min_depth_m": 1.1, "max_depth_m": 1.4},
        "requires_supervision": True,
    },
    {
        "code": "DRL-F3-WEIGHTLESS-HEAD",
        "title": "Weightless Head Supine Float",
        "description": "Total Immersion balance drill letting the water support head weight so hips rise.",
        "primary_focal_cue": "Ears submerged, chin slightly lifted, let the water hold your skull.",
        "steps_json": [
            "Stand in chest-deep water and lean head back until both ears are submerged.",
            "Extend arms into a wide relaxed Y/star shape just under the surface.",
            "Breathe calmly with chest inflated and hold 15-30 seconds before chin-tuck recovery.",
        ],
        "safety_json": {"min_depth_m": 1.1, "max_depth_m": 1.4},
        "requires_supervision": True,
    },
    {
        "code": "DRL-F4-SUPERMAN-GLIDE",
        "title": "TI Superman Streamline Glide",
        "description": "Shape the vessel before adding propulsion; feel passive forward glide.",
        "primary_focal_cue": "Look straight down at the floor; press chest gently to float the hips.",
        "steps_json": [
            "Push gently off the wall into a shoulder-width extended Superman position.",
            "Keep head neutral with eyes down and ankles loose.",
            "Exhale steadily and glide until momentum stops before recovering smoothly.",
        ],
        "safety_json": {"min_depth_m": 1.2, "max_depth_m": 1.8},
        "requires_supervision": True,
    },
    {
        "code": "DRL-S1-TI-SKATE",
        "title": "Total Immersion Side-Lying Skate",
        "description": "Balance on the lower side with an extended lead arm and relaxed trailing shoulder.",
        "primary_focal_cue": "Weightless head aligned with spine; lead fingertips aimed gently down-forward.",
        "steps_json": [
            "Push off on your side with lower arm extended forward and top arm resting along the hip.",
            "Keep goggles pointed at the bottom corner of the pool and kick gently from the hips.",
            "Swim 12.5m per side focusing on stillness and long body line.",
        ],
        "safety_json": {"min_depth_m": 1.2, "max_depth_m": 2.0},
        "requires_supervision": True,
    },
    {
        "code": "DRL-S1-6-KICK-SWITCH",
        "title": "6-Kick Switch with Patient Lead Hand",
        "description": "Connect core-driven hip rotation with smooth freestyle arm exchange.",
        "primary_focal_cue": "Hold the lead hand patient until the recovering hand enters the mail slot.",
        "steps_json": [
            "Start in Skate position and take six relaxed kicks.",
            "Initiate rotation from the hips while spearing the recovering hand forward.",
            "Settle cleanly on the opposite side for six kicks and repeat across the length.",
        ],
        "safety_json": {"min_depth_m": 1.2, "max_depth_m": 2.0},
        "requires_supervision": True,
    },
    {
        "code": "DRL-S1-ONE-GOGGLE-BREATH",
        "title": "One-Goggle / Pillow-Head Side Breathing",
        "description": "Balanced rotational freestyle breathing keeping one goggle near the surface so hips stay high.",
        "primary_focal_cue": "Rest your head on the water like a pillow as your torso rotates to breathe.",
        "steps_json": [
            "Exhale steadily underwater while gliding in Skate balance.",
            "Roll hips and shoulders smoothly together until one goggle clears the surface.",
            "Inhale calmly without lifting the chin forward, then return eyes straight down.",
        ],
        "safety_json": {
            "min_depth_m": 1.2,
            "max_depth_m": 2.0,
            "notes": "Short 15-25m repeats with full wall rest; never hold breath to force distance.",
        },
        "requires_supervision": True,
    },
    {
        "code": "DRL-S1-SHARK-FIN-RECOVERY",
        "title": "Shark Fin / Zipper Relaxed Arm Recovery",
        "description": "High-elbow, weightless forearm recovery preventing shoulder strain and flat splash.",
        "primary_focal_cue": "Lead recovery with a relaxed high elbow while fingertips hang weightless near the water.",
        "steps_json": [
            "From Skate position, lift the top elbow gently upward like a shark fin.",
            "Keep wrist and fingers completely loose brushing lightly near the surface.",
            "Pause briefly in balanced side alignment before sliding fingertips smoothly into entry.",
        ],
        "safety_json": {
            "min_depth_m": 1.2,
            "max_depth_m": 2.0,
            "notes": "Stop immediately if you feel any pinching at the front of the shoulder.",
        },
        "requires_supervision": True,
    },
    {
        "code": "DRL-S1-MAIL-SLOT-ENTRY",
        "title": "Mail-Slot Shoulder-Track Entry Spear",
        "description": "Quiet fingertip entry along the swimmer's own shoulder track preventing crossover and overreach.",
        "primary_focal_cue": "Slip fingertips quietly into the water directly ahead of your shoulder and extend forward.",
        "steps_json": [
            "Aim fingertips downward into an imaginary mail slot in front of your shoulder line.",
            "Slide the forearm cleanly underwater without slapping or reaching across the centerline.",
            "Lengthen gently into a streamlined Skate glide as hips rotate onto the opposite side.",
        ],
        "safety_json": {
            "min_depth_m": 1.2,
            "max_depth_m": 2.0,
            "notes": "Keep entry unhurried; avoid forcing shoulder hyper-extension.",
        },
        "requires_supervision": True,
    },
    {
        "code": "DRL-A1-SCULLING-TREAD",
        "title": "Three-Zone Sculling & Calm Vertical Tread",
        "description": "Low-effort deep-water support combining horizontal figure-8 sculling and slow leg support.",
        "primary_focal_cue": "Sweep forearms wide and horizontal; keep breaths slow and unhurried.",
        "steps_json": [
            "In deep water near the lane edge, keep chin just above surface without bobbing.",
            "Perform wide figure-8 palm sculls below chest level paired with relaxed scissor/eggbeater kick.",
            "Hold calm vertical posture for 30s, transition to 10s back float rest, then exit safely.",
        ],
        "safety_json": {
            "min_depth_m": 1.8,
            "notes": "Requires verified F3 back-float recovery; coach poolside with rescue tube.",
        },
        "requires_supervision": True,
    },
]

MILESTONES = [
    {
        "code": "MS-F1-SAFE-ENTRY",
        "title": "Safe Pool Entry, Orientation & Calm Exit",
        "description": "Demonstrates controlled entry, 10s calm face submersion, and unassisted recovery to standing.",
        "safety_gate": True,
        "evidence_policy": "video",
        "rubric_json": {
            "goal": "Enter chest-deep water safely, submerge face calmly for 10 seconds, and stand/exit under control.",
            "criteria": [
                "controlled-water-entry",
                "calm-face-submersion-10s",
                "controlled-stand-recovery",
                "safe-pool-exit",
            ],
        },
        "assessment_context_json": {
            "pool_depth": "shallow_chest_deep",
            "validity_days": 365,
        },
    },
    {
        "code": "MS-F2-RHYTHMIC-BOBS-20",
        "title": "20 Continuous Rhythmic Underwater Bobs",
        "description": "Sustained rhythmic exhalation underwater without gasping, wiping eyes, or holding the wall.",
        "safety_gate": False,
        "evidence_policy": "video",
        "rubric_json": {
            "goal": "Complete 20 unbroken rhythmic bobs with continuous underwater exhalation.",
            "criteria": [
                "continuous-underwater-exhale",
                "twenty-unbroken-bobs",
                "relaxed-surface-inhale",
            ],
        },
        "assessment_context_json": {
            "pool_depth": "shallow_chest_deep",
            "validity_days": 365,
        },
    },
    {
        "code": "MS-F3-BACK-FLOAT-30S",
        "title": "30-Second Unassisted Back Float & Calm Stand",
        "description": "Self-rescue rest float on the back for 30 seconds followed by controlled stand recovery.",
        "safety_gate": True,
        "evidence_policy": "video",
        "rubric_json": {
            "goal": "Maintain an unassisted supine float for 30 continuous seconds and recover calmly to standing.",
            "criteria": [
                "unassisted-supine-entry",
                "thirty-second-calm-float",
                "ears-submerged-neutral-head",
                "controlled-stand-recovery",
            ],
        },
        "assessment_context_json": {
            "pool_depth": "shallow_or_mid",
            "validity_days": 365,
        },
    },
    {
        "code": "MS-F4-STREAMLINE-GLIDE-7M",
        "title": "7-Meter Streamline Push-Off Glide & Flutter Kick",
        "description": "Balanced horizontal vessel shape holding streamline glide followed by relaxed 15m flutter kick.",
        "safety_gate": False,
        "evidence_policy": "video",
        "rubric_json": {
            "goal": "Push off in neutral streamline for 7m and transition smoothly into relaxed flutter kick.",
            "criteria": [
                "neutral-head-eyes-down",
                "seven-meter-glide",
                "hips-near-surface",
            ],
        },
        "assessment_context_json": {
            "pool_depth": "mid_or_deep",
            "validity_days": 365,
        },
    },
    {
        "code": "MS-S1-FREE-25M",
        "title": "25-Meter Continuous Freestyle with Side Breathing",
        "description": "Continuous 25m freestyle demonstrating balanced body line and controlled side breathing.",
        "safety_gate": True,
        "evidence_policy": "video",
        "rubric_json": {
            "goal": "Swim 25m continuous freestyle without touching floor or wall, using controlled side breaths.",
            "criteria": [
                "continuous-25m-unassisted",
                "rhythmic-side-breathing",
                "balanced-horizontal-alignment",
            ],
        },
        "assessment_context_json": {
            "pool_depth": "standard_25m",
            "validity_days": 365,
        },
    },
    {
        "code": "MS-S1-FREE-50M",
        "title": "50-Meter Continuous Freestyle Pathway Gate",
        "description": "Full Beginner-to-50m practical outcome demonstrating efficient, calm 50m freestyle.",
        "safety_gate": False,
        "evidence_policy": "video",
        "rubric_json": {
            "goal": "Swim 50m continuous freestyle maintaining steady rhythm, side breathing, and calm turn.",
            "criteria": [
                "continuous-50m-unassisted",
                "consistent-breathing-rhythm",
                "controlled-turn-and-finish",
            ],
        },
        "assessment_context_json": {
            "pool_depth": "standard_25m",
            "validity_days": 365,
        },
    },
    {
        "code": "MS-A1-TREAD-60S",
        "title": "60-Second Calm Deep-Water Tread & Self-Rescue Recovery",
        "description": "Sustained deep-water treading and calm rest transition required for deep-water autonomy.",
        "safety_gate": True,
        "evidence_policy": "video",
        "rubric_json": {
            "goal": "Tread water calmly in deep water (>1.8m) for 60 continuous seconds without wall support.",
            "criteria": [
                "deep-water-no-floor-contact",
                "sixty-second-vertical-tread",
                "calm-breathing-control",
                "controlled-exit-or-float-transition",
            ],
        },
        "assessment_context_json": {
            "pool_depth": "deep_over_1_8m",
            "validity_days": 180,
        },
    },
]

QUIZZES = [
    {
        "code": "QZ-F1-SAFETY",
        "title": "Water Safety, Pool Rules & Calm Recovery",
        "description": "Core pool-deck safety, depth awareness, and calm recovery rules before independent water work.",
        "passing_percent": 80,
        "questions_json": [
            {
                "id": "q1",
                "prompt": "What should you do first to recover from a front or back float to standing?",
                "options": [
                    "Throw your head up sharply",
                    "Tuck both knees toward your chest and sweep palms gently down",
                    "Kick faster until you hit the wall",
                ],
                "correct_index": 1,
            },
            {
                "id": "q2",
                "prompt": "Why should beginners never dive head-first into shallow water?",
                "options": [
                    "It slows down your lap time",
                    "Risk of serious head and spinal injury on the pool floor",
                    "It loosens goggles",
                ],
                "correct_index": 1,
            },
        ],
    },
    {
        "code": "QZ-F2-BREATHING",
        "title": "CO2 Reflex & Rhythmic Exhalation",
        "description": "Why holding your breath underwater triggers panic and sinks the hips.",
        "passing_percent": 80,
        "questions_json": [
            {
                "id": "q1",
                "prompt": "When should you exhale during rhythmic swimming?",
                "options": [
                    "Only after lifting your head out of the water",
                    "Steadily underwater before turning to inhale",
                    "Hold breath for the full length",
                ],
                "correct_index": 1,
            },
        ],
    },
    {
        "code": "QZ-F4-DRAG",
        "title": "Total Immersion: Vessel Shape vs. Effort",
        "description": "Why head alignment and streamline reduce drag far more than kicking harder.",
        "passing_percent": 80,
        "questions_json": [
            {
                "id": "q1",
                "prompt": "What typically happens to your hips and legs when you lift your head to look forward?",
                "options": [
                    "They sink like a see-saw, sharply increasing drag",
                    "They rise higher out of the water",
                    "Nothing changes",
                ],
                "correct_index": 0,
            },
        ],
    },
]

LESSONS = [
    {
        "code": "LSN-F1-CONFIDENCE",
        "title": "Water Entry, Submersion & Stand Recovery",
        "description": "First structured shallow-water session establishing calm entry, face submersion, and recovery.",
        "duration_minutes": 60,
        "blocks_json": [
            {
                "duration_minutes": 10,
                "activity": "Deck safety brief & breath awareness",
            },
            {"duration_minutes": 15, "activity": "Controlled entry & wall walk"},
            {
                "duration_minutes": 25,
                "activity": "Chin-tuck stand recovery & calm submersion",
            },
            {"duration_minutes": 10, "activity": "Assessment check & homework cues"},
        ],
        "safety_json": {"max_ratio": "6:1", "depth": "shallow_chest_deep"},
        "drills": [
            ("DRL-F1-WALL-WALK", 0, 2, Decimal("10.0")),
            ("DRL-F1-CHIN-TUCK-STAND", 1, 4, None),
        ],
    },
    {
        "code": "LSN-F2-BREATH-CONTROL",
        "title": "Rhythmic Underwater Exhalation & Bobbing",
        "description": "Eliminating breath-holding tension through continuous nasal/mouth humming bobs.",
        "duration_minutes": 60,
        "blocks_json": [
            {"duration_minutes": 10, "activity": "Warmup wall walks & relaxed exhales"},
            {"duration_minutes": 30, "activity": "Humming sink-down bobs progression"},
            {"duration_minutes": 15, "activity": "20-bob endurance check"},
            {"duration_minutes": 5, "activity": "Cooldown & dryland review"},
        ],
        "safety_json": {"max_ratio": "6:1", "depth": "shallow_chest_deep"},
        "drills": [
            ("DRL-F2-HUMMING-BOBS", 0, 3, None),
        ],
    },
    {
        "code": "LSN-F3-FLOAT-BALANCE",
        "title": "Weightless Head & Back Float Recovery",
        "description": "Building effortless buoyancy and 30-second self-rescue supine float.",
        "duration_minutes": 60,
        "blocks_json": [
            {"duration_minutes": 10, "activity": "Bobs warmup & stand-recovery drill"},
            {
                "duration_minutes": 30,
                "activity": "Weightless head supine float progression",
            },
            {
                "duration_minutes": 15,
                "activity": "30-second back float practical check",
            },
            {"duration_minutes": 5, "activity": "Debrief & video cues"},
        ],
        "safety_json": {"max_ratio": "6:1", "depth": "shallow_or_mid"},
        "drills": [
            ("DRL-F1-CHIN-TUCK-STAND", 0, 2, None),
            ("DRL-F3-WEIGHTLESS-HEAD", 1, 4, None),
        ],
    },
    {
        "code": "LSN-F4-STREAMLINE",
        "title": "Superman Streamline Glide & Vessel Shape",
        "description": "Total Immersion streamline alignment and relaxed hip-driven flutter kick.",
        "duration_minutes": 60,
        "blocks_json": [
            {"duration_minutes": 10, "activity": "Supine float & breathing warmup"},
            {
                "duration_minutes": 30,
                "activity": "TI Superman glide distance progression",
            },
            {"duration_minutes": 15, "activity": "7m streamline glide assessment"},
            {"duration_minutes": 5, "activity": "Cooldown & next-module preview"},
        ],
        "safety_json": {"max_ratio": "6:1", "depth": "mid_or_deep"},
        "drills": [
            ("DRL-F4-SUPERMAN-GLIDE", 0, 4, Decimal("12.5")),
        ],
    },
    {
        "code": "LSN-S1-FREESTYLE-CORE",
        "title": "Side-Lying Skate & 6-Kick Switch Freestyle",
        "description": "Connecting TI side balance with core rotation and patient lead-hand freestyle.",
        "duration_minutes": 75,
        "blocks_json": [
            {"duration_minutes": 10, "activity": "Superman glide warmup"},
            {"duration_minutes": 25, "activity": "Side-lying Skate balance left/right"},
            {"duration_minutes": 25, "activity": "6-Kick Switch into full freestyle"},
            {"duration_minutes": 15, "activity": "25m/50m video assessment"},
        ],
        "safety_json": {"max_ratio": "6:1", "depth": "standard_25m"},
        "drills": [
            ("DRL-S1-TI-SKATE", 0, 4, Decimal("12.5")),
            ("DRL-S1-6-KICK-SWITCH", 1, 4, Decimal("25.0")),
        ],
    },
    {
        "code": "LSN-A1-DEEP-WATER",
        "title": "Deep-Water Treading & Energy Conservation",
        "description": "Calm 60-second deep-water treading and self-rescue back-float rest transitions.",
        "duration_minutes": 60,
        "blocks_json": [
            {
                "duration_minutes": 10,
                "activity": "Deep-water safety orientation & back-float check",
            },
            {
                "duration_minutes": 30,
                "activity": "Three-zone sculling & relaxed vertical tread",
            },
            {
                "duration_minutes": 15,
                "activity": "60-second supervised deep-water assessment",
            },
            {"duration_minutes": 5, "activity": "Cooldown & Club readiness notes"},
        ],
        "safety_json": {"max_ratio": "5:1", "depth": "deep_over_1_8m"},
        "drills": [
            ("DRL-A1-SCULLING-TREAD", 0, 3, None),
        ],
    },
]

MODULES = [
    {
        "code": "MOD-F1-SAFETY",
        "title": "F1: Water Confidence & Safety",
        "description": "Foundational shallow-water comfort, safe entry/exit, and calm stand recovery.",
        "outcomes_json": [
            "Enter and exit chest-deep water safely without panic",
            "Submerge face calmly for 10s and recover reliably to standing",
        ],
        "completion_policy_json": {
            "all_required_milestones": True,
            "all_required_quizzes": True,
        },
        "lessons": ["LSN-F1-CONFIDENCE"],
        "milestones": [("MS-F1-SAFE-ENTRY", True, 0)],
        "quizzes": [("QZ-F1-SAFETY", True)],
    },
    {
        "code": "MOD-F2-BREATHING",
        "title": "F2: Breathing & Bubble Control",
        "description": "Rhythmic underwater exhalation and panic-free breathing mechanics.",
        "outcomes_json": [
            "Maintain continuous underwater exhalation across 20 rhythmic bobs",
        ],
        "completion_policy_json": {
            "all_required_milestones": True,
            "all_required_quizzes": True,
        },
        "lessons": ["LSN-F2-BREATH-CONTROL"],
        "milestones": [("MS-F2-RHYTHMIC-BOBS-20", True, 0)],
        "quizzes": [("QZ-F2-BREATHING", True)],
    },
    {
        "code": "MOD-F3-FLOAT",
        "title": "F3: Floating, Recovery & Body Line",
        "description": "Unassisted supine float self-rescue and weightless-head buoyancy balance.",
        "outcomes_json": [
            "Hold a calm 30-second unassisted back float and stand cleanly",
        ],
        "completion_policy_json": {"all_required_milestones": True},
        "lessons": ["LSN-F3-FLOAT-BALANCE"],
        "milestones": [("MS-F3-BACK-FLOAT-30S", True, 0)],
        "quizzes": [],
    },
    {
        "code": "MOD-F4-STREAMLINE",
        "title": "F4: Balance & Streamline Propulsion",
        "description": "Total Immersion vessel-shaping glide and relaxed horizontal propulsion.",
        "outcomes_json": [
            "Glide 7m in neutral streamline and sustain balanced flutter kick",
        ],
        "completion_policy_json": {
            "all_required_milestones": True,
            "all_required_quizzes": True,
        },
        "lessons": ["LSN-F4-STREAMLINE"],
        "milestones": [("MS-F4-STREAMLINE-GLIDE-7M", True, 0)],
        "quizzes": [("QZ-F4-DRAG", True)],
    },
    {
        "code": "MOD-S1-FREESTYLE",
        "title": "S1: Freestyle Foundation (25m to 50m)",
        "description": "Core-driven TI freestyle progression from balanced side skate to continuous 50m.",
        "outcomes_json": [
            "Swim 25m continuous freestyle with controlled side breathing",
            "Swim 50m continuous freestyle with steady rhythm",
        ],
        "completion_policy_json": {"all_required_milestones": True},
        "lessons": ["LSN-S1-FREESTYLE-CORE"],
        "milestones": [("MS-S1-FREE-25M", True, 0), ("MS-S1-FREE-50M", True, 1)],
        "quizzes": [],
    },
    {
        "code": "MOD-A1-DEEP-WATER",
        "title": "A1: Deep Water Confidence & Treading",
        "description": "Calm deep-water treading and energy-conserving self-rescue transitions.",
        "outcomes_json": [
            "Tread water calmly in deep water for 60 continuous seconds",
        ],
        "completion_policy_json": {"all_required_milestones": True},
        "lessons": ["LSN-A1-DEEP-WATER"],
        "milestones": [("MS-A1-TREAD-60S", True, 0)],
        "quizzes": [],
    },
]


REMEDIAL_DRILLS = [
    (
        "MS-F1-SAFE-ENTRY",
        "DRL-F1-WALL-WALK",
        0,
        "Build calm wall orientation before free standing.",
    ),
    (
        "MS-F1-SAFE-ENTRY",
        "DRL-F1-CHIN-TUCK-STAND",
        1,
        "Practice knee-tuck recovery until it feels automatic.",
    ),
    (
        "MS-F2-RHYTHMIC-BOBS-20",
        "DRL-F2-HUMMING-BOBS",
        0,
        "Focus on unbroken gentle humming so you never gasp at the surface.",
    ),
    (
        "MS-F3-BACK-FLOAT-30S",
        "DRL-F3-WEIGHTLESS-HEAD",
        0,
        "Submerge both ears and let the water hold your skull weightless.",
    ),
    (
        "MS-F3-BACK-FLOAT-30S",
        "DRL-F1-CHIN-TUCK-STAND",
        1,
        "End every float with a calm knee-tuck stand recovery.",
    ),
    (
        "MS-F4-STREAMLINE-GLIDE-7M",
        "DRL-F4-SUPERMAN-GLIDE",
        0,
        "Press chest gently downward so hips rise without kicking hard.",
    ),
    (
        "MS-S1-FREE-25M",
        "DRL-S1-TI-SKATE",
        0,
        "Hold a long, balanced side-lying line before rotating to breathe.",
    ),
    (
        "MS-S1-FREE-25M",
        "DRL-S1-6-KICK-SWITCH",
        1,
        "Keep the lead hand patient until the recovering hand enters cleanly.",
    ),
    (
        "MS-S1-FREE-25M",
        "DRL-S1-ONE-GOGGLE-BREATH",
        2,
        "Keep one goggle near the surface when rotating to inhale so hips never drop.",
    ),
    (
        "MS-S1-FREE-50M",
        "DRL-S1-SHARK-FIN-RECOVERY",
        2,
        "Relax the recovering forearm to save energy over continuous 50m swims.",
    ),
    (
        "MS-S1-FREE-50M",
        "DRL-S1-MAIL-SLOT-ENTRY",
        3,
        "Enter smoothly on your shoulder track to maintain a straight, low-drag line.",
    ),
    (
        "MS-S1-FREE-50M",
        "DRL-S1-6-KICK-SWITCH",
        0,
        "Use core hip rotation to conserve energy across 50 meters.",
    ),
    (
        "MS-A1-TREAD-60S",
        "DRL-A1-SCULLING-TREAD",
        0,
        "Keep forearm sculls wide and horizontal; avoid vertical splashing.",
    ),
]


async def _get_or_create_definition(
    db, model, *, code: str, title: str, description: str
):
    existing = await db.scalar(select(model).where(model.code == code))
    if existing is not None:
        return existing, False
    row = model(code=code, title=title, description=description)
    db.add(row)
    await db.flush()
    return row, True


async def _get_or_create_v1(db, version_model, fk_name: str, parent_id, fields: dict):
    existing = await db.scalar(
        select(version_model).where(
            getattr(version_model, fk_name) == parent_id,
            version_model.version == 1,
        )
    )
    if existing is not None:
        return existing, False
    row = version_model(
        **{fk_name: parent_id},
        version=1,
        state="draft",
        created_by_auth_id=SEED_ACTOR,
        **fields,
    )
    db.add(row)
    await db.flush()
    return row, True


async def seed_academy_curriculum_v2(db) -> dict[str, int]:
    """Idempotently seed baseline V2 Academy curriculum definitions & v1 releases."""
    counts = {
        "drills_created": 0,
        "milestones_created": 0,
        "quizzes_created": 0,
        "lessons_created": 0,
        "modules_created": 0,
        "remedial_links_created": 0,
    }
    now = utc_now()

    drill_v1: dict[str, AcademyDrillVersion] = {}
    drill_defs_by_code: dict[str, AcademyDrill] = {}
    for item in DRILLS:
        defn, created = await _get_or_create_definition(
            db,
            AcademyDrill,
            code=item["code"],
            title=item["title"],
            description=item["description"],
        )
        drill_defs_by_code[item["code"]] = defn
        if created:
            counts["drills_created"] += 1
        ver, ver_created = await _get_or_create_v1(
            db,
            AcademyDrillVersion,
            "drill_id",
            defn.id,
            {
                "primary_focal_cue": item["primary_focal_cue"],
                "steps_json": item["steps_json"],
                "safety_json": item["safety_json"],
                "requires_supervision": item["requires_supervision"],
            },
        )
        if ver_created or ver.state != "published":
            ver.state = "published"
            ver.published_at = ver.published_at or now
        drill_v1[item["code"]] = ver

    milestone_v1: dict[str, AcademyMilestoneVersion] = {}
    milestone_defs_by_code: dict[str, AcademyMilestoneDefinition] = {}
    for item in MILESTONES:
        defn, created = await _get_or_create_definition(
            db,
            AcademyMilestoneDefinition,
            code=item["code"],
            title=item["title"],
            description=item["description"],
        )
        milestone_defs_by_code[item["code"]] = defn
        if created:
            counts["milestones_created"] += 1
        ver, ver_created = await _get_or_create_v1(
            db,
            AcademyMilestoneVersion,
            "definition_id",
            defn.id,
            {
                "rubric_json": item["rubric_json"],
                "evidence_policy": item["evidence_policy"],
                "assessment_context_json": item["assessment_context_json"],
                "safety_gate": item["safety_gate"],
            },
        )
        if ver_created or ver.state != "published":
            ver.state = "published"
            ver.published_at = ver.published_at or now
        milestone_v1[item["code"]] = ver

    quiz_v1: dict[str, AcademyKnowledgeQuizVersion] = {}
    for item in QUIZZES:
        defn, created = await _get_or_create_definition(
            db,
            AcademyKnowledgeQuiz,
            code=item["code"],
            title=item["title"],
            description=item["description"],
        )
        if created:
            counts["quizzes_created"] += 1
        ver, ver_created = await _get_or_create_v1(
            db,
            AcademyKnowledgeQuizVersion,
            "quiz_id",
            defn.id,
            {
                "questions_json": item["questions_json"],
                "passing_percent": item["passing_percent"],
            },
        )
        if ver_created or ver.state != "published":
            ver.state = "published"
            ver.published_at = ver.published_at or now
        quiz_v1[item["code"]] = ver

    lesson_v1: dict[str, AcademyLessonVersion] = {}
    for item in LESSONS:
        defn, created = await _get_or_create_definition(
            db,
            AcademyLessonBlueprint,
            code=item["code"],
            title=item["title"],
            description=item["description"],
        )
        if created:
            counts["lessons_created"] += 1
        ver, ver_created = await _get_or_create_v1(
            db,
            AcademyLessonVersion,
            "lesson_id",
            defn.id,
            {
                "duration_minutes": item["duration_minutes"],
                "blocks_json": item["blocks_json"],
                "safety_json": item["safety_json"],
            },
        )
        if ver.state == "draft":
            for drill_code, order_index, sets, distance_m in item["drills"]:
                dv = drill_v1[drill_code]
                exists = await db.scalar(
                    select(AcademyLessonDrill.id).where(
                        AcademyLessonDrill.lesson_version_id == ver.id,
                        AcademyLessonDrill.drill_version_id == dv.id,
                    )
                )
                if exists is None:
                    db.add(
                        AcademyLessonDrill(
                            lesson_version_id=ver.id,
                            drill_version_id=dv.id,
                            order_index=order_index,
                            sets=sets,
                            distance_m=distance_m,
                        )
                    )
            await db.flush()
            ver.state = "published"
            ver.published_at = ver.published_at or now
        lesson_v1[item["code"]] = ver

    for item in MODULES:
        defn, created = await _get_or_create_definition(
            db,
            AcademyModule,
            code=item["code"],
            title=item["title"],
            description=item["description"],
        )
        if created:
            counts["modules_created"] += 1
        ver, _ = await _get_or_create_v1(
            db,
            AcademyModuleVersion,
            "module_id",
            defn.id,
            {
                "outcomes_json": item["outcomes_json"],
                "completion_policy_json": item["completion_policy_json"],
            },
        )
        if ver.state == "draft":
            for idx, lesson_code in enumerate(item["lessons"]):
                lv = lesson_v1[lesson_code]
                exists = await db.scalar(
                    select(AcademyModuleLesson.id).where(
                        AcademyModuleLesson.module_version_id == ver.id,
                        AcademyModuleLesson.lesson_version_id == lv.id,
                    )
                )
                if exists is None:
                    db.add(
                        AcademyModuleLesson(
                            module_version_id=ver.id,
                            lesson_version_id=lv.id,
                            order_index=idx,
                        )
                    )
            for milestone_code, required, order_index in item["milestones"]:
                mv = milestone_v1[milestone_code]
                exists = await db.scalar(
                    select(AcademyModuleMilestone.id).where(
                        AcademyModuleMilestone.module_version_id == ver.id,
                        AcademyModuleMilestone.milestone_version_id == mv.id,
                    )
                )
                if exists is None:
                    db.add(
                        AcademyModuleMilestone(
                            module_version_id=ver.id,
                            milestone_version_id=mv.id,
                            required_for_completion=required,
                            order_index=order_index,
                        )
                    )
            for quiz_code, required in item["quizzes"]:
                qv = quiz_v1[quiz_code]
                exists = await db.scalar(
                    select(AcademyModuleQuiz.id).where(
                        AcademyModuleQuiz.module_version_id == ver.id,
                        AcademyModuleQuiz.quiz_version_id == qv.id,
                    )
                )
                if exists is None:
                    db.add(
                        AcademyModuleQuiz(
                            module_version_id=ver.id,
                            quiz_version_id=qv.id,
                            required_for_completion=required,
                        )
                    )
            await db.flush()
            ver.state = "published"
            ver.published_at = ver.published_at or now

    for ms_code, drill_code, priority, tip in REMEDIAL_DRILLS:
        ms_def = milestone_defs_by_code[ms_code]
        dr_def = drill_defs_by_code[drill_code]
        exists = await db.scalar(
            select(AcademyMilestoneRemedialDrill.id).where(
                AcademyMilestoneRemedialDrill.milestone_definition_id == ms_def.id,
                AcademyMilestoneRemedialDrill.drill_id == dr_def.id,
            )
        )
        if exists is None:
            db.add(
                AcademyMilestoneRemedialDrill(
                    milestone_definition_id=ms_def.id,
                    drill_id=dr_def.id,
                    priority_order=priority,
                    coach_tip=tip,
                )
            )
            counts["remedial_links_created"] += 1

    await db.commit()
    return counts


async def main() -> None:
    async with AsyncSessionLocal() as db:
        counts = await seed_academy_curriculum_v2(db)
        print("Seeded SwimBuddz Academy V2 curriculum catalog:", counts)


if __name__ == "__main__":
    asyncio.run(main())
