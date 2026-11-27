"""Canonical SwimBuddz Swim Scorecard skill & benchmark catalog (ADR-026).

Grounded in SwimBuddz's Adult Learning Principles, Total Immersion efficiency
order (Balance -> Streamline/Drag -> Propulsion), and the SwimBuddz Beginner
Method (ADR-019). Keeping the catalog versioned in code allows adding new
drills/skills cleanly without disruptive enum migrations, while every
`skill_code` remains a stable key persisted in `member_skill_progress`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class SkillDomainSpec:
    code: str
    title: str
    short_title: str
    description: str
    order_index: int
    layer: str  # "foundations" | "strokes" | "advanced"
    default_expanded_for_beginner: bool = False


@dataclass(frozen=True)
class SkillSpec:
    code: str
    domain: str
    title: str
    description: str
    ti_focal_point: str
    order_index: int
    is_safety_gate: bool = False
    club_readiness_auto_verify: bool = False


PROFICIENCY_ORDER: Dict[str, int] = {
    "not_started": 0,
    "learning": 1,
    "confident": 2,
    "mastered": 3,
}

PROFICIENCY_LABELS: Dict[str, str] = {
    "not_started": "Not started",
    "learning": "Learning",
    "confident": "Confident",
    "mastered": "Mastered",
}

STROKE_LABELS: Dict[str, str] = {
    "freestyle": "Freestyle",
    "backstroke": "Backstroke",
    "breaststroke": "Breaststroke",
    "butterfly": "Butterfly",
    "medley": "Individual Medley",
    "open_water": "Open Water",
    "any": "Any Stroke",
}

BENCHMARK_TYPE_SPECS: List[Dict[str, object]] = [
    {
        "code": "max_distance",
        "title": "Max Continuous Distance",
        "short_title": "Max Distance",
        "unit": "m",
        "description": "Longest nonstop swim distance covered comfortably in a stroke.",
        "higher_is_better": True,
        "requires_pool_length": False,
        "club_perk": False,
    },
    {
        "code": "time_trial",
        "title": "Time & Pace Trial",
        "short_title": "Time Trial",
        "unit": "s",
        "description": "Timed distance effort (25m, 50m, 100m, 200m, 400m, 1km) with auto pace/100m.",
        "higher_is_better": False,
        "requires_pool_length": False,
        "club_perk": True,
    },
    {
        "code": "spl",
        "title": "Strokes Per Length (SPL)",
        "short_title": "SPL",
        "unit": "strokes/length",
        "description": "Total arm strokes to cover one pool length — core TI distance-per-stroke metric.",
        "higher_is_better": False,
        "requires_pool_length": True,
        "club_perk": True,
    },
    {
        "code": "stroke_rate",
        "title": "Stroke Rate (SPM)",
        "short_title": "Stroke Rate",
        "unit": "spm",
        "description": "Cadence measured in strokes per minute — pair with SPL to track true efficiency.",
        "higher_is_better": None,
        "requires_pool_length": False,
        "club_perk": True,
    },
    {
        "code": "swolf",
        "title": "SWOLF Efficiency",
        "short_title": "SWOLF",
        "unit": "score",
        "description": "Time (seconds) + strokes for one pool length. Lower means faster and smoother.",
        "higher_is_better": False,
        "requires_pool_length": True,
        "club_perk": True,
    },
    {
        "code": "open_water",
        "title": "Open Water Swim",
        "short_title": "Open Water",
        "unit": "m",
        "description": "Tracked open-water swim distance, duration, pace/100m, and sea/lagoon conditions.",
        "higher_is_better": True,
        "requires_pool_length": False,
        "club_perk": True,
    },
]

BENCHMARK_TYPE_BY_CODE: Dict[str, Dict[str, object]] = {
    spec["code"]: spec
    for spec in BENCHMARK_TYPE_SPECS  # type: ignore[misc]
}

CLUB_ONLY_BENCHMARK_TYPES = {
    str(spec["code"]) for spec in BENCHMARK_TYPE_SPECS if spec["club_perk"]
}

SKILL_DOMAIN_SPECS: Tuple[SkillDomainSpec, ...] = (
    SkillDomainSpec(
        code="foundations",
        title="Water Foundations & Safety",
        short_title="Foundations",
        description="Comfort, rhythmic breathing, buoyancy, deep-water safety, and Club readiness gates.",
        order_index=1,
        layer="foundations",
        default_expanded_for_beginner=True,
    ),
    SkillDomainSpec(
        code="freestyle",
        title="Freestyle (Front Crawl)",
        short_title="Freestyle",
        description="Balance-first freestyle mechanics progressing from horizontal trim to smooth continuous laps.",
        order_index=2,
        layer="strokes",
        default_expanded_for_beginner=True,
    ),
    SkillDomainSpec(
        code="backstroke",
        title="Backstroke",
        short_title="Backstroke",
        description="Relaxed supine balance, alternating shoulder roll, steady kick, and wall flag awareness.",
        order_index=3,
        layer="strokes",
    ),
    SkillDomainSpec(
        code="breaststroke",
        title="Breaststroke",
        short_title="Breaststroke",
        description="Symmetrical whip kick, compact pull-breathe rhythm, and streamlined glide efficiency.",
        order_index=4,
        layer="strokes",
    ),
    SkillDomainSpec(
        code="butterfly",
        title="Butterfly",
        short_title="Butterfly",
        description="Fluid chest-driven undulation, two-beat dolphin timing, and relaxed sweeping recovery.",
        order_index=5,
        layer="strokes",
    ),
    SkillDomainSpec(
        code="advanced_skills",
        title="Turns, Starts & Open Water",
        short_title="Turns & Open Water",
        description="Pool walls, streamlined push-offs, safe deep-water entries, and calm open-water navigation.",
        order_index=6,
        layer="advanced",
    ),
)

SKILL_SPECS: Tuple[SkillSpec, ...] = (
    # ── 1. Water Foundations & Safety ─────────────────────────────────────
    SkillSpec(
        code="fnd_water_comfort",
        domain="foundations",
        title="Water Comfort & Calm Submersion",
        description="Submerges face cleanly with relaxed shoulders and zero panic.",
        ti_focal_point="Soft eyes looking straight down; let the water support your head.",
        order_index=1,
        club_readiness_auto_verify=True,
    ),
    SkillSpec(
        code="fnd_rhythmic_breathing",
        domain="foundations",
        title="Rhythmic Underwater Exhalation (Bobs)",
        description="Continuous steady exhale underwater followed by calm surface inhales without gasping.",
        ti_focal_point="Gentle continuous hum through the nose whenever your face is submerged.",
        order_index=2,
        club_readiness_auto_verify=True,
    ),
    SkillSpec(
        code="fnd_front_back_float",
        domain="foundations",
        title="Unassisted Front & Back Float",
        description="Holds relaxed prone and supine floats for 15–30 seconds without fighting buoyancy.",
        ti_focal_point="Press chest gently into the water and keep ears submerged to float hips high.",
        order_index=3,
        club_readiness_auto_verify=True,
    ),
    SkillSpec(
        code="fnd_standing_recovery",
        domain="foundations",
        title="Controlled Standing Recovery",
        description="Transitions calmly from front or back float back to standing on pool floor.",
        ti_focal_point="Tuck knees to chest first, press palms down gently, then plant feet.",
        order_index=4,
        club_readiness_auto_verify=True,
    ),
    SkillSpec(
        code="fnd_deep_water_tread",
        domain="foundations",
        title="30s Deep-Water Treading",
        description="Safety gate: sustains calm vertical treading in deep water for at least 30 seconds unassisted.",
        ti_focal_point="Slow eggbeater/scissor legs and wide sculling palms — stay relaxed and breathe.",
        order_index=5,
        is_safety_gate=True,
        club_readiness_auto_verify=True,
    ),
    SkillSpec(
        code="fnd_survival_25m",
        domain="foundations",
        title="25m Continuous Survival Swim",
        description="Safety gate: swims 25 meters nonstop without touching the pool floor or lane rope.",
        ti_focal_point="Unrushed rhythm and complete underwater exhalation from wall to wall.",
        order_index=6,
        is_safety_gate=True,
        club_readiness_auto_verify=True,
    ),
    SkillSpec(
        code="fnd_pool_etiquette",
        domain="foundations",
        title="Lane Etiquette & Water Safety",
        description="Circle-swims safely, leaves wall space, respects pod spacing, and knows self-rescue basics.",
        ti_focal_point="Keep right in shared lanes, wait 5s between swimmers, and check before pushing off.",
        order_index=7,
    ),
    # ── 2. Freestyle (Front Crawl) ────────────────────────────────────────
    SkillSpec(
        code="free_horizontal_balance",
        domain="freestyle",
        title="Horizontal Trim & Head Alignment",
        description="Maintains neutral head position and high hips with a compact, relaxed flutter kick.",
        ti_focal_point="Crown of the head points forward; eyes look down so hips ride near the surface.",
        order_index=1,
    ),
    SkillSpec(
        code="free_streamline_glide",
        domain="freestyle",
        title="Streamline Push-Off & Glide",
        description="Pushes off in a narrow streamline and holds a patient extended lead arm.",
        ti_focal_point="Shape a long, sleek vessel before taking the first stroke.",
        order_index=2,
    ),
    SkillSpec(
        code="free_side_breathing",
        domain="freestyle",
        title="Smooth Side-Rotation Breathing",
        description="Rotates head smoothly with the body to breathe low in the bow wave without lifting chin.",
        ti_focal_point="Keep one goggle lens in the water and let the shoulder roll uncover your mouth.",
        order_index=3,
    ),
    SkillSpec(
        code="free_bilateral_breathing",
        domain="freestyle",
        title="Bilateral Breathing (Every 3 Strokes)",
        description="Breathes comfortably to both left and right sides with symmetrical stroke timing.",
        ti_focal_point="Exhale steadily over strokes 1–2–3 so each side feels equally unhurried.",
        order_index=4,
    ),
    SkillSpec(
        code="free_body_rotation",
        domain="freestyle",
        title="Whole-Body Rotation (Core Roll)",
        description="Rotates torso cleanly side-to-side as one unit rather than swimming flat.",
        ti_focal_point="Turn from hips and core into a clean skate position on each spear.",
        order_index=5,
    ),
    SkillSpec(
        code="free_high_elbow_recovery",
        domain="freestyle",
        title="Relaxed High-Elbow Recovery & Quiet Entry",
        description="Swings a relaxed forearm forward with elevated elbow and enters quietly without splash.",
        ti_focal_point="Lead recovery with the elbow; hang a heavy, limp wrist and slide fingers cleanly in.",
        order_index=6,
    ),
    SkillSpec(
        code="free_catch_finish",
        domain="freestyle",
        title="Bent-Elbow Catch & Pull Finish to Thigh",
        description="Anchors forearm downward cleanly and finishes the stroke smoothly past the hip.",
        ti_focal_point="Reach over a barrel into a bent-elbow hold and finish cleanly to your thigh.",
        order_index=7,
    ),
    SkillSpec(
        code="free_continuous_50m",
        domain="freestyle",
        title="50m+ Relaxed Continuous Freestyle",
        description="Completes 50 meters or more of composed, rhythmic freestyle with consistent breathing.",
        ti_focal_point="Swim tall and quiet — maintain stroke length rather than speeding up when tired.",
        order_index=8,
    ),
    # ── 3. Backstroke ─────────────────────────────────────────────────────
    SkillSpec(
        code="back_supine_balance",
        domain="backstroke",
        title="Supine Alignment & Head Stability",
        description="Floats flat on the back with still head, ears submerged, and neutral chin.",
        ti_focal_point="Rest the back of your head on a water pillow and look straight up.",
        order_index=1,
    ),
    SkillSpec(
        code="back_flutter_kick",
        domain="backstroke",
        title="Continuous Backstroke Flutter Kick",
        description="Steady hip-driven kick with relaxed ankles and knees staying just beneath the surface.",
        ti_focal_point="Kick from the hips with floppy ankles; boil the surface lightly without knees popping out.",
        order_index=2,
    ),
    SkillSpec(
        code="back_shoulder_roll",
        domain="backstroke",
        title="Shoulder Rotation & Straight-Arm Recovery",
        description="Alternates shoulder roll cleanly with thumb-out exit and pinky-first entry.",
        ti_focal_point="Lift the shoulder clear of the surface first; enter quietly at 11 and 1 o'clock.",
        order_index=3,
    ),
    SkillSpec(
        code="back_alternating_pull",
        domain="backstroke",
        title="Bent-Arm Catch & Continuous Rhythm",
        description="Connects catch to hip-roll propulsion with unbroken opposite-arm timing.",
        ti_focal_point="Keep one arm pulling while the other recovers so momentum never stalls.",
        order_index=4,
    ),
    SkillSpec(
        code="back_flags_finish",
        domain="backstroke",
        title="Backstroke Flags & Wall Finish",
        description="Counts strokes accurately from overhead flags to touch the wall safely without peaking.",
        ti_focal_point="Know your exact stroke count from the 5m flags to stay streamlined to the wall.",
        order_index=5,
    ),
    # ── 4. Breaststroke ───────────────────────────────────────────────────
    SkillSpec(
        code="breast_whip_kick",
        domain="breaststroke",
        title="Symmetrical Whip Kick",
        description="Draws heels cleanly toward glutes with dorsiflexed feet and snaps legs together.",
        ti_focal_point="Keep knees narrow, turn toes out before pressing water back, finish ankles together.",
        order_index=1,
    ),
    SkillSpec(
        code="breast_pull_breathe",
        domain="breaststroke",
        title="Compact Pull & Low Breath",
        description="Uses a compact outsweep/insweep in front of shoulders with a low, forward breath.",
        ti_focal_point="Hands stay in front of your chest line; keep chin skimming the water as you breathe.",
        order_index=2,
    ),
    SkillSpec(
        code="breast_timing_glide",
        domain="breaststroke",
        title="Pull–Breathe–Kick–Glide Timing",
        description="Separates pull and kick cleanly to enjoy a sleek, low-drag glide every cycle.",
        ti_focal_point="Shoot hands forward into streamline BEFORE finishing the kick, then ride the glide.",
        order_index=3,
    ),
    SkillSpec(
        code="breast_continuous_50m",
        domain="breaststroke",
        title="50m+ Continuous Breaststroke",
        description="Swims 50 meters or more of unhurried, efficient breaststroke with steady glide distance.",
        ti_focal_point="Stretch each glide for a calm two-count before starting the next pull.",
        order_index=4,
    ),
    # ── 5. Butterfly ──────────────────────────────────────────────────────
    SkillSpec(
        code="fly_dolphin_undulation",
        domain="butterfly",
        title="Chest-Driven Dolphin Undulation",
        description="Generates fluid body wave from sternum through hips and relaxed feet.",
        ti_focal_point="Press the chest gently forward and down; let the hips and toes follow like a wave.",
        order_index=1,
    ),
    SkillSpec(
        code="fly_two_beat_timing",
        domain="butterfly",
        title="Two-Beat Dolphin Kick Timing",
        description="Synchronizes first kick on hand entry and second kick as hands exit past hips.",
        ti_focal_point="Kick in, kick out — let the entry kick launch your glide forward.",
        order_index=2,
    ),
    SkillSpec(
        code="fly_arm_recovery",
        domain="butterfly",
        title="Low Sweeping Double-Arm Recovery",
        description="Recovers both arms wide and relaxed just above the water surface.",
        ti_focal_point="Thumbs skim low and wide with soft elbows; slide forward without slamming water.",
        order_index=3,
    ),
    SkillSpec(
        code="fly_breath_timing",
        domain="butterfly",
        title="Low Forward Breath Timing",
        description="Breathes early with chin near water and drops head cleanly before hands re-enter.",
        ti_focal_point="Head goes down first so your hips stay buoyant as hands spear forward.",
        order_index=4,
    ),
    SkillSpec(
        code="fly_continuous_25m",
        domain="butterfly",
        title="25m+ Continuous Butterfly",
        description="Completes a full 25m length of rhythmic, controlled butterfly without stalling.",
        ti_focal_point="Prioritize smooth wave rhythm over brute arm power.",
        order_index=5,
    ),
    # ── 6. Turns, Starts & Open Water ─────────────────────────────────────
    SkillSpec(
        code="adv_open_turn",
        domain="advanced_skills",
        title="Clean Open Turn & Streamline Push-Off",
        description="Touches cleanly, tucks knees swiftly, rotates onto side, and pushes off streamlined.",
        ti_focal_point="Stay low off the wall in a tight streamline before breaking the surface.",
        order_index=1,
    ),
    SkillSpec(
        code="adv_flip_turn",
        domain="advanced_skills",
        title="Freestyle Flip (Tumble) Turn",
        description="Executes a tight somersault into a streamlined push-off rotating from back to front.",
        ti_focal_point="Tuck chin cleanly at the T-mark, exhale through nose, plant feet shoulder-width.",
        order_index=2,
    ),
    SkillSpec(
        code="adv_dive_entry",
        domain="advanced_skills",
        title="Safe Standing / Kneeling Dive Entry",
        description="Safety gate: performs a controlled, streamlined head-first entry in verified deep water.",
        ti_focal_point="Lock biceps behind ears, tuck chin, and enter through one clean hole in the water.",
        order_index=3,
        is_safety_gate=True,
    ),
    SkillSpec(
        code="adv_open_water_sighting",
        domain="advanced_skills",
        title="Open-Water Sighting (Alligator Eyes)",
        description="Lifts eyes smoothly to sight buoys/landmarks without dropping hips or breaking rhythm.",
        ti_focal_point="Peek forward just over the surface, then roll immediately to the side to breathe.",
        order_index=4,
    ),
    SkillSpec(
        code="adv_open_water_calm",
        domain="advanced_skills",
        title="Open-Water Buoyancy & Choppy-Water Calm",
        description="Safety gate: swims calmly in open water without pool walls/floor, handling chop and rests.",
        ti_focal_point="Rely on calm back-float rests, bilateral breathing, and steady sighting rhythm.",
        order_index=5,
        is_safety_gate=True,
    ),
)

DOMAIN_BY_CODE: Dict[str, SkillDomainSpec] = {d.code: d for d in SKILL_DOMAIN_SPECS}
SKILL_BY_CODE: Dict[str, SkillSpec] = {s.code: s for s in SKILL_SPECS}
SKILLS_BY_DOMAIN: Dict[str, List[SkillSpec]] = {}
for _skill in SKILL_SPECS:
    SKILLS_BY_DOMAIN.setdefault(_skill.domain, []).append(_skill)
for _domain_skills in SKILLS_BY_DOMAIN.values():
    _domain_skills.sort(key=lambda s: s.order_index)


def get_skill_spec(skill_code: str) -> Optional[SkillSpec]:
    return SKILL_BY_CODE.get(skill_code)


def serialize_catalog() -> dict:
    """Return JSON-serializable catalog payload for clients."""
    return {
        "proficiencies": [
            {"code": code, "label": PROFICIENCY_LABELS[code], "rank": rank}
            for code, rank in PROFICIENCY_ORDER.items()
        ],
        "strokes": [
            {"code": code, "label": label} for code, label in STROKE_LABELS.items()
        ],
        "benchmark_types": list(BENCHMARK_TYPE_SPECS),
        "domains": [
            {
                "code": d.code,
                "title": d.title,
                "short_title": d.short_title,
                "description": d.description,
                "order_index": d.order_index,
                "layer": d.layer,
                "default_expanded_for_beginner": d.default_expanded_for_beginner,
                "skills": [
                    {
                        "code": s.code,
                        "domain": s.domain,
                        "title": s.title,
                        "description": s.description,
                        "ti_focal_point": s.ti_focal_point,
                        "order_index": s.order_index,
                        "is_safety_gate": s.is_safety_gate,
                    }
                    for s in SKILLS_BY_DOMAIN.get(d.code, [])
                ],
            }
            for d in SKILL_DOMAIN_SPECS
        ],
    }
