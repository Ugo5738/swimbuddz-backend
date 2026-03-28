"""Lagos domain-knowledge priors for seasonality forecasting.

These constants encode what we know about swimming demand patterns in Lagos
before any platform data exists. They serve as the starting point for the
model and are progressively replaced by real observations.

Sources:
- Lagos rainy season: April–October (NiMet historical data)
- Nigerian school calendar: 3 terms with breaks
- Public holiday calendar: Federal Government of Nigeria
- Cash-flow patterns: salary cycle on 25th–30th of month
"""

# ── Monthly seasonal demand indices ──
# Normalised so the 12-month average ≈ 1.00.
# Values >1.0 = above-average demand, <1.0 = below-average.
LAGOS_SEASONAL_INDICES: dict[int, float] = {
    1: 1.15,  # Jan: New Year energy, dry season, school resumes
    2: 1.10,  # Feb: Dry season peak, pleasant weather
    3: 1.05,  # Mar: Still dry, school term winding down
    4: 0.85,  # Apr: Early rains begin, Easter disruption
    5: 0.80,  # May: Rains intensify, mid-school-term
    6: 0.75,  # Jun: Peak rainy season, school holidays begin
    7: 0.70,  # Jul: Heaviest rainfall month + school holiday
    8: 0.80,  # Aug: "August break" — brief drier spell mid-rainy season
    9: 0.85,  # Sep: Rains tapering, third term begins
    10: 1.00,  # Oct: Rains ending, Oct 1 Independence Day
    11: 1.10,  # Nov: Dry season begins, pleasant weather returns
    12: 0.85,  # Dec: Holidays, travel season, Christmas/New Year disruption
}

# ── Lagos monthly rainfall averages (mm) ──
# Source: NiMet / World Bank climate data for Lagos (1991-2020 averages)
LAGOS_MONTHLY_RAINFALL_MM: dict[int, float] = {
    1: 13.0,
    2: 30.0,
    3: 65.0,
    4: 120.0,
    5: 200.0,
    6: 315.0,
    7: 250.0,
    8: 85.0,  # August break
    9: 200.0,
    10: 145.0,
    11: 35.0,
    12: 15.0,
}

RAINFALL_CATEGORIES: dict[str, tuple[float, float]] = {
    "dry": (0, 50),
    "light": (50, 100),
    "moderate": (100, 200),
    "heavy": (200, 300),
    "peak": (300, 999),
}


def rainfall_category(mm: float) -> str:
    """Classify monthly rainfall into a category."""
    for cat, (lo, hi) in RAINFALL_CATEGORIES.items():
        if lo <= mm < hi:
            return cat
    return "peak"


# ── Nigerian school calendar (typical) ──
# Term 1: Jan–Mar, Term 2: May–Jul, Term 3: Sep–Dec
# Holidays: Apr (Easter break), Aug (long break), Dec (Christmas break)
LAGOS_SCHOOL_CALENDAR: dict[int, dict] = {
    1: {"term_active": True, "exam_period": False, "holiday": False},
    2: {"term_active": True, "exam_period": False, "holiday": False},
    3: {
        "term_active": True,
        "exam_period": True,
        "holiday": False,
    },  # End-of-term exams
    4: {"term_active": False, "exam_period": False, "holiday": True},  # Easter break
    5: {"term_active": True, "exam_period": False, "holiday": False},
    6: {
        "term_active": True,
        "exam_period": True,
        "holiday": False,
    },  # End-of-term exams
    7: {"term_active": False, "exam_period": False, "holiday": True},  # Long vacation
    8: {"term_active": False, "exam_period": False, "holiday": True},  # Long vacation
    9: {"term_active": True, "exam_period": False, "holiday": False},
    10: {"term_active": True, "exam_period": False, "holiday": False},
    11: {
        "term_active": True,
        "exam_period": True,
        "holiday": False,
    },  # End-of-term exams
    12: {
        "term_active": False,
        "exam_period": False,
        "holiday": True,
    },  # Christmas break
}

# ── Nigerian public holidays 2026 ──
LAGOS_PUBLIC_HOLIDAYS_2026: dict[int, list[str]] = {
    1: ["New Year's Day"],
    2: [],
    3: [],
    4: ["Good Friday", "Easter Monday"],
    5: ["Workers' Day", "Democracy Day"],
    6: [],
    7: [],
    8: [],
    9: [],
    10: ["Independence Day"],
    11: [],
    12: ["Christmas Day", "Boxing Day"],
}
# Eid holidays shift yearly — add manually when dates are confirmed
# Typical: 2 days Eid al-Fitr (Ramadan end) + 2 days Eid al-Adha

# ── Default model parameters ──
DEFAULT_BASELINE_ATTENDANCE = (
    150  # Estimated monthly total attendance for a young platform
)
DEFAULT_TREND_RATE = 0.015  # 1.5% monthly growth (startup assumption)
DEFAULT_LAUNCH_MONTH = 1  # Month index 0 for trend calculation (Jan 2026)
DEFAULT_LAUNCH_YEAR = 2026

# ── Recommended actions per demand level ──
ACTION_RULES: dict[str, list[str]] = {
    "low": [
        "Run retention campaigns (discounts for existing members)",
        "Promote indoor/covered pool sessions",
        "Focus on academy cohort enrollment for next term",
        "Conserve cash — reduce discretionary spending",
        "Use downtime for coach training and content creation",
    ],
    "moderate": [
        "Standard operations — maintain service quality",
        "Begin pre-marketing for upcoming peak season",
        "Run referral programs to grow word-of-mouth",
        "Review and restock store inventory",
    ],
    "high": [
        "Maximise session capacity — add extra time slots",
        "Push new member acquisition campaigns",
        "Stock store inventory for increased demand",
        "Schedule community events and open meets",
    ],
    "peak": [
        "All hands on deck — maximum sessions, maximum coaches",
        "Premium pricing opportunity (limited-slot sessions)",
        "Capture testimonials, photos, and social content",
        "Plan and promote flagship community events",
        "Onboard new coaches if capacity is constrained",
    ],
}
